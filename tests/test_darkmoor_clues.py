import asyncio
import ast
import inspect
import textwrap
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.automation_ownership import get_client_automation_ownership
from src.paths import advance_dialog_path, decline_quest_path, npc_range_path
from src.questing import Quester
from tests.test_npc_mainline_menu import window


class DarkmoorClueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.goal = 0., 7
        self.text = '调查 线索 地点：Graveholm'
        self.events, self.popup = [], ''
        self.yes = window('btnLeft', '是')
        self.no = window('btnRight', '否')
        self.title = window('liveTitle', '线索')
        self.dialog = window('wndDialogMain', children=[self.yes, self.no, self.title], visible=False)
        self.body = ''
        self.client = SimpleNamespace(title='p1', questing_status=True, auto_dialogue_running=True,
            quest_recovery_owner=None, quest_party_hitters=[], quest_party_status_session=None,
            zone_name=AsyncMock(return_value=Quester.DARKMOOR_CLUE_ZONE),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(side_effect=lambda: self.dialog.is_visible.return_value),
            teleport=AsyncMock(), send_key=AsyncMock(), root_window=window('root'),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))))
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot())
        self.quester._window_text = AsyncMock(side_effect=lambda w: w.maybe_text.return_value)

        async def teleport(point):
            self.assert_owned()
            self.events.append(('tp', point))
            self.client.body.position.return_value = point
            self.popup = '阿克托指挥官' if point == Quester.DARKMOOR_CLUE_NPC_POSITION else '线索'

        async def key(key, seconds):
            self.assert_owned()
            self.assertEqual(key, Keycode.X)
            self.events.append(('x', self.popup))
            self.dialog.is_visible.return_value = True
            if self.popup == '线索':
                self.open_confirmation()
            else:
                self.body = 'NPC 普通剧情对话'
                self.title.maybe_text.return_value = '阿克托指挥官'
                self.no.maybe_text.return_value = '继续'
                self.yes.is_visible.return_value = False

        async def click(client, button):
            self.assertTrue(get_client_automation_ownership(client).locked)
            self.assertIs(button, self.yes)
            self.events.append(('yes', button.name.return_value))
            self.close_dialogue()

        async def sleep(seconds):
            self.now += seconds

        self.client.teleport.side_effect, self.client.send_key.side_effect = teleport, key
        self.quester._click_ui_window = AsyncMock(side_effect=click)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(
            side_effect=lambda c: not self.dialog.is_visible.return_value)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: bool(self.popup) if p == npc_range_path else
            self.dialog.is_visible.return_value if p in (advance_dialog_path, decline_quest_path) else False)))
        stack.enter_context(patch('src.questing.get_window_from_path', new=AsyncMock(
            side_effect=lambda root, p: self.dialog if p == advance_dialog_path[:-1] else
            self.no if p == advance_dialog_path else self.yes if p == decline_quest_path else None)))
        self.popup_title = stack.enter_context(patch('src.questing.get_popup_title', new=AsyncMock(side_effect=lambda c: self.popup)))
        self.dialogue = stack.enter_context(patch('src.questing.read_dialogue_text', new=AsyncMock(side_effect=lambda c: self.body)))
        stack.enter_context(patch('src.questing.logger'))

    def snapshot(self):
        return Quester.DARKMOOR_CANTRIP_QUEST_ID, self.goal, self.text

    def assert_owned(self):
        self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
        self.assertTrue(get_client_automation_ownership(self.client).locked)

    def open_confirmation(self):
        self.dialog.is_visible.return_value = True
        self.body = '这是一本充满笔记、咒语和公式的日记。要给阿克托指挥官看看吗？'
        self.title.maybe_text.return_value = '线索'
        self.yes.is_visible.return_value = True
        self.yes.maybe_text.return_value = '是'
        self.no.maybe_text.return_value = '否'

    def close_dialogue(self):
        self.dialog.is_visible.return_value = False
        self.body = ''
        self.client.quest_dialogue_settle = None

    async def handle(self):
        return await self.quester._maybe_handle_darkmoor_cantrips(self.client)

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_two_clues_yes_and_same_npc_in_order_without_replay(self):
        await self.handle()
        self.assertEqual(self.client._xuanshu_darkmoor_clues['phase'], 'npc_dialogue')
        self.assertFalse(await self.handle())
        self.assertEqual(len(self.events), 5)
        self.close_dialogue()
        await self.handle()
        self.close_dialogue()
        self.assertFalse(await self.handle())
        expected = []
        for point in Quester.DARKMOOR_CLUE_POSITIONS:
            expected += [('tp', point), ('x', '线索'), ('yes', 'btnLeft'),
                         ('tp', Quester.DARKMOOR_CLUE_NPC_POSITION), ('x', '阿克托指挥官')]
        self.assertEqual(self.events, expected)
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        self.assertFalse(await restarted._maybe_handle_darkmoor_cantrips(self.client))
        self.assertEqual(self.client.teleport.await_count, 4)
        self.assert_released()

    async def test_clue_yes_precedes_generic_offer_and_never_clicks_no(self):
        self.open_confirmation()
        self.client.body.position.return_value = Quester.DARKMOOR_CLUE_POSITIONS[0]
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, self.yes)
        self.client.send_key.assert_not_awaited()
        self.assertFalse(hasattr(self.client, 'quest_invitation_state'))
        self.assertEqual(self.client._xuanshu_darkmoor_clues['phase'], 'npc')
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_CLUE_NPC_POSITION)

    async def test_auto_dialogue_only_clicks_yes_but_never_tps(self):
        self.client.questing_status = False
        self.open_confirmation()
        self.client.body.position.return_value = Quester.DARKMOOR_CLUE_POSITIONS[1]
        await self.quester._handle_darkmoor_clue_confirmation(self.client)
        self.quester._click_ui_window.assert_awaited_once_with(self.client, self.yes)
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_wrong_zone_id_objective_location_and_hitter_do_not_route(self):
        self.client.zone_name.return_value = 'Darkmoor/Another'
        self.assertFalse(await self.handle())
        self.client.zone_name.return_value = Quester.DARKMOOR_CLUE_ZONE
        for text in ('调查 其他东西 地点：Graveholm', '调查 线索 地点：Elsewhere', ''):
            self.text = text
            self.assertFalse(await self.handle())
        self.text = '调查 线索 地点：Graveholm'
        self.quester._dungeon_quest_snapshot.return_value = (99, 7, self.text)
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.assertFalse(await self.handle())
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: self.snapshot()
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_unreadable_or_wrong_yes_button_protects_confirmation_without_input(self):
        self.open_confirmation()
        self.client.body.position.return_value = Quester.DARKMOOR_CLUE_POSITIONS[0]
        self.yes.maybe_text.return_value = '接受'
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.yes.maybe_text.return_value = '是'
        self.yes.is_control_grayed.return_value = True
        await self.quester._advance_npc_dialogue(self.client)
        self.yes.is_control_grayed.return_value = False
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        await self.quester._advance_npc_dialogue(self.client)
        self.quester._click_ui_window.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_other_dialogue_is_not_clue_confirmation(self):
        self.open_confirmation()
        self.title.maybe_text.return_value = '其他对象'
        self.assertFalse(await self.quester._handle_darkmoor_clue_confirmation(self.client))
        self.title.maybe_text.return_value = '线索'
        self.body = '要购买这个物品吗？'
        self.assertFalse(await self.quester._handle_darkmoor_clue_confirmation(self.client))
        self.quester._click_ui_window.assert_not_awaited()

    async def test_priorities_defer_without_inputs(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active',
                     'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            self.assertTrue(await self.handle())
            setattr(self.client, attr, False)
        self.client.quest_recovery_owner = 'other'
        self.assertTrue(await self.handle())
        self.client.quest_recovery_owner = None
        self.client.quest_dungeon_recovery = {'active': True}
        self.assertTrue(await self.handle())
        self.assertEqual(self.events, [])

    async def test_wrong_popup_is_bounded_without_x_or_replay(self):
        self.popup_title.side_effect = None
        self.popup_title.return_value = '其他对象'
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.assertTrue(await self.handle())
        self.client.teleport.assert_awaited_once()
        self.assertLess(self.now, 8)
        self.assert_released()

    async def test_absent_confirmation_never_moves_to_npc(self):
        self.client.send_key.side_effect = None
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_CLUE_POSITIONS[0])
        self.quester._click_ui_window.assert_not_awaited()
        self.assert_released()

    async def test_yes_without_ui_response_never_tps_to_npc_or_reclicks(self):
        self.quester._click_ui_window.side_effect = None
        await self.handle()
        self.quester._click_ui_window.assert_awaited_once()
        self.client.teleport.assert_awaited_once()
        self.assertTrue(await self.handle())
        self.quester._click_ui_window.assert_awaited_once()
        self.assert_released()

    async def test_npc_without_dialogue_never_moves_to_second_clue(self):
        original = self.client.send_key.side_effect
        async def key(*args):
            if self.popup == '线索':
                await original(*args)
        self.client.send_key.side_effect = key
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.client._xuanshu_darkmoor_clues['phase'], 'failed')
        self.assert_released()

    async def test_cancellation_releases_and_survives_worker_recreation(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        self.assertTrue(await restarted._maybe_handle_darkmoor_clues(self.client))
        self.client.teleport.assert_awaited_once()

    async def test_loading_or_refill_during_last_yes_read_blocks_click(self):
        self.open_confirmation()
        self.client.body.position.return_value = Quester.DARKMOOR_CLUE_POSITIONS[0]
        reads = 0
        async def body(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.refilling_potions = True
            return self.body
        self.dialogue.side_effect = body
        await self.quester._handle_darkmoor_clue_confirmation(self.client)
        self.quester._click_ui_window.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_task_or_zone_change_after_clue_x_stops_old_route(self):
        original = self.client.send_key.side_effect
        async def key(*args):
            await original(*args)
            self.client.zone_name.return_value = 'Another/Zone'
        self.client.send_key.side_effect = key
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.quester._click_ui_window.assert_not_awaited()
        self.assert_released()

    async def test_real_progress_after_first_npc_does_not_investigate_second(self):
        await self.handle()
        self.close_dialogue()
        self.goal += 1
        self.text = '拜访 其他 NPC 地点：Graveholm'
        self.assertFalse(await self.handle())
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_initial_loading_battle_or_busy_defers_then_resumes(self):
        for method in (self.client.is_loading, self.client.in_battle):
            method.return_value = True
            self.assertTrue(await self.handle())
            method.return_value = False
        self.free.side_effect = None
        self.free.return_value = False
        self.assertTrue(await self.handle())
        self.assertFalse(hasattr(self.client, '_xuanshu_darkmoor_clues'))
        self.client.teleport.assert_not_awaited()
        self.free.side_effect = lambda c: not self.dialog.is_visible.return_value
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_task_change_during_final_confirmation_snapshot_blocks_yes(self):
        self.open_confirmation()
        self.client.body.position.return_value = Quester.DARKMOOR_CLUE_POSITIONS[0]
        reads = 0
        def snapshot(client):
            nonlocal reads
            reads += 1
            if reads == 3:
                self.goal += 1
            return self.snapshot()
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.quester._handle_darkmoor_clue_confirmation(self.client)
        self.quester._click_ui_window.assert_not_awaited()

    async def test_final_popup_read_takeover_prevents_clue_x(self):
        reads = 0
        async def title(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.quest_party_probe_pending = True
            return self.popup
        self.popup_title.side_effect = title
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_wrong_npc_title_never_interacts_or_runs_second_clue(self):
        self.popup_title.side_effect = lambda c: '错误的 NPC' if self.popup == '阿克托指挥官' else self.popup
        await self.handle()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assert_released()

    def test_shared_route_and_auto_dialogue_guard_precede_generic_processing(self):
        source = textwrap.dedent(inspect.getsource(Quester._maybe_handle_darkmoor_cantrips))
        self.assertLess(source.index('_maybe_handle_darkmoor_clues'), source.index("owner = 'darkmoor_cantrip'"))
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf8'))
        dialogue = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_dialogue')
        calls = {name: min(n.lineno for n in ast.walk(dialogue) if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 for name in ('_handle_darkmoor_clue_confirmation', '_advance_npc_dialogue')}
        self.assertLess(calls['_handle_darkmoor_clue_confirmation'], calls['_advance_npc_dialogue'])
