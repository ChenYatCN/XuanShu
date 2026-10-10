"""Visible local X, arrival feedback and the real dialogue state share one flow."""
import asyncio
import math
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import Keycode, XYZ
from src import questing as q, teleport_math as tm
from src.automation_ownership import get_client_automation_ownership
from tests import test_quest_x_turn_recovery as x_fixture
from tests import test_shared_movement_interaction as movement_fixture
from tests import test_party_dungeon_interaction as party_fixture

REAL_SLEEP = asyncio.sleep


class LocalXTests(unittest.IsolatedAsyncioTestCase):
    setUp = x_fixture.QuestXTurnRecoveryTests.setUp
    asyncSetUp = x_fixture.QuestXTurnRecoveryTests.asyncSetUp

    async def test_crystal_and_console_need_no_name_or_action_text_match(self):
        for title, objective, prompt in (
                ('月球晶体', '使用 月球水晶', '按X 使用水晶'),
                ('心脏控制台', '使用 中心控制台', '按X或鼠标 使用'),
                ('硫磺桶', '收集硫磺', '按 或 收集'),
                ('', '操作某物', '按X 未知操作')):
            with self.subTest(title=title):
                self.client.quest_interaction_attempt = None
                self.client._quest_x_turn_failed = None
                self.client.auto_dialogue_running = True
                self.text, self.prompt = objective, prompt
                self.client.send_key.reset_mock()
                with (patch.object(q, 'get_popup_title', AsyncMock(return_value=title)),
                      patch.object(q, 'get_quest_name', AsyncMock(side_effect=AssertionError('name matching invoked')))):
                    self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
                    self.assertTrue(await self.quester._quest_local_interaction_ready(self.client, self.target))
                    await self.quester.handle_quest_interaction(self.client, self.target)
                self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_dialogue_loop_flag_does_not_pause_finite_x_retry(self):
        self.client.auto_dialogue_running = True
        await self.quester.handle_quest_interaction(self.client, self.target)
        state = self.client.quest_interaction_attempt
        self.now = 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertGreater(state['turn_x_count'], 3)

    async def test_changed_wording_under_input_lock_is_not_a_text_matching_gate(self):
        async def changed(*args):
            self.prompt = '按X 未分类的新措辞'
        with patch.object(self.quester, '_note_quest_x_lock_wait', AsyncMock(side_effect=changed)):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertEqual(self.client.quest_interaction_attempt['prompt'], self.prompt)

    async def test_goal_change_during_final_ui_read_never_sends_old_x(self):
        reads = 0
        async def title(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.goal_id.return_value = 8
            return 'Lever'
        with patch.object(q, 'get_popup_title', AsyncMock(side_effect=title)):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.client.send_key.assert_not_awaited()

    async def test_real_dialogue_or_changed_target_never_sends_x(self):
        for event in ('dialogue', 'quest', 'goal', 'zone', 'target'):
            with self.subTest(event=event):
                self.dialogue = event == 'dialogue'
                self.client.send_key.reset_mock()
                self.client.quest_id.return_value = 42
                self.client.goal_id.return_value = 7
                self.client.zone_name.return_value = 'World/Area'
                self.client.quest_position.position.side_effect = lambda: self.target
                async def change(*args):
                    if event == 'quest': self.client.quest_id.return_value = 43
                    elif event == 'goal': self.client.goal_id.return_value = 8
                    elif event == 'zone': self.client.zone_name.return_value = 'World/New'
                    elif event == 'target': self.client.quest_position.position.side_effect = lambda: XYZ(300, 200, 0)
                with patch.object(self.quester, '_note_quest_x_lock_wait', AsyncMock(side_effect=change)):
                    await self.quester.handle_quest_interaction(self.client, self.target)
                self.client.send_key.assert_not_awaited()


class ArrivalTests(unittest.IsolatedAsyncioTestCase):
    setUp = movement_fixture.SharedMovementInteractionTests.setUp
    land = movement_fixture.SharedMovementInteractionTests.land

    def local(self, gap=1):
        self.client.quest_party_hitters = []
        self.client.quest_party_group_dungeon_zone = None
        self.position = XYZ(self.target.x - gap, self.target.y, self.target.z)
        self.client.auto_dialogue_running = True
        self.prompt = '按 或 收集'
        self.popup_title = '木炭箱'
        self.objective = '任务文字不相同'

    async def test_visible_x_at_1_6_15_90_157u_skips_all_movement(self):
        for gap in (1, 6, 15, 90, 157):
            with self.subTest(gap=gap):
                self.local(gap)
                self.visible = True
                self.client.quest_interaction_attempt = None
                self.client._quest_x_turn_failed = None
                self.client.send_key.reset_mock()
                with patch.object(q, 'collision_tp', AsyncMock()) as collision:
                    await self.quester.auto_quest_solo()
                collision.assert_not_awaited()
                self.client.goto.assert_not_awaited()
                self.navmap.assert_not_awaited()
                self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_already_at_point_without_prompt_waits_finitely_and_never_removes_again(self):
        self.local()
        self.visible = False
        with patch.object(q, 'collision_tp', AsyncMock()) as collision:
            with patch.object(tm, '_WALK_TIMEOUT', .05):
                for _ in range(3):
                    await self.quester.teleport_to_quest_target(self.client, self.target)
        collision.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assertIn(id(self.client), self.quester._quest_movement_waiting)

    async def test_collision_near_return_records_arrival_without_any_input(self):
        self.local()
        result = {}
        await tm.collision_tp(self.client, self.target, approach_result=result)
        self.assertTrue(result['landed'])
        self.assertTrue(result['walk_completed'])
        self.assertFalse(result['walk_attempted'])
        self.client.teleport.assert_not_awaited()
        self.client.goto.assert_not_awaited()

    async def test_actual_dialogue_stops_movement_and_x_despite_visible_prompt(self):
        self.local(90)
        self.visible, self.dialogue = True, True
        with patch.object(q, 'collision_tp', AsyncMock()) as collision:
            await self.quester.auto_quest_solo()
        collision.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_identity_zone_or_arrow_change_after_navmap_stops_old_move(self):
        for event in ('quest', 'goal', 'zone', 'target'):
            with self.subTest(event=event):
                self.local(104)
                self.visible = False
                self.quest, self.goal, self.zone = 42, 7, 'World/Zone'
                xyz = self.target
                async def native(client, point):
                    if event == 'quest': self.quest = 43
                    elif event == 'goal': self.goal = 8
                    elif event == 'zone': self.zone = 'World/New'
                    else: self.target = XYZ(xyz.x + 1000, xyz.y, xyz.z)
                with patch.object(q, 'navmap_tp', AsyncMock(side_effect=native)) as navmap:
                    await self.quester.move_until_quest_interaction(self.client, xyz)
                navmap.assert_awaited_once_with(self.client, xyz)
                self.client.goto.assert_not_awaited()
                self.client.send_key.assert_not_awaited()
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_ui_wait_is_cancelled_when_task_coordinates_change(self):
        self.local()
        self.visible = False
        xyz = self.target
        async def tick(seconds):
            if seconds == .1:
                self.target = XYZ(xyz.x + 1000, xyz.y, xyz.z)
            await REAL_SLEEP(0)
        with patch.object(q.asyncio, 'sleep', side_effect=tick):
            await self.quester.teleport_to_quest_target(self.client, xyz)
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.quester._quest_movement_waiting, {})

    async def test_change_during_last_hud_read_prevents_old_navmap(self):
        for event in ('goal', 'target'):
            with self.subTest(event=event):
                self.local(104)
                self.visible = False
                self.goal = 7
                xyz = self.target
                async def changed(client):
                    if event == 'goal': self.goal = 8
                    else: self.target = XYZ(xyz.x + 1000, xyz.y, xyz.z)
                    return self.quest, self.goal, self.objective
                self.quester._dungeon_quest_snapshot.side_effect = changed
                with patch.object(q, 'collision_tp', AsyncMock()) as collision:
                    await self.quester.move_until_quest_interaction(self.client, xyz)
                collision.assert_not_awaited()
                self.client.goto.assert_not_awaited()
                self.client.send_key.assert_not_awaited()

    async def test_diagnostics_separate_dialogue_loop_from_real_dialogue(self):
        self.local()
        self.visible = True
        await self.quester._note_quest_x_blocked(self.client, '测试反馈等待', self.target)
        states, details = self.log.info.call_args.args[10:12]
        self.assertTrue(states['dialogue_loop_running'])
        self.assertFalse(states['dialogue_active'])
        self.assertNotIn('dialogue_loop_running', details['blocking_states'])
        with patch.object(q, 'is_visible_by_path', AsyncMock(return_value=True)):
            await self.quester._note_quest_x_blocked(self.client, '实际对话', self.target)
        states, details = self.log.info.call_args.args[10:12]
        self.assertTrue(states['dialogue_active'])
        self.assertIn('dialogue_active', details['blocking_states'])

    async def test_visible_x_during_navmap_drains_owner_then_runs_existing_handler(self):
        self.local(104)
        self.visible = False
        cancelled = []
        async def native(*args):
            self.visible = True
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        self.navmap.side_effect = native
        await self.quester.auto_quest_solo()
        self.assertEqual(cancelled, [True])
        self.navmap.assert_awaited_once_with(self.client, self.target)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(get_client_automation_ownership(self.client).locked)


