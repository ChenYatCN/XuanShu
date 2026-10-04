import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
from tests import test_dueling_tent_recovery as fixtures


class ScholomanceExitTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.SCHOLOMANCE_LAB_ZONE
    owner = 'easton_house'
    failed_attr = '_xuanshu_scholomance_exit_failed'

    def setUp(self):
        fixtures.DuelingTentRecoveryTests.setUp(self)
        self.progress = (Quester.SCHOLOMANCE_EXIT_QUEST_ID, 8,
                         '面对 米兰达·布莱尔 地点：Scholomance')
        self.target = XYZ(-644.310, 8555.007, 1693.219)
        self.client.body.position.return_value = self.target

    async def move(self, *times, quester=None):
        for self.now in times:
            await (quester or self.quester).teleport_to_quest_target(self.client, self.target)

    def transition(self):
        async def teleport(point):
            self.assertEqual((point.x, point.y, point.z), (-554.610, 8442.424, 1693.215))
            self.assertEqual(self.client.quest_recovery_owner, self.owner)
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.zone = 'Darkmoor/Interiors/VerifiedArrival'
        self.client.teleport.side_effect = teleport

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_three_stalled_tps_over_ten_seconds_then_exact_exit_and_sync(self):
        self.transition()
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.client.quest_party_quest_worker_zone, self.zone)
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.client.quest_position.position.assert_awaited_once()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        self.assert_released()

    async def test_two_tps_after_ten_seconds_are_not_enough(self):
        await self.move(0, 12)
        self.client.teleport.assert_not_awaited()

    async def test_exactly_three_tps_and_ten_seconds_trigger(self):
        self.transition()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()

    async def test_wrong_quest_objective_location_and_region_never_exit(self):
        for progress, zone in (
            ((123, 8, self.progress[2]), self.source_zone),
            ((Quester.SCHOLOMANCE_EXIT_QUEST_ID, 8, '进入 说谎者之林 地点：Scholomance'), self.source_zone),
            ((Quester.SCHOLOMANCE_EXIT_QUEST_ID, 8, '面对 另一个人 地点：Scholomance'), self.source_zone),
            ((Quester.SCHOLOMANCE_EXIT_QUEST_ID, 8, '面对 米兰达·布莱尔 地点：Graveholm'), self.source_zone),
            (self.progress, 'Darkmoor/DM_Z05_Scholomance'),
            (None, self.source_zone)):
            self.progress, self.zone = progress, zone
            await self.move(0, 5, 10)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_goal_change_resets_stall(self):
        await self.move(0, 4, 8)
        self.progress = (self.progress[0], 9, self.progress[2])
        await self.move(11, 15, 19)
        self.client.teleport.assert_not_awaited()

    async def test_rejected_tp_body_bounce_still_counts_without_progress(self):
        self.transition()
        async def rejected(*args, **kwargs):
            self.client._collision_tp_rejections = getattr(self.client, '_collision_tp_rejections', 0) + 1
            self.client.body.position.return_value = XYZ(100, 8660.254, 2297.168)
        self.collision.side_effect = rejected
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()

    async def test_target_jitter_does_not_reset_watch(self):
        self.transition()
        await self.move(0, 5)
        self.target = XYZ(-650, 8557, 1693)
        await self.move(10)
        self.client.teleport.assert_awaited_once()

    async def test_normal_approach_resets_counter(self):
        self.client.body.position.return_value = XYZ(2000, 0, 0)
        self.collision.side_effect = lambda *a, **kw: setattr(self.client.body.position, 'return_value', self.target)
        await self.move(0)
        self.collision.side_effect = None
        await self.move(5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_normal_tp_progress_prevents_fallback(self):
        await self.move(0, 5)
        self.collision.side_effect = lambda *a, **kw: setattr(self, 'progress', (self.progress[0], 9, '进入 下一处 地点：Scholomance'))
        await self.move(10)
        self.client.teleport.assert_not_awaited()

    async def test_configured_quester_supported_assigned_hitter_blocked(self):
        self.client.quest_party_status_session = object()
        self.transition()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.assert_released()
        self.client.teleport.reset_mock()
        self.zone = self.source_zone
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(20, 25, 30)
        self.client.teleport.assert_not_awaited()

    async def test_priorities_busy_ui_and_task_stop_block_special_tp(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.move(0, 5, 10)
            setattr(self.client, attr, False)
        self.visible.return_value = True
        await self.move(0, 5, 10)
        self.visible.return_value = False
        self.client.questing_status = False
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_two_failed_exit_tps_persist_across_worker_recreation(self):
        await self.move(0, 5, 10)
        self.assertEqual(self.client.teleport.await_count, 2)
        count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await self.move(40, 50, 60, quester=restarted)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, count)
        self.assert_released()

    async def test_takeover_before_first_exit_tp_defers_without_exhausting_stage(self):
        original = self.quester._recover_dueling_tent
        async def takeover(client, progress, pending, **kwargs):
            client.refilling_potions = True
            await original(client, progress, pending, **kwargs)
        self.quester._recover_dueling_tent = takeover
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()
        self.client.refilling_potions = False
        self.quester._recover_dueling_tent = original
        self.transition()
        await self.move(11, 16, 21)
        self.client.teleport.assert_awaited_once()

    async def test_last_snapshot_priority_change_prevents_exit_tp(self):
        self.client.quest_recovery_owner = self.owner
        async def snapshot(_):
            self.client.quest_party_battle_rescue_active = True
            return self.progress
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        with self.assertRaises(RuntimeError):
            await self.quester._recover_dueling_tent(self.client, self.progress,
                AsyncMock(return_value=False), scholomance=True)
        self.client.teleport.assert_not_awaited()

    async def test_goal_progress_during_exit_tp_stops_second_attempt(self):
        self.client.teleport.side_effect = lambda point: setattr(self, 'progress', (self.progress[0], 9, '新步骤'))
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()

    async def test_loading_alone_is_not_confirmed_zone_change(self):
        self.client.teleport.side_effect = lambda point: setattr(self.client.is_loading, 'return_value', True)
        def limit():
            if self.now > 20:
                raise TimeoutError('No actual new zone')
        self.on_tick = limit
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.assertFalse(getattr(self.client, 'quest_party_probe_pending', False))
        self.client.quest_position.position.assert_not_awaited()
        self.assert_released()

    async def test_cancellation_releases_both_owners_and_never_replays(self):
        self.client.teleport.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 5, 10)
        self.assert_released()
        await self.move(40, 50)
        self.client.teleport.assert_awaited_once()

    async def test_generic_reentry_yields_to_dedicated_exit_watch(self):
        self.client.goto = AsyncMock()
        for _ in range(5):
            self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.goto.assert_not_awaited()
