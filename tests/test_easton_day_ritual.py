import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
from tests import test_dueling_tent_recovery as recovery_tests


class EastonDayRitualTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.EASTON_DAY_RITUAL_ZONE

    def setUp(self):
        recovery_tests.DuelingTentRecoveryTests.setUp(self)
        self.progress = (Quester.EASTON_DAY_QUEST_ID, 4, '追随 阿列克斯 疗愈者 地点：Graveholm')
        self.target = XYZ(-7206.248, 2036.380, 851.999)
        self.events = []
        self.client.body.write_orientation = AsyncMock(side_effect=lambda orientation: self.events.append(('body', orientation)))
        self.camera = SimpleNamespace(update_orientation=AsyncMock(side_effect=lambda orientation: self.events.append(('camera', orientation))))
        self.client.game_client = SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera))
        self.client.send_key = AsyncMock(side_effect=lambda key, duration: self.events.append(('key', key)))

        async def teleport(point):
            self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.events.append(('tp', point))
            self.client.body.position.return_value = point
        self.client.teleport.side_effect = teleport
        patcher = patch('src.questing.get_quest_name', new=AsyncMock(side_effect=lambda c: self.progress[2]))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def move(self, *times, quester=None):
        for self.now in times:
            await (quester or self.quester).teleport_to_quest_target(self.client, self.target)

    def photo_goal(self):
        self.progress = (Quester.EASTON_DAY_QUEST_ID, 5, 'Photomance 仪式 地点：Graveholm')

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_arrival_after_three_tps_ten_seconds_sets_exact_position_and_view_without_photo(self):
        await self.move(0, 5)
        self.client.teleport.assert_not_awaited()
        await self.move(10)
        self.assertEqual(self.events, [('tp', Quester.EASTON_DAY_RITUAL_POSITION),
            ('body', Quester.EASTON_DAY_RITUAL_ORIENTATION), ('camera', Quester.EASTON_DAY_RITUAL_ORIENTATION)])
        self.assertEqual(self.collision.await_count, 3)
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_photo_sets_body_and_camera_before_z_and_does_not_replay(self):
        self.photo_goal()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.events, [('tp', Quester.EASTON_DAY_RITUAL_POSITION),
            ('body', Quester.EASTON_DAY_RITUAL_ORIENTATION), ('camera', Quester.EASTON_DAY_RITUAL_ORIENTATION),
            ('key', Keycode.S), ('key', Keycode.Z), ('key', Keycode.Z)])
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(len(self.events), 6)
        self.assert_released()

    async def test_arrival_then_actual_photo_goal_allows_one_new_attempt(self):
        await self.move(0, 5, 10)
        self.photo_goal()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.client.send_key.await_count, 3)
        self.assert_released()

    async def test_blocked_photo_navigation_uses_same_photo_recovery(self):
        self.photo_goal()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once_with(Quester.EASTON_DAY_RITUAL_POSITION)
        self.assertEqual(self.client.send_key.await_count, 3)

    async def test_same_goal_survives_worker_recreation_and_blocks_normal_tp(self):
        await self.move(0, 5, 10)
        count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await self.move(30, 40, 50, quester=restarted)
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.collision.await_count, count)

    async def test_target_jitter_does_not_restart_observation(self):
        for self.now in (0, 5, 10):
            self.target = XYZ(-7206.248 - self.now, 2036.380, 851.999)
            await self.move(self.now)
        self.client.teleport.assert_awaited_once()

    async def test_other_house_other_quest_or_earlier_battle_target_never_redirects(self):
        for zone in ('Darkmoor/Interiors/DM_Z01I02_EastonHouse', 'Darkmoor/Interiors/DM_Z01I05_BrokenBranchHovel'):
            self.zone = zone
            self.progress = (99, 4, '追随 阿列克斯 地点：Graveholm')
            await self.move(0, 5, 10)
        self.zone = self.source_zone
        await self.move(0, 5, 10)
        self.progress = (Quester.EASTON_DAY_QUEST_ID, 4, '追随 阿列克斯 地点：Graveholm')
        self.target = XYZ(-2723.326, 1830.182, 852.874)
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_only_matching_ritual_photo_goal_may_take_special_photo(self):
        self.photo_goal()
        self.progress = (99, 5, self.progress[2])
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_priorities_and_assigned_hitter_block_special_inputs(self):
        self.photo_goal()
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active',
                     'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.quester.take_photomancy_photo(self.client, self.progress[2])
            setattr(self.client, attr, False)
        self.client.quest_party_status_session = 'follow'
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.quest_party_status_session = None
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.events, [])
        self.assert_released()

    async def test_loading_battle_dialogue_stopped_and_other_owner_block_photo(self):
        self.photo_goal()
        for attr in ('is_loading', 'in_battle', 'is_in_dialog'):
            getattr(self.client, attr).return_value = True
            await self.quester.take_photomancy_photo(self.client, self.progress[2])
            getattr(self.client, attr).return_value = False
        self.client.questing_status = False
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.questing_status = True
        self.client.quest_recovery_owner = 'other'
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.events, [])
        self.assertEqual(self.client.quest_recovery_owner, 'other')

    async def test_normal_approach_is_not_misclassified_as_stall(self):
        async def approach(*args, **kwargs):
            current = await self.client.body.position()
            self.client.body.position.return_value = XYZ(current.x - 300, current.y + 100, current.z)
        self.collision.side_effect = approach
        await self.move(0, 5, 10, 15)
        self.client.teleport.assert_not_awaited()

    async def test_collision_rejection_counts_despite_small_body_motion(self):
        async def reject(*args, **kwargs):
            self.client._collision_tp_rejections = getattr(self.client, '_collision_tp_rejections', 0) + 1
            self.client.body.position.return_value = XYZ(-self.now * 200, 0, 0)
        self.collision.side_effect = reject
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()

    async def test_real_goal_progress_resets_stall_counter(self):
        await self.move(0, 5)
        self.progress = (Quester.EASTON_DAY_QUEST_ID, 5, 'Photomance 仪式 地点：Graveholm')
        await self.move(10, 15)
        self.client.teleport.assert_not_awaited()

    async def test_tp_without_confirmed_landing_never_rotates_or_photos_or_replays(self):
        self.photo_goal()
        self.client.teleport.side_effect = None
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.body.write_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once()
        self.assertLess(self.now, 6)
        self.assert_released()

    async def test_loading_after_tp_prevents_orientation_and_photo(self):
        self.photo_goal()
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.body.write_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_last_snapshot_priority_takeover_prevents_tp_and_defers_attempt(self):
        self.photo_goal()
        def snapshot(client):
            client.quest_party_probe_pending = True
            return self.progress
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_not_awaited()
        self.assertFalse(hasattr(self.client, '_xuanshu_easton_day_ritual_attempt'))
        self.assert_released()

    async def test_camera_lookup_task_change_prevents_camera_write_and_z(self):
        self.photo_goal()
        async def camera():
            self.progress = (Quester.EASTON_DAY_QUEST_ID, 6, '拜访 其他 NPC 地点：Graveholm')
            return self.camera
        self.client.game_client.selected_camera_controller.side_effect = camera
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.camera.update_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_progress_after_first_z_skips_second_z(self):
        self.photo_goal()
        async def key(key, duration):
            if key == Keycode.Z:
                self.progress = (Quester.EASTON_DAY_QUEST_ID, 6, '拜访 阿列克斯 地点：Graveholm')
        self.client.send_key.side_effect = key
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual([call.args for call in self.client.send_key.await_args_list],
                         [(Keycode.S, .2), (Keycode.Z, .1)])
        self.assert_released()

    async def test_camera_unreadable_never_blindly_photos(self):
        self.photo_goal()
        self.client.game_client.selected_camera_controller.return_value = None
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_cancelled_attempt_releases_owner_and_is_not_replayed(self):
        self.photo_goal()
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assert_released()
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await restarted.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once()

    async def test_generic_trigger_reentry_cannot_return_to_rejected_ritual_target(self):
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        await self.move(0, 5, 10)
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.teleport.assert_awaited_once_with(Quester.EASTON_DAY_RITUAL_POSITION)
