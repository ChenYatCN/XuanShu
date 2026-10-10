"""Run with: python -X utf8 -m unittest tests.test_quest_x_diagnostics."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src import questing as q
from src import utils
from src.automation_ownership import automation_owner, get_client_automation_ownership

REAL_SLEEP = asyncio.sleep


class QuestXDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.target = XYZ(100, 200, 0)
        self.client = SimpleNamespace(
            title='p3', questing_status=True, quest_party_hitters=[],
            auto_dialogue_running=False, refilling_potions=False, quest_recovery_owner=None,
            entity_detect_combat_status=False, post_combat_cleanup_active=False,
            quest_party_battle_rescue_active=False, quest_party_probe_pending=False,
            quest_party_target_sync_active=False, quest_party_quest_worker_restart_requested=False,
            post_combat_movement_active=False, mainline_chain_retry_active=False,
            _character_selection_active=False, quest_interaction_attempt=None,
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            zone_name=AsyncMock(return_value='World/Area'),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 190, 0))),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            send_key=AsyncMock(), root_window=object(),
        )
        self.quester = q.Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value='Press X to Use')
        self.quester.read_quest_txt = AsyncMock(return_value='Use Lever (0/2)')
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.quester.party_dungeon_entry_visible = AsyncMock(return_value=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.log = self.stack.enter_context(patch.object(q, 'logger', Mock()))
        self.visible = self.stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: path == q.npc_range_path)))
        self.title = self.stack.enter_context(patch.object(q, 'get_popup_title', AsyncMock(return_value='Lever')))
        self.free = self.stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(q, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(q, 'read_dialogue_text', AsyncMock(return_value='')))
        self.stack.enter_context(patch.object(q, 'is_spiral_door_open', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(q, 'time', SimpleNamespace(monotonic=lambda: self.now)))

    def record(self):
        # Keep the main identity distinct from any assigned hitter.
        args = self.log.info.call_args.args
        self.assertIn('[任务X诊断]', args[0])
        self.assertEqual(args[1], 'p3')
        self.assertEqual(args[3:7], (42, 7, 'World/Area', 10.0))
        return args

    async def test_changed_reason_or_state_logs_immediately_same_cause_is_limited(self):
        await self.quester._note_quest_x_blocked(self.client, '重试间隔未到', self.target)
        self.record()
        self.now += 1
        await self.quester._note_quest_x_blocked(self.client, '重试间隔未到', self.target)
        self.assertEqual(self.log.info.call_count, 1)
        await self.quester._note_quest_x_blocked(self.client, '输入锁等待', self.target)
        self.assertEqual(self.log.info.call_count, 2)
        await self.quester._note_quest_x_blocked(self.client, '重试间隔未到', self.target)
        self.assertEqual(self.log.info.call_count, 3)
        self.client.refilling_potions = True
        await self.quester._note_quest_x_blocked(self.client, '重试间隔未到', self.target)
        self.assertEqual(self.log.info.call_count, 4)
        self.now += 15
        await self.quester._note_quest_x_blocked(self.client, '重试间隔未到', self.target)
        self.assertEqual(self.log.info.call_count, 5)
        self.client.send_key.assert_not_awaited()

    async def test_absent_window_and_distant_target_do_not_spam(self):
        self.visible.side_effect = None
        self.visible.return_value = False
        await self.quester._note_quest_x_blocked(self.client, '未结束移动', self.target)
        self.visible.return_value = True
        self.client.body.position.return_value = XYZ(2000, 0, 0)
        await self.quester._note_quest_x_blocked(self.client, '共享同步', self.target)
        self.log.info.assert_not_called()

    async def test_loading_to_battle_change_inside_same_guard_is_logged_immediately(self):
        self.client.is_loading.return_value = True
        await self.quester._note_quest_x_blocked(self.client, '状态保护', self.target)
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = True
        await self.quester._note_quest_x_blocked(self.client, '状态保护', self.target)
        self.assertEqual(self.log.info.call_count, 2)
        self.assertEqual(self.record()[11]['blocking_states'], ['battle'])

    async def test_shared_member_diagnostic_keeps_taskers_identity_and_records_blocked_peer(self):
        peer = SimpleNamespace(title='p4', body=SimpleNamespace(position=AsyncMock(return_value=self.target)),
            zone_name=AsyncMock(return_value='Other/Area'), is_loading=AsyncMock(return_value=True),
            in_battle=AsyncMock(return_value=False), refilling_potions=False, quest_recovery_owner=None)
        self.client.quest_party_hitters = [peer]
        await self.quester._note_quest_x_blocked(self.client, '共享交互成员检查未通过', self.target, member='p4')
        state = self.record()[11]['member_state']
        self.assertEqual(state['zone'], 'Other/Area')
        self.assertTrue(state['loading'])
        self.assertEqual(self.record()[3:6], (42, 7, 'World/Area'))

    async def test_loading_and_battle_are_observable_without_input(self):
        for flag in ('is_loading', 'in_battle'):
            getattr(self.client, flag).return_value = True
            self.now += 15
            self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
            record = self.record()
            self.assertTrue(record[10]['loading' if flag == 'is_loading' else 'battle'])
            getattr(self.client, flag).return_value = False
        self.client.send_key.assert_not_awaited()

    async def test_empty_title_is_allowed_but_window_read_failure_still_stops(self):
        self.title.return_value = None
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
        error = RuntimeError('window rebuilt')
        self.visible.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            await self.quester.quest_interaction_ready(self.client, self.target)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.record()[2], '交互窗口读取')
        self.assertIn('RuntimeError', self.record()[12]['window'])
        self.client.send_key.assert_not_awaited()

    async def test_popup_failure_is_recorded_without_changing_empty_string_fallback(self):
        del self.quester.read_popup
        with patch.object(q, 'get_window_from_path', AsyncMock(side_effect=RuntimeError('popup replaced'))):
            self.assertEqual(await self.quester.read_popup(self.client), '')
            await self.quester._note_quest_x_blocked(self.client, '共享交互提示读取未通过', self.target)
        self.assertIn('RuntimeError: popup replaced', self.record()[12]['prompt'])
        self.client.send_key.assert_not_awaited()

    async def test_swallowed_title_read_error_keeps_none_fallback_and_exact_error_type(self):
        with (patch.object(q, 'get_popup_title', utils.get_popup_title),
              patch.object(utils, 'is_visible_by_path', AsyncMock(side_effect=ValueError('title replaced')))):
            self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
            await self.quester._note_quest_x_blocked(self.client, '标题观测', self.target)
        self.assertEqual(self.record()[12]['title'], 'ValueError: title replaced')
        self.client.send_key.assert_not_awaited()

    async def test_refill_recovery_and_dialogue_guards_keep_no_input(self):
        for attr, value in (('refilling_potions', True), ('quest_recovery_owner', 'potion-return')):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                self.free.return_value = True
                self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
                self.assertEqual(self.record()[10][attr], value)
                setattr(self.client, attr, None if attr == 'quest_recovery_owner' else False)
        self.client.send_key.assert_not_awaited()

    async def test_actual_dialogue_blocks_x_but_loop_flag_is_only_function_state(self):
        self.client.auto_dialogue_running = True
        self.free.return_value = False
        with patch.object(q, 'read_dialogue_text', AsyncMock(return_value='Current NPC dialogue')):
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            await self.quester._note_quest_x_blocked(self.client, '实际对话', self.target)
        states, details = self.record()[10:12]
        self.assertTrue(states['dialogue_loop_running'])
        self.assertTrue(states['dialogue_active'])
        self.assertNotIn('dialogue_loop_running', details['blocking_states'])
        self.client.send_key.assert_not_awaited()

    async def test_changed_target_is_logged_against_taskers_own_identity(self):
        new_target = XYZ(2000, 2000, 0)
        self.client.quest_position.position.return_value = new_target
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        args = self.log.info.call_args.args
        self.assertEqual(args[3:6], (42, 7, 'World/Area'))
        self.assertTrue(args[11]['target_changed'])
        self.assertEqual(args[11]['distance_to_checked_target'], 10.0)
        self.client.send_key.assert_not_awaited()

    async def test_retry_interval_and_count_limit_are_logged_without_extra_input(self):
        signature = ((42, 7, 'World/Area'), (100, 200, 0), 'Press X to Use')
        self.client.quest_interaction_attempt = dict(signature=signature, attempts=0, next_at=self.now + 5)
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual(self.record()[2], '重试间隔未到')
        self.assertEqual(self.record()[11]['retry_in'], 5.0)
        self.client.quest_interaction_attempt.update(next_at=0, attempts=2)
        self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual(self.record()[2], 'X次数限制，进入原30秒重试间隔')
        self.assertEqual(self.client.quest_interaction_attempt['next_at'], self.now + 30)
        self.client.send_key.assert_not_awaited()

    async def test_input_lock_wait_is_logged_and_original_acquisition_is_preserved(self):
        held, release = asyncio.Event(), asyncio.Event()
        async def owner():
            async with automation_owner(self.client, 'test-dialogue'):
                held.set()
                await release.wait()
        holder = asyncio.create_task(owner())
        await held.wait()
        with patch.object(q.asyncio, 'sleep', AsyncMock()):
            action = asyncio.create_task(self.quester.handle_quest_interaction(self.client, self.target))
            try:
                for _ in range(50):
                    if self.log.info.called:
                        break
                    await REAL_SLEEP(.01)
                record = self.record()
                self.assertEqual(record[2], '输入锁等待')
                self.assertEqual(record[11]['lock_owner'], 'test-dialogue')
                self.client.send_key.assert_not_awaited()
                release.set()
                self.assertTrue(await asyncio.wait_for(action, 2))
                self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
            finally:
                release.set()
                action.cancel()
                await asyncio.gather(holder, action, return_exceptions=True)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_diagnostic_failure_does_not_authorize_input_or_change_exception_path(self):
        self.client.refilling_potions = True
        self.client.body.position.side_effect = RuntimeError('memory invalidated')
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
