import asyncio
import ast
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.auto_pet import auto_pet, nomnom
from src.gui.commands import GUICommand, GUICommandType
from src.hotkey_groups import HotkeyGroups, run_client_worker
from src.paths import (
    pet_feed_window_visible_path, pet_feed_window_cancel_button_path,
    pet_feed_window_energy_cost_textbox_path, skip_pet_game_button_path,
    skipped_pet_game_rewards_window_path,
)
from src.task_lifecycle import gather_owned


class PetEnergyStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_questing_does_not_send_stopped_client_back_to_pet_game(self):
        client = AsyncMock()
        client.auto_pet_status = False
        client.feeding_pet_status = True
        await auto_pet(client, False, False, questing=True)
        self.assertFalse(client.feeding_pet_status)
        client.zone_name.assert_not_awaited()
        client.send_key.assert_not_awaited()
        client.teleport.assert_not_awaited()

    async def exercise_energy(self, energy, only_dance=False):
        client = AsyncMock()
        client.title = 'p1'
        client.auto_pet_status = True
        picker_open = True
        cost = SimpleNamespace(maybe_text=AsyncMock(return_value='<center>8'))
        remaining = SimpleNamespace(maybe_text=AsyncMock(return_value=f'能量：{energy}/100'))

        async def control(_client, path):
            return cost if path == pet_feed_window_energy_cost_textbox_path else remaining

        async def visible(_client, path):
            return picker_open if path == pet_feed_window_visible_path else True

        async def click(_client, path):
            nonlocal picker_open
            self.assertIn(path, (pet_feed_window_cancel_button_path, skip_pet_game_button_path))
            picker_open = False

        with (
            patch('src.auto_pet._open_pet_game_window', new=AsyncMock(return_value=True)),
            patch('src.auto_pet._pet_picker_control', new=AsyncMock(side_effect=control)),
            patch('src.auto_pet._pet_picker_visible', new=AsyncMock(side_effect=visible)),
            patch('src.auto_pet._click_pet_picker', new=AsyncMock(side_effect=click)) as clicks,
            patch('src.auto_pet.is_visible_by_path', new=AsyncMock(
                side_effect=lambda c, path: path == skipped_pet_game_rewards_window_path)),
            patch('src.auto_pet.asyncio.sleep', new=AsyncMock()),
            patch('src.auto_pet.attempt_activate_dance_hook', new=AsyncMock()) as activate,
            patch('src.auto_pet.attempt_deactivate_dance_hook', new=AsyncMock()) as deactivate,
            patch('src.auto_pet._start_pet_dance_game', new=AsyncMock()) as start,
            patch('src.auto_pet.dancedance', new=AsyncMock()) as dance,
        ):
            await nomnom(client, False, only_dance)
        self.assertFalse(picker_open)
        self.assertFalse(client.feeding_pet_status)
        client.send_key.assert_not_awaited()
        activate.assert_not_awaited()
        deactivate.assert_not_awaited()
        start.assert_not_awaited()
        dance.assert_not_awaited()
        return client, clicks

    async def test_insufficient_energy_closes_picker_and_disables_only_this_client(self):
        peer = SimpleNamespace(auto_pet_status=True)
        for only_dance in (False, True):
            with self.subTest(only_dance=only_dance):
                client, clicks = await self.exercise_energy(7, only_dance)
                self.assertFalse(client.auto_pet_status)
                clicks.assert_awaited_once_with(client, pet_feed_window_cancel_button_path)
                self.assertTrue(peer.auto_pet_status)

    async def test_energy_equal_to_or_above_cost_still_allows_game(self):
        for energy in (8, 9):
            with self.subTest(energy=energy):
                client, clicks = await self.exercise_energy(energy)
                self.assertTrue(client.auto_pet_status)
                clicks.assert_awaited_once_with(client, skip_pet_game_button_path)


class PetEnergyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, clients, feed, extra_functions=()):
        names = {'auto_pet_loop', *extra_functions}
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.AsyncFunctionDef) and node.name in names]

        async def sleep(_seconds):
            await asyncio.sleep(0)

        namespace = dict(
            asyncio=SimpleNamespace(sleep=sleep, create_task=asyncio.create_task,
                                    current_task=asyncio.current_task, Event=asyncio.Event),
            walker=SimpleNamespace(clients=clients), nomnom=feed,
            auto_pet_status=True, auto_pet_task=None, freecam_status=False,
            ignore_pet_level_up=False, only_play_dance_game=False,
            gather_owned=gather_owned, run_client_worker=run_client_worker,
            logger=Mock(), gui_send_queue=Mock(), try_task_coro=AsyncMock(),
            scoped_worker_active=defaultdict(set),
            bool_to_string=lambda value: 'Enabled' if value else 'Disabled',
            xuanshu_gui=SimpleNamespace(GUICommand=GUICommand, GUICommandType=GUICommandType),
        )
        exec(compile('from __future__ import annotations\n' +
                     ast.unparse(ast.Module(body=functions, type_ignores=[])),
                     'XuanShu.py', 'exec'), namespace)
        return namespace

    async def test_legacy_loop_stops_after_next_round_and_can_restart(self):
        client = SimpleNamespace(title='p1', auto_pet_status=True)
        calls = 0

        async def feed(c, **kwargs):
            nonlocal calls
            calls += 1
            if calls >= 2:
                c.auto_pet_status = False

        namespace = self.runtime([client], AsyncMock(side_effect=feed))
        await asyncio.wait_for(namespace['auto_pet_loop'](), 1)
        self.assertEqual(calls, 2)
        self.assertFalse(namespace['auto_pet_status'])
        client.auto_pet_status = True
        namespace['auto_pet_status'] = True
        await asyncio.wait_for(namespace['auto_pet_loop'](), 1)
        self.assertEqual(calls, 3)

    async def test_scoped_stop_removes_only_exhausted_group_and_does_not_restore_flag(self):
        clients = [SimpleNamespace(title='p1', auto_pet_status=True),
                   SimpleNamespace(title='p2', auto_pet_status=False)]
        healthy = asyncio.Event()

        async def feed(c, **kwargs):
            if c is clients[0]:
                c.auto_pet_status = False
                return
            healthy.set()
            await asyncio.Event().wait()

        namespace = self.runtime(clients, AsyncMock(side_effect=feed), ('run_hotkey_group',))
        manager = HotkeyGroups(lambda: clients, namespace['run_hotkey_group'])
        namespace['hotkey_groups'] = manager
        try:
            await manager.toggle('toggle_auto_pet', ['p1', 'p2'])
            depleted_task = manager.groups[('toggle_auto_pet', frozenset((id(clients[0]),)))][1]
            await asyncio.wait_for(healthy.wait(), 1)
            await asyncio.wait_for(depleted_task, 1)
            self.assertFalse(clients[0].auto_pet_status)
            self.assertTrue(clients[1].auto_pet_status)
            self.assertEqual([c.title for members, _ in manager.groups.values() for c in members], ['p2'])
            self.assertEqual(namespace['scoped_worker_active']['toggle_auto_pet'], {id(clients[1])})
            # A fresh user toggle starts a new worker even after energy exhaustion.
            await manager.toggle('toggle_auto_pet', ['p1'])
            restarted = manager.groups[('toggle_auto_pet', frozenset((id(clients[0]),)))][1]
            await asyncio.wait_for(restarted, 1)
            self.assertEqual([call.args[0].title for call in namespace['nomnom'].await_args_list].count('p1'), 2)
        finally:
            await manager.stop()

    async def test_disabled_client_is_not_reopened_by_legacy_worker_restart(self):
        client = SimpleNamespace(title='p1', auto_pet_status=False, feeding_pet_status=True)
        feed = AsyncMock()
        namespace = self.runtime([client], feed)
        await asyncio.wait_for(namespace['auto_pet_loop'](), 1)
        feed.assert_not_awaited()
        self.assertFalse(client.feeding_pet_status)

    async def test_legacy_exhausted_client_does_not_stop_healthy_peer(self):
        clients = [SimpleNamespace(title='p1', auto_pet_status=True),
                   SimpleNamespace(title='p2', auto_pet_status=True)]
        healthy = asyncio.Event()

        async def feed(c, **kwargs):
            if c is clients[0]:
                c.auto_pet_status = False
                return
            healthy.set()
            await asyncio.Event().wait()

        namespace = self.runtime(clients, AsyncMock(side_effect=feed))
        task = asyncio.create_task(namespace['auto_pet_loop']())
        try:
            await asyncio.wait_for(healthy.wait(), 1)
            self.assertFalse(clients[0].auto_pet_status)
            self.assertFalse(clients[0].feeding_pet_status)
            self.assertTrue(clients[1].auto_pet_status)
            self.assertTrue(namespace['auto_pet_status'])
            self.assertFalse(task.done())
            self.assertEqual([call.args[0].title for call in namespace['nomnom'].await_args_list].count('p1'), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_legacy_toggle_restarts_completed_task_and_stops_mixed_flags(self):
        clients = [SimpleNamespace(title='p1', auto_pet_status=False),
                   SimpleNamespace(title='p2', auto_pet_status=True)]
        namespace = self.runtime(clients, AsyncMock(), ('toggle_auto_pet_hotkey',))
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        namespace['auto_pet_task'] = finished
        await namespace['toggle_auto_pet_hotkey']()
        task = namespace['auto_pet_task']
        try:
            self.assertTrue(namespace['auto_pet_status'])
            self.assertTrue(all(c.auto_pet_status for c in clients))
            clients[0].auto_pet_status = False
            await namespace['toggle_auto_pet_hotkey']()
            self.assertFalse(namespace['auto_pet_status'])
            self.assertTrue(all(not c.auto_pet_status for c in clients))
            self.assertIsNone(namespace['auto_pet_task'])
        finally:
            await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
