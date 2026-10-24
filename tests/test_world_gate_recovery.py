"""The live enter button decides whether automatic selection needs a fallback."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Rectangle
from src import utils, teleport_math as movement
from src.questing import Quester

REAL_SLEEP, REAL_TIMEOUT = asyncio.sleep, asyncio.timeout


class Window:
    def __init__(self, name, fixture, children=(), text=''):
        self.label, self.fixture, self.nodes, self.text = name, fixture, list(children), text
        self.up = None
        self.visible = True
        for child in self.nodes:
            child.up = self

    async def name(self):
        return self.label

    async def is_visible(self):
        return self.visible and self.fixture.menu and (self.label != 'selectedWorldCheckMark' or self.fixture.mark_visible)

    async def children(self):
        return self.nodes

    async def parent(self):
        return self.up

    async def get_child_by_name(self, name):
        return next(child for child in self.nodes if child.label == name)

    async def get_windows_with_name(self, name):
        result = [self] if self.label == name else []
        for child in self.nodes:
            result.extend(await child.get_windows_with_name(name))
        return result

    async def maybe_text(self):
        return self.text

    async def read_wide_string_from_offset(self, _offset):
        return self.text

    async def is_control_grayed(self):
        return self.label == 'teleportButton' and self.fixture.disabled

    async def window_rectangle(self):
        row = 0 if self.label == 'wbtnWizardCity' or self.fixture.selected == 'WizardCity' else 1
        if self.label.startswith('wbtn'):
            row = 0 if self.label == 'wbtnWizardCity' else 1
            return Rectangle(0, row * 88, 64, row * 88 + 64)
        return Rectangle(32, row * 88 + 31, 65, row * 88 + 64)


class WorldGateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.zone = 'Arcanum/AR_Z01_Hub'
        self.quest, self.goal, self.target = 10, 20, XYZ(100, 200, 3)
        self.selected, self.destination = 'Krokotopia', 'Krokotopia/KT_Hub'
        self.menu = self.mark_visible = True
        self.disabled = self.miss_selection = self.loading = self.battle = False
        self.travel = 'success'
        self.events = []
        self.on_sleep = lambda _delay: None
        self.mark = Window('selectedWorldCheckMark', self)
        self.panel = Window('optionWindow', self, [
            Window('pageCount', self, text='<center>1/1</center>'),
            Window('opt0', self, text='Wizard City'), Window('opt1', self, text='Krokotopia'),
            Window('wbtnWizardCity', self), Window('wbtnKrokotopia', self), self.mark,
            Window('leftButton', self), Window('rightButton', self)])
        self.button = Window('teleportButton', self)
        self.control = Window('ControlSprite', self, [self.panel, self.button])
        self.root = Window('root', self, [Window('WorldView', self, [Window('', self,
            [Window('messageBoxBG', self, [self.control])])])])
        mouse = AsyncMock()
        mouse.click_window.side_effect = self.click
        mouse.click_window_with_name.side_effect = self.click_name
        self.client = SimpleNamespace(title='p1', process_id=1, root_window=self.root,
            questing_status=True, body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            quest_id=AsyncMock(side_effect=lambda: self.quest), goal_id=AsyncMock(side_effect=lambda: self.goal),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(side_effect=lambda: self.loading),
            in_battle=AsyncMock(side_effect=lambda: self.battle),
            is_in_npc_range=AsyncMock(return_value=False), send_key=AsyncMock(), mouse_handler=mouse,
            wait_for_zone_change=AsyncMock(), quest_manager=AsyncMock(return_value=SimpleNamespace(
                quest_data=AsyncMock(side_effect=lambda: {self.quest: SimpleNamespace(
                    goal_data=AsyncMock(side_effect=lambda: {self.goal: SimpleNamespace(
                        goal_destination_zone=AsyncMock(side_effect=lambda: self.destination))}))}))))
        async def sleep(delay):
            self.on_sleep(delay)
            await REAL_SLEEP(0)
        self.stack.enter_context(patch('src.utils.asyncio.sleep', sleep))
        self.stack.enter_context(patch('src.utils.asyncio.timeout', lambda _: REAL_TIMEOUT(.06)))
        self.stack.enter_context(patch('src.utils.read_control_text', lambda window: window.maybe_text()))
        self.stack.enter_context(patch('src.teleport_math._movement_body', AsyncMock(return_value=(self.client.body, None))))
        self.stack.enter_context(patch('src.teleport_math.is_free', AsyncMock(
            side_effect=lambda _: not (self.loading or self.battle))))
        self.stack.enter_context(patch('src.teleport_math.is_visible_by_path', AsyncMock(return_value=False)))

    async def click_name(self, name):
        self.events.append(name)
        if name.startswith('wbtn') and not self.miss_selection:
            self.selected = name[4:]
            self.mark_visible = True
            self.disabled = False
        if name == 'teleportButton':
            await self.click(self.button)

    async def click(self, window):
        if window.label != 'teleportButton':
            return await self.click_name(window.label)
        self.events.append(('enter', self.selected))
        if self.travel == 'success' or self.travel == 'city-only' and self.selected == 'WizardCity':
            self.zone = self.selected + '/Hub'
            self.menu = False
        elif self.travel == 'wrong-zone':
            self.zone = 'Other/Zone'
            self.menu = False

    async def test_available_wrong_auto_selection_enters_without_city_reselection(self):
        self.selected = 'WizardCity'
        with patch('src.utils.go_to_new_world', wraps=utils.go_to_new_world) as select:
            self.assertFalse(await utils.spiral_door_with_quest(self.client))
        select.assert_not_awaited()
        self.assertEqual(self.events, [('enter', 'WizardCity')])
        self.assertTrue(self.zone.startswith('WizardCity/'))

    async def test_correct_selection_requires_actual_destination_confirmation(self):
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertTrue(self.zone.startswith('Krokotopia/'))

    async def test_available_missing_selection_mark_enters_current_world(self):
        self.mark_visible = False
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_disabled_enter_does_not_click_until_city_selection_enables_it(self):
        self.disabled = True
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual([e for e in self.events if isinstance(e, tuple)], [('enter', 'WizardCity')])

    async def test_available_click_without_zone_change_does_not_fallback(self):
        self.travel = 'city-only'
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_failed_city_enter_is_blocked_across_worker_restart(self):
        self.disabled = True
        self.travel = 'timeout'
        with patch('src.utils.go_to_new_world', wraps=utils.go_to_new_world) as select:
            self.assertFalse(await utils.spiral_door_with_quest(self.client))
            inputs = list(self.events)
            Quester(self.client, [self.client], 1)
            self.disabled = True
            self.assertFalse(await utils.spiral_door_with_quest(self.client))
            self.assertEqual(self.events, inputs)
            self.assertEqual(select.await_count, 1)
        self.assertIn('魔法城', self.client._quest_internal_block_reason)

    async def test_failed_explicit_selection_never_enters_wrong_world(self):
        self.disabled = True
        self.selected = 'Krokotopia'
        self.destination = 'Arcanum/AR_Z01_Hub'
        self.miss_selection = True
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(any(isinstance(e, tuple) for e in self.events))

    async def test_goal_change_during_selection_aborts_old_recovery(self):
        self.disabled = True
        self.destination = 'Arcanum/AR_Z01_Hub'
        self.on_sleep = lambda delay: setattr(self, 'goal', 21) if delay == .4 else None
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(any(isinstance(e, tuple) for e in self.events))

    async def test_unexpected_zone_change_stops_old_recovery_without_city_input(self):
        self.travel = 'wrong-zone'
        with patch('src.utils.go_to_new_world', wraps=utils.go_to_new_world) as select:
            self.assertFalse(await utils.spiral_door_with_quest(self.client))
            select.assert_not_awaited()
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_available_without_destination_confirms_real_zone_change(self):
        self.destination = None
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertEqual(self.zone, 'Krokotopia/Hub')
        self.assertGreater(self.client.quest_position.position.await_count, 1)

    async def test_available_destination_read_error_does_not_select_city(self):
        self.client.quest_manager.side_effect = None
        self.client.quest_manager.return_value = SimpleNamespace(quest_data=AsyncMock(return_value={
            10: SimpleNamespace(goal_data=AsyncMock(return_value={20: SimpleNamespace(
                goal_destination_zone=AsyncMock(side_effect=ValueError('goal read failed')))}))}))
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_available_selection_read_error_does_not_block_enter(self):
        with patch('src.utils.get_spiral_selected_world', AsyncMock(side_effect=ValueError('mark read failed'))):
            self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_available_without_selection_or_destination_enters_current_world(self):
        self.destination, self.mark_visible = None, False
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_unknown_missing_button_never_consumes_fallback(self):
        self.control.nodes.remove(self.button)
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_unknown_hidden_button_never_consumes_fallback(self):
        self.button.visible = False
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_unknown_button_state_read_error_never_consumes_fallback(self):
        self.button.is_control_grayed = AsyncMock(side_effect=ValueError('button read failed'))
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_unknown_non_boolean_button_state_never_consumes_fallback(self):
        self.button.is_control_grayed = AsyncMock(return_value=None)
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_unknown_button_can_become_available_during_finite_wait(self):
        self.control.nodes.remove(self.button)
        self.on_sleep = lambda _: self.control.nodes.append(self.button) if self.button not in self.control.nodes else None
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_blocked_available_recovers_without_resetting_used_fallback(self):
        self.client._quest_world_gate_recovery = dict(task=(10, 20), fallback_used=True, blocked='old failure')
        self.client._quest_internal_block_reason = 'old failure'
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertTrue(self.client._quest_world_gate_recovery['fallback_used'])
        self.assertEqual(self.client._quest_world_gate_recovery['blocked'], '')
        self.assertEqual(self.client._quest_internal_block_reason, '')

    async def test_disabled_fallback_budget_survives_available_recovery(self):
        self.client._quest_world_gate_recovery = dict(task=(10, 20), fallback_used=True, blocked='old failure')
        self.disabled = True
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.disabled = False
        self.travel = 'timeout'
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.events.clear()
        self.disabled = True
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.events)
        self.assertTrue(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_missing_destination_ui_close_without_zone_change_is_not_arrival(self):
        self.destination, self.travel = None, 'timeout'
        self.on_sleep = lambda _: setattr(self, 'menu', False)
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertEqual(self.zone, 'Arcanum/AR_Z01_Hub')

    async def test_readable_expected_world_ui_close_without_zone_change_is_not_arrival(self):
        self.zone, self.destination, self.travel = 'Krokotopia/Hub', 'Krokotopia/Hub', 'timeout'
        self.on_sleep = lambda _: setattr(self, 'menu', False)
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_zone_change_waits_for_loading_to_end_before_task_reread(self):
        original_click = self.click
        async def click(window):
            await original_click(window)
            self.loading = True
        self.client.mouse_handler.click_window.side_effect = click
        def settle(_):
            self.assertTrue(self.loading)
            self.loading = False
        self.on_sleep = settle
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertFalse(self.loading)
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_zone_change_while_loading_does_not_confirm_arrival(self):
        original_click = self.click
        async def click(window):
            await original_click(window)
            self.loading = True
        self.client.mouse_handler.click_window.side_effect = click
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_arrival_task_reread_owner_change_aborts_old_recovery(self):
        async def target():
            if self.zone != 'Arcanum/AR_Z01_Hub':
                self.client.quest_recovery_owner = 'other'
            return self.target
        self.client.quest_position.position.side_effect = target
        self.assertFalse(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events, [('enter', 'Krokotopia')])

    async def test_enter_wait_aborts_for_task_battle_stop_or_owner_change(self):
        for cause in ('quest', 'goal', 'target', 'battle', 'stop', 'owner', 'disconnect'):
            with self.subTest(cause=cause):
                self.zone = 'Arcanum/AR_Z01_Hub'
                self.quest, self.goal, self.target = 10, 20, XYZ(100, 200, 3)
                self.menu, self.client.questing_status = True, True
                self.battle, self.client.quest_recovery_owner = False, None
                self.client.is_running = lambda: True
                self.travel = 'timeout'
                self.events.clear()
                def interrupt(_):
                    if cause == 'quest': self.quest = 11
                    elif cause == 'goal': self.goal = 21
                    elif cause == 'target': self.target = XYZ(900, 200, 3)
                    elif cause == 'battle': self.battle = True
                    elif cause == 'stop': self.client.questing_status = False
                    elif cause == 'owner': self.client.quest_recovery_owner = 'other'
                    else: self.client.is_running = lambda: False
                self.on_sleep = interrupt
                self.assertFalse(await utils.spiral_door_with_quest(self.client))
                self.assertEqual(self.events, [('enter', 'Krokotopia')])
                self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_cancellation_releases_owner_without_consuming_available_fallback(self):
        from src.automation_ownership import get_client_automation_ownership
        self.travel = 'timeout'
        def cancel(_):
            raise asyncio.CancelledError()
        self.on_sleep = cancel
        with self.assertRaises(asyncio.CancelledError):
            await utils.spiral_door_with_quest(self.client)
        self.assertEqual(self.events, [('enter', 'Krokotopia')])
        self.assertFalse(self.client._quest_world_gate_recovery['fallback_used'])
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_disabled_without_destination_still_selects_city_once(self):
        self.destination, self.disabled = None, True
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events.count('wbtnWizardCity'), 1)
        self.assertEqual(self.events[-1], ('enter', 'WizardCity'))
        self.assertTrue(self.client._quest_world_gate_recovery['fallback_used'])

    async def test_cancellation_during_disabled_fallback_preserves_budget(self):
        from src.automation_ownership import get_client_automation_ownership
        self.disabled = True
        def cancel(_):
            raise asyncio.CancelledError()
        self.on_sleep = cancel
        with self.assertRaises(asyncio.CancelledError):
            await utils.spiral_door_with_quest(self.client)
        self.assertTrue(self.client._quest_world_gate_recovery['fallback_used'])
        self.assertFalse(any(isinstance(event, tuple) for event in self.events))
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_stop_loading_battle_or_control_change_preserves_input_guards(self):
        for cause in ('stop', 'loading', 'battle', 'control'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.client.questing_status = cause != 'stop'
                self.loading, self.battle = cause == 'loading', cause == 'battle'
                with patch('src.teleport_math._movement_control_changed', AsyncMock(return_value=cause == 'control')):
                    self.assertFalse(await utils.spiral_door_with_quest(self.client))
                self.assertFalse(self.events)

    async def test_world_selection_is_quester_only_in_legacy_group_path(self):
        hitter = SimpleNamespace(title='p2', process_id=2, zone_name=AsyncMock(return_value=self.zone))
        quester = Quester(self.client, [self.client, hitter], 1)
        quester.new_world_doors = AsyncMock(return_value=False)
        with patch('src.questing.spiral_door_with_quest', AsyncMock(return_value=True)) as enter, \
             patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
             patch('src.questing.go_to_new_world', AsyncMock()) as follow_select:
            await quester.handle_spiral_navigation()
        enter.assert_awaited_once_with(self.client)
        follow_select.assert_not_awaited()

    async def test_existing_selector_can_choose_only_and_honor_stop(self):
        self.assertTrue(await utils.go_to_new_world(self.client, 'WizardCity', open_window=False,
                                                    select_only=True, stop=AsyncMock(return_value=None)))
        self.assertEqual(self.selected, 'WizardCity')
        self.assertFalse(any(isinstance(e, tuple) for e in self.events))
        self.events.clear()
        self.assertFalse(await utils.go_to_new_world(self.client, 'Krokotopia', open_window=False,
                                                     select_only=True, stop=AsyncMock(return_value='task changed')))
        self.assertFalse(self.events)

    async def test_guarded_selector_handles_worldview_root_already_selected_by_client(self):
        self.root.nodes = self.root.nodes[0].nodes
        for child in self.root.nodes:
            child.up = self.root
        self.selected = 'WizardCity'
        self.destination = 'WizardCity/Hub'
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events[-1], ('enter', 'WizardCity'))

    async def test_guarded_selector_uses_active_pane_with_hidden_duplicate_modal(self):
        hidden = Window('', self, [Window('messageBoxBG', self, [Window('ControlSprite', self,
            [Window('optionWindow', self)])])])
        hidden.visible = False
        self.root.nodes[0].nodes.insert(0, hidden)
        self.selected = 'WizardCity'
        self.destination = 'WizardCity/Hub'
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events[-1], ('enter', 'WizardCity'))

    async def test_guarded_selector_accepts_existing_plain_page_counter(self):
        self.panel.nodes[0].text = '1/1'
        self.selected = 'WizardCity'
        self.destination = 'WizardCity/Hub'
        self.assertTrue(await utils.spiral_door_with_quest(self.client))
        self.assertEqual(self.events[-1], ('enter', 'WizardCity'))

    async def test_actual_legacy_group_world_x_is_sent_only_to_quester(self):
        from src.paths import npc_range_path
        hitter = SimpleNamespace(title='p2', process_id=2, questing_status=True,
            send_key=AsyncMock(), is_loading=AsyncMock(return_value=False), root_window=self.root)
        reader = Quester(self.client, [self.client, hitter], 1)
        reader.leader_wait_for_free = AsyncMock()
        reader.quest_interaction_ready = AsyncMock(return_value=True)
        reader._maybe_photo_giant_vat = AsyncMock(return_value=False)
        reader.read_popup = AsyncMock(return_value='Press X to interact')
        reader._dungeon_quest_snapshot = AsyncMock(return_value=(self.quest, self.goal, 'unchanged'))
        reader._recover_quest_x_direction = AsyncMock(return_value=False)
        reader.read_spiral_door_title = AsyncMock(return_value='Spiral Map')
        with patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), \
             patch('src.questing.close_automation_popup', AsyncMock(return_value=False)), \
             patch('src.questing.is_spiral_door_open', AsyncMock(side_effect=lambda _:
                 self.client.send_key.await_count > 0)), \
             patch('src.questing.safe_click_window', AsyncMock()), \
             patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)), \
             patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda _, path: path == npc_range_path)), \
             patch('src.questing.get_popup_title', AsyncMock(return_value='世界之门')), \
             patch('src.questing.spiral_door_with_quest', AsyncMock(return_value=True)) as select_world:
            await reader.handle_normal_quests([hitter], True)
        self.client.send_key.assert_awaited_once()
        hitter.send_key.assert_not_awaited()
        select_world.assert_awaited_once_with(self.client)


if __name__ == '__main__':
    unittest.main()
