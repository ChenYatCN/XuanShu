import asyncio
import ast
import inspect
import textwrap
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from wizwalker import XYZ, Keycode
from src.automation_ownership import get_client_automation_ownership
from src.paths import open_cantrips_path, advance_dialog_path
from src.questing import Quester


class DarkmoorCantripTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.goal = 1
        self.text = 'Cast Calescent Tracker 在断棍上 地点：Graveholm'
        self.toolbar, self.armed = False, False
        self.events = []
        self.mouse = MagicMock()
        self.mouse.click, self.mouse.click_window = AsyncMock(), AsyncMock()
        self.mouse.set_mouse_position = AsyncMock()
        self.camera = SimpleNamespace(update_orientation=AsyncMock())
        self.template = SimpleNamespace(display_name=AsyncMock(side_effect=lambda:
            'Cantrips_00000026' if '仪式物品' in self.text else 'Cantrips_00000209'))
        self.card = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_parents=AsyncMock(return_value=[]),
            maybe_graphical_spell=AsyncMock(return_value=SimpleNamespace(
                spell_template=AsyncMock(return_value=self.template))),
            maybe_spell_grayed=AsyncMock(return_value=False))
        self.client = SimpleNamespace(title='p1', questing_status=True,
            quest_recovery_owner=None, quest_party_hitters=[],
            zone_name=AsyncMock(return_value=Quester.DARKMOOR_CANTRIP_ZONE),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False), send_key=AsyncMock(), teleport=AsyncMock(),
            body=SimpleNamespace(position=AsyncMock(return_value=Quester.DARKMOOR_TRACKER_POSITION),
                                 write_orientation=AsyncMock()),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
            mouse_handler=self.mouse, root_window=SimpleNamespace(get_windows_with_type=AsyncMock(
                side_effect=lambda name: [self.card] if self.toolbar else [])))
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot())
        self.quester.read_popup = AsyncMock(return_value='与 NPC 对话')
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester._darkmoor_cantrip_texts = AsyncMock(side_effect=lambda c:
            ['单击目标来释放场外魔咒'] if self.armed else [])

        async def sleep(seconds):
            self.now += seconds
        async def open_toolbar(client, path):
            self.assertEqual(path, open_cantrips_path)
            self.assert_owned()
            self.events.append(('open', None))
            self.toolbar = True
        async def select(window):
            self.assert_owned()
            self.assertIs(window, self.card)
            self.events.append(('card', await self.template.display_name()))
            self.toolbar, self.armed = False, True
        async def click(x, y):
            self.assert_owned()
            self.assertTrue(self.armed)
            self.events.append(('target', (x, y)))
            self.armed = False
            self.client.is_in_dialog.return_value = True
            self.goal += 1
            self.text = ('拜访 NPC 地点：Graveholm' if '仪式物品' in self.text
                         else 'Follow Calescent Tracker 前往断枝窝。 地点：Graveholm')
        async def teleport(point):
            self.assert_owned()
            self.assertEqual(point, Quester.DARKMOOR_RITUAL_POSITION)
            self.events.append(('tp', (point.x, point.y, point.z)))
            self.client.body.position.return_value = point
            if self.quester._darkmoor_cantrip_stage(self.text) != 'ritual':
                self.client.is_in_dialog.return_value = True
                self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        self.mouse.click.side_effect, self.mouse.click_window.side_effect = click, select
        self.client.teleport.side_effect = teleport
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)))
        stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(side_effect=lambda c:
            not c.is_loading.return_value and not c.in_battle.return_value and not c.is_in_dialog.return_value)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: c.is_in_dialog.return_value if p == advance_dialog_path
            else p == open_cantrips_path)))
        stack.enter_context(patch('src.questing.click_window_by_path', new=AsyncMock(side_effect=open_toolbar)))
        self.camera_state = stack.enter_context(patch('src.questing.get_camera_state', new=AsyncMock(
            return_value={'client_w': 1152, 'client_h': 864})))
        self.log = stack.enter_context(patch('src.questing.logger'))

    def snapshot(self):
        return Quester.DARKMOOR_CANTRIP_QUEST_ID, self.goal, self.text

    def assert_owned(self):
        self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
        self.assertTrue(get_client_automation_ownership(self.client).locked)

    def dialogue_finished(self):
        self.client.is_in_dialog.return_value = False
        self.client.quest_dialogue_settle = None

    async def handle(self):
        return await self.quester._maybe_handle_darkmoor_cantrips(self.client)

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(self.client._xuanshu_darkmoor_cantrip_stage['active'])

    async def test_full_sequence_hands_both_dialogues_back_before_next_input(self):
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [('open', None), ('card', 'Cantrips_00000209'), ('target', (575, 710))])
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'tracker_dialogue')
        self.assert_released()
        await self.handle()  # Active dialogue must never trigger the second TP.
        self.client.teleport.assert_not_awaited()
        self.dialogue_finished()
        await self.handle()
        self.assertEqual(self.events[-1], ('tp', (361.769, -18787.988, 27.134)))
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'ritual_dialogue')
        self.assert_released()
        self.dialogue_finished()
        await self.handle()
        self.assertEqual(self.events[-3:], [('open', None), ('card', 'Cantrips_00000026'), ('target', (543, 186))])
        self.client.body.write_orientation.assert_awaited_once_with(Quester.DARKMOOR_RITUAL_ORIENTATION)
        self.camera.update_orientation.assert_awaited_once_with(Quester.DARKMOOR_RITUAL_ORIENTATION)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'completed')
        self.assert_released()

    async def test_wrong_zone_quest_goal_and_assigned_hitter_do_nothing(self):
        self.client.zone_name.return_value = 'Darkmoor/Other'
        self.assertFalse(await self.handle())
        self.client.zone_name.return_value = Quester.DARKMOOR_CANTRIP_ZONE
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: (99, 1, self.text)
        self.assertFalse(await self.handle())
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: self.snapshot()
        for text in ('Cantrip 其他物品 地点：Graveholm', 'Cantrip 仪式物品 地点：其他区域'):
            self.text = text
            self.assertFalse(await self.handle())
        self.text = 'Cantrip 仪式物品 地点：Graveholm'
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_normal_task_tp_reaches_tree_before_special_step(self):
        self.client.body.position.return_value = XYZ(0, 0, 0)
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_busy_states_defer_without_inputs(self):
        for attr, value in (('quest_recovery_owner', 'dungeon_quest'), ('refilling_potions', True),
                            ('quest_party_probe_pending', True), ('quest_party_battle_rescue_active', True),
                            ('quest_party_quest_worker_restart_requested', True),
                            ('post_combat_movement_active', True), ('mainline_chain_retry_active', True)):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                self.assertTrue(await self.handle())
                setattr(self.client, attr, None if attr == 'quest_recovery_owner' else False)
        self.client.questing_status = False
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_ritual_can_resume_when_user_already_finished_tracker(self):
        self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        await self.handle()
        self.assertEqual(self.events[0], ('tp', (361.769, -18787.988, 27.134)))
        self.assertNotIn(('card', 'Cantrips_00000209'), self.events)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'completed')

    async def test_armed_target_retry_only_clicks_twice_and_adjusts_a_once(self):
        self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        original = self.mouse.click.side_effect
        clicks = 0
        async def click(x, y):
            nonlocal clicks
            clicks += 1
            if clicks == 1:
                self.events.append(('miss', (x, y)))
            else:
                await original(x, y)
        self.mouse.click.side_effect = click
        await self.handle()
        self.assertEqual(clicks, 2)
        self.mouse.click_window.assert_awaited_once()
        self.client.send_key.assert_awaited_once_with(Keycode.A, .1)
        self.assertEqual(self.camera.update_orientation.await_count, 2)
        self.assert_released()

    async def test_consumed_spell_without_progress_never_reselected_on_restart(self):
        async def click(*args):
            self.armed = False
        self.mouse.click.side_effect = click
        await self.handle()
        self.mouse.click_window.assert_awaited_once()
        self.mouse.click.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await restarted._maybe_handle_darkmoor_cantrips(self.client)
        self.mouse.click.assert_awaited_once()
        self.assert_released()

    async def test_cancelled_click_releases_both_owners_and_does_not_replay(self):
        self.mouse.click.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        await self.handle()
        self.mouse.click.assert_awaited_once()

    async def test_no_targeting_banner_never_clicks_the_scene(self):
        self.quester._darkmoor_cantrip_texts.side_effect = None
        self.quester._darkmoor_cantrip_texts.return_value = []
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_different_aspect_ratio_blocks_scene_click(self):
        self.camera_state.return_value = {'client_w': 1920, 'client_h': 1080}
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_refill_or_task_change_during_card_read_prevents_selection(self):
        for mode in ('refill', 'task'):
            with self.subTest(mode=mode):
                self.toolbar = True
                async def code():
                    if mode == 'refill':
                        self.client.refilling_potions = True
                    else:
                        self.goal = 2
                    return 'Cantrips_00000209'
                self.template.display_name.side_effect = code
                await self.handle()
                self.mouse.click_window.assert_not_awaited()
                self.assert_released()
                self.client.refilling_potions = False
                self.goal = 1
                del self.client._xuanshu_darkmoor_cantrip_stage

    async def test_wrong_or_disabled_card_is_not_selected_by_slot(self):
        self.toolbar = True
        self.template.display_name.return_value = 'Other_00000000'
        self.template.display_name.side_effect = None
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_hover_fallback_requires_exact_spell_title_then_armed_mode(self):
        self.client.root_window.get_windows_with_type.side_effect = None
        self.client.root_window.get_windows_with_type.return_value = []
        self.quester._darkmoor_cantrip_texts.side_effect = lambda c: (
            ['单击目标来释放场外魔咒'] if self.armed else ['Calescent Tracker'] if self.toolbar else [])
        original = self.mouse.click.side_effect
        async def click(x, y):
            if self.toolbar:
                self.assertEqual((x, y), (470, 766))
                self.toolbar, self.armed = False, True
            else:
                await original(x, y)
        self.mouse.click.side_effect = click
        await self.handle()
        self.assertEqual(self.mouse.click.await_count, 2)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'tracker_dialogue')
        self.assert_released()

    async def test_unseen_first_dialogue_never_teleports_to_ritual(self):
        self.client._xuanshu_darkmoor_cantrip_stage = {
            'snapshot': self.snapshot(), 'phase': 'tracker_dialogue', 'dialogue_seen': False}
        await self.handle()
        self.client.teleport.assert_not_awaited()

    async def test_ritual_dialogue_waits_for_existing_settle_before_cast(self):
        self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        self.client._xuanshu_darkmoor_cantrip_stage = {
            'snapshot': self.snapshot(), 'phase': 'ritual_dialogue', 'dialogue_seen': True}
        self.client.quest_dialogue_settle = {'snapshot': None, 'since': None}
        self.assertFalse(await self.handle())
        self.assertEqual(self.events, [])

    async def test_gray_spell_is_not_selected_even_with_matching_hover_title(self):
        self.toolbar = True
        self.card.maybe_spell_grayed.return_value = True
        self.quester._darkmoor_cantrip_texts.side_effect = None
        self.quester._darkmoor_cantrip_texts.return_value = ['Calescent Tracker']
        await self.handle()
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_second_target_miss_is_bounded_without_reselecting_spell(self):
        self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        self.mouse.click.side_effect = None  # Both clicks leave target mode armed.
        await self.handle()
        self.assertEqual(self.mouse.click.await_count, 2)
        self.mouse.click_window.assert_awaited_once()
        self.client.send_key.assert_awaited_once_with(Keycode.A, .1)
        self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'failed')
        await self.handle()
        self.assertEqual(self.mouse.click.await_count, 2)
        self.assert_released()

    async def test_loading_during_last_point_read_prevents_scene_click(self):
        async def camera(client):
            client.is_loading.return_value = True
            return {'client_w': 1152, 'client_h': 864}
        self.camera_state.side_effect = camera
        await self.handle()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_same_aspect_window_scales_user_marked_coordinates(self):
        self.camera_state.return_value = {'client_w': 920, 'client_h': 690}
        self.assertEqual(await self.quester._darkmoor_cantrip_point(self.client, 543, 186), (434, 149))

    async def test_active_dungeon_recovery_defers_without_ui_inputs(self):
        self.client.quest_dungeon_recovery = {'active': True}
        await self.handle()
        self.assertEqual(self.events, [])

    async def test_unconfirmed_ritual_arrival_never_selects_a_spell(self):
        self.text, self.goal = 'Cantrip 仪式物品 地点：Graveholm', 3
        self.client.teleport.side_effect = None
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_RITUAL_POSITION)
        self.mouse.click_window.assert_not_awaited()
        self.mouse.click.assert_not_awaited()
        self.assert_released()

    async def test_forced_story_dialogue_without_advance_button_is_handed_back(self):
        original = self.mouse.click.side_effect
        async def click(*args):
            await original(*args)
            self.client.is_in_dialog.return_value = False
        self.mouse.click.side_effect = click
        with patch('src.questing.read_dialogue_text', new=AsyncMock(side_effect=lambda c:
                '线索剧情对话' if self.goal > 1 else '')):
            await self.handle()
            self.assertEqual(self.client._xuanshu_darkmoor_cantrip_stage['phase'], 'tracker_dialogue')
            self.assertTrue(self.client._xuanshu_darkmoor_cantrip_stage['dialogue_seen'])
            self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.assert_released()

    def test_both_workers_and_direct_tp_route_special_before_mainline_finder(self):
        for worker in (Quester.auto_quest_solo, Quester.auto_quest_leader, Quester.teleport_to_quest_target):
            tree = ast.parse(textwrap.dedent(inspect.getsource(worker)))
            calls = {name: sorted(node.lineno for node in ast.walk(tree)
                                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                 and node.func.attr == name)
                     for name in ('_maybe_handle_darkmoor_cantrips', '_maybe_recover_mainline', 'move_until_quest_interaction')}
            self.assertTrue(calls['_maybe_handle_darkmoor_cantrips'])
            for other in ('_maybe_recover_mainline', 'move_until_quest_interaction'):
                if calls[other]:
                    self.assertLess(calls['_maybe_handle_darkmoor_cantrips'][0], calls[other][0])
