import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.chat_translation import ChatTranslationMonitor, _chat_edit
from src.window_text import read_control_text
from tests.test_chat_manual_send import client


def control(text, *, kind='ControlFreeChat', capacity=None, pointer=0x40000):
    encoded = text.encode('utf-16-le')
    length = len(encoded) // 2
    capacity = (7 if length <= 7 else length + 8) if capacity is None else capacity
    address = 0x20000 + (712 if kind in ('ControlText', 'ControlList', 'ControlFreeChat') else 736)
    header = {address: pointer, address + 16: length, address + 24: capacity}

    async def typed(at, _primitive):
        return header[at]

    async def read(at, size):
        if at != (address if capacity == 7 else pointer):
            raise AssertionError(f'wrong string address: {at}')
        return encoded[:size]

    return SimpleNamespace(
        maybe_read_type_name=AsyncMock(return_value=kind),
        read_base_address=AsyncMock(return_value=0x20000),
        read_typed=AsyncMock(side_effect=typed),
        read_bytes=AsyncMock(side_effect=read),
        # Root and packaged dependencies have different maybe_text offsets.
        maybe_text=AsyncMock(side_effect=RuntimeError('Unable to read memory at address 7.')),
    )


class ChatControlTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_logged_freechat_capacity_is_not_a_string_pointer(self):
        window = control('')
        base = 0x1F702224F90 - 736
        window.read_base_address.return_value = base
        # The live failure logged these fields at the old +736 header. Its
        # first value 7 belongs to the capacity at +712+24, not a pointer.
        # An empty draft at the correct header is the regression fixture;
        # the original log did not capture the actual draft length at +728.
        values = {base + 712: 0, base + 728: 0, base + 736: 7,
                  base + 752: 256, base + 760: 2160565320096}
        window.read_typed.side_effect = lambda at, _primitive: values[at]
        self.assertEqual(await read_control_text(window), '')
        self.assertEqual([call.args[0] for call in window.read_typed.await_args_list],
                         [base + 728, base + 736])
        window.read_bytes.assert_not_awaited()

    async def test_empty_draft_and_inline_text(self):
        for text in ('', 'hello', '1234567', '聊天草稿'):
            with self.subTest(text=text):
                window = control(text)
                self.assertEqual(await read_control_text(window), text)
                window.maybe_text.assert_not_awaited()

    async def test_heap_draft_uses_validated_pointer_not_dependency_fallback(self):
        for kind in ('ControlFreeChat', 'ControlText', 'ControlList', 'ControlButton'):
            with self.subTest(kind=kind):
                window = control('/s hello', kind=kind)
                self.assertEqual(await read_control_text(window), '/s hello')
                window.maybe_text.assert_not_awaited()

    async def test_invalid_small_pointer_never_dereferenced(self):
        window = control('/s hello', pointer=7)
        with self.assertRaisesRegex(RuntimeError, 'ControlFreeChat.*pointer=7'):
            await read_control_text(window)
        window.read_bytes.assert_not_awaited()
        window.maybe_text.assert_not_awaited()

    async def test_invalid_header_is_not_treated_as_empty_draft(self):
        for text, capacity in (('', 0), ('draft', 2), ('12345678', 7), ('draft', -1)):
            with self.subTest(text=text, capacity=capacity):
                window = control(text, capacity=capacity)
                with self.assertRaisesRegex(RuntimeError, 'ControlFreeChat.*length=.*capacity='):
                    await read_control_text(window)
                window.read_bytes.assert_not_awaited()
                window.maybe_text.assert_not_awaited()

    async def test_invalid_utf16_is_not_treated_as_empty_draft(self):
        window = control('x')
        window.read_bytes = AsyncMock(return_value=b'\x00\xd8')
        with self.assertRaises(UnicodeDecodeError):
            await read_control_text(window)


class ChatTextSendIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_chat_log_does_not_imply_visible_input(self):
        first = client()
        nodes = {name: SimpleNamespace(is_visible=AsyncMock(return_value=True),
                 maybe_read_type_name=AsyncMock(return_value='ControlFreeChat'))
                 for name in ('WorldView', 'WizardChatBox', 'chatContainer', 'chatEditContainer', 'chatEdit')}
        for node in nodes.values():
            node.get_child_by_name = AsyncMock(side_effect=lambda name: nodes[name])
        first.root_window = SimpleNamespace(get_child_by_name=AsyncMock(side_effect=lambda name: nodes[name]))
        nodes['chatEditContainer'].is_visible.return_value = False
        with self.assertRaisesRegex(RuntimeError, '展开聊天输入栏.*只显示聊天记录不够'):
            await _chat_edit(first)
        first.mouse_handler.click_window.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()

    async def send(self, draft):
        first = client()
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        typed = AsyncMock()
        readback = control('hello')
        async def edit(_client):
            return readback if typed.await_count else draft
        with patch('src.chat_translation._chat_edit', AsyncMock(side_effect=edit)), \
             patch('src.chat_translation._type_chat', typed), \
             patch('src.chat_translation._chat_texts', AsyncMock(return_value=[''])):
            # Unlike the original manual-send fixtures, this exercises the
            # actual inline/heap reader through the complete send transaction.
            await monitor._manual_send([first], 'p1', 'hello', verify_seconds=0)
        return first, typed, events[-1]

    async def test_actual_empty_to_heap_readback_fills_body_only(self):
        first, typed, final = await self.send(control(''))
        typed.assert_awaited_once_with(first, 'hello')
        first.send_hotkey.assert_not_awaited()
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])

    async def test_actual_heap_draft_is_preserved(self):
        first, typed, final = await self.send(control('my existing draft'))
        typed.assert_not_awaited()
        first.mouse_handler.click_window.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()
        self.assertIn('已有草稿', final['status'])

    async def test_invalid_pointer_reports_control_and_step_without_input(self):
        first, typed, final = await self.send(control('/s hello', pointer=7))
        typed.assert_not_awaited()
        first.mouse_handler.click_window.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()
        self.assertIn('读取聊天输入控件的草稿', final['status'])
        self.assertIn('ControlFreeChat', final['status'])
        self.assertIn('pointer=7', final['status'])

    async def test_unreadable_control_reports_actual_step_without_input(self):
        draft = control('')
        draft.read_typed.side_effect = RuntimeError('Unable to read memory at address 7.')
        first, typed, final = await self.send(draft)
        typed.assert_not_awaited()
        first.send_hotkey.assert_not_awaited()
        self.assertIn('读取聊天输入控件的草稿', final['status'])
        self.assertIn('address 7', final['status'])

