"""Deimos-style ordinary interaction keeps one X and never spin-navigates."""
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.paths import npc_range_path
from tests.test_stale_interaction_movement import Fixture


class ArrivedInteractionBlockTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective = 'Use Task Lever'
        self.title, self.prompt = 'Task Lever', 'Press X to Use'
        self.nearby(0)
        self.range_visible = True
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))
        self.navigation = self.stack.enter_context(patch('src.questing.collision_tp',
            AsyncMock(wraps=movement.collision_tp)))
        self.quester.handle_questing_zone_change = AsyncMock()
        self.quester._recover_door_approach = AsyncMock()

    async def rounds(self, count=24):
        for _ in range(count):
            await self.quester.auto_quest_solo()

    async def test_visible_prompt_has_one_bounded_retry_without_navigation_spin(self):
        await self.rounds()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertEqual(self.navigation.await_count, 0)

    async def test_prompt_change_starts_one_new_interaction(self):
        await self.rounds(3)
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.title = 'Task Lever Updated'
        await self.rounds(3)
        self.assertEqual(self.keys, [Keycode.X] * 4)
        self.assertEqual(self.navigation.await_count, 0)

    async def test_task_change_starts_one_new_interaction(self):
        await self.rounds(3)
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.goal += 1
        self.target = XYZ(self.target.x + 100, self.target.y, self.target.z)
        self.nearby(0)
        await self.rounds(3)
        self.assertEqual(self.keys, [Keycode.X] * 4)
        self.assertEqual(self.navigation.await_count, 0)

    async def test_far_prompt_never_sends_x(self):
        self.position = XYZ(self.target.x - 800, self.target.y, self.target.z)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_loading_battle_and_dialogue_never_send_x(self):
        for attr, value in (('is_loading', True), ('in_battle', True), ('dialogue', True)):
            with self.subTest(attr=attr):
                self.setUp()
                if attr == 'dialogue':
                    self.dialogue = value
                else:
                    getattr(self.client, attr).return_value = value
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [])
