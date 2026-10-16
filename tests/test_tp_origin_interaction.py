"""Rejected task TP protects the original popup, not the entire task."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import teleport_math as movement
from src.paths import npc_range_path
import tests.test_stale_interaction_movement as prompts
import tests.test_walk_navmap_fallback as collision


class OriginInteractionTests(prompts.Fixture):
    async def failure(self, signature=('World Gate', 'Interact')):
        task = await movement._movement_task_snapshot(self.client)
        state = dict(key=(task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)),
                     control=None, walks=1, anchor=self.position, origin=self.position,
                     rejected=True, confirmed=False, ready=False,
                     announced=False, origin_signature=signature)
        self.client._quest_tp_rejected = state
        return state

    async def test_changed_prompt_at_361_allows_real_x_and_checks_task_progress(self):
        self.nearby(361)
        await self.failure()
        self.title, self.prompt = 'Storm Turbine A', '按下 或 互动'
        self.objective = 'Use Storm Turbine A'
        before = await self.quester._dungeon_quest_snapshot(self.client)
        self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 1)
        self.assertNotEqual(await self.quester._dungeon_quest_snapshot(self.client), before)

    async def test_stable_new_position_has_no_150_or_toward_target_requirement(self):
        self.nearby(300)
        await self.failure((self.title, self.prompt))
        self.position = XYZ(self.target.x-361, self.target.y, self.target.z)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_same_title_with_different_action_is_a_changed_prompt(self):
        self.nearby(361)
        await self.failure((self.title, 'Different action'))
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_same_origin_title_and_action_keeps_world_gate_blocked(self):
        self.nearby(361)
        await self.failure((self.title, self.prompt))
        for _ in range(3):
            await self.quester.auto_quest_solo()
        self.assertNotIn(Keycode.X, self.keys)

    async def test_changed_prompt_does_not_bypass_pending_landing(self):
        self.nearby(361)
        state = await self.failure()
        state['pending'] = self.target, self.position, await self.client.zone_name()
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_unreadable_prompt_retains_tp_baseline_without_new_interaction_context(self):
        self.nearby(361)
        state = await self.failure((self.title, self.prompt))
        self.quester.read_popup.side_effect = RuntimeError('unreadable')
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertEqual(state['origin_signature'], (self.title, self.prompt))
        self.assertIsNone(getattr(self.client, '_quest_internal_failure_wait', None))
        self.assertLessEqual(self.quester.read_popup.await_count, 3)

    async def test_missing_pre_tp_identity_has_finite_recovery_without_new_baseline(self):
        self.nearby(361)
        state = await self.failure(None)
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertIsNotNone(self.client._quest_internal_failure_wait)
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
        self.clock += 3
        self.assertFalse(await self.quester._wait_internal_quest_failure(self.client))
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertIsNone(state['origin_signature'])

    async def test_position_that_bounces_during_release_check_is_still_blocked(self):
        self.nearby(600)
        state = await self.failure((self.title, self.prompt))
        self.position = XYZ(self.target.x-361, self.target.y, self.target.z)
        self.after_sleep = lambda _delay: setattr(self, 'position', state['origin'])
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_no_tp_near_target_and_other_client_are_unaffected(self):
        self.nearby(361)
        await self.failure((self.title, self.prompt))
        other = SimpleNamespace(**vars(self.client))
        other._quest_tp_rejected = None
        self.assertTrue(await self.quester.quest_interaction_ready(other, self.target))
        self.client._quest_tp_rejected = None
        self.position = self.target
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_out_of_range_changed_prompt_still_uses_ordinary_range(self):
        self.nearby(750)
        await self.failure()
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_task_or_cancel_during_stable_position_check_cannot_grant_old_x(self):
        for cause in ('goal', 'stop'):
            with self.subTest(cause=cause):
                self.nearby(600)
                self.client.questing_status = True
                state = await self.failure((self.title, self.prompt))
                state['task_client'] = self.client
                task = await movement._movement_task_snapshot(self.client)
                self.position = XYZ(self.target.x-361, self.target.y, self.target.z)
                def change(_delay):
                    if cause == 'goal':
                        self.goal += 1
                    else:
                        self.client.questing_status = False
                self.after_sleep = change
                self.assertFalse(await self.quester.quest_interaction_ready(
                    self.client, self.target, task_before=task))
                self.assertNotIn(Keycode.X, self.keys)

    async def test_pet_origin_guard_reads_pet_and_isolated_player_position(self):
        self.nearby(0)  # The player body alone reports being at the task target.
        pet_position = XYZ(self.target.x-361, self.target.y, self.target.z)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        pet = SimpleNamespace(position=AsyncMock(side_effect=lambda: pet_position),
                              parent_client_object=AsyncMock(return_value=parent))
        with patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))):
            await movement._movement_body(self.client)
            state = await self.failure((self.title, self.prompt))
            state['control'] = self.client._quest_movement_control
            state['origin'] = state['anchor'] = pet_position
            self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
            pet_position = XYZ(self.target.x-250, self.target.y, self.target.z)
            self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
            self.assertEqual(tuple(await self.client.body.position()), tuple(self.target))

    async def test_control_or_dialogue_during_release_wait_still_blocks_handoff(self):
        for flag in ('refilling_potions', 'quest_recovery_owner', '_quest_dialogue_update_pending'):
            with self.subTest(flag=flag):
                self.nearby(600)
                state = await self.failure((self.title, self.prompt))
                state['task_client'] = self.client
                self.position = XYZ(self.target.x-361, self.target.y, self.target.z)
                self.after_sleep = lambda _delay: setattr(self.client, flag, True)
                self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
                setattr(self.client, flag, False)


class AttemptIdentityTests(unittest.IsolatedAsyncioTestCase):
    setUp_base = collision.WalkNavmapFallbackTests.setUp
    sleep = collision.WalkNavmapFallbackTests.sleep
    solve = collision.WalkNavmapFallbackTests.solve
    plan = collision.WalkNavmapFallbackTests.plan
    teleport = collision.WalkNavmapFallbackTests.teleport
    goto = collision.WalkNavmapFallbackTests.goto

    def setUp(self):
        self.setUp_base()
        self.position = XYZ(0, 0, 0)
        self.target = self.quest_target = self.safe = self.first_solve = XYZ(5000, 0, 0)
        self.path = None
        self.origin_title, self.action = 'World Gate', 'Interact'
        self.client.root_window = object()
        self.client.questing_status = True
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _c, path: path == npc_range_path)))
        self.stack.enter_context(patch.object(movement, 'get_popup_title', AsyncMock(
            side_effect=lambda _c: self.origin_title)))
        self.stack.enter_context(patch.object(movement, 'get_window_from_path', AsyncMock(
            return_value=SimpleNamespace(maybe_text=AsyncMock(side_effect=lambda: self.action)))))
        self.stack.enter_context(patch.object(movement, '_retreat_toward', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(movement, 'navmap_tp', AsyncMock(return_value=None)))

    async def test_actual_tp_captures_origin_identity_and_retries_preserve_record(self):
        self.assertEqual(await movement.collision_tp(self.client, self.target), 'failed')
        state = self.client._quest_tp_rejected
        self.assertEqual(state['origin_signature'], ('World Gate', 'Interact'))
        self.assertEqual(tuple(state['origin']), (0, 0, 0))
        for _ in range(3):
            await movement.collision_tp(self.client, self.target)
            self.assertIs(self.client._quest_tp_rejected, state)
            self.assertEqual(state['origin_signature'], ('World Gate', 'Interact'))
        self.assertEqual(len([e for e in self.events if e[0] == 'tp']), 1)

    async def test_actual_tp_baseline_uses_position_at_input_after_planning(self):
        def solve(*_args, **_kwargs):
            self.position = XYZ(1000, 0, 0)
            self.origin_title = 'Door at new start'
            return self.target, 'clear'
        with patch('src.collision_math.find_walkable_teleport_point', side_effect=solve):
            await movement.collision_tp(self.client, self.target)
        state = self.client._quest_tp_rejected
        self.assertEqual(tuple(state['origin']), (1000, 0, 0))
        self.assertEqual(state['origin_signature'], ('Door at new start', 'Interact'))

    async def test_late_bounce_retry_reuses_confirmed_attempt_without_rebaseline(self):
        self.reject_first = False
        self.assertEqual(await movement.collision_tp(self.client, self.target), 'ready')
        state = self.client._quest_tp_landing
        self.position = state['origin']
        self.assertEqual(await movement.collision_tp(self.client, self.target), 'failed')
        self.assertIs(self.client._quest_tp_rejected, state)
        self.assertEqual(state['origin_signature'], ('World Gate', 'Interact'))
        self.assertEqual(len([e for e in self.events if e[0] == 'tp']), 1)

    async def test_walk_threshold_without_departure_cannot_clear_same_popup_guard(self):
        self.target = self.quest_target = self.safe = self.first_solve = XYZ(100, 0, 0)
        task = await movement._movement_task_snapshot(self.client)
        self.client._quest_target_first_move = dict(
            pending=True, active=True, task=task, target=self.target)
        self.assertEqual(await movement.collision_tp(self.client, self.target), 'failed')
        state = self.client._quest_tp_rejected
        self.assertIsNotNone(state)
        self.assertEqual(state['origin_signature'], ('World Gate', 'Interact'))
        self.assertTrue(await movement._movement_failure_blocks_interaction(self.client, self.target, task))

    async def test_unreadable_before_tp_is_not_replaced_by_post_tp_identity(self):
        self.origin_title = ''
        await movement.collision_tp(self.client, self.target)
        state = self.client._quest_tp_rejected
        self.assertIsNone(state['origin_signature'])
        self.origin_title = 'New readable popup'
        await movement.collision_tp(self.client, self.target)
        self.assertIs(self.client._quest_tp_rejected, state)
        self.assertIsNone(state['origin_signature'])
