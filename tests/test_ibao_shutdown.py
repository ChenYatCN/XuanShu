import asyncio
import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from src.ibao_runtime import _TrackedMouseHandler, _TrackedClient, IbaoGroups, launch_for_recovery


class InterruptibleMouse:
    """Model WizWalker's awaitable hook install/remove boundaries."""
    def __init__(self, boundary):
        self.boundary = boundary
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.active = False
        self.clicks = 0

    async def __aenter__(self):
        self.active = True
        if self.boundary == 'enter':
            self.reached.set()
            await self.release.wait()

    async def __aexit__(self, *args):
        if self.boundary == 'exit':
            self.reached.set()
            await self.release.wait()
        self.active = False

    async def click_window(self, *args):
        self.clicks += 1


class IbaoShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def check_cancelled_mouse(self, boundary):
        mouse = InterruptibleMouse(boundary)
        task = asyncio.create_task(_TrackedMouseHandler(mouse, Mock()).click_window('quit'))
        await asyncio.wait_for(mouse.reached.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        mouse.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(mouse.active, 'mouse hook survived cancellation at ' + boundary)

    async def test_cancel_during_hook_activation_releases_hook(self):
        await self.check_cancelled_mouse('enter')

    async def test_cancel_during_hook_deactivation_releases_hook(self):
        await self.check_cancelled_mouse('exit')

    async def test_repeated_cancel_waits_for_mouse_cleanup_without_clicking(self):
        mouse = InterruptibleMouse('enter')
        task = asyncio.create_task(_TrackedMouseHandler(mouse, Mock()).click_window('quit'))
        await mouse.reached.wait()
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        self.assertFalse(task.done())
        mouse.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(mouse.active)
        self.assertEqual(mouse.clicks, 0)

    async def test_native_launch_is_joined_before_cancelled_stop_returns(self):
        started, release = threading.Event(), threading.Event()
        finished = threading.Event()
        released = Mock()
        def launch(*args):
            started.set()
            release.wait(2)
            finished.set()
            return 4321
        task = asyncio.create_task(launch_for_recovery(launch, 'account', 'path', released))
        try:
            await asyncio.to_thread(started.wait, 1)
            self.assertTrue(started.is_set())
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done(), 'stopped was acknowledged with native login still running')
            released.assert_not_called()
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        released.assert_called_once_with(4321)

    async def test_normal_native_launch_still_returns_handle(self):
        released = Mock()
        self.assertEqual(await launch_for_recovery(lambda *args: 4321, 'account', 'path', released), 4321)
        released.assert_not_called()

    async def test_not_started_has_no_production_worker(self):
        client = SimpleNamespace(title='p1', mouse_handler=AsyncMock())
        worker, recovery = AsyncMock(), AsyncMock()
        manager = IbaoGroups(lambda: [client], Mock(), worker=worker, recovery=recovery)
        await manager.remove_missing()
        await manager.stop()
        self.assertFalse(manager.groups)
        worker.assert_not_awaited()
        recovery.assert_not_awaited()
        client.mouse_handler.__aenter__.assert_not_awaited()

    async def test_stopping_state_and_revoked_input_until_worker_cleanup_finishes(self):
        client = SimpleNamespace(title='p1', send_key=AsyncMock(), teleport=AsyncMock(), mouse_handler=AsyncMock())
        started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        published, captured = [], {}
        async def worker(client, settings):
            captured.update(settings)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish.wait()
        async def guard(run, clients):
            return await run()
        recovery = AsyncMock()
        manager = IbaoGroups(lambda: [client], published.append, worker, guard, recovery=recovery)
        manager.add(['p1'], {})
        await started.wait()
        stop = asyncio.create_task(manager.stop())
        await cleaning.wait()
        self.assertEqual(manager.rows()[0]['state'], '正在停止')
        self.assertTrue(client.is_ibao)
        stale = _TrackedClient(client, Mock(), captured['_is_active'])
        for action in (lambda: stale.send_key('ESC'), lambda: stale.teleport('point'),
                       lambda: stale.mouse_handler.click_window('play')):
            with self.assertRaises(asyncio.CancelledError):
                await action()
        captured['_collection_callback']()
        self.assertEqual(manager.groups[0]['collected'], 0)
        finish.set()
        await stop
        self.assertFalse(manager.groups)
        self.assertFalse(client.is_ibao)
        self.assertEqual(manager.rows()[0]['state'], '已停止')
        client.send_key.assert_not_awaited()
        client.teleport.assert_not_awaited()
        recovery.assert_not_awaited()

    async def test_cancelled_replacement_drains_config_and_unhooks_new_client(self):
        import wizwalker
        ready, config_ready, config_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        replacement = SimpleNamespace(title='p1', close=AsyncMock())
        walker = SimpleNamespace(clients=[], _managed_handles=[], client_cls=lambda _: replacement)
        async def prepare(client):
            ready.set()
            await asyncio.Event().wait()
        async def configure(*args):
            config_ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                config_stopped.set()
        namespace = {
            'Client': object, 'asyncio': asyncio, 'walker': walker, 'logger': Mock(),
            'client_resizing_manager': SimpleNamespace(teardown_client=AsyncMock()),
            'window_config_applied': set(), 'launched_account_map': {},
            '_hooking_in_progress': set(), '_kill_process_by_handle': Mock(),
            'get_all_wizard_handles': lambda: [], 'released_handles': set(),
            '_send_hooked_clients_update': Mock(), 'utils': SimpleNamespace(get_wiz_install=lambda: 'path'),
            'wizlaunch': SimpleNamespace(launch_instance=lambda *args: 4321),
            'client_resizing': True, '_apply_account_window_config': configure,
            '_init_client_attrs': AsyncMock(), 'wizwalker': wizwalker,
        }
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == '_relaunch_managed_client')
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        with patch('src.ibao_runtime.prepare_restarted_client', prepare):
            task = asyncio.create_task(namespace['_relaunch_managed_client'](1234, 'account', 'p1'))
            await asyncio.wait_for(ready.wait(), 2)
            await config_ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(config_stopped.is_set())
        replacement.close.assert_awaited_once()
        namespace['_init_client_attrs'].assert_not_awaited()
        self.assertEqual(walker.clients, [])
        self.assertEqual(walker._managed_handles, [])
        self.assertEqual(namespace['_hooking_in_progress'], set())
        self.assertIn(4321, namespace['released_handles'])
