"""Recovery enhancements retained around Deimos-style ordinary X."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.paths import npc_range_path
import tests.test_interaction_a_search as fixtures


class DeimosRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.InteractionASearchTests.setUp
    sleep = fixtures.InteractionASearchTests.sleep
    send_key = fixtures.InteractionASearchTests.send_key

    def setUpWithRange(self):
        self.setUp()
        self.range_visible = False
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))

    async def test_a_search_finds_popup_without_x_then_try_sends_one(self):
        self.setUpWithRange()
        self.on_a = lambda: setattr(self, 'range_visible', True)
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator used by A-search'))):
            self.assertTrue(await self.quester._search_deimos_interaction_prompt(self.client))
            self.assertEqual(self.keys.count(Keycode.X), 0)
            self.assertTrue(await self.quester._try_deimos_interaction(
                self.client, [self.client]))
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_auto_quest_a_search_then_deimos_x(self):
        self.setUpWithRange()
        self.on_a = lambda: setattr(self, 'range_visible', True)
        self.quester.teleport_to_quest_target = AsyncMock(return_value='ready')
        await self.quester.auto_quest_solo()
        self.assertGreater(self.keys.count(Keycode.A), 0)
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_a_search_is_bounded_per_task_zone_target(self):
        self.setUpWithRange()
        self.assertFalse(await self.quester._search_deimos_interaction_prompt(self.client))
        first = self.keys.count(Keycode.A)
        self.assertGreater(first, 0)
        self.assertFalse(await self.quester._search_deimos_interaction_prompt(self.client))
        self.assertEqual(self.keys.count(Keycode.A), first)
        self.goal += 1
        self.assertFalse(await self.quester._search_deimos_interaction_prompt(self.client))
        self.assertGreater(self.keys.count(Keycode.A), first)

    async def test_talk_post_x_uses_mature_handler_without_evaluator(self):
        self.prompt = 'Press X to Talk'
        self.objective = 'Talk to the Guardian'
        self.range_visible = True
        self.quester.handle_npc_talking_quests = AsyncMock(return_value=True)
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator used for X'))):
            self.assertTrue(await self.quester._try_deimos_interaction(
                self.client, [self.client]))
        self.quester.handle_npc_talking_quests.assert_awaited_once()
        args, kwargs = self.quester.handle_npc_talking_quests.await_args
        self.assertTrue(kwargs['post_x'])
        self.assertFalse(kwargs['direction_search'])
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_talk_post_x_confirms_stable_update_without_evaluator(self):
        self.dialogue = True
        self.objective = 'Talk to the Guardian'
        self.prompt = 'Press X to Talk'
        def settle(delay):
            if self.clock > 1.0:
                self.dialogue = False
                self.goal = 8
                self.objective = 'Mirage next objective'
                self.target = XYZ(1100, 2100, 3)
        self.after_sleep = settle
        before = await self.quester._dungeon_quest_snapshot(self.client)
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator used post-X'))):
            self.assertTrue(await self.quester.handle_npc_talking_quests(
                self.client, [self.client], post_x=True,
                direction_search=False, before_progress=before))
        self.assertEqual(self.goal, 8)

    async def test_photo_priority_runs_before_ordinary_x(self):
        self.objective = '<center>按下 Z 来拍照 废物排放管道 地点：尼布尔海姆</center>'
        self.title, self.prompt = 'Task Lever', 'Press X to Use'
        self.range_visible = True
        self.quester.take_photomancy_photo = AsyncMock()
        self.assertTrue(await self.quester._try_deimos_interaction(
            self.client, [self.client]))
        self.quester.take_photomancy_photo.assert_awaited_once()
        self.assertIn('拍照', self.quester.take_photomancy_photo.await_args.args[1])
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_unsafe_follower_does_not_receive_group_x(self):
        safe = self.client
        unsafe = SimpleNamespace(
            title='p2', questing_status=True,
            is_loading=AsyncMock(return_value=True),
            in_battle=AsyncMock(return_value=False),
            entity_detect_combat_status=False, send_key=AsyncMock())
        self.assertTrue(await self.quester._send_deimos_x(safe, [safe, unsafe]))
        self.assertEqual(self.keys.count(Keycode.X), 1)
        unsafe.send_key.assert_not_awaited()

    async def test_one_x_retries_once_then_stops(self):
        self.range_visible = True
        await self.quester._try_deimos_interaction(self.client, [self.client])
        await self.quester._try_deimos_interaction(self.client, [self.client])
        await self.quester._try_deimos_interaction(self.client, [self.client])
        self.assertEqual(self.keys.count(Keycode.X), 2)

    async def test_first_x_outcome_prevents_retry(self):
        self.range_visible = True
        def loading_after_x():
            self.client.is_loading.return_value = True
        self.on_x = loading_after_x
        await self.quester._try_deimos_interaction(self.client, [self.client])
        await self.quester._try_deimos_interaction(self.client, [self.client])
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_transition_prompt_restores_provenance_state(self):
        self.prompt = 'Press X to Ride'
        self.title = 'Ferry'
        self.range_visible = True
        self.assertTrue(await self.quester._try_deimos_interaction(
            self.client, [self.client]))
        provenance = getattr(self.client, '_quest_transition_provenance', None)
        self.assertIsInstance(provenance, dict)
        self.assertEqual(provenance['phase'], 'pending')
        self.assertEqual(provenance['source_zone'], 'SunlessShrineEnt')

    async def test_generic_probe_is_not_called_by_ordinary_auto_quest(self):
        self.range_visible = False
        with patch('src.current_target_probe.run_probe', AsyncMock()) as probe:
            await self.quester.auto_quest_solo()
        probe.assert_not_awaited()
