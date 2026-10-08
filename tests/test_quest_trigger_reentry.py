import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from wizwalker import XYZ
from src.questing import (
    Quester, claim_quest_recovery, release_quest_recovery,
)

REAL_SLEEP = asyncio.sleep
REAL_TIMEOUT = asyncio.timeout


class QuestTriggerReentryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000.0, 2000.0, 0.0)
        self.now = 0.0
        self.position = self.target
        self.client = AsyncMock()
        self.client.title = "p1"
        self.client.questing_status = True
        self.client.refilling_potions = False
        self.client.quest_recovery_owner = None
        self.client.quest_dungeon_recovery = None
        self.client.quest_party_quester = None
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
        self.quester.read_popup = AsyncMock(return_value='Press X to Talk')
        self.recover = AsyncMock(return_value=False)
        self.navmap = AsyncMock()
        self.patches = [
            patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True)),
            patch("src.questing.is_spiral_door_open", new=AsyncMock(return_value=False)),
            patch("src.questing.is_visible_by_path", new=AsyncMock(return_value=False)),
            patch("src.questing.asyncio.sleep", new=AsyncMock()),
            patch("src.questing.time", SimpleNamespace(monotonic=lambda: self.now)),
            patch("src.collision.get_collision_data", new=AsyncMock(return_value=b'')),
            patch("src.collision.CollisionWorld"),
            patch("src.teleport_math._resolve_player_radius", new=AsyncMock(return_value=45)),
            patch("src.teleport_math._recover_near_target", new=self.recover),
            patch("src.questing.navmap_tp", new=self.navmap),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def observe(self, count=3, step=15):
        results = []
        for _ in range(count):
            results.append(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
            self.now += step
        return results

    async def test_thirty_seconds_then_far_landing_and_forced_walk(self):
        self.assertEqual(await self.observe(), [False, False, True])
        self.recover.assert_awaited_once()
        kwargs = self.recover.await_args.kwargs
        self.assertEqual((kwargs['min_distance'], kwargs['max_distance'], kwargs['goal_radius']),
                         (500, 1500, 5))
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.navmap.assert_not_awaited()

    async def test_quick_samples_keep_normal_tp_available(self):
        self.assertEqual(await self.observe(5, step=1), [False] * 5)
        self.recover.assert_not_awaited()

    async def test_second_approach_and_two_attempt_limit_even_when_far(self):
        await self.observe()
        self.position = XYZ(2000, 2000, 0)  # A retreat must not reactivate ordinary TP.
        self.assertEqual(await self.observe(), [True, True, True])
        self.assertEqual(await self.observe(5), [True] * 5)
        self.assertEqual(self.recover.await_count, 2)

    async def test_prompt_success_does_not_repeat_for_unchanged_quest(self):
        await self.observe()
        self.quester.quest_interaction_ready.return_value = True
        self.assertEqual(await self.observe(5), [False] * 5)
        self.assertEqual(self.recover.await_count, 1)
        self.assertNotIn(id(self.client), self.quester._trigger_reentry)

    async def test_exploration_uses_existing_reentry_instead_of_noop_tp(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Puerto Nuovo')
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.recover.await_count, 1)
        self.navmap.assert_awaited_once_with(self.client, self.target, reenter=True)

    async def test_ungol_door_at_one_unit_reenters_while_hitter_probe_is_pending(self):
        self.target = XYZ(578.4835, 121.3395, .165)
        self.position = XYZ(578.4835, 121.3395, -1.1)
        self.client.quest_position.position.return_value = self.target
        self.client.zone_name.return_value = 'Khrysalis/Interiors/KR_Z11_I06_WeftTowerTop'
        self.client.quest_party_probe_pending = True
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, '前往 Ungol之路 地点：蜂巢')
        self.assertEqual(await self.observe(), [False, False, True])
        self.navmap.assert_awaited_once_with(self.client, self.target, reenter=True)
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_hitter_probe_and_own_refill_still_prevent_exploration_reentry(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, '前往 Ungol之路 地点：蜂巢')
        self.client.quest_party_probe_pending = True
        self.client.quest_party_quester = SimpleNamespace(title='p3')
        self.assertEqual(await self.observe(), [False, False, False])
        self.client.quest_party_quester = None
        self.client.refilling_potions = True
        self.assertEqual(await self.observe(), [False, False, False])
        self.recover.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_exploration_at_coordinate_without_progress_keeps_two_attempt_limit(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        self.recover.return_value = True  # Walking to the coordinate alone is not progress.
        await self.observe()
        await self.observe()
        self.assertEqual(await self.observe(4), [True] * 4)
        self.assertEqual(self.navmap.await_count, 2)
        self.assertEqual(self.recover.await_count, 2)

    async def test_zone_change_in_collision_reentry_skips_navigation_fallback(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        async def change_zone(*args, **kwargs):
            self.client.zone_name.return_value = 'Khrysalis/KR_Z11_TheHive'
            return True
        self.recover.side_effect = change_zone
        await self.observe()
        self.navmap.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._trigger_reentry)

    async def test_navigation_fallback_task_progress_cancels_and_drains_only_own_movement(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        drained = asyncio.Event()
        async def navigate(*args, **kwargs):
            self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Talk To Old Cob')
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.navmap.side_effect = navigate
        peer = asyncio.create_task(asyncio.Event().wait())
        try:
            await self.observe()
            self.assertTrue(drained.is_set())
            self.assertFalse(peer.done())
            self.assertIsNone(self.client.quest_recovery_owner)
            self.assertNotIn(id(self.client), self.quester._trigger_reentry)
        finally:
            peer.cancel()
            await asyncio.gather(peer, return_exceptions=True)

    async def test_navigation_fallback_dialogue_prompt_stops_and_releases_owner(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        drained = asyncio.Event()
        async def navigate(*args, **kwargs):
            self.quester.quest_interaction_ready.return_value = True
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.navmap.side_effect = navigate
        await self.observe()
        self.assertTrue(drained.is_set())
        self.assertIsNone(self.client.quest_recovery_owner)
        self.client.send_key.assert_not_awaited()

    async def test_navigation_timeout_drains_movement_and_releases_owner(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        drained = asyncio.Event()
        async def navigate(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.navmap.side_effect = navigate
        with patch('src.questing.asyncio.sleep', new=REAL_SLEEP), \
                patch('src.questing.asyncio.timeout', side_effect=lambda duration: REAL_TIMEOUT(.01)) as timeout:
            await self.observe()
        timeout.assert_called_once_with(15)
        self.assertTrue(drained.is_set())
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertEqual(self.quester._trigger_reentry[id(self.client)]['attempts'], 1)

    async def test_navigation_cancellation_drains_movement_and_releases_owner(self):
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Go To Ungol Road')
        started, drained = asyncio.Event(), asyncio.Event()
        async def navigate(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.navmap.side_effect = navigate
        with patch('src.questing.asyncio.sleep', new=REAL_SLEEP):
            task = asyncio.create_task(self.observe())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(drained.is_set())
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_exhausted_goal_resumes_on_real_progress(self):
        await self.observe()
        await self.observe()
        self.assertEqual(await self.observe(3), [True] * 3)
        self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Use Next Trigger')
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertEqual(self.recover.await_count, 3)

    async def test_far_target_and_non_interaction_goal_never_move(self):
        self.position = XYZ(2000.0, 2000.0, 0.0)
        self.assertEqual(await self.observe(5), [False] * 5)
        self.position = self.target
        self.quester._dungeon_quest_snapshot.return_value = (42, 7, "Defeat 0/3")
        self.assertEqual(await self.observe(5), [False] * 5)
        self.client.goto.assert_not_awaited()
        self.recover.assert_not_awaited()

    async def test_progress_change_and_busy_state_reset_observations(self):
        self.assertEqual(await self.observe(2), [False, False])
        self.client.quest_party_probe_pending = True
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.quest_party_probe_pending = False
        self.quester._dungeon_quest_snapshot.return_value = (42, 8, "Talk To Merle")
        self.assertEqual(await self.observe(2), [False, False])
        self.client.goto.assert_not_awaited()
        self.recover.assert_not_awaited()

    async def test_zone_change_during_retreat_aborts_and_releases_lock(self):
        async def change_zone(*args, **kwargs):
            self.client.zone_name.return_value = "World/Other"
            self.assertTrue(await kwargs['stop_condition']())
            return True

        self.recover.side_effect = change_zone
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertNotIn(id(self.client), self.quester._trigger_reentry)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancellation_during_retreat_releases_lock(self):
        self.recover.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.observe()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_wrong_nearby_prompt_does_not_suppress_recovery(self):
        from src.paths import npc_range_path
        with patch('src.questing.is_visible_by_path', new=AsyncMock(
                side_effect=lambda client, path: path == npc_range_path)):
            self.assertEqual(await self.observe(), [False, False, True])
        self.recover.assert_awaited_once()

    async def test_task_or_target_progress_during_walk_stops_and_resets(self):
        for change in ('task', 'target'):
            self.quester._trigger_reentry.clear()
            self.quester._dungeon_quest_snapshot.return_value = (42, 7, 'Talk To Merle')
            self.client.quest_position.position.return_value = self.target
            async def progress(*args, **kwargs):
                if change == 'task':
                    self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Talk To Merle')
                else:
                    self.client.quest_position.position.return_value = XYZ(1500, 2000, 0)
                self.assertTrue(await kwargs['stop_condition']())
                return True
            self.recover.side_effect = progress
            self.assertEqual(await self.observe(), [False, False, True])
            self.assertNotIn(id(self.client), self.quester._trigger_reentry)

    async def test_matching_talk_prompt_during_walk_stops_and_clears_reentry(self):
        async def prompt(*args, **kwargs):
            self.quester.quest_interaction_ready.return_value = True
            self.assertTrue(await kwargs['stop_condition']())
            return True
        self.recover.side_effect = prompt
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertNotIn(id(self.client), self.quester._trigger_reentry)

    async def test_matching_collect_prompt_during_walk_stops_final_stretch(self):
        self.quester.read_popup.return_value = 'Press X to Collect'
        async def prompt(*args, **kwargs):
            self.quester.quest_interaction_ready.return_value = True
            self.assertTrue(await kwargs['stop_condition']())
            return True
        self.recover.side_effect = prompt
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertNotIn(id(self.client), self.quester._trigger_reentry)

    async def test_unrelated_talk_prompt_during_walk_does_not_stop_reentry(self):
        async def unrelated(*args, **kwargs):
            self.assertFalse(await kwargs['stop_condition']())
            return True
        self.recover.side_effect = unrelated
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertIn(id(self.client), self.quester._trigger_reentry)

    async def test_formal_dialogue_during_walk_stops_and_releases_recovery(self):
        async def dialogue(*args, **kwargs):
            with patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=False)):
                self.assertTrue(await kwargs['stop_condition']())
            return True
        self.recover.side_effect = dialogue
        self.assertEqual(await self.observe(), [False, False, True])
        self.assertIsNone(self.client.quest_recovery_owner)
        self.client.send_key.assert_not_awaited()

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
