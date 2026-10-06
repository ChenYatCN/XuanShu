import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.chat_translation import ChatTranslationMonitor, _chat_readback
from tests.test_chat_manual_send import client


class ChatReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def fill(self, prefix_text='Say:', *, actual=None, delay=False,
                   hidden=False, replaced=False, late_prefix=None, text='hello', translated=None):
        first = client()
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        if translated is not None:
            monitor.translation_api = Mock(translate=Mock(return_value=translated))
        body = translated if translated is not None else text
        actual = body if actual is None else actual
        prefix = SimpleNamespace(is_visible=AsyncMock(return_value=not hidden),
                                 maybe_read_type_name=AsyncMock(return_value='ControlText'))
        parent = SimpleNamespace(get_child_by_name=AsyncMock(return_value=prefix))
        edit = SimpleNamespace(read_base_address=AsyncMock(return_value=100),
                               parent=AsyncMock(return_value=parent))
        replacement = SimpleNamespace(read_base_address=AsyncMock(return_value=200))
        typed = AsyncMock()
        count = 0

        async def read(node):
            nonlocal count
            self.assertIsNot(node, prefix, 'Filling must not inspect/change channel or recipient')
            if not typed.await_count:
                return ''
            count += 1
            return body[:-1] if delay and count < 3 else actual

        async def locate(_client):
            return replacement if replaced and typed.await_count else edit

        async def zone():
            nonlocal prefix_text
            if late_prefix is not None and typed.await_count:
                prefix_text = late_prefix
            return 'World/Area'

        first.zone_name.side_effect = zone
        with patch('src.chat_translation._chat_edit', AsyncMock(side_effect=locate)), \
             patch('src.chat_translation.read_control_text', read), \
             patch('src.chat_translation._type_chat', typed), \
             patch('src.chat_translation._chat_texts', AsyncMock()) as logs:
            await monitor._manual_send([first], 'p1', text, verify_seconds=0)
        typed.assert_awaited_once_with(first, body)
        edit.parent.assert_not_awaited()
        parent.get_child_by_name.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()
        logs.assert_not_awaited()
        self.assertFalse(events[-1]['invoked'])
        self.assertFalse(events[-1]['local_echo'])
        return first, events[-1], prefix_text

    async def test_nearby_group_whisper_and_unknown_channel_remain_user_selected(self):
        for prefix in ('Say:', '说:', 'Group:', '队伍:', '马龙', 'Tell to Alex:', '', 'unknown'):
            with self.subTest(prefix=prefix):
                _, result, retained = await self.fill(prefix)
                self.assertTrue(result['filled'])
                self.assertEqual(retained, prefix)

    async def test_hidden_prefix_does_not_block_body_filling_or_trigger_commands(self):
        for prefix in ('Say:', '', 'Group:', '马龙'):
            _, result, retained = await self.fill(prefix, hidden=True)
            self.assertTrue(result['filled'])
            self.assertEqual(retained, prefix)

    async def test_logged_chinese_flow_fills_english_with_empty_channel(self):
        _, result, _ = await self.fill('', hidden=True, text='我是谁', translated='Who am I?')
        self.assertTrue(result['filled'])
        self.assertEqual(result['message'], 'Who am I?')
        self.assertIn('未发送', result['status'])
        self.assertNotIn('无法确认附近', result['status'])

    async def test_chinese_translation_preserves_private_recipient(self):
        _, result, retained = await self.fill('马龙', text='你好', translated='hello')
        self.assertTrue(result['filled'])
        self.assertEqual(retained, '马龙')

    async def test_original_message_whitespace_is_preserved_exactly(self):
        _, result, _ = await self.fill(text=' hello ', actual=' hello ')
        self.assertTrue(result['filled'])

    async def test_readback_does_not_accept_extra_whitespace_or_inserted_channel_command(self):
        for actual in (' hello', '  hello', '\thello', 'hello ', 'hello!', '/s hello'):
            with self.subTest(actual=actual):
                _, result, _ = await self.fill(actual=actual)
                self.assertFalse(result['filled'])
                self.assertIn('回读不一致', result['status'])

    async def test_replaced_input_address_is_not_successful_filling(self):
        _, result, _ = await self.fill(replaced=True)
        self.assertFalse(result['filled'])

    async def test_delayed_ui_processing_is_read_only_not_retyping(self):
        _, result, _ = await self.fill(delay=True)
        self.assertTrue(result['filled'])

    async def test_user_channel_change_is_not_overridden_and_never_auto_sent(self):
        _, result, retained = await self.fill(late_prefix='马龙')
        self.assertTrue(result['filled'])
        self.assertEqual(retained, '马龙')

    async def test_unreadable_prefix_is_irrelevant_to_exact_body_readback(self):
        first = client()
        edit = SimpleNamespace(read_base_address=AsyncMock(return_value=100),
                               parent=AsyncMock(side_effect=RuntimeError('missing parent')))
        with patch('src.chat_translation._chat_edit', AsyncMock(return_value=edit)), \
             patch('src.chat_translation.read_control_text', AsyncMock(return_value='hello')):
            self.assertTrue(await _chat_readback(first, edit, 'hello'))
        edit.parent.assert_not_awaited()