class PartyTextTests(unittest.IsolatedAsyncioTestCase):
    setUp = party_fixture.PartyDungeonInteractionTests.setUp
    member = party_fixture.PartyDungeonInteractionTests.member
    token = party_fixture.PartyDungeonInteractionTests.token
    regroup = party_fixture.PartyDungeonInteractionTests.regroup
    automatic_transition = party_fixture.PartyDungeonInteractionTests.automatic_transition

    def different_texts(self):
        self.quester.read_popup.side_effect = lambda member: 'Press X to Enter' if member is self.p1 else '按X 其他动作'
        self.title_reader.side_effect = lambda member: '入口标题A' if member is self.p1 else '另一个标题B'

    async def test_shared_entry_allows_different_member_titles_and_wording(self):
        self.different_texts()
        self.p1.entity_detect_combat_status = self.p2.entity_detect_combat_status = False
        del self.quester.enter_party_dungeon
        self.quester._mainline_identity = AsyncMock(return_value=None)
        self.assertTrue(await self.quester.enter_party_dungeon([self.p1, self.p2]))
        self.assertCountEqual(self.events, ['p1', 'p2'])
        self.assertFalse(get_client_automation_ownership(self.p1).locked)
        self.assertFalse(get_client_automation_ownership(self.p2).locked)

    async def test_shared_mechanism_different_member_prompt_still_requires_live_proof(self):
        self.different_texts()
        self.proof.return_value = False
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.assertEqual(self.events, [])
        self.proof.return_value = True
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.assertCountEqual(self.events, ['p1', 'p2'])

    async def test_hitter_world_gate_never_receives_shared_x(self):
        self.different_texts()
        self.title_reader.side_effect = lambda member: '入口' if member is self.p1 else 'World Gate'
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.assertEqual(self.events, [])

    async def test_verified_source_room_does_not_require_action_word_classification(self):
        self.automatic_transition()
        self.prompt, self.title = '按X 未知动作', '任务交互物'
        with patch.object(q, 'collision_tp', AsyncMock()) as move:
            await self.regroup()
        move.assert_awaited_once_with(self.p2, self.target)
        self.p2.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.p1.send_key.assert_not_awaited()
