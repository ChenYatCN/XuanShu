import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import (
    Quester, claim_quest_recovery, release_quest_recovery,
)


class QuestTriggerReentryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000.0, 2000.0, 0.0)
        self.position = self.target
        self.client = AsyncMock()
        self.client.title = "p1"
        self.client.questing_status = True
        self.client.refilling_potions = False
        self.client.quest_recovery_owner = None
        self.client.quest_dungeon_recovery = None
        for name in (
            "quest_party_probe_pending", "quest_party_battle_rescue_active",
            "quest_party_quest_worker_restart_requested", "post_combat_movement_active",
            "mainline_chain_retry_active",
        ):
            setattr(self.client, name, False)
        self.client.zone_name.return_value = "World/Zone"
        self.client.quest_position.position.return_value = self.target
        self.client.body.position.side_effect = lambda: self.position
        self.client.body.yaw.return_value = 0.0

        async def goto(x, y):
            self.position = XYZ(x, y, 0.0)

        self.client.goto.side_effect = goto
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(
            return_value=(42, 7, "Talk To Merle")
        )
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        self.patches = [
            patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True)),
            patch("src.questing.is_spiral_door_open", new=AsyncMock(return_value=False)),
            patch("src.questing.is_visible_by_path", new=AsyncMock(return_value=False)),
            patch("src.questing.asyncio.sleep", new=AsyncMock()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def observe(self, count=3):
        return [
            await self.quester._maybe_reenter_quest_trigger(self.client, self.target)
            for _ in range(count)
        ]

    async def test_three_stable_observations_then_walk_away_and_back(self):
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.client.goto.await_count, 2)
        self.client.goto.assert_any_await(1900.0, 2000.0)
        self.client.goto.assert_any_await(1000.0, 2000.0)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_second_direction_and_two_attempt_limit(self):
        await self.observe()
        self.assertEqual(await self.observe(), [False, False, True])
        self.client.goto.assert_any_await(1000.0, 2900.0)
        self.assertEqual(await self.observe(5), [True] * 5)
        self.assertEqual(self.client.goto.await_count, 4)

    async def test_prompt_success_does_not_repeat_for_unchanged_quest(self):
        self.quester.quest_interaction_ready.return_value = True
        await self.observe()
        self.assertEqual(await self.observe(5), [False] * 5)
        self.assertEqual(self.client.goto.await_count, 2)

    async def test_exploration_uses_existing_reentry_instead_of_noop_tp(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Puerto Nuovo')
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.client.goto.await_count, 2)

    async def test_exhausted_goal_resumes_on_real_progress(self):
        await self.observe()
        await self.observe()
        self.assertEqual(await self.observe(3), [True] * 3)
        self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Use Next Trigger')
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.client.goto.await_count, 6)

    async def test_far_target_and_non_interaction_goal_never_move(self):
        self.position = XYZ(2000.0, 2000.0, 0.0)
        self.assertEqual(await self.observe(5), [False] * 5)
        self.position = self.target
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, "Defeat 0/3")
        self.assertEqual(await self.observe(5), [False] * 5)
        self.client.goto.assert_not_awaited()

    async def test_progress_change_and_busy_state_reset_observations(self):
        self.assertEqual(await self.observe(2), [False, False])
        self.client.quest_party_probe_pending = True
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.quest_party_probe_pending = False
        self.quester._dungeon_quest_snapshot.return_value = (42, 8, "Talk To Merle")
        self.assertEqual(await self.observe(2), [False, False])
        self.client.goto.assert_not_awaited()

    async def test_zone_change_during_retreat_aborts_and_releases_lock(self):
        async def change_zone(x, y):
            self.position = XYZ(x, y, 0.0)
            self.client.zone_name.return_value = "World/Other"

        self.client.goto.side_effect = change_zone
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.client.goto.await_count, 1)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancellation_during_retreat_releases_lock(self):
        self.client.goto.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.observe()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_recovery_owner_excludes_npc_and_dungeon_recovery(self):
        self.assertTrue(claim_quest_recovery(self.client, "trigger_reentry"))
        self.assertFalse(claim_quest_recovery(self.client, "dungeon_quest"))
        self.quester._handle_npc_talking_quests = AsyncMock(return_value=True)
        self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.quester._handle_npc_talking_quests.assert_not_awaited()
        release_quest_recovery(self.client, "trigger_reentry")
        self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertIsNone(self.client.quest_recovery_owner)


if __name__ == "__main__":
    unittest.main()
