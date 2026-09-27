import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class QuestPartyBattleSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        helper_names = {'hitter_near_quester', 'attempt_battle_coordinate_sync'}
        helpers = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name in helper_names
        ]
        self.hitter = SimpleNamespace(
            in_battle=AsyncMock(return_value=False),
            is_loading=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Dungeon/RoomA'),
            body=SimpleNamespace(position=AsyncMock(return_value=2000)),
            teleport=AsyncMock(),
            client_being_helped=None,
        )
        self.quester = SimpleNamespace(
            in_battle=AsyncMock(return_value=True),
            body=SimpleNamespace(position=AsyncMock(return_value=0)),
        )
        self.same_area = AsyncMock(return_value=True)
        namespace = {
            'asyncio': SimpleNamespace(wait_for=asyncio.wait_for, sleep=AsyncMock()),
            'hitter': self.hitter,
            'quester': self.quester,
            'hitter_is_in_quester_area': self.same_area,
            'calc_Distance': lambda a, b: abs(a - b),
        }
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        self.near = namespace['hitter_near_quester']
        self.attempt = namespace['attempt_battle_coordinate_sync']

    async def test_teleport_requires_confirmed_arrival(self):
        async def arrive(_):
            self.hitter.body.position.return_value = 100

        self.hitter.teleport.side_effect = arrive
        self.assertTrue(await self.attempt('Dungeon/RoomA'))
        self.hitter.teleport.assert_awaited_once_with(0)

    async def test_teleport_without_movement_fails_after_one_attempt(self):
        self.assertFalse(await self.attempt('Dungeon/RoomA'))
        self.hitter.teleport.assert_awaited_once_with(0)

    async def test_same_coordinates_in_different_instance_are_not_success(self):
        self.hitter.body.position.return_value = 0
        self.same_area.return_value = False
        self.assertFalse(await self.near('Dungeon/RoomA'))
        self.assertFalse(await self.attempt('Dungeon/RoomA'))

    async def test_entering_battle_is_success_even_without_area_probe(self):
        self.hitter.in_battle.return_value = True
        self.hitter.client_being_helped = self.quester
        self.assertTrue(await self.attempt('Dungeon/RoomA'))

    async def test_unrelated_battle_is_not_success(self):
        self.hitter.in_battle.return_value = True
        self.same_area.return_value = False
        self.assertFalse(await self.attempt('Dungeon/RoomA'))


if __name__ == '__main__':
    unittest.main()
