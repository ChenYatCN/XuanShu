import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.chat_translation import ChatTranslationMonitor, _chat_edit, _chat_ready, _manual_chat_owner
from src.automation_ownership import automation_owner
from tests.test_chat_translation import chat_line


class Mouse:
    def __init__(self):
        self.click_window = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def client(title='p1', handle=1):
    return SimpleNamespace(title=title, window_handle=handle, is_running=lambda: True,
        is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
        is_in_dialog=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='World/Area'),
        game_client=SimpleNamespace(player_gid=AsyncMock(return_value=31)),
        mouse_handler=Mouse(), send_hotkey=AsyncMock())


class ManualSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_input_uses_readable_game_input_label(self):
        for hidden in ('chatEditContainer', 'chatEdit'):
            with self.subTest(hidden=hidden):
                first = client()
                nodes = {name: SimpleNamespace(is_visible=AsyncMock(return_value=name != hidden))
                         for name in ('WorldView', 'WizardChatBox', 'chatContainer', 'chatEditContainer', 'chatEdit')}
                for node in nodes.values():
                    node.get_child_by_name = AsyncMock(side_effect=lambda name: nodes[name])
                first.root_window = SimpleNamespace(get_child_by_name=AsyncMock(side_effect=lambda name: nodes[name]))
                with self.assertRaisesRegex(RuntimeError, '游戏聊天输入栏不可见') as caught:
                    await _chat_edit(first)
                self.assertNotIn(hidden, str(caught.exception))
                first.mouse_handler.click_window.assert_not_awaited()

    async def test_verified_self_lines_keep_original_and_other_same_body_is_not_suppressed(self):
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        monitor.translation_api = Mock()
        self_line = {'kind': 'message', 'source': 'chat_log', 'handle': 1,
                     'title': 'p1', 'sender_gid': 0, 'sender_name': '你', 'message': 'hello'}
        monitor._publish_message(self_line)
        self.assertEqual(events[0]['message'], 'hello')
        self.assertTrue(monitor.translation_queue.empty())
        monitor.translation_api.translate.assert_not_called()
        monitor._publish_message({**self_line, 'sender_gid': 42, 'sender_name': 'Other'})
        self.assertEqual(monitor.translation_queue.qsize(), 1)
        monitor.translation_task.cancel()
        await asyncio.gather(monitor.translation_task, return_exceptions=True)

    async def run_send(self, first=None, peers=None, draft='', mismatch=False, rows=None, allow_busy=False,
                       input_text='hello', api=None, consumed_command=False, own_echo=False):
        first = first or client()
        peers = peers or [first]
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        monitor.translation_api = api
        monitor.translation_clients = {peer.window_handle: peer for peer in peers}
        monitor.auto_reply = True
        edit = SimpleNamespace(read_base_address=AsyncMock(return_value=100))
        text = ''
        reads = 0

        async def echo(*args):
            monitor._publish_message({'kind': 'message', 'source': 'chat_log',
                'handle': first.window_handle, 'title': first.title, 'channel': '附近',
                'sender_gid': 0, 'sender_name': '你', 'message': 'hello'})
            await asyncio.sleep(0)
        if own_echo:
            first.send_hotkey.side_effect = echo

        async def read(_edit):
            return draft or text

        async def type_text(target, wire):
            nonlocal text
            self.assertIs(target, first)
            self.assertEqual(wire, 'hello')
            text = 'wrong' if mismatch else wire

        async def logs(peer):
            nonlocal reads
            reads += 1
            if reads <= len(peers):
                return [chat_line('hello')]  # Existing hello is not receipt.
            if rows:
                monitor.observation_counter += 1
                monitor.observed.append((monitor.observation_counter, peer.title,
                                         '附近', 31, 'hello'))
                return [chat_line('hello') + '\n' + rows]
            return [chat_line('hello')]

        with patch('src.chat_translation._chat_edit', AsyncMock(return_value=edit)), \
             patch('src.chat_translation.read_control_text', read), \
             patch('src.chat_translation._type_chat', type_text), \
             patch('src.chat_translation._chat_texts', logs):
            await monitor._manual_send(peers, 'p1', input_text, verify_seconds=.03, allow_busy=allow_busy)
            final = events[-1]
            if own_echo and final['filled']:
                await echo()  # User confirms the retained English draft later.
            if monitor.translation_task:
                await monitor.translation_task
        return first, final

    async def test_translated_send_and_manual_confirm_echo_never_translate_twice(self):
        for consumed in (False, True):
            api = Mock(translate=Mock(return_value='hello'))
            first, final = await self.run_send(input_text='你好', api=api,
                                               consumed_command=consumed, own_echo=True)
            api.translate.assert_called_once_with('你好', target_language='en')
            self.assertTrue(final['filled'])
            self.assertFalse(final['invoked'])
            first.send_hotkey.assert_not_awaited()

    async def test_chinese_is_translated_before_input_without_send_or_receipt_claim(self):
        api = Mock(translate=Mock(return_value='hello'))
        first, final = await self.run_send(input_text='你好', api=api, rows=chat_line('hello'))
        api.translate.assert_called_once_with('你好', target_language='en')
        self.assertEqual(final['source_message'], '你好')
        self.assertEqual(final['message'], 'hello')
        self.assertTrue(final['filled'])
        self.assertFalse(final['local_echo'])
        first.send_hotkey.assert_not_awaited()

    async def test_english_is_not_translated_back_to_chinese(self):
        api = Mock()
        first, final = await self.run_send(api=api)
        api.translate.assert_not_called()
        self.assertEqual(final['message'], 'hello')
        self.assertTrue(final['filled'])
        first.send_hotkey.assert_not_awaited()

    async def test_missing_or_failed_translation_never_inputs_original_chinese(self):
        from src.chat_translation_api import TranslationError
        for api in (None, Mock(translate=Mock(side_effect=TranslationError('接口超时'))),
                    Mock(translate=Mock(side_effect=RuntimeError('secret transport details')))):
            first, final = await self.run_send(input_text='你好', api=api)
            first.mouse_handler.click_window.assert_not_awaited()
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])
            self.assertNotIn('secret transport details', final['status'])

    async def test_invalid_or_too_long_english_translation_never_inputs(self):
        for translated in ('', None, 'x' * 81, '仍是中文', 'hello\nworld', '😀'):
            api = Mock(translate=Mock(return_value=translated))
            first, final = await self.run_send(input_text='你好', api=api)
            first.mouse_handler.click_window.assert_not_awaited()
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])

    async def test_translated_draft_with_unknown_channel_requires_manual_confirmation(self):
        api = Mock(translate=Mock(return_value='hello'))
        first, final = await self.run_send(input_text='你好', api=api, consumed_command=True)
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])
        self.assertEqual(final['message'], 'hello')
        first.send_hotkey.assert_not_awaited()

    async def test_fallback_still_preserves_existing_draft_and_rejects_mismatch(self):
        for options in ({'draft': 'existing'}, {'mismatch': True}):
            first, final = await self.run_send(**options)
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['filled'])

    async def test_translation_configuration_change_aborts_before_game_input(self):
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        monitor.translation_api = Mock()
        first = client()
        async def translate(*args, **kwargs):
            monitor._reset_translation()
            return 'hello'
        with patch('src.chat_translation.asyncio.to_thread', AsyncMock(side_effect=translate)), \
             patch('src.chat_translation._type_chat', AsyncMock()) as typed:
            await monitor._manual_send([first], 'p1', '你好', verify_seconds=0)
        typed.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()
        self.assertIn('配置已变化', events[-1]['error'])

    async def test_cancellation_during_translation_never_inputs_or_sends(self):
        monitor = ChatTranslationMonitor(lambda event: None)
        monitor.enabled = True
        monitor.translation_api = Mock()
        first = client()
        with patch('src.chat_translation.asyncio.to_thread', AsyncMock(side_effect=asyncio.CancelledError)), \
             patch('src.chat_translation._type_chat', AsyncMock()) as typed:
            with self.assertRaises(asyncio.CancelledError):
                await monitor._manual_send([first], 'p1', '你好', verify_seconds=0)
        typed.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()

    async def test_only_p1_is_filled_and_no_send_receipts_are_claimed(self):
        first, second = client(), client('p2', 2)
        first, final = await self.run_send(first, [first, second], rows=chat_line('hello'))
        first.send_hotkey.assert_not_awaited()
        first.mouse_handler.click_window.assert_awaited_once()
        second.send_hotkey.assert_not_awaited()
        second.mouse_handler.click_window.assert_not_awaited()
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])
        self.assertFalse(final['local_echo'])
        self.assertEqual(final['peer_receipts'], [])
        self.assertEqual(final['listener_capture'], [])

    async def test_old_hello_not_proof(self):
        _, final = await self.run_send()
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])
        self.assertFalse(final['local_echo'])
        self.assertEqual(final['listener_capture'], [])

    async def test_missing_player_gid_uses_character_gid(self):
        first = client()
        first.game_client.player_gid.return_value = 0
        first.client_object = SimpleNamespace(global_id_full=AsyncMock(return_value=31))
        first, final = await self.run_send(first, rows=chat_line('hello'))
        first.send_hotkey.assert_not_awaited()
        self.assertTrue(final['filled'])
        self.assertFalse(final['local_echo'])

    async def test_unavailable_gid_allows_manual_send_without_receipt_claim(self):
        first = client()
        first.game_client.player_gid.side_effect = RuntimeError('unavailable')
        first, final = await self.run_send(first, rows=chat_line('hello'))
        first.send_hotkey.assert_not_awaited()
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])
        self.assertFalse(final['local_echo'])
        self.assertEqual(final['peer_receipts'], [])
        self.assertEqual(final['listener_capture'], [])

    async def test_missing_zone_still_blocks_send_with_specific_reason(self):
        first = client()
        first.zone_name.return_value = None
        first, final = await self.run_send(first)
        first.send_hotkey.assert_not_awaited()
        self.assertIn('区域读取为空', final['status'])

    async def test_wrong_gid_and_wrong_channel_not_proof(self):
        for row in (chat_line('hello', gid=99), chat_line('hello', icon='Group')):
            _, final = await self.run_send(rows=row)
            self.assertFalse(final['local_echo'])

    async def test_draft_or_mismatch_never_enter(self):
        for kwargs in ({'draft': 'my draft'}, {'mismatch': True}):
            first, final = await self.run_send(**kwargs)
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])

    async def test_busy_offline_loading_dialogue_battle_never_enter(self):
        for attr in ('questing_status', 'is_loading', 'in_battle', 'is_in_dialog', 'is_running'):
            first = client()
            setattr(first, attr, (lambda: False) if attr == 'is_running' else
                    True if attr == 'questing_status' else AsyncMock(return_value=True))
            first, final = await self.run_send(first)
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])

    async def test_missing_ui_and_missing_p1(self):
        first = client('p2')
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        await monitor._manual_send([first], 'p1', 'hello', verify_seconds=0)
        first.send_hotkey.assert_not_awaited()
        first.root_window = SimpleNamespace(get_child_by_name=AsyncMock(side_effect=ValueError('missing')))
        with self.assertRaises(ValueError):
            await _chat_edit(first)

    async def test_ownership_prevents_send(self):
        first = client()
        async with automation_owner(first, 'combat'):
            _, final = await self.run_send(first)
        self.assertIn('combat', final['status'])
        first.send_hotkey.assert_not_awaited()

    async def test_duplicate_request_not_queued_and_auto_reply_disabled(self):
        monitor = ChatTranslationMonitor(lambda _event: None)
        monitor.auto_reply = True
        wait = asyncio.Event()
        with patch.object(monitor, '_manual_send', AsyncMock(side_effect=lambda *args: wait.wait())) as send:
            monitor.request_manual_send([], 'p1', 'hello')
            task = monitor.manual_task
            monitor.request_manual_send([], 'p1', 'hello')
            self.assertIs(task, monitor.manual_task)
            self.assertFalse(monitor.auto_reply)
            await monitor.stop()

    async def test_override_allows_battle_and_automatic_quest_fill_once(self):
        first = client()
        first.questing_status = True
        first.combat_status = True
        first.in_battle.return_value = True
        first, final = await self.run_send(first, allow_busy=True)
        first.send_hotkey.assert_not_awaited()
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])

    async def test_override_keeps_loading_dialogue_draft_and_readback_guards(self):
        for attr in ('is_loading', 'is_in_dialog'):
            first = client()
            getattr(first, attr).return_value = True
            first, final = await self.run_send(first, allow_busy=True)
            self.assertFalse(final['invoked'])
            first.send_hotkey.assert_not_awaited()
        for options in ({'draft': 'existing'}, {'mismatch': True}):
            first, final = await self.run_send(allow_busy=True, **options)
            first.send_hotkey.assert_not_awaited()

    async def test_override_waits_for_combat_click_and_keeps_exclusive_ownership(self):
        first = client()
        acquired = asyncio.Event()
        release = asyncio.Event()
        async def combat():
            async with automation_owner(first, 'combat-click'):
                acquired.set()
                await release.wait()
        task = asyncio.create_task(combat())
        await acquired.wait()
        async def unlock():
            await asyncio.sleep(.01)
            release.set()
        unlock_task = asyncio.create_task(unlock())
        async with _manual_chat_owner(first, True):
            from src.automation_ownership import get_client_automation_ownership
            self.assertEqual(get_client_automation_ownership(first).owner_label, 'manual-nearby-chat')
        await asyncio.gather(task, unlock_task)

