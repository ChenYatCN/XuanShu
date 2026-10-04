import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wizwalker import Keycode, XYZ
from src.questing import Quester
from tests import test_easton_day_ritual as ritual_tests


class MayorServantPhotoTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.DARKMOOR_MAYOR_PHOTO_ZONE

    def setUp(self):
        ritual_tests.EastonDayRitualTests.setUp(self)
        self.progress = (881, 3, 'Photomance 半死不活的附庸 地点：Mortal Plain')
        self.quester._mainline_identity = AsyncMock(return_value=(
            881, 'QuestTitle_19BE7D', '最近的受害者', {'world': 'darkmoor', 'number': 30}, True))

    def assert_released(self):
        ritual_tests.EastonDayRitualTests.assert_released(self)

    async def handle(self):
        return await self.quester._maybe_handle_darkmoor_cantrips(self.client)

    def harp_goal(self):
        self.progress = (881, 4, 'Photomance 预兆竖琴 地点：Mortal Plain')

    async def test_harp_exact_position_body_and_camera_before_photo(self):
        self.harp_goal()
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('tp', Quester.DARKMOOR_MAYOR_HARP_POSITION),
            ('body', Quester.DARKMOOR_MAYOR_HARP_ORIENTATION),
            ('camera', Quester.DARKMOOR_MAYOR_HARP_ORIENTATION),
            ('key', Keycode.S), ('key', Keycode.Z), ('key', Keycode.Z)])
        point = self.events[0][1]
        self.assertEqual((point.x, point.y, point.z), (-1203.329, 3168.999, .999))
        self.assertEqual(tuple(self.events[1][1]), (0.000, 0.000, 1.490))
        self.assert_released()

    async def test_harp_common_photo_route_and_worker_recreation_do_not_replay(self):
        self.harp_goal()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        restarted._mainline_identity = self.quester._mainline_identity
        await restarted.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_HARP_POSITION)
        self.camera.update_orientation.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_HARP_ORIENTATION)
        self.assertEqual(self.client.send_key.await_count, 3)
        self.assert_released()

    async def test_harp_direct_task_tp_avoids_normal_movement(self):
        self.harp_goal()
        await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_HARP_POSITION)
        self.collision.assert_not_awaited()

    async def test_servant_then_harp_uses_separate_position_and_view(self):
        await self.handle()
        await self.handle()
        self.harp_goal()
        await self.handle()
        await self.handle()
        self.assertEqual([args.args[0] for args in self.client.teleport.await_args_list],
                         [Quester.DARKMOOR_MAYOR_PHOTO_POSITION, Quester.DARKMOOR_MAYOR_HARP_POSITION])
        self.assertEqual([args.args[0] for args in self.camera.update_orientation.await_args_list],
                         [Quester.DARKMOOR_MAYOR_PHOTO_ORIENTATION, Quester.DARKMOOR_MAYOR_HARP_ORIENTATION])
        self.assertEqual(self.client.send_key.await_count, 6)
        self.assertEqual(self.client._xuanshu_darkmoor_mayor_photo_attempt, (self.source_zone, self.progress))
        self.assert_released()

    async def test_harp_wrong_stage_location_branch_or_stopped_cannot_blindly_photo(self):
        for text in ('拜访 预兆竖琴 地点：Mortal Plain', 'Photomance 预兆竖琴 地点：Graveholm'):
            self.progress = (881, 4, text)
            self.assertFalse(await self.quester._maybe_handle_mayor_photo(self.client))
        self.harp_goal()
        self.zone = 'Darkmoor/DM_Z02_MortalPlain'
        self.assertFalse(await self.quester._maybe_handle_mayor_photo(self.client))
        self.zone = self.source_zone
        for identity in (None, (882, 'QuestTitle_19BE7D', '', None, True),
                         (881, 'QuestTitle_19BE81', '', None, True)):
            self.quester._mainline_identity.return_value = identity
            await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.quester._mainline_identity.return_value = (881, 'QuestTitle_19BE7D', '', None, True)
        self.client.questing_status = False
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.events, [])
        self.assert_released()

    async def test_harp_goal_change_during_camera_lookup_stops_old_stage(self):
        self.harp_goal()
        async def camera():
            self.progress = (881, 5, 'Photomance 半死不活的附庸 地点：Mortal Plain')
            return self.camera
        self.client.game_client.selected_camera_controller.side_effect = camera
        await self.handle()
        self.camera.update_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_harp_actual_progress_after_first_z_skips_second(self):
        self.harp_goal()
        async def key(key, seconds):
            if key == Keycode.Z:
                self.progress = (881, 5, '拜访 NPC 地点：Mortal Plain')
        self.client.send_key.side_effect = key
        await self.handle()
        self.assertEqual([call.args for call in self.client.send_key.await_args_list],
                         [(Keycode.S, .2), (Keycode.Z, .1)])
        self.assert_released()

    async def test_harp_cancellation_releases_and_does_not_replay(self):
        self.harp_goal()
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_HARP_POSITION)
        self.assert_released()

    async def test_stop_during_backward_step_prevents_photo(self):
        self.harp_goal()
        async def key(key, duration):
            self.assertEqual((key, duration), (Keycode.S, .2))
            self.client.questing_status = False
        self.client.send_key.side_effect = key
        await self.handle()
        self.client.send_key.assert_awaited_once_with(Keycode.S, .2)
        self.assert_released()

    async def test_cancel_during_backward_step_releases_and_never_replays(self):
        self.harp_goal()
        self.client.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.client.send_key.assert_awaited_once_with(Keycode.S, .2)
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_HARP_POSITION)

    async def test_exact_user_position_body_and_camera_before_photo(self):
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('tp', Quester.DARKMOOR_MAYOR_PHOTO_POSITION),
            ('body', Quester.DARKMOOR_MAYOR_PHOTO_ORIENTATION),
            ('camera', Quester.DARKMOOR_MAYOR_PHOTO_ORIENTATION),
            ('key', Keycode.S), ('key', Keycode.Z), ('key', Keycode.Z)])
        self.assertEqual((self.events[0][1].x, self.events[0][1].y, self.events[0][1].z),
                         (531.067, -473.838, .999))
        self.assertEqual(self.client._xuanshu_darkmoor_mayor_photo_attempt, (self.source_zone, self.progress))
        self.assert_released()

    async def test_regular_photo_call_reuses_same_positioned_flow(self):
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_PHOTO_POSITION)
        self.camera.update_orientation.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_PHOTO_ORIENTATION)
        self.assertEqual(self.client.send_key.await_count, 3)

    async def test_direct_task_tp_routes_photo_before_normal_movement(self):
        await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_MAYOR_PHOTO_POSITION)
        self.collision.assert_not_awaited()

    async def test_same_stage_does_not_replay_from_photo_loop_or_new_worker(self):
        await self.handle()
        await self.quester.take_photomancy_photo(self.client, self.progress[2])
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        restarted._mainline_identity = self.quester._mainline_identity
        await restarted.take_photomancy_photo(self.client, self.progress[2])
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.client.send_key.await_count, 3)

    async def test_wrong_actual_zone_and_objective_do_not_trigger(self):
        self.zone = 'Darkmoor/DM_Z02_MortalPlain'
        self.assertFalse(await self.quester._maybe_handle_mayor_photo(self.client))
        self.zone = self.source_zone
        for text in ('Photomance 其他东西 地点：Mortal Plain',
                     '拜访 半死不活的附庸 地点：Mortal Plain',
                     'Photomance 半死不活的附庸 地点：Graveholm'):
            self.progress = (881, 3, text)
            self.assertFalse(await self.quester._maybe_handle_mayor_photo(self.client))
        self.assertEqual(self.events, [])

    async def test_other_branch_or_unreadable_identity_never_guesses_id_or_blindly_photos(self):
        for identity in (None, (882, 'QuestTitle_19BE7D', '', None, True),
                         (881, 'QuestTitle_19BE81', '', None, True),
                         (881, 'QuestTitle_19BE84', '', None, True)):
            self.quester._mainline_identity.return_value = identity
            await self.quester.take_photomancy_photo(self.client, self.progress[2])
        self.assertEqual(self.events, [])

    async def test_priorities_hitter_stopped_and_other_owner_block_inputs(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.handle()
            setattr(self.client, attr, False)
        self.client.quest_recovery_owner = 'other'
        await self.handle()
        self.client.quest_recovery_owner = None
        self.client.questing_status = False
        await self.handle()
        self.client.questing_status = True
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.handle()
        self.assertEqual(self.events, [])
        self.assert_released()

    async def test_loading_battle_and_dialogue_prevent_photo(self):
        for method in (self.client.is_loading, self.client.in_battle, self.client.is_in_dialog):
            method.return_value = True
            await self.handle()
            method.return_value = False
        self.assertEqual(self.events, [])
        self.assert_released()

    async def test_unconfirmed_landing_never_rotates_or_photos(self):
        self.client.teleport.side_effect = None
        await self.handle()
        self.client.body.write_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_loading_after_tp_prevents_rotation_and_z(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.body.write_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_camera_lookup_task_change_prevents_camera_write_and_z(self):
        async def camera():
            self.progress = (881, 4, '拜访 NPC 地点：Mortal Plain')
            return self.camera
        self.client.game_client.selected_camera_controller.side_effect = camera
        await self.handle()
        self.camera.update_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_unreadable_camera_never_photos(self):
        self.client.game_client.selected_camera_controller.return_value = None
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_actual_progress_after_first_z_skips_second(self):
        async def key(key, seconds):
            if key == Keycode.Z:
                self.progress = (881, 4, '拜访 NPC 地点：Mortal Plain')
        self.client.send_key.side_effect = key
        await self.handle()
        self.assertEqual([call.args for call in self.client.send_key.await_args_list],
                         [(Keycode.S, .2), (Keycode.Z, .1)])
        self.assert_released()

    async def test_cancellation_releases_and_does_not_replay(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.client.teleport.assert_awaited_once()
