"""Current-goal pet mode, finite UI switching and controlled-body continuation."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.questing import Quester
from src import teleport_math as movement
from src.interaction_prompts import quest_has_action
from src.paths import pet_system_button_path, play_as_pet_button_path
from src.collecting import CollectSearch, SearchState, Candidate
from tests import test_pet_task_tp as fixtures
from tests import test_interaction_a_search as interactions
from src.automation_ownership import get_client_automation_ownership


class PetModeTests(unittest.IsolatedAsyncioTestCase):
    setUp_base = fixtures.PetTaskTPTests.setUp_base
    solve = fixtures.PetTaskTPTests.solve
    plan = fixtures.PetTaskTPTests.plan
    goto = fixtures.PetTaskTPTests.goto
    teleport = fixtures.PetTaskTPTests.teleport
    sleep = fixtures.PetTaskTPTests.sleep

    def setUp(self):
        fixtures.PetTaskTPTests.setUp(self)
        self.pet_mode = False
        self.client.root_window = object()
        self.task_text = '使用宠物模式寻找大黄蜂'
        self.expanded = self.disabled = self.fail_open = self.fail_play = False
        self.child_flag_visible = False
        self.menu_events = []
        self.on_click = lambda path: None
        self.reader.read_quest_txt = AsyncMock(side_effect=lambda _: self.task_text)
        self.reader._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: (
            self.quest_id, self.goal_id, self.task_text))
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            is_control_grayed=AsyncMock(side_effect=lambda: self.disabled))
        self.menu = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            is_control_grayed=AsyncMock(return_value=False))
        async def window(root, path):
            return self.button if path == play_as_pet_button_path else self.menu
        async def visible(client, path):
            if path == play_as_pet_button_path:
                return self.expanded or self.child_flag_visible
            if path == play_as_pet_button_path[:-1]:
                return self.expanded
            return path == pet_system_button_path
        async def click(client, path, *args):
            self.menu_events.append((client.title, tuple(path)))
            if path == pet_system_button_path and not self.fail_open:
                self.expanded = True
            elif path == play_as_pet_button_path and not self.fail_play:
                self.pet_mode = True
            self.on_click(path)
        self.stack.enter_context(patch('src.questing.get_window_from_path', side_effect=window))
        self.stack.enter_context(patch('src.questing.is_visible_by_path', side_effect=visible))
        self.stack.enter_context(patch('src.questing.click_window_by_path', side_effect=click))

    async def ensure(self):
        return await self.reader._ensure_quest_pet_mode(self.client)

    async def test_current_action_parser_supports_supplied_goal_and_verified_english(self):
        for goal in ('使用宠物模式寻找大黄蜂', '<center>使用宠物模式以寻找目标</center>',
                     'Use Pet Mode to Find Bumblebee', '<string;WizardQuestGoals_00000667>'):
            with self.subTest(goal=goal):
                self.assertTrue(quest_has_action(goal, 'pet_mode'))
        for goal in ('寻找大黄蜂', '训练宠物', 'Talk to the Pet Trainer', 'Play with your Pet'):
            self.assertFalse(quest_has_action(goal, 'pet_mode'))

    async def test_closed_menu_opens_before_play_for_actual_client(self):
        self.client.title = 'p4'
        self.assertTrue(await self.ensure())
        self.assertEqual(self.menu_events, [('p4', tuple(pet_system_button_path)),
                                            ('p4', tuple(play_as_pet_button_path))])
        self.assertTrue(self.pet_mode)

    async def test_hidden_layout_does_not_count_visible_child_as_expanded(self):
        self.child_flag_visible = True
        self.assertTrue(await self.ensure())
        self.assertEqual(self.menu_events[0][1], tuple(pet_system_button_path))

    async def test_already_in_verified_pet_mode_does_not_click_or_toggle_out(self):
        self.pet_mode = True
        self.assertTrue(await self.ensure())
        self.assertTrue(await self.ensure())
        self.assertFalse(self.menu_events)

    async def test_open_menu_clicks_only_play_button(self):
        self.expanded = True
        self.assertTrue(await self.ensure())
        self.assertEqual([path for _, path in self.menu_events], [tuple(play_as_pet_button_path)])

    async def test_click_is_not_success_and_failed_attempt_does_not_repeat_after_restart(self):
        self.fail_play = True
        self.assertFalse(await self.ensure())
        for _ in range(5):
            self.reader = Quester(self.client, [self.client], None)
            self.reader.read_quest_txt = AsyncMock(side_effect=lambda _: self.task_text)
            self.reader._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: (
                self.quest_id, self.goal_id, self.task_text))
            self.assertFalse(await self.ensure())
        self.assertEqual(len(self.menu_events), 2)
        self.assertFalse(self.pet_mode)

    async def test_disabled_button_is_not_clicked_and_expander_is_not_toggled_repeatedly(self):
        self.disabled = True
        for _ in range(4):
            self.assertFalse(await self.ensure())
        self.assertEqual([path for _, path in self.menu_events], [tuple(pet_system_button_path)])

    async def test_failed_menu_open_has_finite_exit(self):
        self.fail_open = True
        self.assertFalse(await self.ensure())
        self.assertFalse(await self.ensure())
        self.assertEqual(len(self.menu_events), 1)

    async def test_subunit_target_jitter_does_not_renew_failed_switch(self):
        self.fail_play = True
        self.assertFalse(await self.ensure())
        self.quest_target = XYZ(self.target.x + .2, self.target.y, self.target.z)
        self.assertFalse(await self.ensure())
        self.assertEqual(len(self.menu_events), 2)

    async def test_unreadable_goal_frame_does_not_renew_failed_switch(self):
        self.fail_play = True
        self.assertFalse(await self.ensure())
        saved = self.client._quest_pet_mode_transition
        original = self.task_text
        self.task_text = ''
        self.assertFalse(await self.ensure())
        self.assertIs(self.client._quest_pet_mode_transition, saved)
        self.task_text = original
        self.assertFalse(await self.ensure())
        self.assertEqual(len(self.menu_events), 2)

    async def test_ordinary_goal_ignores_historical_completion_popup(self):
        self.task_text = '与校长交谈'
        self.reader.read_popup = AsyncMock(return_value='任务完成：使用宠物模式寻找大黄蜂')
        self.assertTrue(await self.ensure())
        self.assertFalse(self.menu_events)
        self.reader.read_popup.assert_not_awaited()

    async def test_old_movement_is_cancelled_and_released_before_menu_input(self):
        started = asyncio.Event()
        async def old_move():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.menu_events.append(('released', ()))
        old = asyncio.create_task(old_move())
        self.client._quest_active_movement = old
        await started.wait()
        try:
            self.assertTrue(await self.ensure())
            self.assertEqual(self.menu_events[0], ('released', ()))
            self.assertTrue(old.done())
        finally:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)

    async def test_target_is_reread_after_mode_change_and_pet_continues_real_task_tp(self):
        old_player = tuple(self.player_position)
        updated = XYZ(self.target.x + 300, self.target.y, self.target.z)
        def clicked(path):
            if path == play_as_pet_button_path:
                self.quest_target = updated
        self.on_click = clicked
        self.assertTrue(await self.ensure())
        self.assertGreaterEqual(self.client.quest_position.position.await_count, 2)
        body, control = await movement._movement_body(self.client)
        self.assertIs(body, self.pet_body)
        self.assertEqual(control[0], self.pet_address)
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.quest_target), 'ready')
        self.assertEqual(tuple(self.position), tuple(updated))
        self.assertEqual(tuple(self.player_position), old_player)

    async def test_task_change_while_opening_prevents_old_play_click(self):
        self.on_click = lambda path: setattr(self, 'goal_id', self.goal_id + 1)
        self.assertFalse(await self.ensure())
        self.assertEqual([path for _, path in self.menu_events], [tuple(pet_system_button_path)])

    async def test_loading_battle_stop_and_training_block_before_menu_input(self):
        for cause in ('loading', 'battle', 'stop', 'training'):
            with self.subTest(cause=cause):
                if cause == 'stop': self.client.questing_status = False
                elif cause == 'training': self.client.feeding_pet_status = True
                else: setattr(self, cause, True)
                self.assertFalse(await self.ensure())
                self.assertFalse(self.menu_events)
                self.client.questing_status = True
                self.client.feeding_pet_status = False
                self.loading = self.battle = False

    async def test_later_goal_is_reassessed_without_adding_exit_strategy(self):
        self.fail_play = True
        self.assertFalse(await self.ensure())
        self.goal_id += 1
        self.task_text = '使用宠物模式寻找另一目标'
        self.fail_play = False
        self.assertTrue(await self.ensure())
        self.task_text = '与校长交谈'
        events = len(self.menu_events)
        self.assertTrue(await self.ensure())
        self.assertTrue(self.pet_mode)
        self.assertEqual(len(self.menu_events), events)

    async def test_cancellation_while_opening_releases_ownership_without_play(self):
        self.on_click = lambda path: asyncio.current_task().cancel()
        switch = asyncio.create_task(self.ensure())
        with self.assertRaises(asyncio.CancelledError):
            await switch
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertEqual([path for _, path in self.menu_events], [tuple(pet_system_button_path)])
        self.assertTrue(self.client.questing_status)

    async def test_unverified_pet_camera_is_not_click_success(self):
        self.client.game_client.selected_camera_controller = AsyncMock(return_value=None)
        self.assertFalse(await self.ensure())
        self.client.game_client.selected_camera_controller = AsyncMock(return_value=self.camera)
        self.assertTrue(await self.ensure())
        self.assertEqual(len(self.menu_events), 2)

    async def test_task_and_zone_change_during_switch_end_old_attempt(self):
        for cause in ('goal', 'zone'):
            with self.subTest(cause=cause):
                self.pet_mode = False
                self.expanded = False
                self.menu_events.clear()
                self.client._quest_pet_mode_transition = None
                def clicked(path):
                    if path == play_as_pet_button_path:
                        if cause == 'goal': self.goal_id += 1
                        else: self.zone = 'Other/Zone'
                self.on_click = clicked
                self.assertFalse(await self.ensure())
                self.assertEqual(len(self.menu_events), 2)


class PetCollectTests(unittest.IsolatedAsyncioTestCase):
    setUp = PetModeTests.setUp
    setUp_base = PetModeTests.setUp_base
    solve = PetModeTests.solve
    plan = PetModeTests.plan
    goto = PetModeTests.goto
    teleport = PetModeTests.teleport
    sleep = PetModeTests.sleep

    async def search(self):
        self.pet_mode = True
        search = CollectSearch(self.reader, self.client)
        search.zone, search.quest_id = self.zone, self.quest_id
        _, search.control = await movement._movement_body(self.client)
        search.goal = SimpleNamespace(target='Honey', key=('Honey', 'Area'), total=1, current=0)
        search.state = SearchState(('fixture',))
        search.names = SimpleNamespace(score=lambda *_: 100)
        return search

    async def test_collect_prompt_range_uses_pet_position_not_stationary_player(self):
        search = await self.search()
        candidate = Candidate(None, self.position, '', 'Honey', 'Honey', 1, 100)
        with patch('src.collecting.is_visible_by_path', AsyncMock(return_value=True)), \
             patch('src.collecting.get_popup_title', AsyncMock(return_value='Honey')):
            self.assertTrue(await search.prompt_ready(candidate))
        self.assertGreater(movement.calc_Distance(self.player_position, candidate.xyz), 750)

    async def test_collect_mode_change_stops_existing_search(self):
        search = await self.search()
        self.pet_mode = False
        self.assertFalse(await search.active())

    async def test_collect_stale_player_hook_is_not_used_for_pet_input(self):
        search = await self.search()
        self.client.client_object.actor_body = AsyncMock(return_value=self.client.body)
        self.assertFalse(await search.active())

    async def test_region_search_restores_pet_origin_not_player_origin(self):
        search = await self.search()
        origin = tuple(self.position)
        self.client.get_base_entity_list = AsyncMock(return_value=[])
        self.reader.get_zone_chunks = AsyncMock(return_value=[XYZ(2000, 3000, 0)])
        search.snapshot = AsyncMock(return_value=search.goal)
        search.scan = AsyncMock(return_value=False)
        search.loaded_entities = AsyncMock(return_value=[])
        with patch('src.collecting.installed_catalog', return_value=None):
            self.assertFalse(await search.run())
        self.assertEqual(tuple(self.position), origin)
        self.assertNotEqual(tuple(self.position), tuple(self.player_position))


class AutoPetEntryTests(unittest.IsolatedAsyncioTestCase):
    setUp = interactions.InteractionASearchTests.setUp
    sleep = interactions.InteractionASearchTests.sleep
    send_key = interactions.InteractionASearchTests.send_key

    async def test_solo_prepares_actual_client_before_old_movement(self):
        self.quester._ensure_quest_pet_mode = AsyncMock(return_value=False)
        self.quester.teleport_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester._ensure_quest_pet_mode.assert_awaited_once_with(self.client)
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_legacy_prepares_actual_quester_before_old_movement(self):
        self.quester._ensure_quest_pet_mode = AsyncMock(return_value=False)
        self.quester.teleport_to_quest_target = AsyncMock()
        await self.quester.handle_normal_quests([], False)
        self.quester._ensure_quest_pet_mode.assert_awaited_once_with(self.quester.current_leader_client)
        self.quester.teleport_to_quest_target.assert_not_awaited()


class PetInteractionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    sleep = interactions.InteractionASearchTests.sleep
    send_key = interactions.InteractionASearchTests.send_key

    def setUp(self):
        interactions.InteractionASearchTests.setUp(self)
        self.pet_mode = True
        self.pet_body = self.client.body
        self.pet_body.read_base_address = AsyncMock(return_value=200)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        self.pet_body.parent_client_object = AsyncMock(return_value=parent)
        self.client.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(-10000, -10000, 0)),
            read_base_address=AsyncMock(return_value=100), yaw=AsyncMock(return_value=0))
        attached = SimpleNamespace(object_name=AsyncMock(side_effect=lambda:
            'PetObject' if self.pet_mode else 'Player Object'), actor_body=AsyncMock(return_value=self.pet_body))
        camera = SimpleNamespace(read_base_address=AsyncMock(return_value=300),
            attached_client_object=AsyncMock(return_value=attached))
        self.client.game_client = SimpleNamespace(elastic_camera_controller=AsyncMock(return_value=camera),
            selected_camera_controller=AsyncMock(return_value=camera))

    async def search(self):
        return await self.quester._recover_quest_x_direction(self.client,
            await self.quester._dungeon_quest_snapshot(self.client), self.target,
            await self.client.zone_name(), [self.client])

    async def test_pet_interaction_turn_reads_pet_position_and_yaw(self):
        self.on_a = lambda: setattr(self, 'goal', self.goal + 1)
        self.assertTrue(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 1)
        self.client.body.position.assert_not_awaited()
        self.client.body.yaw.assert_not_awaited()
        self.assertGreater(self.pet_body.yaw.await_count, 0)
        self.assertFalse(self.held)

    async def test_mode_change_during_turn_stops_without_reading_old_body_yaw(self):
        self.on_a = lambda: setattr(self, 'pet_mode', False)
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 1)
        self.client.body.yaw.assert_not_awaited()
        self.assertFalse(self.held)

if __name__ == '__main__':
    unittest.main()
