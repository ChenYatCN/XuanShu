import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.ibao_core import wizardInfo, CharacterSelectionError, Keycode
from src.ibao_runtime import create_session
from wizwalker.errors import ClientClosedError, HookNotActive


class CharacterSelectionTests(unittest.IsolatedAsyncioTestCase):
    def session(self, pages, *, page_turning=False, delay=.3, unreadable=0):
        state = SimpleNamespace(page=0, index=0, clock=0, unreadable=unreadable)
        client = SimpleNamespace(title='p1', window_handle=1, root_window=object())
        scope = create_session(client, {'page_turning': page_turning, 'switch_delay': delay})
        async def sleep(seconds):
            state.clock += seconds + .01
        scope['asyncio'] = SimpleNamespace(sleep=sleep,
            get_running_loop=lambda: SimpleNamespace(time=lambda: state.clock),
            timeout=asyncio.timeout)
        async def key(code, _duration=0):
            if code == Keycode.TAB:
                state.index = (state.index + 1) % len(pages[state.page])
        client.send_key = AsyncMock(side_effect=key)
        client.is_loading = AsyncMock(return_value=False)
        client.wait_for_zone_change = AsyncMock()
        async def visible(_root, path):
            if path == scope['logOutConfirm'] or path == scope['playButton']:
                return True
            if path == scope['rightClassRoomButton']:
                return len(pages) > 1 and state.page == 0
            if path == scope['leftClassRoomButton']:
                return state.page == 1
            return False
        scope['is_visible_by_path'] = visible
        async def read(path):
            if state.unreadable:
                state.unreadable -= 1
                raise RuntimeError('temporarily unreadable')
            wizard = pages[state.page][state.index]
            return {tuple(scope['txtName']): wizard.Name, tuple(scope['txtLevel']): wizard.Level,
                    tuple(scope['txtLocation']): wizard.Location}[tuple(path)]
        async def window(_root, path):
            async def control_visible():
                return await scope['is_visible_by_path'](_root, path)
            return SimpleNamespace(path=path, maybe_text=lambda: read(path),
                is_visible=AsyncMock(side_effect=control_visible))
        scope['window_from_path'] = window
        entered = []
        async def click(_client, path):
            if path == scope['rightClassRoomButton']:
                state.page, state.index = 1, 0
            elif path == scope['leftClassRoomButton']:
                state.page, state.index = 0, 0
            elif path == scope['playButton']:
                entered.append(pages[state.page][state.index])
        scope['click_window_until_gone'] = AsyncMock(side_effect=click)
        async def click_control(control):
            return await scope['click_window_until_gone'](client, control.path)
        client.mouse_handler = SimpleNamespace(click_window=AsyncMock(
            side_effect=click_control))
        return client, scope, state, entered

    def wizard(self, name):
        return wizardInfo(name, '100', 'The Commons', 0, 0, 0)

    async def test_delayed_settings_and_confirmation_are_clicked_when_visible(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        state.phase = 'closed'
        state.ready_at = .5
        original_visible = scope['is_visible_by_path']
        original_click = scope['click_window_until_gone']
        async def visible(root, path):
            if state.phase == 'closed' and state.clock >= state.ready_at:
                state.phase = 'settings'
            elif state.phase == 'pending' and state.clock >= state.ready_at:
                state.phase = 'confirm'
            if path == scope['quitButton']:
                return state.phase == 'settings'
            if path == scope['logOutConfirm']:
                return state.phase == 'confirm'
            if path == scope['playButton']:
                return state.phase == 'selection'
            return await original_visible(root, path)
        async def click(tracked, path):
            if path == scope['quitButton']:
                self.assertEqual(state.phase, 'settings')
                state.phase, state.ready_at = 'pending', state.clock + .25
            elif path == scope['logOutConfirm']:
                self.assertEqual(state.phase, 'confirm')
                state.phase = 'selection'
            else:
                await original_click(tracked, path)
        scope['is_visible_by_path'] = visible
        scope['click_window_until_gone'] = AsyncMock(side_effect=click)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertEqual(client.send_key.await_count, 1)
        self.assertEqual([call.args[1] for call in scope['click_window_until_gone'].await_args_list],
                         [scope['quitButton'], scope['logOutConfirm'], scope['playButton']])

    async def test_existing_settings_or_confirmation_do_not_receive_escape(self):
        for initial in ('settings', 'confirm'):
            with self.subTest(initial=initial):
                target = self.wizard('target')
                client, scope, state, entered = self.session([[target]])
                state.phase = initial
                async def visible(root, path):
                    return ((path == scope['quitButton'] and state.phase == 'settings')
                            or (path == scope['logOutConfirm'] and state.phase == 'confirm')
                            or (path == scope['playButton'] and state.phase == 'selection'))
                original_click = scope['click_window_until_gone']
                async def click(tracked, path):
                    if path == scope['quitButton']:
                        state.phase = 'confirm'
                    elif path == scope['logOutConfirm']:
                        state.phase = 'selection'
                    else:
                        await original_click(tracked, path)
                scope['is_visible_by_path'] = visible
                scope['click_window_until_gone'] = AsyncMock(side_effect=click)
                await scope['logout_and_in'](client, target, True, 'p1')
                client.send_key.assert_not_awaited()
                self.assertEqual(entered, [target])

    async def test_loading_transition_does_not_send_keys_or_click_stale_menu(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        client.is_loading = AsyncMock(side_effect=lambda: state.clock < .5)
        original_visible = scope['is_visible_by_path']
        async def visible(root, path):
            self.assertGreaterEqual(state.clock, .5)
            return await original_visible(root, path)
        scope['is_visible_by_path'] = visible
        await scope['logout_and_in'](client, target, True, 'p1')
        client.send_key.assert_not_awaited()
        self.assertEqual(entered, [target])

    async def test_stuck_logout_click_is_bounded_and_never_enters(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        scope['asyncio'].timeout = lambda seconds: asyncio.timeout(.02)
        scope['is_visible_by_path'] = AsyncMock(side_effect=lambda root, path: path == scope['quitButton'])
        cancelled = asyncio.Event()
        async def click(tracked, path):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        scope['click_window_until_gone'] = AsyncMock(side_effect=click)
        with self.assertRaisesRegex(CharacterSelectionError, '登出超时'):
            await scope['logout_and_in'](client, target, True, 'p1')
        self.assertTrue(cancelled.is_set())
        self.assertEqual(entered, [])
        client.wait_for_zone_change.assert_not_awaited()

    async def test_cancellation_during_logout_propagates_without_entering(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        client.is_loading = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [])
        client.send_key.assert_not_awaited()

    async def test_confirmation_over_visible_settings_is_prioritized(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        state.phase = 'settings'
        async def visible(root, path):
            return ((path == scope['quitButton'] and state.phase in ('settings', 'confirm'))
                    or (path == scope['logOutConfirm'] and state.phase == 'confirm')
                    or (path == scope['playButton'] and state.phase == 'selection'))
        original_click = scope['click_window_until_gone']
        async def click(tracked, path):
            if path == scope['quitButton']:
                self.assertEqual(state.phase, 'settings')
                state.phase = 'confirm'
            elif path == scope['logOutConfirm']:
                state.phase = 'selection'
            else:
                await original_click(tracked, path)
        scope['is_visible_by_path'] = visible
        scope['click_window_until_gone'] = AsyncMock(side_effect=click)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertEqual([call.args[0].path for call in client.mouse_handler.click_window.await_args_list],
                         [scope['quitButton'], scope['logOutConfirm']])
        client.send_key.assert_not_awaited()

    async def test_transient_logout_read_failure_is_retried_locally(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        client.is_loading = AsyncMock(side_effect=[RuntimeError('temporary read failure'), False])
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertGreaterEqual(client.is_loading.await_count, 2)
        client.send_key.assert_not_awaited()

    async def test_persistent_logout_read_failure_reaches_short_recovery(self):
        client, scope, state, entered = self.session([[self.wizard('target')]])
        client.is_loading = AsyncMock(side_effect=RuntimeError('temporary read failure'))
        with self.assertRaisesRegex(CharacterSelectionError, '登出超时'):
            await scope['logout_and_in'](client, self.wizard('target'), True, 'p1')
        self.assertGreaterEqual(state.clock, 15)
        self.assertLess(state.clock, 16)
        self.assertEqual(entered, [])
        client.send_key.assert_not_awaited()

    async def test_slow_loading_uses_separate_budget(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        client.is_loading = AsyncMock(side_effect=lambda: state.clock < 16)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertGreaterEqual(state.clock, 16)
        client.send_key.assert_not_awaited()

    async def test_permanent_loading_still_has_a_limit(self):
        client, scope, state, entered = self.session([[self.wizard('target')]])
        client.is_loading = AsyncMock(return_value=True)
        with self.assertRaisesRegex(CharacterSelectionError, '加载超时'):
            await scope['logout_and_in'](client, self.wizard('target'), True, 'p1')
        self.assertGreaterEqual(state.clock, 60)
        self.assertLess(state.clock, 61)
        self.assertEqual(entered, [])
        client.send_key.assert_not_awaited()

    async def test_missing_connection_is_not_swallowed_as_read_failure(self):
        for error in (HookNotActive('Client'), ClientClosedError()):
            with self.subTest(error=type(error).__name__):
                target = self.wizard('target')
                client, scope, state, entered = self.session([[target]])
                client.is_loading = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)):
                    await scope['logout_and_in'](client, target, True, 'p1')
                self.assertEqual(entered, [])
                client.send_key.assert_not_awaited()

    async def test_already_selected_target_no_tab_and_one_play(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertFalse(any(call.args[0] == Keycode.TAB for call in client.send_key.await_args_list))
        client.send_key.assert_not_awaited()
        client.wait_for_zone_change.assert_awaited_once()

    async def test_target_scanned_in_order_not_wrong_character(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[self.wizard('a'), self.wizard('b'), target]])
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])
        self.assertEqual(sum(c.args[0] == Keycode.TAB for c in client.send_key.await_args_list), 2)

    async def test_missing_target_never_enters_after_three_scans(self):
        client, scope, state, entered = self.session([[self.wizard('a'), self.wizard('b')]])
        with self.assertRaises(CharacterSelectionError):
            await scope['logout_and_in'](client, self.wizard('missing'), True, 'p1')
        self.assertEqual(entered, [])
        client.wait_for_zone_change.assert_not_awaited()

    async def test_page_turning_reads_target_after_flip(self):
        target = self.wizard('seventh')
        client, scope, state, entered = self.session([[self.wizard('a'), self.wizard('b')], [target]], page_turning=True)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])

    async def test_page_turning_disabled_cannot_enter_other_page(self):
        target = self.wizard('seventh')
        client, scope, state, entered = self.session([[self.wizard('a')], [target]])
        with self.assertRaises(CharacterSelectionError):
            await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(state.page, 0)
        self.assertEqual(entered, [])

    async def test_slow_configured_interval_does_not_expire_at_four_seconds(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[self.wizard('a'), target]], delay=10)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])

    async def test_transient_failed_read_recovers_without_stale_match(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[self.wizard('a'), target]], unreadable=2)
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [target])

    async def test_same_character_reload_verifies_and_never_tabs(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]])
        await scope['logout_and_in'](client, target, False, 'p1')
        self.assertEqual(entered, [target])
        client, scope, state, entered = self.session([[self.wizard('wrong')]])
        with self.assertRaises(CharacterSelectionError):
            await scope['logout_and_in'](client, target, False, 'p1')
        self.assertEqual(entered, [])

    async def test_target_changes_before_play_cannot_enter_wrong_character(self):
        target = self.wizard('target')
        wrong = self.wizard('wrong')
        scope_pages = [[target]]
        client, scope, state, entered = self.session(scope_pages)
        original = scope['window_from_path']
        reads = 0
        async def window(root, path):
            nonlocal reads
            if path == scope['txtName']:
                reads += 1
                if reads == 2:
                    scope_pages[0][0] = wrong
            return await original(root, path)
        scope['window_from_path'] = window
        with self.assertRaises(CharacterSelectionError):
            await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [])

    async def test_unreadable_selection_never_enters(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[target]], unreadable=1000)
        with self.assertRaises(CharacterSelectionError):
            await scope['logout_and_in'](client, target, True, 'p1')
        self.assertEqual(entered, [])

    async def test_failed_read_after_page_turn_is_retried_fresh(self):
        target = self.wizard('target')
        client, scope, state, entered = self.session([[self.wizard('a')], [target]], page_turning=True)
        original = scope['window_from_path']
        failed = False
        async def window(root, path):
            nonlocal failed
            if state.page == 1 and not failed:
                failed = True
                raise RuntimeError('page still loading')
            return await original(root, path)
        scope['window_from_path'] = window
        await scope['logout_and_in'](client, target, True, 'p1')
        self.assertTrue(failed)
        self.assertEqual(entered, [target])

    async def test_cancellation_during_selection_never_enters(self):
        client, scope, state, entered = self.session([[self.wizard('a')]])
        scope['window_from_path'] = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await scope['logout_and_in'](client, self.wizard('target'), True, 'p1')
        self.assertEqual(entered, [])
