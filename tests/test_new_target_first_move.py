"""A confirmed target change gets one movement attempt before a nearby old popup."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, MemoryReadError
from src import teleport_math as movement
from src.questing import Quester
from src.paths import npc_range_path
import tests.test_landing_validation_handoff as fixtures
from tests.test_dungeon_party_transition import PartyTransitionFixture

REAL_SLEEP = asyncio.sleep


class FirstMoveTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.LandingTests.setUp
    setUp_base = fixtures.LandingTests.setUp_base
    solve = fixtures.LandingTests.solve
    plan = fixtures.LandingTests.plan
    goto = fixtures.LandingTests.goto
    teleport = fixtures.LandingTests.teleport
    cleanup_task = fixtures.LandingTests.cleanup_task

    async def sleep(self, delay):
        if delay == .75:
            self.on_confirm()
        await fixtures.LandingTests.sleep(self, delay)

    async def baseline(self):
        self.on_confirm = lambda: None
        self.position = self.target
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'ready')
        self.bounce = False
        self.release.set()

    def next_target(self, *, coordinates=True, goal=True, gap=182):
        self.position = self.target
        if coordinates:
            self.target = self.quest_target = self.safe = self.first_solve = XYZ(
                self.target.x + gap, self.target.y, self.target.z)
        else:
            self.position = XYZ(self.target.x-gap, self.target.y, self.target.z)
        if goal:
            self.goal_id += 1
        self.origin = self.position

    async def test_adjacent_new_target_runs_real_tp_once_before_old_popup(self):
        await self.baseline()
        self.next_target(gap=60)
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'ready')
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.validations, 1)
        self.assertTrue(await self.reader.quest_interaction_ready(self.client, self.target))
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_same_coordinates_with_new_goal_still_attempts_movement(self):
        await self.baseline()
        self.next_target(coordinates=False)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_coordinate_only_change_is_a_new_target(self):
        await self.baseline()
        self.next_target(goal=False)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_quest_id_change_is_a_new_target(self):
        await self.baseline()
        self.next_target(coordinates=False, goal=False)
        self.quest_id += 1
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_coordinate_jitter_does_not_rearm_consumed_attempt(self):
        await self.baseline()
        self.next_target()
        await self.reader.move_until_quest_interaction(self.client, self.target)
        for offset in (.2, -.4, .6):
            self.quest_target = XYZ(self.target.x+offset, self.target.y, self.target.z)
            await self.reader.move_until_quest_interaction(self.client, self.quest_target)
        self.assertEqual(len(self.events), 1)

    async def test_transient_coordinate_change_is_not_a_confirmed_target(self):
        await self.baseline()
        old = self.target
        self.next_target(goal=False)
        self.on_confirm = lambda: setattr(self, 'quest_target', old)
        result = await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertIn(result, ('blocked', 'changed'))
        self.assertFalse(self.events)

    async def test_nonfinite_coordinates_do_not_replace_a_confirmed_target(self):
        await self.baseline()
        self.next_target()
        self.quest_target = XYZ(float('nan'), self.target.y, self.target.z)
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
        self.quest_target = self.target
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_read_failure_preserves_baseline_and_does_not_grant_x(self):
        await self.baseline()
        self.next_target()
        with patch.object(self.client.quest_position, 'position', AsyncMock(side_effect=MemoryReadError(998))):
            self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
            self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
        self.assertFalse(self.events)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_confirmed_new_target_waits_for_safety_without_consuming_attempt(self):
        await self.baseline()
        self.next_target()
        # Observe through the pre-X gate, then defer movement for each existing owner/state.
        self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
        for flag in ('refilling_potions', 'quest_recovery_owner', 'quest_party_safe_return_active',
                     'post_combat_movement_active', '_quest_dialogue_update_pending', 'entity_detect_combat_status'):
            with self.subTest(flag=flag):
                setattr(self.client, flag, True)
                self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
                self.assertFalse(self.events)
                setattr(self.client, flag, False)
        for flag in ('loading', 'battle'):
            setattr(self, flag, True)
            self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
            setattr(self, flag, False)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_already_at_new_goal_uses_arrival_without_a_pointless_tp(self):
        await self.baseline()
        self.goal_id += 1
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertTrue(await self.reader.quest_interaction_ready(self.client, self.target))
        self.assertFalse(self.events)

    async def test_bounce_then_retry_keeps_existing_finite_recovery(self):
        await self.baseline()
        self.next_target()
        self.bounce = True
        for attempt in range(3):
            result = await self.reader.move_until_quest_interaction(self.client, self.target)
            self.assertIn(result, ('failed', 'exhausted') if attempt == 0 else ('blocked',))
            self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
            self.assertTrue(self.client.questing_status)
        self.assertEqual(len([e for e in self.events if e[0]=='tp']), 1)

    async def test_failed_first_tp_then_successful_walk_releases_interaction(self):
        await self.baseline()
        self.next_target()
        self.bounce = True
        self.path = [tuple(self.origin), tuple(self.target)]
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'ready')
        self.assertTrue(await self.reader.quest_interaction_ready(self.client, self.target))
        self.assertEqual(len([e for e in self.events if e[0]=='tp']), 1)
        self.assertTrue(any(e[0]=='walk' for e in self.events))

    async def test_cancel_before_input_preserves_the_single_pending_attempt(self):
        await self.baseline()
        self.next_target()
        entered = asyncio.Event()
        async def planning(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch('src.questing.collision_tp', AsyncMock(side_effect=planning)):
            task = asyncio.create_task(self.reader.move_until_quest_interaction(self.client, self.target))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.events)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len(self.events), 1)

    async def test_prompt_during_pre_input_planning_cannot_grant_interaction(self):
        await self.baseline()
        self.next_target()
        entered = asyncio.Event()
        async def planning(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch('src.questing.collision_tp', AsyncMock(side_effect=planning)):
            task = asyncio.create_task(self.reader.move_until_quest_interaction(self.client, self.target))
            self.addAsyncCleanup(self.cleanup_task, task)
            await asyncio.wait_for(entered.wait(), 1)
            self.assertFalse(await movement._movement_interaction_handoff(self.client, self.target, None))
            self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cancel_after_record_creation_before_tp_keeps_attempt_pending(self):
        await self.baseline()
        self.next_target()
        entered = asyncio.Event()
        async def before_input(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(movement, '_teleport_once_verified', AsyncMock(side_effect=before_input)):
            task = asyncio.create_task(self.reader.move_until_quest_interaction(self.client, self.target))
            self.addAsyncCleanup(self.cleanup_task, task)
            await asyncio.wait_for(entered.wait(), 1)
            self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.events)
        await self.reader.move_until_quest_interaction(self.client, self.target)
        self.assertEqual(len([e for e in self.events if e[0]=='tp']), 1)

    async def test_task_and_zone_change_during_first_landing_end_old_operation(self):
        for cause in ('task', 'zone'):
            with self.subTest(cause=cause):
                await self.baseline()
                self.next_target()
                self.started.clear()
                self.release.clear()
                old_task = await movement._movement_task_snapshot(self.client)
                task = asyncio.create_task(self.reader.move_until_quest_interaction(self.client, self.target))
                self.addAsyncCleanup(self.cleanup_task, task)
                await asyncio.wait_for(self.started.wait(), 1)
                if cause == 'task':
                    self.goal_id += 1
                else:
                    self.zone += 'Next'
                self.assertEqual(await asyncio.wait_for(task, 1), 'changed')
                self.assertFalse(await self.reader.quest_interaction_ready(
                    self.client, self.target, task_before=old_task))
                self.release.set()
                self.client._quest_tp_rejected = None

    async def test_same_xyz_shared_goal_change_counts_for_hitter_with_unchanged_own_task(self):
        session = SimpleNamespace(transition={'movement_task': (self.quest_id, 5, self.target, self.zone)})
        with patch('src.questing.dungeon_session_for', return_value=session):
            await self.baseline()
            self.next_target(coordinates=False, goal=False)
            session.transition['movement_task'] = (self.quest_id, 6, self.target, self.zone)
            await self.reader.move_until_quest_interaction(self.client, self.target, leader_client=self.client)
            self.assertEqual(len(self.events), 1)

    async def test_new_shared_goal_does_not_reuse_previous_goals_landing_or_failure(self):
        session = SimpleNamespace(transition={'movement_task': (self.quest_id, 5, self.target, self.zone)})
        with patch('src.questing.dungeon_session_for', return_value=session):
            await self.baseline()
            for goal, bounce in ((6, False), (7, True), (8, False)):
                self.next_target(coordinates=False, goal=False)
                self.bounce = bounce
                session.transition['movement_task'] = (self.quest_id, goal, self.target, self.zone)
                await self.reader.move_until_quest_interaction(self.client, self.target, leader_client=self.client)
                self.assertEqual(len([e for e in self.events if e[0]=='tp']), goal-5)

    async def test_cancel_during_landing_keeps_validation_but_does_not_repeat_tp(self):
        await self.baseline()
        self.next_target()
        self.release.clear()
        task = asyncio.create_task(self.reader.move_until_quest_interaction(self.client, self.target))
        self.addAsyncCleanup(self.cleanup_task, task)
        await asyncio.wait_for(self.started.wait(), 1)
        self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.bounce = True
        self.release.set()
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'failed')
        self.assertEqual(len([e for e in self.events if e[0]=='tp']), 1)
        self.assertFalse(await self.reader.quest_interaction_ready(self.client, self.target))

    async def test_clients_and_recreated_quester_keep_separate_once_state(self):
        await self.baseline()
        self.next_target()
        await self.reader.move_until_quest_interaction(self.client, self.target)
        other = SimpleNamespace(**vars(self.client))
        # A real second client has no state copied from the first client.
        for key in list(vars(other)):
            if key.startswith('_quest_'):
                delattr(other, key)
        self.position = self.origin
        separate = Quester(other, [other], None)
        separate._npc_dialogue_visible = AsyncMock(return_value=False)
        separate._quest_interaction_signature = AsyncMock(return_value=None)
        self.assertEqual(await separate.move_until_quest_interaction(other, self.target), 'ready')
        self.assertEqual(len(self.events), 1)
        recreated = Quester(self.client, [self.client], None)
        recreated._npc_dialogue_visible = AsyncMock(return_value=False)
        recreated._quest_interaction_signature = AsyncMock(return_value=None)
        await recreated.move_until_quest_interaction(self.client, self.target)
        # The confirmed landing now bounced, so existing recovery, never a new forced TP.
        self.assertEqual(len([e for e in self.events if e[0]=='tp']), 1)



class SharedFirstMoveTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        self.reader._npc_dialogue_visible = AsyncMock(return_value=False)
        self.reader._quest_interaction_signature = AsyncMock(return_value=None)
        self.stack.enter_context(patch.object(movement, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _client, path: path == npc_range_path)))
        self.calls = []
        async def advance(client, target, leader_client=None):
            task = await movement._movement_task_snapshot(leader_client or client)
            self.assertIsNone(await movement._movement_stop_reason(client, target,
                await client.zone_name(), task))
            self.calls.append(client)
            client.position = target
            return 'ready'
        self.stack.enter_context(patch('src.questing.collision_tp', AsyncMock(side_effect=advance)))

    async def prepare(self):
        session = await self.enter()
        self.target = self.q.quest_position.position.return_value
        self.h.position = self.target
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assertFalse(self.calls)
        return session

    async def test_real_shared_caller_prioritizes_quester_new_goal_with_same_xyz(self):
        await self.prepare()
        self.q.goal_id.return_value += 1
        self.h.position = XYZ(self.target.x-60, self.target.y, self.target.z)
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assertEqual(self.calls, [self.h])
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assertEqual(self.calls, [self.h])

    async def test_real_hitter_cannot_attempt_new_goal_before_whole_group_release(self):
        await self.prepare()
        self.q.goal_id.return_value += 1
        self.h.position = XYZ(self.target.x-60, self.target.y, self.target.z)
        self.q._quest_dialogue_update_pending = True
        self.assertFalse(await self.reader.move_dungeon_member())
        self.assertFalse(self.calls)
        self.q._quest_dialogue_update_pending = False
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assertEqual(self.calls, [self.h])
