import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
from tests import test_dueling_tent_recovery as recovery_tests


class EastonDayGuardExitTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.EASTON_DAY_RITUAL_ZONE
    failed_attr = '_xuanshu_easton_day_guard_failed'

    def setUp(self):
        recovery_tests.DuelingTentRecoveryTests.setUp(self)
        self.progress = (Quester.EASTON_DAY_QUEST_ID, 9, '站立 守卫 地点：Graveholm')

    async def handle(self):
        return await self.quester._maybe_handle_darkmoor_cantrips(self.client)

    def transition(self):
        async def teleport(point):
            self.assertEqual(point, Quester.EASTON_DAY_GUARD_EXIT)
            self.assertEqual(self.client.quest_recovery_owner, 'easton_house')
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.zone = 'Darkmoor/DM_Z01_Graveholm'
        self.client.teleport.side_effect = teleport

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_matching_guard_directly_exits_without_stall_or_normal_tp(self):
        self.transition()
        await self.quester.teleport_to_quest_target(self.client, XYZ(1.332, 1696.263, 2))
        self.client.teleport.assert_awaited_once_with(Quester.EASTON_DAY_GUARD_EXIT)
        self.collision.assert_not_awaited()
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, 'Darkmoor/DM_Z01_Graveholm')
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()

    async def test_wrong_zone_parent_goal_and_location_never_exit(self):
        self.zone = Quester.EASTON_HOUSE_ZONE
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.zone = self.source_zone
        for progress in ((99, 9, self.progress[2]),
                         (Quester.EASTON_DAY_QUEST_ID, 9, 'Photomance 仪式 地点：Graveholm'),
                         (Quester.EASTON_DAY_QUEST_ID, 9, '追随 阿列克斯 疗愈者 地点：Graveholm'),
                         (Quester.EASTON_DAY_QUEST_ID, 9, '站立 守卫 地点：Elsewhere'), None):
            self.progress = progress
            self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_two_attempt_limit_and_persistent_failure_across_worker_recreation(self):
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertLess(self.now, 25)
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await restarted.teleport_to_quest_target(self.client, XYZ(1, 1696, 2))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.collision.assert_not_awaited()
        self.assert_released()

    async def test_priorities_and_busy_states_defer_without_tp(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            self.assertTrue(await self.handle())
            setattr(self.client, attr, False)
        for method in (self.client.is_loading, self.client.in_battle):
            method.return_value = True
            self.assertTrue(await self.handle())
            method.return_value = False
        self.leader_free.return_value = False
        self.assertTrue(await self.handle())
        self.leader_free.return_value = True
        self.client.quest_recovery_owner = 'other'
        self.assertTrue(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_assigned_hitters_and_stopped_client_do_not_run_exit(self):
        self.client.questing_status = False
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.questing_status = True
        self.client.quest_party_status_session = 'follow'
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.quest_party_status_session = None
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_normal_ui_prevents_exit_and_does_not_exhaust_attempt(self):
        self.visible.return_value = True
        await self.handle()
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.visible.return_value = False
        self.transition()
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_priority_changes_during_last_snapshot_prevent_tp(self):
        reads = 0
        def snapshot(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                client.refilling_potions = True
            return self.progress
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.handle()
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()

    async def test_task_progress_before_first_tp_cancels_old_exit(self):
        reads = 0
        def snapshot(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                return Quester.EASTON_DAY_QUEST_ID, 10, '拜访 NPC 地点：Graveholm'
            return self.progress
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.handle()
        self.client.teleport.assert_not_awaited()
        self.assert_released()

    async def test_loading_alone_does_not_count_as_confirmed_transition(self):
        self.client.teleport.side_effect = lambda point: setattr(self.client.is_loading, 'return_value', True)
        async def tick(seconds):
            self.now += seconds
            if self.now > 3:
                raise TimeoutError('Loading alone is not a stable new zone')
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)):
            await self.handle()
        self.client.teleport.assert_awaited_once()
        self.client.quest_position.position.assert_not_awaited()
        self.assertFalse(getattr(self.client, 'quest_party_probe_pending', False))
        self.assert_released()

    async def test_real_progress_after_first_tp_stops_second_attempt(self):
        async def teleport(point):
            self.progress = (Quester.EASTON_DAY_QUEST_ID, 10, '拜访 NPC 地点：Graveholm')
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()

    async def test_cancelled_tp_releases_owner_and_never_replays(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        self.assertTrue(await self.handle())
        self.client.teleport.assert_awaited_once()

    async def test_loading_then_stable_new_zone_restores_original_party_sync(self):
        async def teleport(point):
            self.client.is_loading.return_value = True
            ready = self.now + 1
            def tick():
                if self.now >= ready:
                    self.client.is_loading.return_value = False
                    self.zone = 'Darkmoor/DM_Z01_Graveholm'
            self.on_tick = tick
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.client.quest_position.position.assert_awaited_once()
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assert_released()
