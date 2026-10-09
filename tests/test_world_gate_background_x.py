import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wizwalker import Keycode
from src.interaction_prompts import portal_kind


class WorldGateBackgroundXTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.quester = SimpleNamespace(title='p3', quest_party_quester=None, send_key=AsyncMock())
        self.hitter = SimpleNamespace(title='p4', quest_party_quester=self.quester, send_key=AsyncMock())
        self.other = SimpleNamespace(title='p1', quest_party_quester=None, send_key=AsyncMock())
        self.title = '世界之门'
        self.world_open = False
        self.read_title = AsyncMock(side_effect=lambda _: self.title)
        self.open_reader = AsyncMock(side_effect=lambda _: self.world_open)
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == 'mass_key_press')
        namespace = dict(Client=object, asyncio=asyncio, logger=Mock(), Keycode=Keycode,
                         portal_kind=portal_kind, get_popup_title=self.read_title,
                         is_spiral_door_open=self.open_reader)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        self.send = namespace['mass_key_press']

    async def test_background_x_skips_world_gate_hitter_but_keeps_other_clients(self):
        for self.title in ('世界之门', 'World Gate'):
            self.hitter.send_key.reset_mock()
            self.quester.send_key.reset_mock()
            self.other.send_key.reset_mock()
            await self.send(None, [self.quester, self.hitter, self.other], 'X Press', Keycode.X)
            self.hitter.send_key.assert_not_awaited()
            self.quester.send_key.assert_awaited_once_with(key=Keycode.X, seconds=.1)
            self.other.send_key.assert_awaited_once_with(key=Keycode.X, seconds=.1)

    async def test_open_world_selector_also_blocks_background_hitter_x(self):
        self.title = ''
        self.world_open = True
        await self.send(None, [self.hitter], 'X Press', Keycode.X)
        self.hitter.send_key.assert_not_awaited()

    async def test_ordinary_hitter_interaction_still_receives_background_x(self):
        self.title = '魔法藤'
        await self.send(None, [self.hitter], 'X Press', Keycode.X)
        self.hitter.send_key.assert_awaited_once_with(key=Keycode.X, seconds=.1)

    async def test_other_keys_do_not_read_or_filter_world_gate(self):
        await self.send(None, [self.hitter], 'Space Press', Keycode.SPACEBAR)
        self.hitter.send_key.assert_awaited_once_with(key=Keycode.SPACEBAR, seconds=.1)
        self.read_title.assert_not_awaited()
        self.open_reader.assert_not_awaited()

    async def test_explicit_foreground_hitter_x_is_still_manual_input(self):
        await self.send(self.hitter, [], 'X Press', Keycode.X)
        self.hitter.send_key.assert_awaited_once_with(key=Keycode.X, seconds=.1)

    async def test_unreadable_hitter_ui_does_not_cancel_other_client_input(self):
        self.read_title.side_effect = RuntimeError('client UI unavailable')
        await self.send(None, [self.hitter, self.other], 'X Press', Keycode.X)
        self.hitter.send_key.assert_not_awaited()
        self.other.send_key.assert_awaited_once_with(key=Keycode.X, seconds=.1)
