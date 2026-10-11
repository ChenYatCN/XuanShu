import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest


class NPCQuestMenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(Path('src/utils.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'close_npc_quest_menu')
        self.visible = AsyncMock(side_effect=lambda client, path: path == 'menu')
        self.click = AsyncMock()
        scope = dict(Client=object, asyncio=asyncio, is_visible_by_path=self.visible,
                     safe_click_window=self.click, cancel_multiple_quest_menu_path='menu', advance_dialog_path='dialog')
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'src/utils.py', 'exec'), scope)
        self.close = scope['close_npc_quest_menu']
        self.client = SimpleNamespace(is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False))

    async def test_clicks_cancel_for_quest_list(self):
        self.assertTrue(await self.close(self.client))
        self.click.assert_awaited_once_with(self.client, 'menu')

    async def test_keeps_active_quest_dialogue(self):
        self.visible.side_effect = None
        self.visible.return_value = True
        self.assertFalse(await self.close(self.client))
        self.click.assert_not_awaited()

    async def test_no_menu_no_click(self):
        self.visible.side_effect = None
        self.visible.return_value = False
        self.assertFalse(await self.close(self.client))
        self.click.assert_not_awaited()

    async def test_loading_no_click(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await self.close(self.client))
        self.click.assert_not_awaited()
