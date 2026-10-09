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
        self.assertEqual(self.namespace['logger'].warning.call_count, 2)

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

    async def test_teleport_cancellation_propagates_without_logging_or_operating_later_peers(self):
        self.failed.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.sync(self.source, [self.failed, self.healthy])
        self.failed.send_key.assert_not_awaited()
        self.healthy.teleport.assert_not_awaited()
        self.namespace['logger'].warning.assert_not_called()

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
