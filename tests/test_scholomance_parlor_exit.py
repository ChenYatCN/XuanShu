import unittest
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
from tests import test_scholomance_exit as fixtures


class ScholomanceParlorExitTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.SCHOLOMANCE_PARLOR_ZONE
    owner = 'easton_house'
    failed_attr = '_xuanshu_scholomance_parlor_failed'
    move = fixtures.ScholomanceExitTests.move
    assert_released = fixtures.ScholomanceExitTests.assert_released

    # Run the same proven exit safety cases against this independent profile.
    test_three_stalled_tps_over_ten_seconds_then_exact_exit_and_sync = fixtures.ScholomanceExitTests.test_three_stalled_tps_over_ten_seconds_then_exact_exit_and_sync
    test_two_tps_after_ten_seconds_are_not_enough = fixtures.ScholomanceExitTests.test_two_tps_after_ten_seconds_are_not_enough
    test_exactly_three_tps_and_ten_seconds_trigger = fixtures.ScholomanceExitTests.test_exactly_three_tps_and_ten_seconds_trigger
    test_goal_change_resets_stall = fixtures.ScholomanceExitTests.test_goal_change_resets_stall
    test_rejected_tp_body_bounce_still_counts_without_progress = fixtures.ScholomanceExitTests.test_rejected_tp_body_bounce_still_counts_without_progress
    test_target_jitter_does_not_reset_watch = fixtures.ScholomanceExitTests.test_target_jitter_does_not_reset_watch
    test_normal_approach_resets_counter = fixtures.ScholomanceExitTests.test_normal_approach_resets_counter
    test_normal_tp_progress_prevents_fallback = fixtures.ScholomanceExitTests.test_normal_tp_progress_prevents_fallback
    test_configured_quester_supported_assigned_hitter_blocked = fixtures.ScholomanceExitTests.test_configured_quester_supported_assigned_hitter_blocked
    test_priorities_busy_ui_and_task_stop_block_special_tp = fixtures.ScholomanceExitTests.test_priorities_busy_ui_and_task_stop_block_special_tp
    test_two_failed_exit_tps_persist_across_worker_recreation = fixtures.ScholomanceExitTests.test_two_failed_exit_tps_persist_across_worker_recreation
    test_takeover_before_first_exit_tp_defers_without_exhausting_stage = fixtures.ScholomanceExitTests.test_takeover_before_first_exit_tp_defers_without_exhausting_stage
    test_goal_progress_during_exit_tp_stops_second_attempt = fixtures.ScholomanceExitTests.test_goal_progress_during_exit_tp_stops_second_attempt
    test_loading_alone_is_not_confirmed_zone_change = fixtures.ScholomanceExitTests.test_loading_alone_is_not_confirmed_zone_change
    test_cancellation_releases_both_owners_and_never_replays = fixtures.ScholomanceExitTests.test_cancellation_releases_both_owners_and_never_replays
    test_generic_reentry_yields_to_dedicated_exit_watch = fixtures.ScholomanceExitTests.test_generic_reentry_yields_to_dedicated_exit_watch

    def setUp(self):
        fixtures.ScholomanceExitTests.setUp(self)
        self.progress = (9876543, 4, '进入 Spiral Door 地点：Scholomance')
        self.target = XYZ(20.275, -96.1, -51.616)
        self.client.body.position.return_value = self.target

    def transition(self):
        async def teleport(point):
            self.assertEqual((point.x, point.y, point.z), (-423.460, 2150.752, 639.664))
            self.assertEqual(self.client.quest_recovery_owner, self.owner)
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.zone = 'Darkmoor/Interiors/VerifiedParlorArrival'
        self.client.teleport.side_effect = teleport

    async def test_exact_room_objective_location_and_readable_snapshot_required(self):
        original = self.progress
        for snapshot, zone in (
            (original, 'Darkmoor/Interiors/DM_Z05I01_OtherRoom'),
            (original, Quester.SCHOLOMANCE_LAB_ZONE),
            ((9876543, 4, '进入 Other Door 地点：Scholomance'), self.source_zone),
            ((9876543, 4, '进入 Spiral Door 地点：Graveholm'), self.source_zone),
            ((9876543, 4, '面对 米兰达·布莱尔 地点：Scholomance'), self.source_zone),
            (None, self.source_zone)):
            self.progress, self.zone = snapshot, zone
            await self.move(0, 5, 10)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_late_priority_during_snapshot_prevents_parlor_tp(self):
        self.client.quest_recovery_owner = self.owner
        async def snapshot(_):
            self.client.quest_party_battle_rescue_active = True
            return self.progress
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        with self.assertRaises(RuntimeError):
            await self.quester._recover_dueling_tent(self.client, self.progress,
                AsyncMock(return_value=False), parlor=True)
        self.client.teleport.assert_not_awaited()

    async def test_old_scholomance_exit_failure_never_blocks_this_new_scene(self):
        self.client._xuanshu_scholomance_exit_failed = dict(zone=Quester.SCHOLOMANCE_LAB_ZONE,
            objective='面对 米兰达·布莱尔', progress=(Quester.SCHOLOMANCE_EXIT_QUEST_ID, 8, ''),
            target=(0, 0, 0), attempts=2)
        self.transition()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client._xuanshu_scholomance_exit_failed)
        self.assert_released()
