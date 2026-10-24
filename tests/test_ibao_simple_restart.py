"""Select-ready restart must hand off without requiring world pointers."""
import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker.errors import ClientClosedError, MemoryReadError
from src import ibao_core, ibao_runtime as runtime
from tests.test_ibao_restart_lifecycle import launcher_namespace, REAL_SLEEP


def install_real_initializer(ns):
    tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
              and n.name == '_init_client_attrs')
    ns.update(client_speeds={111: 35}, use_team_up=False, discard_duplicate_cards=False,
        fusion_result_side='left', kill_minions_first=False, automatic_team_based_combat=False,
        quest_finder_enabled=True, post_combat_wisps_enabled=False,
        combat_playstyle_overrides={}, active_combat_playstyle=None, default_config=object(),
        use_potions=False, buy_potions=False, client_to_follow=None, client_to_boost=None,
        _mask_uid=lambda uid: 'masked')
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), ns)


class SimpleRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_speed_survives_failed_prepare_and_distinct_retry_pid(self):
        ns = launcher_namespace()
        ns['client_speeds'][111] = 35
        # Real Client.process_id looks up its window; it becomes 0 after kill.
        old_values = vars(ns['_old']).copy()
        old_values.pop('process_id')
        old_type = type('OldWindowClient', (SimpleNamespace,), {
            'process_id': property(lambda self: 111 if 11 in ns['_handles'] else 0)})
        old = old_type(**old_values)
        ns['walker'].clients[:] = [old]
        next_handle = [100]
        def native(*args):
            next_handle[0] += 1
            ns['_handles'].add(next_handle[0])
            return next_handle[0]
        ns['launch_account_instance'] = native
        prepare_count = [0]
        async def prepare(client):
            prepare_count[0] += 1
            if prepare_count[0] == 1: raise ValueError('first prepare failed')
        resumed = asyncio.Event()
        async def worker(client, settings):
            if client is old: raise ibao_core.CharacterSelectionError('restart fixture')
            resumed.set()
            await asyncio.Event().wait()
        async def guard(run, clients): return await run()
        async def fast_sleep(_): await REAL_SLEEP(0)
        manager = runtime.IbaoGroups(lambda: ns['walker'].clients, Mock(), worker, guard,
                                    recovery=ns['_recover_ibao_client'])
        with patch('src.ibao_runtime.prepare_restarted_client', prepare), \
                patch('src.ibao_runtime.asyncio.sleep', fast_sleep):
            manager.add(['p1'], {})
            try:
                await asyncio.wait_for(resumed.wait(), 1)
                new = manager.groups[0]['workers'][0][0]
                self.assertEqual(prepare_count[0], 2)
                self.assertEqual(new.process_id, 202)
                self.assertIn(202, ns['client_speeds'], 'retry lost the original speed cache')
                self.assertEqual(ns['client_speeds'][202], 35)
                self.assertEqual(ns['_handles'], {102})
            finally:
                await manager.stop()

    async def test_normal_initializer_still_reads_character_memory(self):
        ns = launcher_namespace()
        install_real_initializer(ns)
        client = ns['walker'].client_cls(22)
        ns['launched_account_map'][22] = 'account'
        ns['wizlaunch'].update_player_gid = Mock()
        client.client_object = SimpleNamespace(speed_multiplier=AsyncMock(return_value=27))
        client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=170))
        client.game_client = SimpleNamespace(user_id=AsyncMock(return_value=202))
        await ns['_init_client_attrs'](client)
        self.assertEqual(ns['client_speeds'][122], 27)
        self.assertEqual(client.character_level, 170)
        self.assertEqual(client.player_gid, 202)
        self.assertEqual(client.account_nick, 'account')

    async def test_process_dying_at_prepare_return_is_still_cleaned(self):
        ns = launcher_namespace()
        async def prepare(client): ns['_handles'].discard(client.window_handle)
        with patch('src.ibao_runtime.prepare_restarted_client', prepare):
            with self.assertRaises(ClientClosedError):
                await ns['_relaunch_managed_client'](11, 'account', 'p1')
        self.assertEqual(ns['_handles'], set())
        self.assertEqual(ns['walker'].clients, [])
        self.assertEqual(ns['walker']._managed_handles, [])
        self.assertEqual(ns['launched_account_map'], {})
        self.assertEqual(ns['_relaunching_clients'], {})
        self.assertEqual(ns['_hooking_in_progress'], set())

    async def test_remove_missing_leaves_active_recovery_to_its_supervisor(self):
        old = SimpleNamespace(title='p1', is_running=lambda: False)
        group = dict(workers=[(old, None)], recovering=True, stopping=False)
        manager = runtime.IbaoGroups(lambda: [], Mock(), recovery=AsyncMock())
        manager.groups = [group]
        manager._last_publish = manager.clock()
        await manager.remove_missing()
        self.assertNotIn('_unexpected_loss', group)
        self.assertFalse(group['stopping'])

    async def test_ready_play_handoff_does_not_probe_world_memory(self):
        client = SimpleNamespace(title='p1', window_handle=11, process_id=111,
            root_window=object(), is_running=lambda: True, activate_hooks=AsyncMock(),
            zone_name=AsyncMock(side_effect=MemoryReadError(192)),
            is_loading=AsyncMock(side_effect=MemoryReadError(192)))
        with patch('src.ibao_core.is_visible_by_path', AsyncMock(return_value=True)):
            await runtime.prepare_restarted_client(client)
        self.assertTrue(client._ibao_restart_handoff)
        self.assertEqual(client.zone_name.await_count, 0)
        self.assertEqual(client.is_loading.await_count, 0)

    async def test_select_ready_world_memory_192_does_not_kill_and_new_worker_collects(self):
        ns = launcher_namespace()
        install_real_initializer(ns)
        construct = ns['walker'].client_cls
        created, events = [], ns['_events']
        def client_cls(handle):
            client = construct(handle)
            client.client_object = SimpleNamespace(speed_multiplier=AsyncMock(side_effect=MemoryReadError(192)))
            client.stats = SimpleNamespace(reference_level=AsyncMock(side_effect=MemoryReadError(192)))
            client.game_client = SimpleNamespace(user_id=AsyncMock(side_effect=MemoryReadError(192)))
            client.root_window = object()
            async def hooks(**kwargs): events.append(('hooks', handle))
            client.activate_hooks = hooks
            created.append(client)
            return client
        ns['walker'].client_cls = client_cls
        resumed = asyncio.Event()
        async def worker(client, settings):
            if client is ns['_old']:
                raise ibao_core.CharacterSelectionError('restart fixture')
            # A normal later read may still fail while the select screen is up.
            try:
                await client.client_object.speed_multiplier()
            except MemoryReadError:
                pass
            settings['_collection_callback']()
            resumed.set()
            await asyncio.Event().wait()
        async def guard(run, clients): return await run()
        manager = runtime.IbaoGroups(lambda: ns['walker'].clients, Mock(), worker, guard,
                                    recovery=ns['_recover_ibao_client'])
        with patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()), \
                patch('src.ibao_core.is_visible_by_path', AsyncMock(return_value=True)):
            manager.add(['p1'], {})
            group = manager.groups[0]
            try:
                for _ in range(200):
                    if resumed.is_set(): break
                    await REAL_SLEEP(.005)
                self.assertTrue(resumed.is_set(), 'post-prepare address 192 prevented takeover/production')
                new = created[0]
                self.assertEqual(ns['_handles'], {101})
                self.assertNotIn(('kill', 101), events)
                self.assertIs(group['workers'][0][0], new)
                self.assertEqual(group['collected'], 1)
                self.assertEqual(len(group['workers']), 1)
                self.assertEqual(new.account_nick, 'account')
                self.assertEqual(new.player_gid, 101)
                self.assertEqual(new.client_object.speed_multiplier.await_count, 1,
                                 'only the worker should read the unavailable world pointer')
                self.assertEqual(new.stats.reference_level.await_count, 0)
                self.assertEqual(new.game_client.user_id.await_count, 0)
            finally:
                await manager.stop()

    async def test_native_reservation_ends_before_prepare_and_stale_context_is_not_health_gate(self):
        ns = launcher_namespace()
        active = [True]
        async def prepare(client):
            self.assertEqual(ns['_account_launches'], {}, 'native lock still controls prepare/takeover')
            active[0] = False
        context = dict(account='account', handle=11, title='p1', is_active=lambda: active[0])
        with patch('src.ibao_runtime.prepare_restarted_client', prepare), \
                patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()):
            new = await ns['_recover_ibao_client'](ns['_old'], {'_recovery_context': context})
        self.assertIs(new, ns['walker'].clients[0])
        self.assertEqual(ns['_handles'], {101})

    async def test_global_task_refresh_cannot_delay_or_roll_back_usable_client(self):
        ns = launcher_namespace()
        started = asyncio.Event()
        async def refresh():
            started.set()
            raise MemoryReadError(192)
        ns['_restart_active_toggle_tasks'] = refresh
        with patch('src.ibao_runtime.prepare_restarted_client', AsyncMock()), \
                patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()):
            new = await ns['_recover_ibao_client'](ns['_old'], {})
            await asyncio.wait_for(started.wait(), 1)
            await REAL_SLEEP(0)
        self.assertIs(new, ns['walker'].clients[0])
        self.assertEqual(ns['_handles'], {101})

