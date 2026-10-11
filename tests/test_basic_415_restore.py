"""Regression checks for the restored basic path, not game acceptance tests."""
import ast
import asyncio
import math
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src import questing as q, collecting
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.paths import advance_dialog_path, decline_quest_path
from tests import test_quest_x_turn_recovery as turn_fixture

REAL_SLEEP = turn_fixture.REAL_SLEEP


class BasicRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def setUp(self):
        turn_fixture.QuestXTurnRecoveryTests.setUp(self)
        quiet = patch.object(q, 'logger')
        quiet.start()
        self.addCleanup(quiet.stop)

    async def test_fresh_tp_uses_valid_target_despite_missing_goal_metadata(self):
        self.range_visible = False
        self.client.goal_id.return_value = 0
        self.position = XYZ(0, 0, 0)
        async def land(client, target):
            self.position = target
        with patch.object(q, 'navmap_tp', AsyncMock(side_effect=land)) as tp:
            self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        tp.assert_awaited_once_with(self.client, self.target)

    async def test_visible_x_needs_no_valid_goal_or_object_text(self):
        self.client.goal_id.return_value = 0
        self.text, self.prompt = '', ''
        with patch.object(q, 'get_popup_title', AsyncMock(return_value='Wrong NPC')):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual([key for key, _ in self.keys], [Keycode.X])

    async def test_collect_enters_original_search_despite_obsolete_qualification_veto(self):
        self.client._quest_collect_block_reason = ('explicit_non_collect_type:1', None, '')
        self.client.quest_position.position = AsyncMock(return_value=XYZ(0, 0, 0))
        search = SimpleNamespace(run=AsyncMock(return_value=True))
        with patch.object(collecting, 'CollectSearch', Mock(return_value=search)) as factory:
            self.assertTrue(await self.quester.auto_collect_rewrite(self.client))
        factory.assert_called_once_with(self.quester, self.client)
        search.run.assert_awaited_once()

    async def test_zero_coordinate_task_iteration_reaches_collect_without_goal_type(self):
        self.client.use_potions = self.client.auto_pet_status = False
        self.client.entity_detect_combat_status = False
        self.target = XYZ(0, 0, 0)
        self.range_visible = False
        self.quester.auto_collect_rewrite = AsyncMock()
        self.quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        with (patch('src.mainline_progress.log_mainline_progress', AsyncMock()),
              patch.object(q, 'close_npc_quest_menu', AsyncMock(return_value=False)),
              patch.object(q, 'close_automation_popup', AsyncMock(return_value=False)),
              patch.object(q, 'is_free', AsyncMock(return_value=True)),
              patch.object(q, 'is_potion_needed', AsyncMock(return_value=False))):
            await self.quester.auto_quest_solo()
        self.quester.auto_collect_rewrite.assert_awaited_once_with(self.client)

    async def test_no_progress_completes_circle_after_position_jitter_and_input_release(self):
        await self.quester.handle_quest_interaction(self.client, self.target)
        state = self.client.quest_interaction_attempt
        self.now = 5
        self.position = XYZ(self.target.x + 30, self.target.y, self.target.z)
        owned, release = asyncio.Event(), asyncio.Event()
        async def other_input():
            async with automation_owner(self.client, 'npc-dialogue'):
                owned.set()
                await release.wait()
        other = asyncio.create_task(other_input())
        await owned.wait()
        recovery = asyncio.create_task(self.quester._recover_quest_x_direction(self.client, state))
        try:
            for _ in range(3):
                await REAL_SLEEP(0)
            self.assertFalse(recovery.done())
            release.set()
            await recovery
        finally:
            release.set()
            await other
            if not recovery.done():
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_missing_progress_snapshot_does_not_veto_full_circle(self):
        self.text = ''
        await self.quester.handle_quest_interaction(self.client, self.target)
        state = self.client.quest_interaction_attempt
        self.assertIsNone(state['progress'])
        self.now = 5
        await self.quester._recover_quest_x_direction(self.client, state)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)

    async def test_actual_progress_stops_turn_immediately(self):
        await self.quester.handle_quest_interaction(self.client, self.target)
        state = self.client.quest_interaction_attempt
        self.now = 5
        original = self.client.send_key.side_effect
        async def progress(code, seconds):
            await original(code, seconds)
            if code == Keycode.A:
                self.client.goal_id.return_value = 8
        self.client.send_key.side_effect = progress
        await self.quester._recover_quest_x_direction(self.client, state)
        self.assertEqual(sum(code == Keycode.A for code, _ in self.keys), 1)
        self.assertIsNone(self.client.quest_interaction_attempt)


class EnabledDialogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests.test_quest_dialogue_boundary import DialogueBoundaryTests
        DialogueBoundaryTests.setUp(self)
        self.runner._advance_npc_dialogue = q.Quester._advance_npc_dialogue.__get__(self.runner)

    async def test_quest_observes_and_independent_dialogue_advances_all_buttons(self):
        self.client.auto_dialogue_running = True
        self.client.hotkey_accept_sidequests = True
        for self.caption in ('更多', '完成', '接受'):
            with self.subTest(caption=self.caption):
                self.client.send_key.reset_mock()
                self.runner._click_ui_window.reset_mock()
                self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
                self.client.send_key.assert_not_awaited()
                self.runner._click_ui_window.assert_not_awaited()
                async with automation_owner(self.client, 'npc-dialogue'):
                    await self.runner._advance_npc_dialogue(self.client)
                if self.caption == '更多':
                    self.client.send_key.assert_awaited_once_with(Keycode.SPACEBAR)
                else:
                    self.runner._click_ui_window.assert_awaited_once_with(self.client, self.button)
        self.assertIsNone(self.client.quest_dialogue_settle)

    async def test_disabled_waits_for_player_and_closed_page_resumes_immediately(self):
        self.assertTrue(await self.runner._quest_dialogue_blocks_movement(self.client))
        self.client.send_key.assert_not_awaited()
        self.runner._click_ui_window.assert_not_awaited()
        self.ui = False
        self.assertFalse(await self.runner._quest_dialogue_blocks_movement(self.client))

    async def test_complete_keeps_advancing_without_three_attempt_dead_end(self):
        self.client.auto_dialogue_running = True
        self.caption = '完成'
        for _ in range(5):
            await self.runner._quest_dialogue_blocks_movement(self.client)
            async with automation_owner(self.client, 'npc-dialogue'):
                await self.runner._advance_npc_dialogue(self.client)
        self.assertEqual(self.runner._click_ui_window.await_count, 5)

    async def test_accept_follows_existing_sidequest_option(self):
        self.client.auto_dialogue_running = True
        self.caption, self.offer = '接受', True
        await self.runner._advance_npc_dialogue(self.client, accept_sidequests=False)
        self.client.send_key.assert_awaited_once_with(Keycode.ESC)
        await self.runner._advance_npc_dialogue(self.client, accept_sidequests=True)
        self.runner._click_ui_window.assert_awaited_once_with(self.client, self.button)


class PostCombatJumpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events, self.token, self.duel_id = [], ('World/Room', 100, 200, 0), 42
        self.client = SimpleNamespace(title='p1', process_id=1, questing_status=True,
            just_left_combat=True, post_combat_movement_at=0,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            duel=SimpleNamespace(duel_id_full=AsyncMock(side_effect=lambda: self.duel_id)),
            quest_party_shared_target={'xyz': XYZ(200, 300, 0)},
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(200, 300, 0))))
        async def pickup(client, limit):
            self.assertTrue(client.post_combat_cleanup_active)
            self.events.append('wisps')
        async def key(key, seconds):
            self.events.append(key)
        async def tp(xyz):
            self.events.append((xyz.x, xyz.y, xyz.z))
        self.pickup = AsyncMock(side_effect=pickup)
        self.client.send_key = AsyncMock(side_effect=key)
        self.client.teleport = AsyncMock(side_effect=tp)
        self.namespace = dict(Client=object, asyncio=asyncio, Keycode=Keycode, XYZ=XYZ,
            automation_owner=automation_owner, collect_wisps_with_limit=self.pickup,
            _party_area_token=AsyncMock(side_effect=lambda _: self.token), logger=Mock())
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == 'clear_post_combat_phase')
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), self.namespace)
        self.cleanup = self.namespace['clear_post_combat_phase']
        block = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == 'p.just_left_combat and await is_free(p)')
        step = ast.parse('async def step(p):\n    for _ in (0,):\n        pass').body[0]
        step.body[0].body = [block]
        ns = dict(self.namespace, is_free=AsyncMock(return_value=True), time=__import__('time'),
                  original_client_locations={1: 'old'}, clear_post_combat_phase=self.cleanup)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[step], type_ignores=[])),
                     'XuanShu.py', 'exec'), ns)
        self.step = ns['step']

    async def test_order_once_per_battle_and_targets_unchanged_for_quester_and_hitter(self):
        shared_target = self.client.quest_party_shared_target
        for role in ('quester', 'hitter'):
            self.client.quest_party_quester = self.client if role == 'quester' else object()
            self.client.just_left_combat = True
            self.events.clear()
            await self.step(self.client)
            await self.step(self.client)
            self.assertEqual(self.events, ['wisps', Keycode.A, Keycode.D, (0, 0, -10000)])
            self.assertIs(self.client.quest_party_shared_target, shared_target)
            self.client.quest_position.position.assert_not_awaited()
            self.assertFalse(self.client.post_combat_cleanup_active)
            self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_state_change_after_each_phase_skips_old_battle_jump(self):
        for phase in ('wisps', Keycode.A, Keycode.D):
            for change in ('battle', 'loading', 'area', 'duel', 'disabled'):
                with self.subTest(phase=phase, change=change):
                    self.events.clear()
                    self.client.questing_status = True
                    self.client.is_loading.return_value = self.client.in_battle.return_value = False
                    self.duel_id, self.token = 42, ('World/Room', 100, 200, 0)
                    def mutate():
                        if change == 'battle':
                            self.client.in_battle.return_value = True
                        elif change == 'loading':
                            self.client.is_loading.return_value = True
                        elif change == 'area':
                            self.token = ('World/Room', 101, 201, 1)
                        elif change == 'duel':
                            self.duel_id = 43
                        else:
                            self.client.questing_status = False
                    async def pickup(client, limit):
                        self.events.append('wisps')
                        if phase == 'wisps':
                            mutate()
                    async def key(key, seconds):
                        self.events.append(key)
                        if key == phase:
                            mutate()
                    self.pickup.side_effect, self.client.send_key.side_effect = pickup, key
                    self.client.teleport.reset_mock()
                    await self.cleanup(self.client, cleanup=True)
                    self.client.teleport.assert_not_awaited()
                    self.assertFalse(self.client.post_combat_cleanup_active)

    async def test_task_check_cannot_clear_pending_cleanup_before_detector_consumes_it(self):
        with patch.object(q, 'reconcile_combat_state', AsyncMock()) as reconcile:
            self.assertFalse(await q.is_free_leader_questing(self.client))
        reconcile.assert_not_awaited()
        self.assertTrue(self.client.just_left_combat)
        await self.step(self.client)
        self.assertFalse(self.client.just_left_combat)
        self.assertEqual(self.events[-1], (0, 0, -10000))

    async def test_finished_cleanup_releases_task_check_without_a_delay(self):
        await self.step(self.client)
        with (patch.object(q, 'reconcile_combat_state', AsyncMock()),
              patch.object(q, 'read_dialogue_text', AsyncMock(return_value='')),
              patch.object(q, 'is_visible_by_path', AsyncMock(return_value=False))):
            self.assertTrue(await q.is_free_leader_questing(self.client))


if __name__ == '__main__':
    unittest.main()
