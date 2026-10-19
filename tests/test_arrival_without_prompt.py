"""Actual ordinary caller chains must stop renavigating an arrived, unreadable HUD."""
import math
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.paths import npc_range_path
from tests import test_interaction_a_search as fixtures
from tests import test_dungeon_exit_owner as dungeon
from src.questing import Quester


class ArrivalWithoutPromptTests(unittest.IsolatedAsyncioTestCase):
    sleep = fixtures.InteractionASearchTests.sleep
    send_key = fixtures.InteractionASearchTests.send_key

    def setUp(self):
        fixtures.InteractionASearchTests.setUp(self)
        self.position = XYZ(*self.target)
        self.client.body.position.side_effect = lambda: self.position
        self.range_visible = False
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_free', AsyncMock(return_value=True)))
        self.navigation = self.stack.enter_context(patch('src.questing.collision_tp',
            AsyncMock(return_value='ready')))
        self.quester._recover_door_approach = AsyncMock(return_value=False)

    async def rounds(self, count=12):
        for _ in range(count):
            await self.quester.auto_quest_solo()

    async def test_arrived_without_popup_does_not_repeat_same_target_navigation(self):
        await self.rounds()
        self.assertLessEqual(self.navigation.await_count, 1)
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_second_search_after_settle_finds_popup_then_ordinary_x(self):
        self.assertFalse(await self.quester._search_deimos_interaction_prompt(self.client))
        self.assertEqual(self.keys.count(Keycode.X), 0)
        self.clock += .75
        self.on_a = lambda: setattr(self, 'range_visible', True)
        self.assertTrue(await self.quester._search_deimos_interaction_prompt(self.client))
        self.assertEqual(self.keys.count(Keycode.X), 0, 'search has no X permission')
        self.on_x = lambda: setattr(self, 'goal', 8)
        self.assertTrue(await self.quester._try_deimos_interaction(self.client, [self.client]))
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_two_failed_searches_block_without_rotation_or_navigation_spin(self):
        await self.rounds(24)
        self.assertGreater(self.swept, math.tau)
        self.assertLessEqual(self.swept, 2*math.tau+.4)
        self.assertLessEqual(self.navigation.await_count, 1)
        self.assertEqual(self.keys.count(Keycode.X), 0)
        self.assertEqual(self.client._deimos_arrival_recovery['state'], 'blocked')
        self.assertEqual(self.client._deimos_arrival_recovery['attempts'], 2)

    async def test_task_zone_or_target_change_releases_old_recovery(self):
        for cause in ('task', 'zone', 'target'):
            with self.subTest(cause=cause):
                self.setUp()
                try:
                    await self.rounds()
                    old_turns = self.keys.count(Keycode.A)
                    if cause == 'task': self.goal += 1
                    if cause == 'zone': self.client.zone_name.return_value = 'Next/Area'
                    if cause == 'target': self.target = XYZ(self.target.x+100, self.target.y, self.target.z)
                    async def arrive(*_args, **_kwargs):
                        self.position = XYZ(*self.target)
                        return 'ready'
                    self.navigation.side_effect = arrive
                    self.navigation.reset_mock()
                    await self.rounds(4)
                    self.assertLessEqual(self.navigation.await_count, 1)
                    self.assertGreater(self.keys.count(Keycode.A), old_turns)
                finally:
                    self.stack.close()

    async def test_arrival_recovery_never_uses_legacy_evaluator(self):
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('ordinary arrival invoked evaluator'))):
            await self.rounds()
        self.assertLessEqual(self.navigation.await_count, 1)
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_natural_popup_next_round_uses_x_without_navigation(self):
        await self.rounds(1)
        self.navigation.reset_mock()
        self.range_visible = True
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.rounds(1)
        self.navigation.assert_not_awaited()
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_readable_popup_without_title_is_ordinary_interaction(self):
        self.range_visible = True
        self.title = ''
        self.on_x = lambda: setattr(self, 'goal', 8)
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('ordinary reader invoked evaluator'))):
            self.assertTrue(await self.quester._try_deimos_interaction(self.client, [self.client]))
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_untitled_popup_keeps_existing_two_x_retry_budget(self):
        self.range_visible = True
        self.title = ''
        for _ in range(4):
            await self.quester._try_deimos_interaction(self.client, [self.client])
        self.assertEqual(self.keys.count(Keycode.X), 2)

    async def test_optional_title_readability_jitter_does_not_refund_x_budget(self):
        self.range_visible = True
        reads = 0
        def title(_client):
            nonlocal reads
            reads += 1
            return '' if reads % 2 else 'Visible Title'
        with patch('src.questing.get_popup_title', AsyncMock(side_effect=title)):
            for _ in range(4):
                await self.quester._try_deimos_interaction(self.client, [self.client])
        self.assertEqual(self.keys.count(Keycode.X), 2)

    async def test_dungeon_current_target_arrival_still_reaches_popup_search(self):
        f = dungeon.DungeonExitOwnerTests()
        f.setUp()
        self.addCleanup(f.stack.close)
        await f.enter()
        f.q.position = XYZ(*f.target)
        heading = 0.0
        keys = []
        async def send(key, seconds=0):
            nonlocal heading
            keys.append(key)
            if key == Keycode.A: heading += math.pi*seconds
        f.q.send_key.side_effect = send
        f.q.body.yaw = AsyncMock(side_effect=lambda: heading)
        f.caller.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(f.caller)
        f.visible.side_effect = lambda _c, path: False
        f.caller._recover_door_approach = AsyncMock(return_value=False)
        for _ in range(5):
            await f.caller.auto_quest_solo()
        self.assertGreater(keys.count(Keycode.A), 0)
        self.assertLessEqual(keys.count(Keycode.A), 42)
        self.assertEqual(keys.count(Keycode.X), 0)

    async def test_arrived_target_preserves_real_photo_ready_for_shared_worker(self):
        self.objective = 'Photomance a Photo of Lever'
        self.prompt = 'Press Z to Photomance'
        self.range_visible = True
        task = await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(await self.quester._photomancy_interaction_ready(
            self.client, self.target, task, leader_client=self.client))
        result = await self.quester.teleport_to_quest_target(
            self.client, self.target, leader_client=self.client)
        self.assertEqual(result, 'photo-ready')
        self.navigation.assert_not_awaited()
        self.assertEqual(self.keys, [])

    async def test_leader_movement_then_normal_quest_search_is_also_bounded(self):
        self.quester.current_leader_client = self.client
        self.quester.leader_wait_for_free = AsyncMock()
        with patch('src.questing.safe_click_window', AsyncMock(return_value=False)), \
             patch('src.questing.Quester.handle_pending_dungeon_confirmation', AsyncMock(return_value=False)):
            for _ in range(12):
                await self.quester.teleport_to_quest_target(self.client, self.target, leader_client=self.client)
                await self.quester.handle_normal_quests([], False)
        self.assertLessEqual(self.navigation.await_count, 1)
        self.assertLessEqual(self.swept, 2*math.tau+.4)
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_observed_loading_battle_or_dialogue_resets_recovery_lifecycle(self):
        for cause in ('loading', 'battle', 'dialogue'):
            with self.subTest(cause=cause):
                self.setUp()
                try:
                    await self.quester._search_deimos_interaction_prompt(self.client)
                    self.clock += .75
                    await self.quester._search_deimos_interaction_prompt(self.client)
                    before = self.keys.count(Keycode.A)
                    if cause == 'loading': self.client.is_loading.return_value = True
                    if cause == 'battle': self.client.in_battle.return_value = True
                    if cause == 'dialogue': self.dialogue = True
                    self.assertFalse(await self.quester._search_deimos_interaction_prompt(self.client))
                    self.client.is_loading.return_value = False
                    self.client.in_battle.return_value = False
                    self.dialogue = False
                    self.on_a = lambda: setattr(self, 'range_visible', True)
                    self.assertTrue(await self.quester._search_deimos_interaction_prompt(self.client))
                    self.assertGreater(self.keys.count(Keycode.A), before)
                    self.assertEqual(self.keys.count(Keycode.X), 0)
                finally:
                    self.stack.close()
