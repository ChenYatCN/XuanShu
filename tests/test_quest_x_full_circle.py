"""Exercise actual heading, real dialogue feedback and local input ownership."""
import asyncio
import math
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import unittest
from wizwalker import Keycode
from src.questing import Quester
from src.automation_ownership import get_client_automation_ownership, automation_owner
from tests import test_quest_x_turn_recovery as fixture

REAL_SLEEP = asyncio.sleep


class FullCircleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.QuestXTurnRecoveryTests.asyncSetUp
    setUp = fixture.QuestXTurnRecoveryTests.setUp
    first_x = fixture.QuestXTurnRecoveryTests.first_x

    async def search(self):
        state = await self.first_x()
        self.now = 4
        result = await self.quester.handle_quest_interaction(self.client, self.target)
        return state, result

    async def test_actual_yaw_wrap_completes_circle_then_cooldown_ignores_title_jitter(self):
        state, result = await self.search()
        self.assertFalse(result)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertLess(abs(state['turn_angle']), math.tau + math.radians(23))
        self.assertAlmostEqual(state['turn_last_yaw'], state['turn_start_yaw'])
        count = len(self.keys)
        with patch('src.questing.get_popup_title', AsyncMock(return_value='Another object')):
            self.prompt = '按X 新提示'
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual(len(self.keys), count)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.now += 31
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.keys), count + 1)

    async def test_prompt_disappears_and_reappears_without_shortening_circle(self):
        state = await self.first_x()
        key = self.client.send_key.side_effect
        async def changes(code, seconds):
            await key(code, seconds)
            if code == Keycode.A:
                self.range_visible = self.yaw > 6.1 + math.pi
                self.prompt = 'Press X to Open'
        self.client.send_key.side_effect = changes
        self.now = 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertGreater(state['turn_x_count'], 0)

    async def test_progress_after_first_turn_stops_before_full_circle(self):
        state = await self.first_x()
        key = self.client.send_key.side_effect
        async def progress(code, seconds):
            await key(code, seconds)
            if code == Keycode.X:
                self.text = 'Use Lever (1/2)'
        self.client.send_key.side_effect = progress
        self.now = 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreater(state['turn_angle'], 0)
        self.assertLess(state['turn_angle'], math.tau)
        self.assertEqual(state['turn_x_count'], 1)
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.assertIsNone(getattr(self.client, '_quest_x_turn_failed', None))

    async def test_real_wrong_npc_dialogue_resumes_remaining_arc_and_finds_melorie(self):
        self.client.title = 'p3'
        self.text = '拜访梅萝莉'
        self.prompt = '按X 与馆长交谈'
        self.dialogue = True
        self.quester.handle_npc_talking_quests = Quester.handle_npc_talking_quests.__get__(self.quester)
        dialogue_angles = []
        async def dialogue(client):
            dialogue_angles.append(self.yaw)
            self.dialogue = False
        self.quester._advance_npc_dialogue.side_effect = dialogue
        key = self.client.send_key.side_effect
        arcs = []
        async def wrong_then_right(code, seconds):
            await key(code, seconds)
            if code == Keycode.A:
                self.assertFalse(self.dialogue, 'A held during real dialogue')
                arcs.append(self.yaw - 6.1)
                self.prompt = '按X 与梅萝莉交谈' if arcs[-1] >= math.pi else '按X 与馆长交谈'
            else:
                self.dialogue = True
                if arcs and arcs[-1] >= math.pi:
                    self.text = '前往下一区域'
        self.client.send_key.side_effect = wrong_then_right
        with (patch('src.questing.exit_menus', AsyncMock()),
              patch('src.questing.get_popup_title', AsyncMock(side_effect=lambda _: '梅萝莉' if self.yaw - 6.1 >= math.pi else '馆长')),
              patch('src.questing.get_quest_name', AsyncMock(side_effect=AssertionError('name gate invoked')))):
            self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertGreater(len(dialogue_angles), 1)
        self.assertGreaterEqual(arcs[-1], math.pi)
        self.assertLess(arcs[-1], math.tau)
        self.assertEqual(self.text, '前往下一区域')
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_dialogue_without_progress_resumes_and_finishes_entire_circle(self):
        state = await self.first_x()
        key = self.client.send_key.side_effect
        async def dialogue(code, seconds):
            await key(code, seconds)
            if code == Keycode.X:
                self.dialogue = True
        self.client.send_key.side_effect = dialogue
        self.now = 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertGreater(self.quester._advance_npc_dialogue.await_count, 1)
        self.assertIsNone(self.client.quest_interaction_attempt)

    async def test_missing_prompt_for_whole_circle_never_claims_completion(self):
        state = await self.first_x()
        self.range_visible, self.now = False, 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertEqual(state['turn_x_count'], 0)
        self.assertEqual(sum(code == Keycode.X for code, _ in self.keys), 1)
        self.assertIsNotNone(self.client._quest_x_turn_failed)

    async def test_stuck_yaw_is_bounded_and_never_claims_360_degrees(self):
        state = await self.first_x()
        self.client.body.yaw.side_effect = None
        self.client.body.yaw.return_value = 0.2
        self.now = 4
        with patch('src.questing.logger.warning') as warning:
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual(state['turn_angle'], 0)
        self.assertEqual(state['turn_segments'], 20)
        self.assertIn('未完成一圈', warning.call_args.args[2])

    async def test_oscillation_does_not_add_absolute_deltas_to_claim_a_circle(self):
        state = await self.first_x()
        angles = iter([0, .5, .5, 0] * 128)
        self.client.body.yaw.side_effect = lambda: next(angles)
        self.now = 4
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertLess(abs(state['turn_angle']), math.tau)
        self.assertEqual(state['turn_segments'], 256)

    async def test_stop_loading_battle_target_and_recovery_during_held_a_release_exact_key(self):
        original_key = self.client.send_key.side_effect
        mutations = (
            lambda: setattr(self.client, 'questing_status', False),
            lambda: setattr(self.client.is_loading, 'return_value', True),
            lambda: setattr(self.client.in_battle, 'return_value', True),
            lambda: setattr(self.client.goal_id, 'return_value', 8),
            lambda: setattr(self.client, 'quest_recovery_owner', 'other-recovery'))
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.client.questing_status = True
                self.client.is_loading.return_value = self.client.in_battle.return_value = False
                self.client.goal_id.return_value, self.client.quest_recovery_owner = 7, None
                self.client.quest_interaction_attempt = None
                self.client._quest_x_turn_failed = None
                self.keys.clear()
                self.client.send_key.side_effect = original_key
                state = await self.first_x()
                released = asyncio.Event()
                async def held(code, seconds):
                    self.keys.append((code, self.now))
                    if code == Keycode.A:
                        try:
                            mutate()
                            await asyncio.Event().wait()
                        finally:
                            released.set()
                self.client.send_key.side_effect = held
                self.now += 4
                await self.quester._recover_quest_x_direction(self.client, state)
                self.assertTrue(released.is_set())
                self.assertEqual([code for code, _ in self.keys], [Keycode.X, Keycode.A])
                self.assertFalse(get_client_automation_ownership(self.client).locked)
                if self.client.quest_recovery_owner:
                    self.assertEqual(self.client.quest_recovery_owner, 'other-recovery')

    async def test_cancellation_during_held_a_drains_key_up_and_owner(self):
        state = await self.first_x()
        self.now = 4
        started, released = asyncio.Event(), asyncio.Event()
        async def key(code, seconds):
            if code == Keycode.A:
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    released.set()
        self.client.send_key.side_effect = key
        task = asyncio.create_task(self.quester._recover_quest_x_direction(self.client, state))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(released.is_set())
        self.assertIsNone(self.client.quest_interaction_attempt)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_two_groups_keep_angles_and_input_ownership_separate(self):
        first = await self.first_x()
        other = SimpleNamespace(**{k: v for k, v in vars(self.client).items()
                                   if k not in ('quest_interaction_attempt', '_xuanshu_automation_ownership')})
        other.title = 'p3'
        other.quest_id = AsyncMock(return_value=84)
        heading = 1.0
        other.body = SimpleNamespace(position=AsyncMock(return_value=self.target),
                                     yaw=AsyncMock(side_effect=lambda: heading % math.tau))
        other_keys = []
        async def key(code, seconds):
            nonlocal heading
            other_keys.append(code)
            if code == Keycode.A:
                heading += .4
        other.send_key = AsyncMock(side_effect=key)
        second = dict(first, context=((84, 7, 'World/Area'), first['context'][1],
                                     (84, 7, self.text), first['context'][3]), progress=(84, 7, self.text))
        other.quest_interaction_attempt = second
        hold, release = asyncio.Event(), asyncio.Event()
        async def foreign_owner():
            async with automation_owner(self.client, 'other-recovery-input'):
                hold.set()
                await release.wait()
        owner = asyncio.create_task(foreign_owner())
        await hold.wait()
        self.now = 4
        await asyncio.gather(self.quester._recover_quest_x_direction(self.client, first),
                             self.quester._recover_quest_x_direction(other, second))
        self.assertIs(self.client.quest_interaction_attempt, first)
        self.assertEqual(first.get('turn_angle', 0), 0)
        self.assertGreaterEqual(abs(second['turn_angle']), math.tau)
        self.assertEqual(len(self.keys), 1)
        self.assertTrue(get_client_automation_ownership(self.client).locked)
        self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'other-recovery-input')
        self.assertFalse(get_client_automation_ownership(other).locked)
        release.set()
        await owner

    async def test_dialogue_appearing_under_input_claim_preserves_remaining_angle(self):
        state = await self.first_x()
        self.now = 4
        reads = 0
        async def waiting(*args):
            nonlocal reads
            reads += 1
            if reads == 1:
                self.dialogue = True
        with patch.object(self.quester, '_note_quest_x_lock_wait', AsyncMock(side_effect=waiting)):
            await self.quester._recover_quest_x_direction(self.client, state)
        self.assertIs(self.client.quest_interaction_attempt, state)
        self.assertEqual(state['turn_angle'], 0)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.now = state['next_at'] + .1
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertGreaterEqual(abs(state['turn_angle']), math.tau)
        self.assertIsNone(self.client.quest_interaction_attempt)

