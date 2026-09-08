import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from wizwalker.errors import MemoryReadError
from src.window_text import read_control_text
from src.questing import Quester
from src.utils import get_popup_title


class PopupTextTests(unittest.IsolatedAsyncioTestCase):
    def window(self, text, capacity=7, kind='ControlText'):
        window = AsyncMock()
        window.maybe_read_type_name.return_value = kind
        window.read_base_address.return_value = 1000
        address = 1000 + (712 if kind in ('ControlText', 'ControlList') else 736)
        encoded = text.encode('utf-16-le')
        async def typed(at, primitive):
            return {address + 16: len(encoded)//2, address + 24: capacity}[at]
        async def read(at, size):
            self.assertEqual(at, address)
            self.assertEqual(size, len(encoded))
            return encoded
        window.read_typed.side_effect = typed
        window.read_bytes.side_effect = read
        window.maybe_text.return_value = text
        return window

    async def test_short_chinese_titles_are_inline_not_addresses(self):
        for title in ('巨型星盘', '配置站', '七个汉字的标题'):
            with self.subTest(title=title):
                window = self.window(title)
                self.assertEqual(await read_control_text(window), title)
                window.maybe_text.assert_not_awaited()

    async def test_heap_text_uses_existing_reader(self):
        window = self.window('Configuration Station', capacity=31)
        self.assertEqual(await read_control_text(window), 'Configuration Station')
        window.maybe_text.assert_awaited_once()
        window.read_bytes.assert_not_awaited()

    async def test_prompt_read_failure_does_not_restart_worker(self):
        client = AsyncMock()
        with patch('src.utils.is_visible_by_path', AsyncMock(return_value=True)), patch('src.utils.get_window_from_path', AsyncMock(return_value=self.window('巨型星盘'))), patch('src.utils.read_control_text', AsyncMock(side_effect=MemoryReadError(123))):
            self.assertIsNone(await get_popup_title(client))

    async def test_real_title_read_allows_interaction_without_moving(self):
        client = AsyncMock()
        client.is_loading.return_value = False
        client.in_battle.return_value = False
        client.body.position.return_value = XYZ(0, 0, 0)
        quester = Quester(client, [client], None)
        with patch('src.utils.is_visible_by_path', AsyncMock(return_value=True)), patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), patch('src.utils.get_window_from_path', AsyncMock(return_value=self.window('巨型星盘'))), patch('src.questing.get_quest_name', AsyncMock(return_value='安装天国之星于 巨型星盘 地点：天国大本营')), patch('src.questing.collision_tp', AsyncMock()) as move:
            self.assertTrue(await quester.quest_interaction_ready(client, XYZ(10, 0, 0)))
            await quester.move_until_quest_interaction(client, XYZ(10, 0, 0))
            move.assert_not_awaited()
