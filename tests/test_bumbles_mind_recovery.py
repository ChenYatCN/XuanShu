import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester
from tests import test_private_wing_exit as exits


class BumblesMindRecoveryTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.BUMBLES_MIND_ZONE
    failed_attr = '_xuanshu_bumbles_mind_failed'
    move = exits.PrivateWingExitTests.move

    def setUp(self):
        exits.PrivateWingExitTests.setUp(self)
        self.text = '退出 大黄蜂的脑海 地点：废料堆'
        self.progress = (123, 1, self.text)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.text)
        self.battle = False
        self.client.in_battle.side_effect = lambda: self.battle
        async def tick(seconds):
            self.now += seconds
        timer = patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick))
        timer.start()
        self.addCleanup(timer.stop)

    async def test_failure_is_bounded_and_survives_worker_restart(self):
        with patch('src.questing.logger') as log:
            await self.move(0, 4, 8, 11, 40, 50)
        self.assertEqual(self.client.teleport.await_count, 2)
        log.warning.assert_called_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        restarted = Quester(self.client, [self.client], None)
        restarted.read_quest_txt = self.quester.read_quest_txt
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        for self.now in (60, 65, 70, 75):
            await restarted.teleport_to_quest_target(self.client, XYZ(10000, 1, 0))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.progress = (123, 2, 'next')
        await restarted.teleport_to_quest_target(self.client, XYZ(10000, 1, 0))
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_waits_existing_battle_and_actual_progress_before_unlock(self):
        async def teleport(target):
            self.assertEqual(self.client.quest_recovery_owner, 'bumbles_mind')
            self.assertEqual((target.x, target.y, target.z), (2.390, -259.991, -1124.726))
            self.battle = True
        async def tick(seconds):
            self.assertEqual(self.client.quest_recovery_owner, 'bumbles_mind')
            self.now += seconds
            if self.now >= 12:
                self.battle = False
            if self.now >= 13:
                self.progress = (123, 2, 'next')
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)):
            await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.assertGreaterEqual(self.now, 13)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_completed_battle_without_progress_is_not_success(self):
        async def teleport(_):
            self.battle = True
        async def tick(seconds):
            self.now += seconds
            self.battle = False
        self.client.teleport.side_effect = teleport
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)), patch('src.questing.logger') as log:
            await self.move(0, 4, 8, 11)
        log.warning.assert_called_once()
        self.assertIsInstance(getattr(self.client, self.failed_attr), dict)

        count = self.client.teleport.await_count
        await self.move(40, 50, 60)
        self.assertEqual(self.client.teleport.await_count, count)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_unfinished_battle_times_out_without_more_tp(self):
        self.client.teleport.side_effect = lambda _: setattr(self, 'battle', True)
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_other_stage_in_same_map_is_ordinary_movement(self):
        self.text = '击败 敌人 地点：废料堆'
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.quester.move_until_quest_interaction.assert_awaited()

    async def test_task_progress_before_threshold_resets_watch(self):
        await self.move(0, 4)
        self.progress = (123, 2, 'next')
        await self.move(8, 11, 15)
        self.client.teleport.assert_not_awaited()

    async def test_progress_during_normal_tp_cancels_special_tp(self):
        await self.move(0, 4, 8)
        self.collision.side_effect = lambda *args, **kwargs: setattr(self, 'progress', (123, 2, 'next'))
        await self.move(11)
        self.client.teleport.assert_not_awaited()

    async def test_stage_change_during_last_normal_tp_cancels_recovery(self):
        await self.move(0, 4, 8)
        self.collision.side_effect = lambda *args, **kwargs: setattr(self, 'text', '击败 敌人 地点：废料堆')
        await self.move(11)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_unreadable_task_does_not_start_recovery(self):
        self.progress = None
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()

    async def test_assigned_hitter_is_not_moved(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()
        self.assertFalse(await self.quester._maybe_handle_bumbles_mind(self.client))

    async def test_busy_loading_and_other_owner_do_not_start_recovery(self):
        for attr, value in [('quest_recovery_owner', 'dungeon_quest'), ('quest_party_probe_pending', True),
                            ('questing_status', False)]:
            original = getattr(self.client, attr, None)
            setattr(self.client, attr, value)
            await self.move(0, 4, 8, 11)
            setattr(self.client, attr, original)
        self.client.is_loading.return_value = True
        await self.move(20)
        self.client.teleport.assert_not_awaited()

    async def test_cancel_releases_lock_and_suppresses_retrigger(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 4, 8, 11)
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.move(20, 30, 40)
        self.client.teleport.assert_awaited_once()

    async def test_global_end_yields_to_matching_stage(self):
        self.quester.followers_in_correct_zone = AsyncMock(return_value=False)
        self.client.send_key = AsyncMock()
        for self.now in (0, 4, 8, 11):
            await self.quester.zone_recorrect_hub()
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_body_and_target_jitter_do_not_reset_task_stall(self):
        for self.now in (0, 4, 8, 11):
            self.client.body.position.return_value = XYZ(self.now * 200, 0, 0)
            await self.quester.teleport_to_quest_target(self.client, XYZ(9000 + self.now, 0, 0))
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_cosmetic_hud_change_after_battle_is_not_progress(self):
        async def teleport(_):
            self.battle = True
        async def tick(seconds):
            self.now += seconds
            self.battle = False
            self.progress = (123, 1, '退出  大黄蜂的脑海 地点：废料堆')
        self.client.teleport.side_effect = teleport
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)), patch('src.questing.logger') as log:
            await self.move(0, 4, 8, 11)
        log.warning.assert_called_once()
        self.assertIsInstance(getattr(self.client, self.failed_attr), dict)
        count = self.client.teleport.await_count
        await self.move(40, 50, 60)
        self.assertEqual(self.client.teleport.await_count, count)


if __name__ == '__main__':
    unittest.main()
