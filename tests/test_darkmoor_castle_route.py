import asyncio
import ast
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.automation_ownership import get_client_automation_ownership
from src.paths import advance_dialog_path, cancel_multiple_quest_menu_path, npc_range_path
from src.questing import Quester
from tests.test_npc_mainline_menu import window


class DarkmoorCastleRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.goal = 1
        self.text = '找到方法 过去的安全 地点：Mortal Plain'
        self.dialogue = False
        self.battle = False
        self.prompt = False
        self.popup = ''
        self.events = []
        self.client = SimpleNamespace(title='p1', questing_status=True,
            quest_party_status_session=None, quest_recovery_owner=None,
            quest_party_hitters=[], entity_detect_combat_status=False,
            zone_name=AsyncMock(return_value=Quester.DARKMOOR_CASTLE_ZONE),
            in_battle=AsyncMock(side_effect=lambda: self.battle),
            is_loading=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(side_effect=lambda: self.dialogue),
            root_window=window('root'), body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: (123, self.goal, self.text))
        self.quester._mainline_identity = AsyncMock(return_value=(
            123, 'QuestTitle_00002192', '红死之任务', {'world': 'darkmoor', 'number': 40}, True))
        self.quester._window_text = AsyncMock(side_effect=lambda w: w.maybe_text.return_value)
        self.clock_button = window('actualOption', '3:33')
        self.menu = window('NPCServicesWin', children=[window('clockName', '落地钟'),
            window('other', '4:32'), self.clock_button, window('later', '6:07'),
            window('noon', '12:00')], visible=False)

        async def teleport(point):
            self.assert_owned()
            self.events.append(('tp', point))
            self.client.body.position.return_value = point
            kind, _ = Quester.DARKMOOR_CASTLE_ROUTE[self.state()['index']]
            self.dialogue = kind == 'dialogue'
            self.battle = kind == 'battle'
            self.prompt = kind in ('interact', 'clock')
            self.popup = '落地钟' if kind == 'clock' else '当前机关'

        async def key(key, duration):
            self.assert_owned()
            self.assertEqual((key, duration), (Keycode.X, .1))
            self.events.append(('x', self.state()['index']))
            if self.state()['index'] == 9:
                self.menu.is_visible.return_value = True
            else:
                self.goal += 1
                self.prompt = False

        async def click(client, target):
            self.assert_owned()
            self.assertIs(target, self.clock_button)
            self.events.append(('clock', target.maybe_text.return_value))
            self.menu.is_visible.return_value = False
            self.goal += 1

        async def dialogue_step(client):
            if self.dialogue:
                self.events.append(('dialogue', self.state()['index']))
                self.dialogue = False
                self.goal += 1
                return True
            return False

        self.client.teleport.side_effect = teleport
        self.client.send_key.side_effect = key
        self.quester._click_ui_window = AsyncMock(side_effect=click)
        self.quester._quest_dialogue_blocks_movement = AsyncMock(side_effect=dialogue_step)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing',
            new=AsyncMock(side_effect=lambda c: not self.battle and not self.dialogue
                          and not c.entity_detect_combat_status)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: self.prompt if p == npc_range_path else
            self.dialogue if p == advance_dialog_path else
            self.menu.is_visible.return_value if p == cancel_multiple_quest_menu_path else False)))
        stack.enter_context(patch('src.questing.read_dialogue_text', new=AsyncMock(
            side_effect=lambda c: '剧情对话' if self.dialogue else '')))
        stack.enter_context(patch('src.questing.get_popup_title', new=AsyncMock(side_effect=lambda c: self.popup)))
        self.get_window = stack.enter_context(patch('src.questing.get_window_from_path', new=AsyncMock(
            side_effect=lambda root, p: self.menu if p == cancel_multiple_quest_menu_path[:2] else None)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.logger'))

    def state(self):
        return self.client._xuanshu_darkmoor_castle

    def assert_owned(self):
        self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
        self.assertTrue(get_client_automation_ownership(self.client).locked)

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def tick(self, seconds=1, quester=None):
        self.now += seconds
        result = await (quester or self.quester)._maybe_handle_darkmoor_castle(self.client)
        self.assert_released()
        return result

    async def to_step(self, index):
        for _ in range(120):
            state = getattr(self.client, '_xuanshu_darkmoor_castle', None)
            if isinstance(state, dict) and state['index'] == index and state['phase'] == 'move':
                return
            await self.tick()
            if self.battle and self.state()['seen']:
                self.battle = False
                self.goal += 1
        self.fail(f'Route did not reach step {index}: {getattr(self.client, "_xuanshu_darkmoor_castle", None)}')

    async def test_full_exact_route_interactions_clock_and_normal_movement_release(self):
        expected = [(918.222, 208.404, 1.000), (2961.016, 1545.579, 1.000),
            (4793.177, 1544.604, 110.311), (4793.177, 1544.604, 110.311),
            (5146.941, -1541.925, 1.000), (-3492.767, -461.833, 1.000),
            (-4876.514, -496.477, .999), (-6135.229, -463.306, .999),
            (-4971.130, -1658.765, 1.000), (-6331.276, 1571.537, 1.000),
            (-2204.191, 3274.202, 561.399), (2217.120, 3289.541, 561.400),
            (-34.002, 6605.081, -162.617)]
        for _ in range(140):
            if not await self.tick():
                break
            if self.battle and self.state()['seen']:
                self.battle = False
                self.goal += 1
        self.assertEqual(self.state()['phase'], 'completed')
        self.assertEqual([(p.x, p.y, p.z) for event, p in self.events if event == 'tp'], expected)
        self.assertEqual([p for event, p in self.events if event == 'x'], [1, 5, 6, 7, 9, 10, 11])
        self.assertEqual([p for event, p in self.events if event == 'dialogue'], [0, 2, 12])
        self.assertEqual([p for event, p in self.events if event == 'clock'], ['3:33'])
        count = len(self.events)
        self.assertFalse(await self.tick())
        self.assertEqual(len(self.events), count)

    async def test_wrong_zone_parent_and_entry_stage_do_not_start(self):
        self.client.zone_name.return_value = 'Darkmoor/DM_Z02_MortalPlain'
        self.assertFalse(await self.tick())
        self.client.zone_name.return_value = Quester.DARKMOOR_CASTLE_ZONE
        for identity in (None, (124, 'QuestTitle_00002192', '', None, True),
                         (123, 'QuestTitle_19BE8E', '', None, True)):
            self.quester._mainline_identity.return_value = identity
            self.assertFalse(await self.tick())
        self.quester._mainline_identity.return_value = (123, 'QuestTitle_00002192', '', None, True)
        for text in ('找到方法 过去的安全 地点：Graveholm', '击败 其他敌人 地点：Mortal Plain'):
            self.text = text
            self.assertFalse(await self.tick())
        self.client.teleport.assert_not_awaited()

    async def test_unreadable_snapshot_never_moves(self):
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        self.assertTrue(await self.tick())
        self.client.teleport.assert_not_awaited()

    async def test_dialogue_not_triggered_never_skips_to_interaction(self):
        await self.tick()
        self.dialogue = False
        await self.tick()
        await self.tick(16)
        self.assertEqual(self.state()['phase'], 'failed')
        await self.tick(30)
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_ongoing_dialogue_blocks_next_tp(self):
        self.quester._quest_dialogue_blocks_movement.side_effect = None
        self.quester._quest_dialogue_blocks_movement.return_value = True
        await self.tick()
        await self.tick()
        await self.tick(10)
        self.assertEqual(self.state()['index'], 0)
        self.client.teleport.assert_awaited_once()

    async def test_battle_must_start_and_finish_before_next_waypoint(self):
        await self.to_step(3)
        await self.tick()
        await self.tick()
        await self.tick(30)
        self.assertEqual(self.state()['index'], 3)
        self.assertTrue(self.state()['seen'])
        self.battle = False
        await self.tick()
        await self.tick(3)
        self.assertEqual(self.state()['index'], 4)

    async def test_no_actual_battle_never_skips_step(self):
        await self.to_step(3)
        await self.tick()
        self.battle = False
        await self.tick()
        await self.tick(26)
        self.assertEqual(self.state()['phase'], 'failed')
        self.assertEqual(self.state()['index'], 3)

    async def test_actual_battle_is_observed_when_body_moves_and_hud_is_unreadable(self):
        await self.to_step(3)
        await self.tick()
        self.client.body.position.return_value = XYZ(20000, 20000, 20000)
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_battle')
        self.assertTrue(self.state()['seen'])
        self.assertEqual(self.state()['index'], 3)
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: (123, self.goal, self.text)
        self.battle = False
        await self.tick()
        await self.tick(3)
        self.assertEqual(self.state()['index'], 4)

    async def test_interaction_result_unknown_never_repeats_x_or_moves_on(self):
        await self.to_step(1)
        await self.tick()
        self.client.send_key.side_effect = None
        await self.tick()
        await self.tick(16)
        self.assertEqual(self.state()['phase'], 'failed')
        self.assertEqual(self.state()['index'], 1)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        await self.tick()
        self.client.send_key.assert_awaited_once()

    async def test_unconfirmed_landing_never_interacts(self):
        await self.to_step(1)
        self.client.teleport.side_effect = None
        await self.tick()
        await self.tick(6)
        self.assertEqual(self.state()['phase'], 'failed')
        self.client.send_key.assert_not_awaited()

    async def test_clock_wrong_or_duplicate_option_never_guessed(self):
        await self.to_step(9)
        await self.tick()
        await self.tick()
        self.menu.children.return_value.append(window('duplicate', '3:33'))
        await self.tick()
        self.assertEqual(self.state()['phase'], 'failed')
        self.quester._click_ui_window.assert_not_awaited()

    async def test_clock_disabled_choice_never_clicked(self):
        await self.to_step(9)
        await self.tick()
        await self.tick()
        self.clock_button.is_control_grayed.return_value = True
        await self.tick()
        self.quester._click_ui_window.assert_not_awaited()
        await self.tick(16)
        self.assertEqual(self.state()['phase'], 'failed')

    async def test_clock_missing_333_option_never_clicks_another_time(self):
        await self.to_step(9)
        await self.tick()
        await self.tick()
        self.clock_button.maybe_text.return_value = '4:32'
        await self.tick()
        self.assertEqual(self.state()['phase'], 'failed')
        self.quester._click_ui_window.assert_not_awaited()

    async def test_cancelled_clock_click_is_not_repeated(self):
        await self.to_step(9)
        await self.tick()
        await self.tick()
        self.quester._click_ui_window.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tick()
        self.assert_released()
        await self.tick()
        self.quester._click_ui_window.assert_awaited_once()
        self.assertEqual(self.state()['phase'], 'clock_close')

    async def test_dialogue_worker_records_short_page_between_route_ticks(self):
        await self.tick()
        button = window('btnRight', '完成')
        self.get_window.side_effect = lambda root, p: button if p == advance_dialog_path else None
        self.client.send_key.side_effect = None
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.assertTrue(self.state()['seen'])
        self.dialogue = False
        await self.tick()
        await self.tick(3)
        self.assertEqual(self.state()['index'], 1)

    async def test_final_dialogue_can_complete_parent_task_and_release_route(self):
        await self.to_step(12)
        await self.tick()
        await self.tick()
        self.assertTrue(self.state()['seen'])
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: (124, 1, '下一任务')
        await self.tick()
        self.assertFalse(await self.tick(3))
        self.assertEqual(self.state()['phase'], 'completed')

    async def test_final_observed_dialogue_can_finish_when_old_quest_clears(self):
        await self.to_step(12)
        await self.tick()
        await self.tick()
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        await self.tick()
        self.assertFalse(await self.tick(3))
        self.assertEqual(self.state()['phase'], 'completed')

    async def test_clock_is_not_closed_by_generic_dialogue_worker(self):
        await self.to_step(9)
        await self.tick()
        await self.tick()
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.assertTrue(self.menu.is_visible.return_value)
        self.quester._click_ui_window.assert_not_awaited()

    async def test_changed_parent_before_tp_never_moves(self):
        async def changed(client):
            return 124, 1, self.text
        original = self.quester._dungeon_quest_snapshot
        async def acquire_then_change(*args):
            if self.client.quest_recovery_owner is not None:
                return await changed(*args)
            return await original(*args)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=acquire_then_change)
        await self.tick()
        self.client.teleport.assert_not_awaited()

    async def test_busy_stopped_hitter_loading_and_other_owner_do_not_input(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.tick()
            setattr(self.client, attr, False)
        self.client.is_loading.return_value = True
        await self.tick()
        self.client.is_loading.return_value = False
        self.client.questing_status = False
        self.assertFalse(await self.tick())
        self.client.questing_status = True
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.tick())
        self.quester.clients.pop()
        self.client.quest_recovery_owner = 'other'
        self.assertTrue(await self.quester._maybe_handle_darkmoor_castle(self.client))
        self.assertEqual(self.client.quest_recovery_owner, 'other')
        self.client.quest_recovery_owner = None
        self.client.teleport.assert_not_awaited()

    async def test_cancelled_x_is_not_replayed_by_new_worker(self):
        await self.to_step(1)
        await self.tick()
        self.client.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tick()
        self.assert_released()
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await self.tick(2, quester=restarted)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_direct_tp_entry_prioritizes_route_over_hud_point(self):
        await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_CASTLE_ROUTE[0][1])
        self.assert_released()

    def test_solo_and_leader_hooks_precede_generic_dialogue(self):
        tree = ast.parse(Path('src/questing.py').read_text(encoding='utf-8'))
        for name in ('auto_quest_solo', 'auto_quest_leader'):
            method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            calls = [n for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            route = min(n.lineno for n in calls if n.func.attr == '_maybe_handle_darkmoor_castle')
            dialogue = min(n.lineno for n in calls if n.func.attr == '_quest_dialogue_blocks_movement')
            self.assertLess(route, dialogue)


if __name__ == '__main__':
    unittest.main()
