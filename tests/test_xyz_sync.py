import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from wizwalker import Keycode, MemoryInvalidated
from wizwalker.errors import ClientClosedError, ExceptionalTimeout
from src.gui.commands import GUICommand, GUICommandType
from src.task_lifecycle import gather_owned
from src.bot_targeting import resolve_bot_clients
from src.hotkey_groups import client_available
from tests.test_party_area_proof import client
from tests.test_scoped_runtime import load_function


class XYZSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = client(1, 1000)
        self.failed = client(2, 2000)
        self.healthy = client(3, 3000)
        self.unselected = client(4, 4000)
        self.namespace = dict(Client=object, Keycode=Keycode, logger=Mock(),
            asyncio=SimpleNamespace(sleep=AsyncMock()), gather_owned=gather_owned)
        self.sync = load_function('xyz_sync', self.namespace)

    async def test_all_targets_use_one_source_position_and_yaw_snapshot(self):
        self.source.body.position.side_effect = ['first XYZ', 'later XYZ']
        self.source.body.yaw.side_effect = [1.5, 2.5]
        await self.sync(self.source, [self.failed, self.healthy], turn_after=False)
        self.source.body.position.assert_awaited_once()
        self.source.body.yaw.assert_awaited_once()
        for target in (self.failed, self.healthy):
            target.teleport.assert_awaited_once_with('first XYZ', yaw=1.5)
            target.send_key.assert_not_awaited()
        self.namespace['asyncio'].sleep.assert_awaited_once_with(.3)

    async def test_blocked_target_check_or_teleport_does_not_queue_peer(self):
        for stage in ('is_loading', 'teleport'):
            with self.subTest(stage=stage):
                self.setUp()
                blocked = asyncio.Event()
                release = asyncio.Event()
                peer_teleported = asyncio.Event()

                async def slow(*args, **kwargs):
                    blocked.set()
                    await release.wait()
                    return False

                async def healthy(*args, **kwargs):
                    peer_teleported.set()

                getattr(self.failed, stage).side_effect = slow
                self.healthy.teleport.side_effect = healthy
                task = asyncio.create_task(self.sync(self.source, [self.failed, self.healthy]))
                try:
                    await asyncio.wait_for(blocked.wait(), 1)
                    await asyncio.wait_for(peer_teleported.wait(), 1)
                    self.assertFalse(task.done())
                    # Preserve the turn phase and final wait after all teleports.
                    self.healthy.send_key.assert_not_awaited()
                    self.namespace['asyncio'].sleep.assert_not_awaited()
                    release.set()
                    await asyncio.wait_for(task, 1)
                    self.failed.teleport.assert_awaited_once_with('XYZ', yaw=0)
                    self.healthy.teleport.assert_awaited_once_with('XYZ', yaw=0)
                    self.assertEqual(self.healthy.send_key.await_count, 2)
                    self.namespace['asyncio'].sleep.assert_awaited_once_with(.3)
                finally:
                    release.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_delayed_target_rechecks_source_zone_against_shared_snapshot(self):
        peer_teleported = asyncio.Event()

        async def slow_check():
            await peer_teleported.wait()
            return False

        async def healthy(*args, **kwargs):
            self.source.zone_name.return_value = 'New/Zone'
            peer_teleported.set()

        self.failed.is_loading.side_effect = slow_check
        self.healthy.teleport.side_effect = healthy
        await asyncio.wait_for(self.sync(self.source, [self.failed, self.healthy],
                                         turn_after=False), 1)
        self.failed.teleport.assert_not_awaited()
        self.healthy.teleport.assert_awaited_once_with('XYZ', yaw=0)

    async def test_initial_target_refill_recovery_or_loading_does_not_block_peer(self):
        for state in ('refilling_potions', 'quest_recovery_owner', 'loading'):
            with self.subTest(state=state):
                self.setUp()
                if state == 'loading':
                    self.failed.is_loading.return_value = True
                else:
                    setattr(self.failed, state, True if state == 'refilling_potions' else 'recovery')
                await self.sync(self.source, [self.failed, self.healthy])
                self.failed.teleport.assert_not_awaited()
                self.failed.send_key.assert_not_awaited()
                self.healthy.teleport.assert_awaited_once_with('XYZ', yaw=0)
                self.assertEqual(self.healthy.send_key.await_count, 2)

    async def test_state_changed_during_snapshot_is_rechecked_per_target(self):
        for actor in ('source', 'failed'):
            for state in ('refilling_potions', 'quest_recovery_owner', 'loading'):
                with self.subTest(actor=actor, state=state):
                    self.setUp()
                    member = getattr(self, actor)

                    async def position():
                        if state == 'loading':
                            member.is_loading.return_value = True
                        else:
                            setattr(member, state, True if state == 'refilling_potions' else 'recovery')
                        return 'XYZ'

                    self.source.body.position.side_effect = position
                    await self.sync(self.source, [self.failed, self.healthy], turn_after=False)
                    self.failed.teleport.assert_not_awaited()
                    if actor == 'source':
                        self.healthy.teleport.assert_not_awaited()
                    else:
                        self.healthy.teleport.assert_awaited_once_with('XYZ', yaw=0)
                    self.source.body.position.assert_awaited_once()
                    self.source.body.yaw.assert_awaited_once()

    async def test_real_teleport_timeout_skips_only_failed_client_and_does_not_turn_it(self):
        error = ExceptionalTimeout('Timed out waiting for coro should_update')
        self.failed.teleport.side_effect = error
        await self.sync(self.source, [self.failed, self.healthy])
        self.failed.send_key.assert_not_awaited()
        self.healthy.teleport.assert_awaited_once_with('XYZ', yaw=0)
        self.assertEqual([c.kwargs['key'] for c in self.healthy.send_key.await_args_list],
                         [Keycode.A, Keycode.D])
        self.unselected.teleport.assert_not_awaited()
        self.unselected.send_key.assert_not_awaited()
        warning = self.namespace['logger'].warning.call_args.args
        self.assertEqual(warning[1:4], ('p1', 'p2', 'ExceptionalTimeout'))
        self.assertIs(warning[4], error)

    async def test_source_memory_read_failure_never_escapes_or_uses_unknown_coordinates(self):
        self.source.body.position.side_effect = MemoryInvalidated('source invalidated')
        await self.sync(self.source, [self.failed, self.healthy])
        self.failed.teleport.assert_not_awaited()
        self.healthy.teleport.assert_not_awaited()
        self.failed.send_key.assert_not_awaited()
        self.healthy.send_key.assert_not_awaited()
        self.source.body.position.assert_awaited_once()
        self.assertEqual(self.namespace['logger'].warning.call_count, 1)

    async def test_target_state_read_failure_does_not_stop_selected_peer(self):
        self.failed.is_loading.side_effect = MemoryInvalidated('target invalidated')
        await self.sync(self.source, [self.failed, self.healthy], turn_after=False)
        self.failed.teleport.assert_not_awaited()
        self.healthy.teleport.assert_awaited_once()

    async def test_turn_failure_stays_local_and_does_not_send_second_key_to_failed_client(self):
        self.failed.send_key.side_effect = ClientClosedError()
        await self.sync(self.source, [self.failed, self.healthy])
        self.failed.send_key.assert_awaited_once_with(key=Keycode.A, seconds=.1)
        self.assertEqual([c.kwargs['key'] for c in self.healthy.send_key.await_args_list],
                         [Keycode.A, Keycode.D])
        self.namespace['logger'].warning.assert_called_once()

    async def test_teleport_cancellation_propagates_and_drains_started_peers(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def cancelled(*args, **kwargs):
            await started.wait()
            raise asyncio.CancelledError()

        async def healthy(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        self.failed.teleport.side_effect = cancelled
        self.healthy.teleport.side_effect = healthy
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.sync(self.source, [self.failed, self.healthy]), 1)
        self.assertTrue(stopped.is_set())
        self.failed.send_key.assert_not_awaited()
        self.healthy.send_key.assert_not_awaited()
        self.namespace['logger'].warning.assert_not_called()
        self.namespace['asyncio'].sleep.assert_not_awaited()

    async def test_parent_cancellation_drains_all_pending_teleports(self):
        started = asyncio.Event()
        active, stopped = set(), set()

        async def teleport(*args, member, **kwargs):
            active.add(member)
            if len(active) == 2:
                started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.add(member)

        async def first(*args, **kwargs):
            await teleport(*args, member='p2', **kwargs)

        async def second(*args, **kwargs):
            await teleport(*args, member='p3', **kwargs)

        self.failed.teleport.side_effect = first
        self.healthy.teleport.side_effect = second
        task = asyncio.create_task(self.sync(self.source, [self.failed, self.healthy]))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(stopped, {'p2', 'p3'})
            self.failed.send_key.assert_not_awaited()
            self.healthy.send_key.assert_not_awaited()
            self.namespace['logger'].warning.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_turn_cancellation_drains_all_owned_turn_tasks(self):
        started = asyncio.Event()
        active = set()
        stopped = set()
        async def turn(*_, member, **kwargs):
            active.add(member.title)
            if len(active) == 2:
                started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.add(member.title)
        async def failed_turn(*args, **kwargs):
            await turn(*args, member=self.failed, **kwargs)
        async def healthy_turn(*args, **kwargs):
            await turn(*args, member=self.healthy, **kwargs)
        self.failed.send_key.side_effect = failed_turn
        self.healthy.send_key.side_effect = healthy_turn
        task = asyncio.create_task(self.sync(self.source, [self.failed, self.healthy]))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(stopped, {'p2', 'p3'})
            self.failed.send_key.assert_awaited_once()
            self.healthy.send_key.assert_awaited_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_later_sync_can_succeed_after_timeout_without_restart(self):
        self.failed.teleport.side_effect = [ExceptionalTimeout('should_update'), None]
        await self.sync(self.source, [self.failed])
        self.failed.send_key.assert_not_awaited()
        await self.sync(self.source, [self.failed])
        self.assertEqual(self.failed.teleport.await_count, 2)
        self.assertEqual(self.failed.send_key.await_count, 2)

    async def test_actual_gui_xyz_dispatch_survives_timeout_and_handles_next_command(self):
        self.failed.teleport.side_effect = [ExceptionalTimeout('should_update'), None]
        self.namespace.update(walker=SimpleNamespace(clients=[self.source, self.failed]),
            foreground_client=self.source,
            xuanshu_gui=SimpleNamespace(GUICommandType=GUICommandType),
            resolve_bot_clients=resolve_bot_clients, client_available=client_available,
            commands=[GUICommand(GUICommandType.XYZSync, {'clients': ['p1', 'p2']})] * 2,
            next_command=AsyncMock())
        load_function('teleport_hotkey_clients', self.namespace)
        load_function('xyz_sync_hotkey', self.namespace)
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        gui = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == 'handle_gui')
        actual_match, actual_case = next((n, case) for n in ast.walk(gui)
            if isinstance(n, ast.Match) for case in n.cases
            if isinstance(case.pattern, ast.MatchValue)
            and isinstance(case.pattern.value, ast.Attribute)
            and case.pattern.value.attr == 'XYZSync')
        driver = ast.parse('async def dispatch():\n for com in commands:\n  pass\n  await next_command()\n')
        driver.body[0].body[0].body[0] = ast.Match(subject=actual_match.subject, cases=[actual_case])
        ast.fix_missing_locations(driver)
        exec(compile(driver, 'XuanShu.py', 'exec'), self.namespace)
        await self.namespace['dispatch']()
        self.assertEqual(self.namespace['next_command'].await_count, 2)
        self.assertEqual(self.failed.teleport.await_count, 2)
        self.assertEqual(self.failed.send_key.await_count, 2)
