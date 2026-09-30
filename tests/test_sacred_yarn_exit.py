import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester
from tests import test_private_wing_exit as exits


class SacredYarnExitTests(exits.PrivateWingExitTests):
    source_zone = Quester.SACRED_YARN_ZONE
    owner = 'sacred_yarn'
    exit_xyz = (6.900, 2767.551, -2.704)
    failed_attr = '_xuanshu_sacred_yarn_failed'

    def setUp(self):
        super().setUp()
        async def tick(seconds):
            self.now += seconds
        patcher = patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_failure_releases_lock_and_never_repeats_exit_tp(self):
        with patch('src.questing.logger') as log:
            await self.move(0, 4, 8, 11, 35, 45)
        self.assertEqual(self.client.teleport.await_count, 2)
        log.warning.assert_called_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: self.progress)
        for self.now in (50, 55, 60, 65):
            await restarted.teleport_to_quest_target(self.client, XYZ(7000, 1, 0))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.progress = (123, 2, 'Find the exit')
        await restarted.teleport_to_quest_target(self.client, XYZ(7000, 1, 0))
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_stall_uses_exact_exit_and_waits_for_stable_new_zone(self):
        from src.questing import claim_quest_recovery
        async def teleport(target):
            self.assertEqual(self.client.quest_recovery_owner, self.owner)
            self.assertFalse(claim_quest_recovery(self.client, 'dungeon_quest'))
            self.assertEqual((target.x, target.y, target.z), self.exit_xyz)
            self.zone = 'MooShu/NextZone'
        self.client.teleport.side_effect = teleport
        self.zone_stable.side_effect = [True, True, False, True, True]
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.zone_wait.assert_awaited_once_with(self.client, current_zone=self.source_zone)
        self.client.quest_position.position.assert_awaited_once()
        self.assertEqual(self.zone_stable.await_count, 5)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_body_movement_does_not_reset_task_stall(self):
        for step in (0, 4, 8, 11):
            self.client.body.position.return_value = XYZ(step * 200, 0, 0)
            await self.move(step)
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_task_progress_during_last_normal_tp_cancels_recovery(self):
        await self.move(0, 4, 8)
        async def progress(*args, **kwargs):
            self.progress = (123, 2, 'Next objective')
        self.collision.side_effect = progress
        await self.move(11)
        self.client.teleport.assert_not_awaited()

    async def test_task_progress_after_special_tp_cancels_retry(self):
        async def progress(target):
            self.progress = (123, 2, 'Next objective')
        self.client.teleport.side_effect = progress
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancelled_tp_releases_lock_and_retains_event_suppression(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 4, 8, 11)
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.move(20, 30, 40)
        self.client.teleport.assert_awaited_once()

    async def test_loading_blocks_retry_and_nested_task_tp_until_stable(self):
        async def teleport(target):
            self.client.is_loading.return_value = True
        async def wait(*args, **kwargs):
            count = self.collision.await_count
            await self.move(12)
            self.assertEqual(self.collision.await_count, count)
            self.zone = 'MooShu/NextZone'
            self.client.is_loading.return_value = False
        self.client.teleport.side_effect = teleport
        self.zone_wait.side_effect = wait
        self.zone_stable.side_effect = [True, True, False, True, True]
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.zone_stable.await_count, 5)  # two readiness + arrival
        self.client.quest_position.position.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_second_bounded_tp_can_trigger_zone_change(self):
        async def teleport(target):
            if self.client.teleport.await_count == 2:
                self.zone = 'MooShu/NextZone'
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.quest_position.position.assert_awaited_once()
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_assigned_hitter_never_runs_special_or_normal_task_tp(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()

    async def test_unreadable_task_does_not_establish_stall(self):
        self.progress = None
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_busy_state_resets_observation(self):
        for busy in (self.client.is_loading, self.free):
            await self.move(0, 4)
            busy.return_value = busy is self.client.is_loading
            await self.move(11)
            busy.return_value = busy is self.free
            await self.move(12, 16, 20)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_target_coordinate_jitter_is_not_task_progress(self):
        for self.now in (0, 4, 8, 11):
            await self.quester.teleport_to_quest_target(self.client, XYZ(9000 + self.now, 0, 0))
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_transient_empty_arrival_zone_waits_for_stability_and_task_read(self):
        self.quester.PRIVATE_WING_STABLE_SECONDS = 1.5
        arrival_reads = None
        def zone_name():
            nonlocal arrival_reads
            if arrival_reads is not None:
                arrival_reads += 1
                if arrival_reads <= 2:
                    return None
            return self.zone
        async def teleport(target):
            self.zone = 'MooShu/NextZone'
        async def wait(*args, **kwargs):
            nonlocal arrival_reads
            arrival_reads = 0
        self.client.zone_name.side_effect = zone_name
        self.client.teleport.side_effect = teleport
        self.zone_wait.side_effect = wait
        self.quester._dungeon_quest_snapshot.side_effect = lambda _: (
            None if arrival_reads is not None and self.now < 14 else self.progress)
        await self.move(0, 4, 8, 11)
        self.assertGreaterEqual(self.now, 14)
        self.client.quest_position.position.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_zone_wait_exception_logs_once_and_releases_lock(self):
        async def teleport(target):
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        self.zone_wait.side_effect = TimeoutError('loading did not end')
        with patch('src.questing.logger') as log:
            await self.move(0, 4, 8, 11)
        log.warning.assert_called_once()
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)


if __name__ == '__main__':
    unittest.main()
