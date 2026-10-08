import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.hotkey_groups import HotkeyGroups, client_available
from src.paths import play_button_path, potion_usage_path
from src.utils import is_free, logout_and_in, refresh_character_memory


class CharacterSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p2', window_handle=2, combat_config='saved config',
            questing_status=True, combat_status=True, is_ibao=False,
            _world_view_window=object(), _character_registry_addr=123, _quest_client_manager_addr=456,
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='Wallaru/Area'),
            quest_id=AsyncMock(return_value=42), stats=SimpleNamespace(reference_level=AsyncMock(return_value=170)))
        self.peer = SimpleNamespace(title='p1', window_handle=1, combat_status=True)
        self.clients = [self.peer, self.client]
        self.selecting = True
        self.hud = False
        self.now = 10.0
        self.groups = HotkeyGroups(lambda: self.clients, lambda *args: asyncio.Future())
        self.addAsyncCleanup(self.groups.stop)
        async def initialize(client):
            client.questing_status = False
            client.combat_status = False
            client.wizard_name = None
            client.combat_config = 'recomputed config'
        self.namespace = {
            'asyncio': asyncio, 'walker': SimpleNamespace(clients=self.clients),
            '_hooking_in_progress': set(), 'is_visible_by_path': AsyncMock(side_effect=self.visible),
            'play_button_path': play_button_path, 'potion_usage_path': potion_usage_path,
            'stop_questing_on_client_loss': AsyncMock(), 'hotkey_groups': self.groups,
            'fishing_groups': SimpleNamespace(stop=AsyncMock()), 'bot_tasks': {},
            'overlapping_bot_groups': lambda keys, titles: [key for key in keys if 'p2' in key],
            'refresh_character_memory': refresh_character_memory,
            '_init_client_attrs': AsyncMock(side_effect=initialize), '_restart_always_on_tasks': Mock(),
            'time': SimpleNamespace(monotonic=lambda: self.now),
            'logger': SimpleNamespace(warning=Mock(), info=Mock(), debug=Mock()),
        }
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'maintain_client_character_session')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), self.namespace)
        self.maintain = self.namespace['maintain_client_character_session']

    async def visible(self, client, path):
        if path == play_button_path:
            return self.selecting
        self.assertEqual(path, potion_usage_path[:-1])
        return self.hud

    def start_combat(self, client):
        async def wait_forever(*args):
            await asyncio.Future()
        self.groups.run = wait_forever
        self.groups._start('toggle_combat', [client])

    async def test_selection_stops_affected_group_once_preserves_peer_and_clears_caches(self):
        self.start_combat(self.peer)
        self.start_combat(self.client)
        await self.maintain(self.client)
        self.assertFalse(client_available(self.client, self.clients))
        self.assertTrue(client_available(self.peer, self.clients))
        self.assertEqual(len(self.groups.groups), 1)
        self.assertIs(next(iter(self.groups.groups.values()))[0][0], self.peer)
        self.namespace['stop_questing_on_client_loss'].assert_awaited_once_with(
            'p2', reason='已返回选角色界面')
        self.assertFalse(self.client.combat_status)
        self.assertTrue(self.peer.combat_status)
        for field in ('_world_view_window', '_character_registry_addr', '_quest_client_manager_addr'):
            self.assertIsNone(getattr(self.client, field))
        self.assertEqual(self.client.combat_config, 'saved config')
        await self.maintain(self.client)
        self.namespace['stop_questing_on_client_loss'].assert_awaited_once()
        self.namespace['_init_client_attrs'].assert_not_awaited()

    async def test_reentry_requires_stable_hud_and_refreshes_without_auto_restart(self):
        await self.maintain(self.client)
        self.selecting = False
        await self.maintain(self.client)
        self.client.is_loading.assert_not_awaited()
        self.hud = True
        await self.maintain(self.client)
        self.assertTrue(self.client._character_selection_active)
        self.now += 0.4
        await self.maintain(self.client)
        self.namespace['_init_client_attrs'].assert_not_awaited()
        self.now += 0.2
        await self.maintain(self.client)
        self.namespace['_init_client_attrs'].assert_awaited_once_with(self.client)
        self.namespace['_restart_always_on_tasks'].assert_not_called()
        self.assertTrue(client_available(self.client, self.clients))
        self.assertFalse(self.client.questing_status)
        self.assertFalse(self.client.combat_status)
        self.assertEqual(self.client.combat_config, 'saved config')
        self.assertEqual(self.groups.groups, {})
        await self.maintain(self.client)
        self.namespace['_init_client_attrs'].assert_awaited_once()

    async def test_idle_client_selection_and_reentry_leave_peer_quest_and_combat_tasks_running(self):
        self.client.questing_status = False
        self.client.combat_status = False
        self.peer.questing_status = True
        self.start_combat(self.peer)
        peer_key = next(iter(self.groups.groups))
        peer_task = self.groups.groups[peer_key][1]
        peer_monitor = asyncio.create_task(asyncio.Event().wait())
        self.addAsyncCleanup(self.cancel_task, peer_monitor)
        self.namespace['_restart_always_on_tasks'].side_effect = peer_monitor.cancel
        await self.maintain(self.client)
        self.namespace['stop_questing_on_client_loss'].assert_not_awaited()
        self.assertIs(self.groups.groups[peer_key][1], peer_task)
        self.assertFalse(peer_task.done())
        self.assertTrue(self.peer.questing_status)
        self.assertTrue(self.peer.combat_status)
        self.selecting = False
        self.hud = True
        await self.maintain(self.client)
        self.now += 1
        await self.maintain(self.client)
        self.namespace['_restart_always_on_tasks'].assert_not_called()
        self.assertFalse(peer_monitor.done())
        self.assertIs(self.groups.groups[peer_key][1], peer_task)
        self.assertFalse(peer_task.done())
        self.assertTrue(self.peer.questing_status)
        self.assertTrue(self.peer.combat_status)
        self.assertFalse(self.client.questing_status)
        self.assertFalse(self.client.combat_status)
        self.assertIn(self.client, self.clients)

    async def cancel_task(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_pending_quest_group_membership_still_stops_related_questing(self):
        self.client.questing_status = False
        key = ('toggle_questing', ('p2',))
        task = asyncio.create_task(asyncio.Event().wait())
        self.groups.groups[key] = ((self.client,), task)
        await self.maintain(self.client)
        self.namespace['stop_questing_on_client_loss'].assert_awaited_once_with('p2', reason='已返回选角色界面')
        self.assertNotIn(key, self.groups.groups)
        self.assertTrue(task.cancelled())

    async def test_cleanup_retry_preserves_quest_membership_after_partial_status_reset(self):
        async def stop(*_args, **_kwargs):
            self.client.questing_status = False
            if self.namespace['stop_questing_on_client_loss'].await_count == 1:
                raise RuntimeError('partial cleanup')
        self.namespace['stop_questing_on_client_loss'].side_effect = stop
        with self.assertRaisesRegex(RuntimeError, 'partial cleanup'):
            await self.maintain(self.client)
        await self.maintain(self.client)
        self.assertEqual(self.namespace['stop_questing_on_client_loss'].await_count, 2)
        self.assertTrue(self.client._character_session_cleanup_done)

    async def test_loading_or_invalid_character_keeps_input_gate_closed(self):
        await self.maintain(self.client)
        self.selecting = False
        self.hud = True
        self.client.is_loading.return_value = True
        await self.maintain(self.client)
        self.client.is_loading.return_value = False
        self.client.stats.reference_level.return_value = 0
        await self.maintain(self.client)
        self.namespace['_init_client_attrs'].assert_not_awaited()
        self.assertTrue(self.client._character_selection_active)

    async def test_failed_refresh_keeps_gate_closed_and_preserves_playstyle(self):
        await self.maintain(self.client)
        self.selecting = False
        self.hud = True
        await self.maintain(self.client)
        self.now += 1.0
        async def fail(client):
            client.combat_config = 'partially initialized config'
            raise RuntimeError('character stats temporarily unavailable')
        self.namespace['_init_client_attrs'].side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'temporarily unavailable'):
            await self.maintain(self.client)
        self.assertTrue(self.client._character_selection_active)
        self.assertEqual(self.client.combat_config, 'saved config')
        self.namespace['_restart_always_on_tasks'].assert_not_called()

    async def test_failed_cleanup_is_retried_instead_of_leaving_workers_active(self):
        self.namespace['stop_questing_on_client_loss'].side_effect = [RuntimeError('retry cleanup'), None]
        with self.assertRaisesRegex(RuntimeError, 'retry cleanup'):
            await self.maintain(self.client)
        self.assertTrue(self.client._character_selection_active)
        self.assertFalse(self.client._character_session_cleanup_done)
        await self.maintain(self.client)
        self.assertTrue(self.client._character_session_cleanup_done)
        self.assertEqual(self.namespace['stop_questing_on_client_loss'].await_count, 2)

    async def test_intentional_logout_ibao_and_hook_initialization_do_not_stop_tasks(self):
        for field in ('_intentional_character_switch', 'is_ibao'):
            with self.subTest(field=field):
                setattr(self.client, field, True)
                await self.maintain(self.client)
                setattr(self.client, field, False)
        self.namespace['_hooking_in_progress'].add(2)
        await self.maintain(self.client)
        self.namespace['is_visible_by_path'].assert_not_awaited()
        self.namespace['stop_questing_on_client_loss'].assert_not_awaited()

    async def test_selection_is_not_free_even_when_old_memory_claims_idle(self):
        self.client._character_selection_active = True
        self.assertFalse(await is_free(self.client))
        self.client.is_loading.assert_not_awaited()

    async def test_explicit_logout_marker_and_cache_reset_survive_cancellation(self):
        self.client.send_key = AsyncMock()
        async def wait_window(client, path, *args):
            self.assertTrue(client._intentional_character_switch)
            raise asyncio.CancelledError()
        with patch('src.utils.wait_for_window_by_path', new=AsyncMock(side_effect=wait_window)):
            with self.assertRaises(asyncio.CancelledError):
                await logout_and_in(self.client)
        self.assertFalse(self.client._intentional_character_switch)
        self.assertIsNone(self.client._quest_client_manager_addr)
        self.assertIsNone(self.client._world_view_window)

