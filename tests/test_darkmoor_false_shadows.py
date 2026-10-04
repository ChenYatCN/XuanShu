import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from wizwalker import XYZ, Keycode
from src.automation_ownership import get_client_automation_ownership
from src.paths import advance_dialog_path, npc_range_path, open_cantrips_path
from src.questing import Quester


class FalseShadowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0
        self.goal = 1
        self.quest_id = Quester.DARKMOOR_SHADOWS_QUEST_ID
        self.text = 'Cast Magic Touch to Dispel False Shadows 地点：Black Lagoon'
        self.toolbar = self.armed = False
        self.events = []
        self.mouse = MagicMock()
        self.mouse.click = AsyncMock(side_effect=self.target_click)
        self.mouse.click_window = AsyncMock(side_effect=self.select_card)
        self.mouse.set_mouse_position = AsyncMock()
        self.camera = SimpleNamespace(update_orientation=AsyncMock())
        self.template = SimpleNamespace(display_name=AsyncMock(return_value='Cantrips_00000026'))
        self.card = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_parents=AsyncMock(return_value=[]), maybe_spell_grayed=AsyncMock(return_value=False),
            maybe_graphical_spell=AsyncMock(return_value=SimpleNamespace(
                spell_template=AsyncMock(return_value=self.template))))
        self.client = SimpleNamespace(title='p1', questing_status=True, quest_recovery_owner=None,
            quest_party_hitters=[], in_solo_zone=True,
            zone_name=AsyncMock(return_value=Quester.DARKMOOR_SHADOWS_ZONE),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False), teleport=AsyncMock(side_effect=self.teleport),
            send_key=AsyncMock(side_effect=self.interact), mouse_handler=self.mouse,
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0)), write_orientation=AsyncMock()),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
            root_window=SimpleNamespace(get_windows_with_type=AsyncMock(
                side_effect=lambda name: [self.card] if self.toolbar else [])))
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot())
        self.quester._darkmoor_cantrip_texts = AsyncMock(side_effect=lambda c:
            ['单击目标来释放场外魔咒'] if self.armed else [])
        self.quester.read_popup = AsyncMock(return_value='按下 &Icons_XKey& 交谈')
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock(side_effect=self.sleep)))
        stack.enter_context(patch('src.questing.read_dialogue_text', AsyncMock(return_value='')))
        stack.enter_context(patch('src.questing.is_free_leader_questing', AsyncMock(side_effect=lambda c:
            not c.is_loading.return_value and not c.in_battle.return_value and not c.is_in_dialog.return_value)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p:
            c.is_in_dialog.return_value if p == advance_dialog_path else p in (open_cantrips_path, npc_range_path))))
        stack.enter_context(patch('src.questing.click_window_by_path', AsyncMock(side_effect=self.open_toolbar)))
        self.camera_state = stack.enter_context(patch('src.questing.get_camera_state', AsyncMock(
            return_value={'client_w': 1152, 'client_h': 864})))
        stack.enter_context(patch('src.questing.logger'))

    def snapshot(self):
        return self.quest_id, self.goal, self.text

    def assert_owned(self):
        self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
        self.assertTrue(get_client_automation_ownership(self.client).locked)

    async def sleep(self, seconds):
        self.now += seconds

    async def teleport(self, position):
        self.assert_owned()
        self.events.append(('tp', position))
        self.client.body.position.return_value = position

    async def open_toolbar(self, client, path):
        self.assert_owned()
        self.assertEqual(path, open_cantrips_path)
        self.toolbar = True
        self.events.append(('wand', None))

    async def select_card(self, card):
        self.assert_owned()
        self.assertIs(card, self.card)
        self.toolbar, self.armed = False, True
        self.events.append(('card', 'Magic Touch'))

    async def target_click(self, x, y):
        self.assert_owned()
        self.assertTrue(self.armed)
        self.events.append(('target', (x, y)))
        self.armed = False
        self.client.is_in_dialog.return_value = True
        self.goal, self.text = 2, 'Talk to NPC in Black Lagoon'

    async def interact(self, key, duration):
        self.assert_owned()
        self.assertEqual(key, Keycode.X)
        self.events.append(('talk', None))
        self.client.is_in_dialog.return_value = True
        self.goal, self.text = 3, 'Go To Black Lagoon in Black Lagoon'

    async def handle(self):
        return await self.quester._maybe_handle_darkmoor_cantrips(self.client)

    def finish_dialogue(self):
        self.client.is_in_dialog.return_value = False
        self.client.quest_dialogue_settle = None

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(self.client._xuanshu_darkmoor_cantrip_stage['active'])

    async def test_full_sequence_cast_dialogue_reset_return_and_second_dialogue(self):
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('tp', Quester.DARKMOOR_SHADOWS_POSITION),
                                      ('wand', None), ('card', 'Magic Touch'), ('target', (620, 380))])
        self.client.body.write_orientation.assert_awaited_once_with(Quester.DARKMOOR_SHADOWS_ORIENTATION)
        self.camera.update_orientation.assert_awaited_once_with(Quester.DARKMOOR_SHADOWS_ORIENTATION)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'shadows_dialogue')
        self.assert_released()
        self.assertFalse(await self.handle())
        self.assertEqual(self.client.teleport.await_count, 1)
        self.finish_dialogue()
        self.assertTrue(await self.handle())
        self.assertEqual(self.events[-3:], [('tp', Quester.DARKMOOR_SHADOWS_RESET_POSITION),
                                          ('tp', Quester.DARKMOOR_SHADOWS_POSITION), ('talk', None)])
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'shadows_return_dialogue')
        self.assert_released()
        self.assertFalse(await self.handle())
        self.finish_dialogue()
        self.assertFalse(await self.handle())
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'completed')
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 3)
        self.mouse.click.assert_awaited_once_with(620, 380)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_only_verified_quest_region_and_stage_trigger(self):
        for mode in ('quest', 'region', 'objective', 'location'):
            with self.subTest(mode=mode):
                quest_id, text, zone = self.quest_id, self.text, self.client.zone_name.return_value
                if mode == 'quest':
                    self.quest_id = 999
                elif mode == 'region':
                    self.client.zone_name.return_value = 'Other/Room'
                elif mode == 'objective':
                    self.text = 'Cast Magic Touch on another object in Black Lagoon'
                else:
                    self.text = 'Cast Magic Touch to Dispel False Shadows in Graveholm'
                self.assertFalse(await self.handle())
                self.quest_id, self.text, self.client.zone_name.return_value = quest_id, text, zone
        self.assertEqual(self.events, [])

    async def test_configured_quester_is_supported_but_assigned_hitter_is_not(self):
        self.client.quest_party_status_session = object()
        self.assertTrue(await self.handle())
        self.mouse.click.assert_awaited_once()
        self.finish_dialogue()
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.handle())
        self.assertEqual(self.client.teleport.await_count, 1)

    async def test_dialogue_settle_defers_reset_and_return(self):
        await self.handle()
        self.client.is_in_dialog.return_value = False
        self.client.quest_dialogue_settle = {'snapshot': None, 'since': None}
        self.assertFalse(await self.handle())
        self.assertEqual(self.client.teleport.await_count, 1)

    async def test_unseen_cast_dialogue_never_starts_reset(self):
        self.client._xuanshu_darkmoor_cantrip_stage = dict(snapshot=self.snapshot(),
            phase='shadows_dialogue', dialogue_seen=False)
        self.assertTrue(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_old_graveholm_state_never_uses_old_ritual_coordinates(self):
        self.client._xuanshu_darkmoor_cantrip_stage = dict(
            snapshot=(Quester.DARKMOOR_CANTRIP_QUEST_ID, 2, 'Follow Calescent Tracker in Graveholm'),
            phase='tracker_dialogue', dialogue_seen=True)
        self.assertTrue(await self.handle())
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_SHADOWS_POSITION)
        self.mouse.click.assert_awaited_once_with(620, 380)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'shadows_dialogue')
        self.assert_released()

    async def test_failed_initial_arrival_stops_without_cast_or_repeated_tp(self):
        self.client.teleport.side_effect = None
        await self.handle()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.mouse.click.assert_not_awaited()
        self.mouse.click_window.assert_not_awaited()
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_spell_consumed_without_progress_is_not_reselected(self):
        async def consume(*point):
            self.armed = False
        self.mouse.click.side_effect = consume
        await self.handle()
        await self.handle()
        self.mouse.click.assert_awaited_once()
        self.mouse.click_window.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.assert_released()

    async def test_no_targeting_banner_never_clicks_marked_object(self):
        self.quester._darkmoor_cantrip_texts.side_effect = None
        self.quester._darkmoor_cantrip_texts.return_value = []
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_aspect_ratio_change_prevents_scene_click(self):
        self.camera_state.return_value = {'client_w': 1920, 'client_h': 1080}
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_marked_point_scales_for_four_three_client(self):
        self.camera_state.return_value = {'client_w': 921, 'client_h': 691}
        await self.handle()
        self.mouse.click.assert_awaited_once_with(496, 304)

    async def test_priority_during_card_lookup_prevents_selection(self):
        async def name():
            self.client.refilling_potions = True
            return 'Cantrips_00000026'
        self.template.display_name.side_effect = name
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_task_change_during_initial_tp_prevents_cast(self):
        async def changed(point):
            await self.teleport(point)
            self.goal = 2
        self.client.teleport.side_effect = changed
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_failed_reset_arrival_never_returns_or_interacts(self):
        await self.handle()
        self.finish_dialogue()
        self.client.teleport.side_effect = None
        await self.handle()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        self.client.teleport.assert_awaited_with(Quester.DARKMOOR_SHADOWS_RESET_POSITION)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.send_key.assert_not_awaited()
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assert_released()

    async def test_area_change_during_reset_prevents_return_or_interaction(self):
        await self.handle()
        self.finish_dialogue()
        async def leave(point):
            await self.teleport(point)
            self.client.zone_name.return_value = 'Darkmoor/DM_Z04_BlackLagoon'
        self.client.teleport.side_effect = leave
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_missing_talk_prompt_never_spams_x_or_repeats_tp(self):
        await self.handle()
        self.finish_dialogue()
        self.quester.read_popup.return_value = 'Press X to Enter'
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 3)
        self.assert_released()

    async def test_cancellation_releases_owner_and_does_not_restart_same_stage(self):
        self.client.teleport.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.client.teleport.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
