import asyncio
import math
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.automation_ownership import get_client_automation_ownership
from src.paths import advance_dialog_path, cancel_multiple_quest_menu_path, npc_range_path
from src.questing import Quester
from tests import test_quest_415_baseline as ordinary_fixture


REAL_SLEEP = asyncio.sleep


class QuestXTurnRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def setUp(self):
        self.now = 0.0
        self.target = XYZ(100, 200, 0)
        self.position = self.target
        self.text = 'Use Lever (0/2)'
        self.prompt = 'Press X to Use'
        self.dialogue = False
        self.menu = False
        self.free = True
        self.range_visible = True
        self.keys = []
        self.yaw = 6.1
        self.client = SimpleNamespace(
            title='p1', questing_status=True, refilling_potions=False,
            post_combat_cleanup_active=False, quest_party_battle_rescue_active=False,
            quest_party_probe_pending=False, quest_party_quest_worker_restart_requested=False,
            quest_recovery_owner=None, quest_party_hitters=[], auto_dialogue_running=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            zone_name=AsyncMock(return_value='World/Area'),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position),
                                 yaw=AsyncMock(side_effect=lambda: self.yaw % math.tau)),
            teleport=AsyncMock(), root_window=object())

        async def key(code, seconds):
            self.keys.append((code, self.now))
            if code == Keycode.A:
                self.yaw += math.radians(150) * seconds
            self.now += seconds
        self.client.send_key = AsyncMock(side_effect=key)
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.text)
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.quester.handle_npc_talking_quests = AsyncMock(return_value=True)
        self.quester.new_world_doors = AsyncMock(return_value=False)
        self.quester._advance_npc_dialogue = AsyncMock(side_effect=lambda _: setattr(self, 'dialogue', False))

        async def tick(seconds):
            self.now += seconds
            await REAL_SLEEP(0)
        def visible(client, path):
            return (self.dialogue if path == advance_dialog_path else
                    self.menu if path == cancel_multiple_quest_menu_path else
                    self.range_visible if path == npc_range_path else False)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', side_effect=tick))
        stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=visible)))
        stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(return_value='Lever')))
        stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_free_leader_questing', AsyncMock(
            side_effect=lambda _: self.free and not self.dialogue)))

    async def first_x(self):
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual([key for key, _ in self.keys], [Keycode.X])
        return self.client.quest_interaction_attempt

    async def test_first_x_is_unchanged_and_recovery_waits_for_response(self):
        await self.first_x()
        # A second iteration inside the old input cooldown cannot turn.
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.keys), 1)
        self.now = 1.6
        def done(code, seconds):
            self.keys.append((code, self.now))
            if code == Keycode.A:
                self.yaw += .4
            if code == Keycode.X:
                self.client.goal_id.return_value = 8
        self.client.send_key.side_effect = done
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual([key for key, _ in self.keys], [Keycode.X, Keycode.A, Keycode.X])
        self.assertGreaterEqual(self.keys[1][1] - self.keys[0][1], 4.0 - 1e-9)
        self.assertGreaterEqual(self.keys[2][1] - self.keys[1][1], .3 - 1e-9)
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.client.teleport.assert_not_awaited()

    async def test_failed_or_cancelled_first_send_does_not_arm_attempt(self):
        for failure in (RuntimeError('input failed'), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                self.client.quest_interaction_attempt = None
                self.client.send_key.side_effect = failure
                with self.assertRaises(type(failure)):
                    await self.quester.handle_quest_interaction(self.client, self.target)
                self.assertEqual(self.client.quest_interaction_attempt['attempts'], 0)
                self.assertNotIn('sent_at', self.client.quest_interaction_attempt)
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_new_named_object_does_not_reset_same_task_cooldown(self):
        await self.first_x()
        self.now = 4.0
        await self.quester.handle_quest_interaction(self.client, self.target)
        count = len(self.keys)
        with (patch('src.questing.get_popup_title', AsyncMock(return_value='Second Lever')),
              patch('src.questing.get_quest_name', AsyncMock(return_value='Use Lever and Second Lever in Area'))):
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.keys), count)

    async def test_disappeared_prompt_waits_for_delayed_progress_without_more_input(self):
        await self.first_x()
        self.range_visible = False
        self.now = 4.0
        self.text = 'Use Lever (1/2)'
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.now = 6.0
        self.assertEqual([key for key, _ in self.keys], [Keycode.X])
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.assertIsNone(getattr(self.client, '_quest_x_turn_failed', None))

    async def test_temporary_probe_preserves_sent_attempt_and_its_retry_cap(self):
        self.client.quest_party_group_dungeon_zone = 'World/Area'
        state = await self.first_x()
        self.client.quest_party_probe_pending = True
        self.now = 4.0
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertIs(self.client.quest_interaction_attempt, state)
        self.client.quest_party_probe_pending = False
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)

    async def test_pause_between_retries_preserves_remaining_circle(self):
        state = await self.first_x()
        self.now = 4.0
        def pause(code, seconds):
            self.keys.append((code, self.now))
            if code == Keycode.A:
                self.yaw += .4
            if code == Keycode.X:
                self.client.quest_recovery_owner = 'other-recovery'
        self.client.send_key.side_effect = pause
        await self.quester._recover_quest_x_direction(self.client, state)
        self.assertIs(self.client.quest_interaction_attempt, state)
        self.client.quest_recovery_owner = None
        async def resume(code, seconds):
            self.keys.append((code, self.now))
            if code == Keycode.A:
                self.yaw += .4
            self.now += seconds
        self.client.send_key.side_effect = resume
        self.now = max(self.now, state['next_at']) + .1
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertLess(abs(state['turn_angle']), math.tau + .4)

    async def test_first_x_progress_or_dialogue_never_turns(self):
        for event in ('quest', 'goal', 'count', 'dialogue'):
            with self.subTest(event=event):
                self.client.quest_id.return_value = 42
                self.client.goal_id.return_value = 7
                self.client.quest_interaction_attempt = None
                self.client._quest_x_turn_failed = None
                self.text, self.dialogue = 'Use Lever (0/2)', False
                self.keys.clear()
                def done(code, seconds):
                    self.keys.append((code, self.now))
                    if event == 'quest': self.client.quest_id.return_value = 43
                    elif event == 'goal': self.client.goal_id.return_value = 8
                    elif event == 'count': self.text = 'Use Lever (1/2)'
                    else: self.dialogue = True
                self.client.send_key.side_effect = done
                await self.first_x()
                if event == 'dialogue':
                    self.assertIsNotNone(self.client.quest_interaction_attempt)
                else:
                    self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_recovered_dialogue_resumes_without_npc_handler_recursion(self):
        state = await self.first_x()
        self.now = 4.0
        def done(code, seconds):
            self.keys.append((code, self.now))
            if code == Keycode.A:
                self.yaw += .4
                self.prompt = 'Press X to Talk'  # Different nearby object, same quest point.
            else:
                self.dialogue = True
        self.client.send_key.side_effect = done
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.quester._advance_npc_dialogue.assert_awaited()
        self.quester.handle_npc_talking_quests.assert_not_awaited()
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_exhaustion_is_finite_warns_once_and_does_not_rearm_on_prompt_change(self):
        state = await self.first_x()
        self.now = 4.0
        with patch('src.questing.logger.warning') as warning:
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            self.assertIsNone(self.client.quest_interaction_attempt)
            self.prompt = 'Press X to Open'
            for _ in range(3):
                self.now += 5
                self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            warning.assert_called_once()
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        attempts = [at for key, at in self.keys if key == Keycode.X]
        for previous, current in zip(attempts, attempts[1:]):
            self.assertGreaterEqual(current - previous, 4.0)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_invalid_context_cancels_before_any_turn(self):
        mutations = (
            lambda: setattr(self.client, 'questing_status', False),
            lambda: setattr(self.client.quest_id, 'return_value', 43),
            lambda: setattr(self.client.goal_id, 'return_value', 8),
            lambda: setattr(self, 'text', 'Use Lever (1/2)'),
            lambda: setattr(self.client.zone_name, 'return_value', 'World/Other'),
            lambda: setattr(self.client.is_loading, 'return_value', True),
            lambda: setattr(self.client.in_battle, 'return_value', True),
            lambda: setattr(self, 'position', XYZ(1000, 200, 0)),
            lambda: setattr(self, 'target', XYZ(100, 210, 0)),
            lambda: setattr(self, 'free', False),  # Includes existing forced-dialogue/animation guard.
            lambda: setattr(self, 'menu', True),
            lambda: setattr(self.client, 'quest_recovery_owner', 'other-recovery'),
        )
        state = await self.first_x()
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.client.questing_status = True
                self.client.quest_id.return_value, self.client.goal_id.return_value = 42, 7
                self.client.zone_name.return_value = 'World/Area'
                self.client.is_loading.return_value = self.client.in_battle.return_value = False
                self.client.quest_recovery_owner = None
                self.text, self.free, self.dialogue, self.menu = 'Use Lever (0/2)', True, False, False
                self.target = self.position = XYZ(100, 200, 0)
                self.client.quest_interaction_attempt = state
                mutate()
                await self.quester._recover_quest_x_direction(self.client, state)
                # Temporary ownership/menu guards preserve the completed first X.
                if self.client.quest_recovery_owner or self.menu or not self.free and not self.dialogue:
                    self.assertIs(self.client.quest_interaction_attempt, state)
                else:
                    self.assertIsNone(self.client.quest_interaction_attempt)
                self.assertEqual(len(self.keys), 1)

    async def test_stop_during_turn_settle_never_sends_retry_x(self):
        state = await self.first_x()
        self.now = 4.0
        def stop(code, seconds):
            self.keys.append((code, self.now))
            self.client.questing_status = False
        self.client.send_key.side_effect = stop
        await self.quester._recover_quest_x_direction(self.client, state)
        self.assertEqual([key for key, _ in self.keys], [Keycode.X, Keycode.A])
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_cancellation_drains_brief_key_and_clears_state(self):
        for cancelled_key in (Keycode.A, Keycode.X):
            with self.subTest(key=cancelled_key):
                self.keys.clear()
                self.client.quest_interaction_attempt = None
                self.client.send_key.side_effect = lambda code, seconds: self.keys.append((code, self.now))
                state = await self.first_x()
                self.now = 4.0
                started, released = asyncio.Event(), asyncio.Event()
                async def key(code, seconds):
                    self.keys.append((code, self.now))
                    if code == Keycode.A:
                        self.yaw += .4
                    if code == cancelled_key:
                        started.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            released.set()
                self.client.send_key.side_effect = key
                worker = asyncio.create_task(self.quester._recover_quest_x_direction(self.client, state))
                worker.add_done_callback(lambda _: started.set())
                await started.wait()
                self.assertFalse(worker.done(), 'Recovery exited before the test key was sent')
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
                self.assertTrue(released.is_set())
                self.assertIsNone(self.client.quest_interaction_attempt)
                self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_missing_prompt_searches_one_circle_without_claiming_progress_or_more_x(self):
        state = await self.first_x()
        self.range_visible = False
        self.now = 4.0
        with patch('src.questing.logger.warning') as warning:
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            warning.assert_called_once()
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertEqual(sum(key == Keycode.X for key, _ in self.keys), 1)
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_normal_talk_still_uses_existing_handler(self):
        self.prompt = 'Press X to Talk'
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.client, [self.client])
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_unreadable_progress_keeps_original_x_backoff_without_turning(self):
        self.text = ''
        await self.first_x()
        self.now = 4.0
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.now = 8.0
        self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
        self.now = 20.0
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual([key for key, _ in self.keys], [Keycode.X, Keycode.X])
        self.assertIsNone(self.client._quest_x_turn_failed)


class QuestXTurnSoloIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def setUp(self):
        ordinary_fixture.OrdinaryQuest415Tests.setUp(self)
        # Reuse the ordinary-flow fixture with the current optional movement result.
        async def move(client, target, leader_client=None, **kwargs):
            await self.move(client, target, leader_client=leader_client)
        patcher = patch('src.questing.navmap_tp', side_effect=move)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.objective)
        self.yaw = 6.1
        self.client.body.yaw = AsyncMock(side_effect=lambda: self.yaw % math.tau)
        async def key(key, seconds):
            code = key
            if code == Keycode.A:
                self.yaw += .4
            self.now += seconds
        self.client.send_key.side_effect = key

    async def test_legacy_first_x_records_context_and_recovers_before_another_tp(self):
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        moves = self.move.await_count
        self.now = 4.0
        def done(code, seconds):
            if code == Keycode.A:
                self.yaw += .4
            if code == Keycode.X:
                self.client.goal_id.return_value = 8
        self.client.send_key.side_effect = done
        await self.quester.auto_quest_solo()
        self.assertEqual(self.move.await_count, moves)
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.X, Keycode.A, Keycode.X])
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_auto_iteration_keeps_sent_record_while_recovery_owns_input(self):
        await self.quester.auto_quest_solo()
        state = self.client.quest_interaction_attempt
        self.client.quest_recovery_owner = 'potion-refill'
        await self.quester.auto_quest_solo()
        self.assertIs(self.client.quest_interaction_attempt, state)
        self.client.send_key.assert_awaited_once()

    async def test_legacy_full_circle_cooldown_cannot_send_more_x_on_same_target(self):
        await self.quester.auto_quest_solo()
        state = self.client.quest_interaction_attempt
        self.now = 4.0
        with patch('src.questing.logger.warning') as warning:
            await self.quester.auto_quest_solo()
            for _ in range(3):
                self.now += 5
                await self.quester.auto_quest_solo()
            warning.assert_called_once()
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertEqual(sum(call.args[0] == Keycode.X for call in self.client.send_key.await_args_list),
                         1 + state['turn_x_count'])
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_photomancy_keeps_existing_key_order_without_arming_recovery(self):
        self.objective = 'Photomance Tree in Forest'
        await self.quester.auto_quest_solo()
        self.assertEqual([call.args[0] if call.args else call.kwargs['key']
                          for call in self.client.send_key.await_args_list],
                         [Keycode.X, Keycode.Z, Keycode.Z])
        self.assertIsNone(getattr(self.client, 'quest_interaction_attempt', None))


if __name__ == '__main__':
    unittest.main()
