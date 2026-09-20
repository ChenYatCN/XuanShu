import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from src.ibao_runtime import create_session, parse_locations, IbaoGroups
from src.ibao_locations import DEFAULT_LOCATIONS
from src.gui.ibao_dialog import TripleClickGate


class IbaoTests(unittest.TestCase):
    def test_locations_and_isolated_sessions(self):
        self.assertIn('The Commons', parse_locations(DEFAULT_LOCATIONS)[1])
        with self.assertRaises(ValueError):
            parse_locations('bad')
        a = create_session(SimpleNamespace(title='p1', window_handle=1), {'switch_delay': .3})
        b = create_session(SimpleNamespace(title='p2', window_handle=2), {'switch_delay': .8})
        a['activeClients'][0].wizLst.append('first')
        self.assertEqual(b['activeClients'][0].wizLst, [])
        self.assertEqual(b['get_config']()['CHARACTER_SWITCH_DELAY'], .8)
        self.assertIs(a['azothFarmer'].__globals__, a)
        self.assertIs(b['logout_and_in'].__globals__, b)
        self.assertNotIn('runmanager', a)

    def test_three_click_gate(self):
        gate = TripleClickGate()
        self.assertFalse(gate.click(True, 0))
        self.assertFalse(gate.click(True, .2))
        self.assertTrue(gate.click(True, .4))
        gate.click(True, 1)
        gate.click(False, 1.1)
        self.assertFalse(gate.click(True, 1.2))
        self.assertFalse(gate.click(True, 3))


class IbaoGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_client_stops_even_with_recovery_configured(self):
        from unittest.mock import AsyncMock
        client = SimpleNamespace(title='p1')
        clients = [client]
        async def worker(*args):
            await asyncio.Event().wait()
        async def guard(run, clients):
            await run()
        recovery = AsyncMock()
        manager = IbaoGroups(lambda: clients, Mock(), worker, guard, recovery=recovery)
        manager.add(['p1'], {})
        await asyncio.sleep(0)
        client.is_running = lambda: False
        await manager.remove_missing()
        self.assertFalse(manager.groups)
        self.assertFalse(client.is_ibao)
        recovery.assert_not_awaited()

    async def test_hook_inactive_is_terminal_not_recovery_loop(self):
        from unittest.mock import AsyncMock
        from wizwalker.errors import HookNotActive
        client = SimpleNamespace(title='p1')
        async def worker(*args):
            raise HookNotActive('Client')
        async def guard(run, clients):
            await run()
        recovery = AsyncMock()
        manager = IbaoGroups(lambda: [client], Mock(), worker, guard, recovery=recovery)
        manager.add(['p1'], {})
        await asyncio.gather(manager.groups[0]['workers'][0][1], return_exceptions=True)
        await asyncio.sleep(0)
        self.assertFalse(manager.groups)
        recovery.assert_not_awaited()

    async def test_cursor_is_released_after_individual_click(self):
        from unittest.mock import AsyncMock
        from src.ibao_runtime import _TrackedMouseHandler
        mouse = AsyncMock()
        proxy = _TrackedMouseHandler(mouse, Mock())
        await proxy.click_window('button')
        mouse.__aenter__.assert_awaited_once()
        mouse.__aexit__.assert_awaited_once()
        mouse.click_window.assert_awaited_once_with('button')

    async def test_restart_dismisses_title_before_character_ready(self):
        from unittest.mock import AsyncMock, patch
        from src.ibao_runtime import prepare_restarted_client
        client = SimpleNamespace(root_window=object(), activate_hooks=AsyncMock(), send_key=AsyncMock())
        client.mouse_handler = AsyncMock()
        client.is_loading = AsyncMock(side_effect=[True, False])
        client.zone_name = AsyncMock(return_value='World/Zone')
        client.body = SimpleNamespace(position=AsyncMock())
        with patch('src.ibao_core.is_visible_by_path', AsyncMock(side_effect=[False, False, True, False, False])), \
             patch('src.ibao_core.click_window_until_gone', AsyncMock()) as click, \
             patch('src.ibao_runtime.asyncio.sleep', AsyncMock()):
            await prepare_restarted_client(client)
        client.activate_hooks.assert_awaited_once_with(wait_for_ready=False)
        from src import ibao_core
        click.assert_awaited_once()
        self.assertIs(click.await_args.args[0]._client, client)
        self.assertEqual(click.await_args.args[1], ibao_core.playButton)
        client.body.position.assert_awaited_once()
        self.assertEqual(client.send_key.await_count, 2)
        self.assertTrue(all(call.args[0].name == 'ESC' for call in client.send_key.call_args_list))

    async def test_restart_title_wait_has_timeout(self):
        from unittest.mock import AsyncMock, patch
        from src.ibao_runtime import prepare_restarted_client
        client = SimpleNamespace(root_window=object(), activate_hooks=AsyncMock(), send_key=AsyncMock())
        with patch('src.ibao_core.is_visible_by_path', AsyncMock(return_value=False)):
            with self.assertRaises(TimeoutError):
                await prepare_restarted_client(client, timeout=.01)

    async def test_run_client_does_not_hold_cursor_while_waiting(self):
        from unittest.mock import AsyncMock, patch
        from src.ibao_runtime import run_client
        from contextlib import asynccontextmanager
        events = []
        started = asyncio.Event()
        @asynccontextmanager
        async def mouse():
            events.append('activate')
            try:
                yield
            finally:
                events.append('deactivate')
        client = SimpleNamespace(title='p1', window_handle=1,
                                 root_window=object(), mouse_handler=mouse())
        async def visible(*args):
            self.assertEqual(events, [])
            return True
        async def farmer(*args):
            self.assertEqual(events, [])
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append('farmer-cleaned')
        scope = {'is_visible_by_path': visible, 'playButton': [], 'azothFarmer': farmer}
        with patch('src.ibao_runtime.create_session', return_value=scope):
            task = asyncio.create_task(run_client(client, {}))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(events, ['farmer-cleaned'])

    async def test_three_restarts_then_stops_despite_repeated_input(self):
        from unittest.mock import AsyncMock
        client = SimpleNamespace(title='p1')
        async def guard(run, clients):
            return await run()
        async def worker(client, settings):
            while True:
                settings['_activity_callback']()
                await asyncio.sleep(.001)
        recovery = AsyncMock(return_value=client)
        manager = IbaoGroups(lambda: [client], Mock(), worker, guard,
                             recovery=recovery, inactivity_timeout=.01)
        manager.add(['p1'], {})
        await asyncio.wait_for(asyncio.gather(manager.groups[0]['workers'][0][1],
                                             return_exceptions=True), 1)
        await asyncio.sleep(0)
        self.assertEqual(recovery.await_count, 3)
        self.assertFalse(manager.groups)
        self.assertIn('连续 3 次', manager.results[0]['state'])

    async def test_statistics_and_account_settings_survive_reload(self):
        from copy import deepcopy
        data = {}
        store = Mock()
        store.get_setting.side_effect = lambda key: deepcopy(data.get(key))
        store.set_setting.side_effect = lambda key, value: data.update({key: deepcopy(value)})
        client = SimpleNamespace(title='p1', account_nick='account-a')
        started = asyncio.Event()
        async def guard(run, clients):
            return await run()
        async def worker(client, settings):
            settings['_collection_callback']()
            started.set()
            await asyncio.Event().wait()
        manager = IbaoGroups(lambda: [client], Mock(), worker, guard, store=store)
        manager.add(['p1'], {'switch_delay': .7})
        await started.wait()
        manager.clear_round()
        self.assertEqual(manager.groups[0]['collected'], 0)
        self.assertEqual(manager.groups[0]['restarts'], 0)
        self.assertTrue(all(row.get('archived') for row in manager.results))
        await manager.stop()
        restored = IbaoGroups(lambda: [], Mock(), store=store)
        self.assertEqual(restored.configs['account-a']['switch_delay'], .7)
        self.assertEqual(sum(row['collected'] for row in restored.results), 1)

    async def test_chinese_receipt_counts_only_new_message(self):
        from unittest.mock import AsyncMock
        client = SimpleNamespace(title='p1', window_handle=1, root_window=object())
        scope = create_session(client, {})
        chat = SimpleNamespace(maybe_text=AsyncMock(side_effect=[
            '你获得了：万灵秘药', '你获得了：万灵秘药',
            '你获得了：万灵秘药\n你获得了：万灵秘药']))
        scope['window_from_path'] = AsyncMock(return_value=chat)
        scope['petPowerVisibility'] = AsyncMock(return_value=False)
        scope['petPower'] = AsyncMock()
        await asyncio.wait_for(scope['azothCollect'](client, 0), 2)
        self.assertEqual(chat.maybe_text.await_count, 3)

    async def test_ui_guard_matches_worker_lifecycle(self):
        client = SimpleNamespace(title='p1')
        worker_started = asyncio.Event()
        guard_stopped = asyncio.Event()

        async def worker(guarded_client, settings):
            self.assertIs(guarded_client, client)
            self.assertEqual(settings['switch_delay'], .3)
            worker_started.set()
            await asyncio.Event().wait()

        async def guard(run, clients):
            self.assertEqual([c._client for c in clients], [client])
            try:
                return await run()
            finally:
                guard_stopped.set()

        manager = IbaoGroups(lambda: [client], Mock(), worker, guard)
        manager.add(['p1'], {'switch_delay': .3})
        await asyncio.wait_for(worker_started.wait(), 1)
        self.assertTrue(client.is_ibao)
        await manager.stop(['p1'])
        self.assertTrue(guard_stopped.is_set())
        self.assertFalse(client.is_ibao)

    async def test_cancel_farmer_drains_dialogue_task_without_logout(self):
        from unittest.mock import AsyncMock
        client = SimpleNamespace(title='p1', window_handle=1, root_window=object(), send_key=AsyncMock())
        session = create_session(client, {})
        started, stopped = asyncio.Event(), asyncio.Event()
        async def dialogue(c):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        session['skipDialogue'] = dialogue
        session['is_visible_by_path'] = AsyncMock(return_value=False)
        task = asyncio.create_task(session['azothFarmer'](client, 0))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(stopped.is_set())
        self.assertFalse(any(call.args[0].name == 'ESC' for call in client.send_key.call_args_list))

    async def test_stop_and_disconnect_are_independent(self):
        clients = [SimpleNamespace(title='p1'), SimpleNamespace(title='p2')]
        started = {}
        async def worker(client, settings):
            started[client.title] = settings
            await asyncio.Event().wait()
        manager = IbaoGroups(lambda: clients, Mock(), worker)
        settings = {'switch_delay': .3}
        manager.add(['p1', 'p2'], settings)
        self.assertEqual(len(manager.groups), 2)
        self.assertTrue(all(len(g['workers']) == 1 for g in manager.groups))
        self.assertIsNot(manager.groups[0]['settings'], manager.groups[1]['settings'])
        settings['switch_delay'] = 9
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(started['p1']['switch_delay'], .3)
        with self.assertRaises(ValueError):
            manager.add(['p1'], {})
        await manager.stop(['p1'])
        self.assertEqual([c.title for g in manager.groups for c, t in g['workers']], ['p2'])
        clients.clear()
        await manager.remove_missing()
        self.assertEqual(manager.groups, [])
        await manager.stop()

    async def test_idle_recovery_preserves_settings_and_other_client(self):
        from unittest.mock import AsyncMock
        old = SimpleNamespace(title='p1')
        other = SimpleNamespace(title='p2')
        replacement = SimpleNamespace(title='p1')
        clients = [old, other]
        resumed = asyncio.Event()
        async def guard(run, clients):
            return await run()
        async def worker(client, settings):
            if client is replacement:
                self.assertEqual(settings['switch_delay'], .7)
                settings['_collection_callback']()
                resumed.set()
            if client is old:
                settings['_collection_callback']()
            while True:
                if client is not old:
                    settings['_progress_callback']()
                await asyncio.sleep(.005)
        async def recover(client, settings):
            self.assertIs(client, old)
            clients[0] = replacement
            return replacement
        recovery = AsyncMock(side_effect=recover)
        manager = IbaoGroups(lambda: clients, Mock(), worker, guard,
                             recovery=recovery, inactivity_timeout=.05)
        manager.add(['p1', 'p2'], {'switch_delay': .7})
        try:
            await asyncio.wait_for(resumed.wait(), 1)
            recovery.assert_awaited_once()
            self.assertEqual(manager.groups[0]['collected'], 2)
            self.assertTrue(other.is_ibao)
            self.assertFalse(old.is_ibao)
            await manager.stop(['p1'])
            self.assertFalse(replacement.is_ibao)
            self.assertTrue(other.is_ibao)
        finally:
            await manager.stop()

    async def test_stop_during_recovery_cancels_resume(self):
        client = SimpleNamespace(title='p1')
        recovering = asyncio.Event()
        cancelled = asyncio.Event()
        async def guard(run, clients):
            return await run()
        async def worker(client, settings):
            raise OSError('disconnected')
        async def recover(client, settings):
            recovering.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        manager = IbaoGroups(lambda: [], Mock(), worker, guard,
                             recovery=recover, inactivity_timeout=.01)
        manager.clients = lambda: [client]
        manager.add(['p1'], {})
        await asyncio.wait_for(recovering.wait(), 1)
        await manager.stop(['p1'])
        self.assertTrue(cancelled.is_set())
        self.assertEqual(manager.groups, [])
