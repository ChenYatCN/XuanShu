"""Quest observes NPC UI; the explicitly enabled dialogue worker owns clicks."""
import ast
import asyncio
import math
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ, Keycode
from src import questing as q
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.paths import advance_dialog_path, decline_quest_path


class DialogueBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.ui, self.offer, self.caption = 0.0, True, False, '更多'
        self.client = SimpleNamespace(title='p1', questing_status=True, auto_dialogue_running=False,
            root_window=object(),
            mainline_finder_enabled=False, quest_recovery_owner=None,
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            zone_name=AsyncMock(return_value='World/Room'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 0, 0))),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 0, 0))),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={42: object()}))),
            send_key=AsyncMock())
        self.runner = q.Quester(self.client, [self.client], None)
        self.runner._window_text = AsyncMock(side_effect=lambda _: self.caption)
        self.runner._click_ui_window = AsyncMock()
        self.runner._handle_darkmoor_clue_confirmation = AsyncMock(return_value=False)
        self.runner._maybe_recover_mainline = AsyncMock(return_value=False)
        self.runner.read_quest_txt = AsyncMock(return_value='Talk to Guard')
        self.button = SimpleNamespace(is_visible=AsyncMock(side_effect=lambda: self.ui),
                                     is_control_grayed=AsyncMock(return_value=False))
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.visible = stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(
            side_effect=lambda c, p: self.ui if p == advance_dialog_path else self.offer if p == decline_quest_path else False)))
        stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(side_effect=lambda _: not self.ui)))
        stack.enter_context(patch.object(q, 'read_dialogue_text', AsyncMock(return_value='')))
        stack.enter_context(patch.object(q, 'get_window_from_path', AsyncMock(return_value=self.button)))
        stack.enter_context(patch.object(q, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        self.log = stack.enter_context(patch.object(q, 'logger'))

    async def test_quest_only_waits_indefinitely_without_click_x_tp_or_recovery(self):
        self.runner.teleport_to_quest_target = AsyncMock()
        self.runner._continue_mainline_chain = AsyncMock()
        for self.now in (0, 1, 61, 120):
            await self.runner.auto_quest_solo()
        self.client.send_key.assert_not_awaited()
        self.runner._click_ui_window.assert_not_awaited()
        self.runner.teleport_to_quest_target.assert_not_awaited()
        self.runner._continue_mainline_chain.assert_not_awaited()
        self.runner._maybe_recover_mainline.assert_not_awaited()
        messages = [call for call in self.log.info.call_args_list if '[任务对话协调]' in call.args[0]]
        self.assertLessEqual(len(messages), 1)

    async def test_quest_cannot_click_even_when_dialogue_is_enabled(self):
        self.client.auto_dialogue_running = True
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.client.send_key.assert_not_awaited()
        self.runner._click_ui_window.assert_not_awaited()

    async def test_dialogue_owner_can_advance_more_complete_and_accept(self):
        self.client.auto_dialogue_running = True
        self.client.hotkey_accept_sidequests = True
        for self.caption in ('更多', '完成', '接受'):
            self.client.send_key.reset_mock()
            self.runner._click_ui_window.reset_mock()
            async with automation_owner(self.client, 'npc-dialogue'):
                self.assertTrue(await self.runner._advance_npc_dialogue(self.client))
            if self.caption == '更多':
                self.client.send_key.assert_awaited_once_with(Keycode.SPACEBAR)
            else:
                self.runner._click_ui_window.assert_awaited_once_with(self.client, self.button)
            self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_auto_dialogue_off_offer_stays_for_manual_accept(self):
        self.offer, self.caption = True, '接受'
        async with automation_owner(self.client, 'npc-dialogue'):
            self.assertFalse(await self.runner._advance_npc_dialogue(self.client))
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.runner._click_ui_window.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_manual_end_requires_fresh_stable_goal_before_quest_resumes(self):
        self.client.mainline_finder_enabled = True  # Quiet/offer verification belongs to this feature.
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.ui = False
        self.client.goal_id.return_value = 8
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.now = .2
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.now = .4
        self.assertFalse(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.assertIsNone(self.client.quest_dialogue_settle)

    async def test_stop_during_open_dialogue_does_not_reenable_task(self):
        await self.runner.auto_quest_solo()
        self.client.questing_status = False
        await self.runner.auto_quest_solo()
        self.client.send_key.assert_not_awaited()
        self.assertFalse(self.client.questing_status)

    async def test_foreign_group_dialogue_owner_and_flag_are_untouched(self):
        peer = SimpleNamespace(auto_dialogue_running=True)
        async with automation_owner(peer, 'npc-dialogue'):
            await self.runner.auto_quest_solo()
            self.assertTrue(peer.auto_dialogue_running)
            self.assertEqual(get_client_automation_ownership(peer).owner_label, 'npc-dialogue')

    async def test_production_quest_worker_does_not_start_hidden_dialogue(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run_questing_worker')
        reader = SimpleNamespace(auto_quest=AsyncMock())
        hidden = AsyncMock()
        namespace = dict(Client=object, reconcile_combat_state=AsyncMock(), original_client_locations={},
                         party_enabled=True, Quester=Mock(return_value=reader), logger=Mock(),
                         dialogue_loop=hidden, ignore_pet_level_up=False, only_play_dance_game=False)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        await namespace['run_questing_worker'](self.client)
        reader.auto_quest.assert_awaited_once()
        hidden.assert_not_awaited()
        self.assertFalse(self.client.auto_dialogue_running)


class TurnManualDialogueBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_quest_x_turn_recovery import QuestXTurnRecoveryTests
        QuestXTurnRecoveryTests.setUp(self)
        self.first_x = QuestXTurnRecoveryTests.first_x.__get__(self)

    async def test_open_manual_dialogue_preserves_remaining_turn_without_timeout_input(self):
        state = await self.first_x()
        state['turn_angle'] = math.pi / 2
        self.dialogue = True
        for self.now in (5, 65, 125):
            await self.quester._recover_quest_x_direction(self.client, state)
            self.assertIs(self.client.quest_interaction_attempt, state)
            self.assertAlmostEqual(state['turn_angle'], math.pi / 2)
        self.assertEqual([key for key, _ in self.keys], [Keycode.X])
        self.quester._advance_npc_dialogue.assert_not_awaited()
        self.assertFalse(get_client_automation_ownership(self.client).locked)
