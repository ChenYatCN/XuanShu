"""Deimos-style ordinary X qualification, without evaluator permission gates."""
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
import tests.test_interaction_a_search as fixtures


class DeimosOrdinaryInteractionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.InteractionASearchTests.setUp
    sleep = fixtures.InteractionASearchTests.sleep
    send_key = fixtures.InteractionASearchTests.send_key

    def place_at(self, gap):
        self.position = XYZ(self.target.x - gap, self.target.y, self.target.z)
        self.client.body.position.side_effect = lambda: self.position

    async def test_offset_entrance_uses_750_range_without_5u_gate(self):
        self.objective = 'Find Hidden Cave'
        self.title = 'Saboteur Cave'
        self.prompt = 'Press X to Enter'
        self.place_at(83)
        self.quester._enter_deimos_dungeon = AsyncMock()
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator must not qualify X'))):
            handled = await self.quester._try_deimos_interaction(
                self.client, [self.client], leader_mode=False)
        self.assertTrue(handled)
        self.quester._enter_deimos_dungeon.assert_awaited_once()
        self.assertEqual(self.keys, [])

    async def test_find_explore_visit_enter_have_no_objective_action_veto(self):
        for objective in ('Find the hidden path', 'Explore the cave', 'Visit the entrance'):
            with self.subTest(objective=objective):
                self.setUp()
                self.objective = objective
                self.title = 'Cave Door'
                self.prompt = 'Press X to Enter'
                self.quester._enter_deimos_dungeon = AsyncMock()
                handled = await self.quester._try_deimos_interaction(
                    self.client, [self.client], leader_mode=False)
                self.assertTrue(handled)
                self.quester._enter_deimos_dungeon.assert_awaited_once()

    async def test_enter_popup_sends_one_x_and_awaits_dungeon_outcome(self):
        self.objective = 'Find the cave'
        self.title = 'Cave Door'
        self.prompt = 'Press X to Enter'
        self.place_at(83)
        with patch('src.questing.begin_dungeon_entry', AsyncMock(return_value=None)), \
             patch('src.questing.observe_dungeon_session', AsyncMock()) as observed:
            self.assertTrue(await self.quester._try_deimos_interaction(
                self.client, [self.client], leader_mode=False))
            self.assertTrue(await self.quester._try_deimos_interaction(
                self.client, [self.client], leader_mode=False))
        self.assertEqual(self.keys.count(Keycode.X), 1)
        observed.assert_awaited_once_with(self.client)

    async def test_leader_offset_prompt_uses_750_range_and_one_x(self):
        self.objective = 'Find the hidden path'
        self.title = 'Cave Door'
        self.prompt = 'Press X to Use'
        self.place_at(83)
        self.quester.current_leader_client = self.client
        self.quester.leader_wait_for_free = AsyncMock()
        with patch('src.questing.safe_click_window', AsyncMock(return_value=False)), \
             patch('src.questing.Quester.handle_pending_dungeon_confirmation',
                   AsyncMock(return_value=False)), \
             patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator consulted'))):
            await self.quester.handle_normal_quests([], False)
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_unknown_localized_object_action_still_sends_one_x(self):
        self.objective = 'Tend the Zibber Zobber'
        self.title = 'Unmapped Device'
        self.prompt = 'Press X to Frobnify'
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_far_prompt_at_750_is_not_interacted(self):
        self.place_at(750)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_prompt_inside_750_is_interacted(self):
        self.place_at(749)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_loading_battle_and_dialogue_never_send_x(self):
        for attr, value in (('is_loading', True), ('in_battle', True), ('dialogue', True)):
            with self.subTest(attr=attr):
                self.setUp()
                if attr == 'dialogue':
                    self.dialogue = value
                else:
                    getattr(self.client, attr).return_value = value
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_interaction_lifecycle_retries_once_then_stops(self):
        self.objective = 'Use Lever'
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        await self.quester.auto_quest_solo()
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 2)

    async def test_task_change_allows_a_new_interaction_lifecycle(self):
        self.objective = 'Use Lever'
        self.prompt = 'Press X to Use'

        def progress():
            self.goal += 1

        self.on_x = progress
        await self.quester.auto_quest_solo()
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 2)

    async def test_ordinary_x_never_consults_evaluator_or_semantic_matcher(self):
        self.objective = 'Use Lever'
        self.prompt = 'Press X to Use'
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator consulted'))), \
             patch('src.questing.quest_interaction_matches',
                   side_effect=AssertionError('semantic matcher consulted')):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 1)
