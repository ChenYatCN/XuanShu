import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.script_popups import close_script_popup, popup_kind, run_with_script_popups


class ScriptPopupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.title = SimpleNamespace(value='You have been reported!', is_visible=AsyncMock(return_value=True))
        self.caption = SimpleNamespace(value='Remember, the use of foul language...', is_visible=AsyncMock(return_value=True))
        self.confirm = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.reject = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.window = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_windows_with_name=AsyncMock(side_effect=lambda name: {
                'TitleText': [self.title], 'CaptionText': [self.caption],
                'centerButton': [self.confirm], 'rightButton': [self.reject],
            }.get(name, [])))
        self.client = SimpleNamespace(title='p1', is_loading=AsyncMock(return_value=False),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[self.window])),
            mouse_handler=AsyncMock())
        self.endorsement = patch('src.script_popups.close_endorsement_window', AsyncMock(return_value=False))
        self.close_endorsement = self.endorsement.start()
        self.addCleanup(self.endorsement.stop)
        text = patch('src.script_popups.read_control_text', AsyncMock(side_effect=lambda w: w.value))
        text.start()
        self.addCleanup(text.stop)

    async def test_report_notice_acknowledged(self):
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.confirm)

    async def test_friend_request_declined(self):
        self.title.value = 'Friends'
        self.caption.value = 'Accept<br>Amber Level 100<br>as your friend?'
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.reject)

    async def test_endorsement_uses_existing_closer(self):
        self.close_endorsement.return_value = True
        self.assertTrue(await close_script_popup(self.client))
        self.close_endorsement.assert_awaited_once_with(self.client)
        self.client.root_window.get_windows_with_name.assert_not_awaited()

    async def test_purchase_and_teleport_confirmations_are_untouched(self):
        self.title.value = 'Confirm'
        for text in ('Buy this item?', 'Do you want to go to your friend?', 'Are you sure you want to leave this dungeon?'):
            self.caption.value = text
            self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_popup_changed_while_waiting_for_mouse(self):
        async def replace():
            self.title.value = 'Confirm Purchase'
            self.caption.value = 'Buy this item?'
        self.client.mouse_handler.__aenter__.side_effect = replace
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_and_hidden_windows_are_untouched(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await close_script_popup(self.client))
        self.close_endorsement.assert_not_awaited()
        self.client.is_loading.return_value = False
        self.window.is_visible.return_value = False
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    def test_verified_bilingual_text_and_unrelated_report(self):
        self.assertEqual(popup_kind('你已经被举报！', ''), 'reported')
        self.assertEqual(popup_kind('', '是否接受<br>某人 等级 100<br>成为你的好友？'), 'friend_request')
        self.assertEqual(popup_kind('', '你和 某人 成为了好友！'), 'friend_added')
        self.assertIsNone(popup_kind('举报玩家', '你确定要举报此玩家吗？'))
        self.assertIsNone(popup_kind('', '该玩家未被禁言，但已被举报.'))


class ScriptPopupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchers_run_during_wait_and_stop_on_cancel(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        first, other = SimpleNamespace(title='p1'), SimpleNamespace(title='p2')
        async def watch(client):
            self.assertIs(client, first)
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        async def script():
            await asyncio.Future()
        with patch('src.script_popups.watch_script_popups', side_effect=watch) as watcher:
            task = asyncio.create_task(run_with_script_popups(script, [first]))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(watcher.call_count, 1)

    async def test_normal_completion_and_script_failure_drain_watchers(self):
        for fail in (False, True):
            started, stopped = asyncio.Event(), asyncio.Event()
            async def watch(_):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    stopped.set()
            async def script():
                await started.wait()
                if fail:
                    raise ValueError('script failed')
                return 42
            with patch('src.script_popups.watch_script_popups', side_effect=watch):
                if fail:
                    with self.assertRaises(ValueError):
                        await run_with_script_popups(script, [object()])
                else:
                    self.assertEqual(await run_with_script_popups(script, [object()]), 42)
            self.assertTrue(stopped.is_set())
