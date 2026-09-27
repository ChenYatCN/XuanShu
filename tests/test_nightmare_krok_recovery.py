import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.questing import Quester


class NightmareKrokRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.position = XYZ(0, 0, 0)
        self.client = AsyncMock()
        self.client.title = 'p1'
        self.client.questing_status = True
        self.client.entity_detect_combat_status = False
        self.client.quest_recovery_owner = None
        self.client.quest_dungeon_recovery = None
        self.client.quest_nightmare_recovery = None
        self.client.quest_party_hitters = []
        self.client.zone_name.return_value = Quester.NIGHTMARE_ZONE
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.client.body.position.side_effect = lambda: self.position
        self.client.quest_position.position.return_value = XYZ(2, 3, 4)
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Use item'))
        self.quester._trigger_reentry_blocked = AsyncMock(return_value=False)

        async def teleport(point):
            self.assertEqual(self.client.quest_recovery_owner, 'nightmare_krok')
            self.position = point
            if point == self.quester.NIGHTMARE_EXIT:
                self.client.zone_name.return_value = 'Empyrea/Interiors/NextZone'

        async def sleep(seconds):
            self.now += seconds

        self.client.teleport.side_effect = teleport
        self.time_patch = patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now))
        self.sleep_patch = patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep))
        self.free_patch = patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True))
        for item in (self.time_patch, self.sleep_patch, self.free_patch):
            item.start()
            self.addCleanup(item.stop)

    async def start_recovery(self):
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.now = 61.0
        return await self.quester._maybe_recover_nightmare(self.client)

    async def test_strict_twelve_point_order_then_exit_and_stable_zone(self):
        async def quest_changes_after_third_x(*_args):
            if self.client.send_key.await_count == 3:
                self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Next objective')
        self.client.send_key.side_effect = quest_changes_after_third_x
        self.assertTrue(await self.start_recovery())
        points = [call.args[0] for call in self.client.teleport.await_args_list]
        self.assertEqual(points, [*self.quester.NIGHTMARE_POINTS, self.quester.NIGHTMARE_EXIT])
        self.assertEqual(self.client.send_key.await_count, 12)
        self.assertTrue(all(call.args == (Keycode.X, 0.1)
                            for call in self.client.send_key.await_args_list))
        self.assertGreaterEqual(self.now, 61 + 12 * 0.8 + 1.5)
        self.client.quest_position.position.assert_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(self.client.quest_nightmare_recovery)

    async def test_quest_progress_resets_stall_window(self):
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.now = 61.0
        self.quester._dungeon_quest_snapshot.return_value = (42, 8, 'Next objective')
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.client.teleport.assert_not_awaited()
        self.now = 122.0
        self.assertTrue(await self.quester._maybe_recover_nightmare(self.client))

    async def test_loading_must_finish_and_new_zone_stabilize(self):
        final_at = [None]
        async def teleport(point):
            self.position = point
            if point == self.quester.NIGHTMARE_EXIT:
                final_at[0] = self.now
                self.client.is_loading.return_value = True
        async def sleep(seconds):
            self.now += seconds
            if final_at[0] is not None and self.now - final_at[0] < 4.0:
                self.assertEqual(self.client.quest_recovery_owner, 'nightmare_krok')
            if final_at[0] is not None and self.now - final_at[0] >= 4.0:
                self.client.zone_name.return_value = 'Empyrea/Interiors/NextZone'
                self.client.is_loading.return_value = False
        self.client.teleport.side_effect = teleport
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)):
            self.assertTrue(await self.start_recovery())
        self.assertGreaterEqual(self.now - final_at[0], 5.5)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_wrong_zone_or_other_recovery_never_starts(self):
        self.client.zone_name.return_value = 'Empyrea/Interiors/Other'
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.client.teleport.assert_not_awaited()
        self.client.zone_name.return_value = self.quester.NIGHTMARE_ZONE
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.now = 61.0
        self.client.quest_recovery_owner = 'dungeon_quest'
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_failed_point_retries_twice_without_x_or_looping(self):
        self.client.teleport.side_effect = RuntimeError('blocked')
        self.assertTrue(await self.start_recovery())
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.send_key.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.now = 200.0
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        restarted._trigger_reentry_blocked = self.quester._trigger_reentry_blocked
        self.assertFalse(await restarted._maybe_recover_nightmare(self.client))

    async def test_failed_x_is_retried_once_at_the_same_point(self):
        async def send_x(*_args):
            if self.client.send_key.await_count == 1:
                raise RuntimeError('missed X')
        self.client.send_key.side_effect = send_x
        self.assertTrue(await self.start_recovery())
        points = [call.args[0] for call in self.client.teleport.await_args_list]
        self.assertEqual(points[:2], [self.quester.NIGHTMARE_POINTS[0]] * 2)
        self.assertEqual(points[2:], [*self.quester.NIGHTMARE_POINTS[1:], self.quester.NIGHTMARE_EXIT])
        self.assertEqual(self.client.send_key.await_count, 13)

    async def test_final_zone_timeout_releases_lock(self):
        async def stay_in_zone(point):
            self.position = point
        self.client.teleport.side_effect = stay_in_zone
        self.assertTrue(await self.start_recovery())
        self.assertEqual(self.client.teleport.await_count, 13)
        self.assertGreaterEqual(self.now, 61 + 25)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_loading_without_zone_change_times_out(self):
        async def start_loading(point):
            self.position = point
            if point == self.quester.NIGHTMARE_EXIT:
                self.client.is_loading.return_value = True
        self.client.teleport.side_effect = start_loading
        self.assertTrue(await self.start_recovery())
        self.assertEqual(self.client.teleport.await_count, 13)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_final_tp_error_after_zone_change_still_waits_for_stability(self):
        async def teleport(point):
            self.position = point
            if point == self.quester.NIGHTMARE_EXIT:
                self.client.zone_name.return_value = 'Empyrea/Interiors/NextZone'
                raise RuntimeError('teleport acknowledgement was lost')
        self.client.teleport.side_effect = teleport
        self.assertTrue(await self.start_recovery())
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancellation_releases_lock(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        self.assertFalse(await self.quester._maybe_recover_nightmare(self.client))
        self.now = 61.0
        with self.assertRaises(asyncio.CancelledError):
            await self.quester._maybe_recover_nightmare(self.client)
        self.assertIsNone(self.client.quest_recovery_owner)


if __name__ == '__main__':
    unittest.main()
