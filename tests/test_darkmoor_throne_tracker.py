import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.questing import Quester
from tests import test_darkmoor_false_shadows as fixtures


class ThroneTrackerTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the existing Cantrip UI/ownership fixture, not its scenario tests.
    sleep = fixtures.FalseShadowsTests.sleep
    assert_owned = fixtures.FalseShadowsTests.assert_owned
    open_toolbar = fixtures.FalseShadowsTests.open_toolbar
    handle = fixtures.FalseShadowsTests.handle
    finish_dialogue = fixtures.FalseShadowsTests.finish_dialogue
    assert_released = fixtures.FalseShadowsTests.assert_released
    snapshot = fixtures.FalseShadowsTests.snapshot
    teleport = fixtures.FalseShadowsTests.teleport
    interact = fixtures.FalseShadowsTests.interact

    def setUp(self):
        fixtures.FalseShadowsTests.setUp(self)
        self.quest_id = 8675309  # Arbitrary live fixture ID, not a production constant.
        self.text = 'Cast Calescent Tracker 找到灰魔 地点：Graveholm'
        self.client.zone_name.return_value = Quester.DARKMOOR_THRONE_ZONE
        self.template.display_name.return_value = 'Cantrips_00000209'
        self.cast_position = XYZ(300, 400, 200)
        self.client.body.position.return_value = self.cast_position
        self.client.quest_position = SimpleNamespace(position=AsyncMock(return_value=self.cast_position))
        self.client.teleport.side_effect = self.follow_tp

    async def select_card(self, card):
        self.assert_owned()
        self.assertIs(card, self.card)
        self.toolbar, self.armed = False, True
        self.events.append(('card', 'Calescent Tracker'))

    async def target_click(self, x, y):
        self.assert_owned()
        self.assertTrue(self.armed)
        self.events.append(('target', (x, y)))
        self.armed = False
        self.follow_stage()

    async def follow_tp(self, position):
        await self.teleport(position)
        self.assertEqual(position, Quester.DARKMOOR_THRONE_FOLLOW_POSITION)
        self.client.is_in_dialog.return_value = True

    def follow_stage(self):
        self.goal, self.text = 2, 'Follow Calescent Tracker 找到灰魔 地点：Graveholm'

    def next_stage(self):
        self.goal, self.text = 3, '输入 Secret Lab 地点：Graveholm'

    async def test_cast_follow_exact_tp_dialogue_and_normal_resume(self):
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('wand', None), ('card', 'Calescent Tracker'), ('target', (575, 710))])
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'throne_follow')
        self.client.teleport.assert_not_awaited()
        self.assert_released()
        self.assertTrue(await self.handle())
        point = self.events[-1][1]
        self.assertEqual((point.x, point.y, point.z), (1584.332, 6442.159, 212.394))
        self.assert_released()
        self.assertFalse(await self.handle())
        self.finish_dialogue()
        self.next_stage()
        self.assertFalse(await self.handle())
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_THRONE_FOLLOW_POSITION)
        self.mouse.click.assert_awaited_once_with(575, 710)
        self.client.body.write_orientation.assert_not_awaited()
        self.camera.update_orientation.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_already_follow_never_recasts(self):
        self.follow_stage()
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('tp', Quester.DARKMOOR_THRONE_FOLLOW_POSITION)])
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_old_scene_state_never_uses_old_coordinates(self):
        self.client._xuanshu_darkmoor_cantrip_stage = dict(snapshot=self.snapshot(),
            phase='tracker_dialogue', dialogue_seen=True, position_attempts=0)
        await self.handle()
        self.assertEqual(self.events[-1], ('target', (575, 710)))
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['snapshot'][0], self.quest_id)
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_THRONE_FOLLOW_POSITION)

    async def test_exact_region_objective_location_and_hitter_gates(self):
        for mode in ('region', 'objective', 'location', 'hitter', 'stopped'):
            with self.subTest(mode=mode):
                if mode == 'region':
                    self.client.zone_name.return_value = Quester.DARKMOOR_CANTRIP_ZONE
                elif mode == 'objective':
                    self.text = 'Cast Calescent Tracker 在断棍上 地点：Graveholm'
                elif mode == 'location':
                    self.text = 'Cast Calescent Tracker 找到灰魔 地点：Black Lagoon'
                elif mode == 'hitter':
                    self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
                else:
                    self.client.questing_status = False
                self.assertFalse(await self.handle())
                self.client.zone_name.return_value = Quester.DARKMOOR_THRONE_ZONE
                self.text = 'Cast Calescent Tracker 找到灰魔 地点：Graveholm'
                self.quester.clients = [self.client]
                self.client.questing_status = True
        self.assertEqual(self.events, [])

    async def test_configured_quester_supported(self):
        self.client.quest_party_status_session = object()
        self.follow_stage()
        await self.handle()
        self.client.teleport.assert_awaited_once()

    async def test_cast_far_from_live_target_leaves_normal_tp_in_charge(self):
        self.client.body.position.return_value = XYZ(1000, 2000, 200)
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_invalid_or_unreadable_cast_target_never_clicks(self):
        for point in (XYZ(0, 0, 0), XYZ(float('nan'), 400, 200)):
            self.client.quest_position.position.return_value = point
            self.assertFalse(await self.handle())
        self.client.quest_position.position.side_effect = RuntimeError('unreadable')
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_gray_card_never_clicks_scene(self):
        self.toolbar = True
        self.card.maybe_spell_grayed.return_value = True
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_no_target_banner_never_clicks_scene(self):
        self.quester._darkmoor_cantrip_texts.side_effect = None
        self.quester._darkmoor_cantrip_texts.return_value = []
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_resized_4_3_scales_marked_mound_point(self):
        self.camera_state.return_value = {'client_w': 768, 'client_h': 576}
        await self.handle()
        self.mouse.click.assert_awaited_once_with(383, 473)

    async def test_different_aspect_ratio_prevents_scene_click(self):
        self.camera_state.return_value = {'client_w': 1920, 'client_h': 1080}
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_position_changed_during_point_read_prevents_scene_click(self):
        async def read_camera(client):
            self.client.body.position.return_value = XYZ(2000, 3000, 200)
            return {'client_w': 1152, 'client_h': 864}
        self.camera_state.side_effect = read_camera
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_cast_consumed_without_progress_bounded_and_no_recast_on_restart(self):
        async def miss(*args):
            self.armed = False
        self.mouse.click.side_effect = miss
        await self.handle()
        self.mouse.click.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot())
        await self.handle()
        self.mouse.click.assert_awaited_once()
        self.assert_released()

    async def test_two_armed_misses_are_bounded_without_reselection(self):
        self.mouse.click.side_effect = None
        await self.handle()
        self.assertEqual(self.mouse.click.await_count, 2)
        self.mouse.click_window.assert_awaited_once()
        await self.handle()
        self.assertEqual(self.mouse.click.await_count, 2)
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_follow_unconfirmed_tp_never_retries_or_recasts(self):
        self.follow_stage()
        self.client.teleport.side_effect = self.teleport
        await self.handle()
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.mouse.click.assert_not_awaited()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.assert_released()

    async def test_direct_follow_progress_without_dialogue_resumes(self):
        self.follow_stage()
        async def tp(position):
            await self.teleport(position)
            self.next_stage()
        self.client.teleport.side_effect = tp
        self.assertTrue(await self.handle())
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'completed')
        self.assertFalse(await self.handle())
        self.client.teleport.assert_awaited_once()

    async def test_cancellation_releases_ownership_and_never_replays(self):
        self.mouse.click.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.mouse.click.assert_awaited_once()

    async def test_follow_priority_defers_tp(self):
        self.follow_stage()
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'post_combat_movement_active'):
            setattr(self.client, attr, True)
            self.assertTrue(await self.handle())
            setattr(self.client, attr, False)
        self.client.teleport.assert_not_awaited()

    async def test_cast_progress_before_follow_does_not_recast_through_dialogue(self):
        original = self.mouse.click.side_effect
        async def cast(*args):
            await original(*args)
            self.client.is_in_dialog.return_value = True
        self.mouse.click.side_effect = cast
        await self.handle()
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.finish_dialogue()
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.mouse.click.assert_awaited_once()

    async def test_follow_dialogue_settle_no_repeat_tp_or_recast(self):
        self.follow_stage()
        await self.handle()
        self.client.is_in_dialog.return_value = False
        self.assertFalse(await self.handle())
        self.finish_dialogue()
        self.assertTrue(await self.handle())
        self.now += 9
        await self.handle()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.client.teleport.assert_awaited_once()

    async def test_late_refill_prevents_card_selection(self):
        self.toolbar = True
        async def identity():
            self.client.refilling_potions = True
            return 'Cantrips_00000209'
        self.template.display_name.side_effect = identity
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_late_quest_identity_change_prevents_card_selection(self):
        self.toolbar = True
        async def identity():
            self.quest_id += 1
            return 'Cantrips_00000209'
        self.template.display_name.side_effect = identity
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_follow_cancel_releases_ownership_without_repeat_tp(self):
        self.follow_stage()
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.client.teleport.assert_awaited_once()

    async def test_cast_dialogue_without_stage_progress_never_recasts(self):
        async def cast(*args):
            self.armed = False
            self.client.is_in_dialog.return_value = True
        self.mouse.click.side_effect = cast
        await self.handle()
        self.assertFalse(await self.handle())
        self.finish_dialogue()
        self.assertTrue(await self.handle())
        self.mouse.click.assert_awaited_once()
        self.client.teleport.assert_not_awaited()
