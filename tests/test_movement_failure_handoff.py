"""Task TP failures constrain the existing interaction and finite walk callers."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import teleport_math as movement
from src.paths import npc_range_path
import tests.test_stale_interaction_movement as prompts
import tests.test_walk_waypoint_execution as walks
import tests.test_dungeon_party_transition as transitions
import tests.test_walk_navmap_fallback as collision


class FailedMovementTests(prompts.Fixture):
    async def failure(self, walks=1):
        task = await movement._movement_task_snapshot(self.client)
        key = (task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target))
        state = dict(key=key, walks=walks, anchor=self.position, origin=self.position,
                     ready=False, announced=False, rejected=True,
                     origin_signature=(self.title, self.prompt))
        self.client._quest_tp_rejected = state
        return state

    async def test_bounced_world_gate_never_gets_x_on_this_or_next_iteration(self):
        self.nearby(100)
        self.title = '世界之门'
        self.prompt = 'Press X to interact'
        await self.failure()
        for _ in range(3):
            await self.quester.auto_quest_solo()
        self.assertNotIn(Keycode.X, self.keys)

    async def test_target_world_gate_without_failed_movement_keeps_existing_handoff(self):
        self.nearby(100)
        self.title = '世界之门'
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_failure_cannot_cancel_movement_just_because_old_prompt_is_near(self):
        self.nearby(100)
        await self.failure()
        with patch('src.questing.collision_tp', AsyncMock(return_value='failed')) as tp:
            result = await self.quester.move_until_quest_interaction(self.client, self.target)
        tp.assert_awaited_once()
        self.assertEqual(result, 'failed')

    async def test_successful_approach_releases_handoff_without_claiming_quest_progress(self):
        self.nearby(354)
        state = await self.failure()
        self.position = XYZ(self.target.x-140, self.target.y, self.target.z)
        state['ready'] = True
        task = await movement._movement_task_snapshot(self.client)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertTrue(movement._same_movement_task(await movement._movement_task_snapshot(self.client), task))
        self.position = state['origin']
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertTrue(state['origin_guard_released'])  # A stable departure retired this old attempt.

    async def test_new_task_or_zone_ignores_old_failure_and_clients_are_isolated(self):
        self.nearby(100)
        await self.failure()
        self.goal += 1
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
        self.client._quest_tp_rejected = None
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_sub_unit_arrow_jitter_and_unreadable_task_do_not_release_failure(self):
        self.nearby(100)
        await self.failure()
        self.target = XYZ(self.target.x+.2,self.target.y,self.target.z)
        self.assertFalse(await self.quester.quest_interaction_ready(self.client,self.target))
        self.client.quest_id.side_effect = RuntimeError('temporarily unreadable')
        self.assertFalse(await self.quester.quest_interaction_ready(self.client,self.target))

    async def test_zone_change_releases_failure_and_other_client_has_no_failure(self):
        self.nearby(100)
        await self.failure()
        other = SimpleNamespace(**vars(self.client))
        other._quest_tp_rejected = None
        self.assertTrue(await self.quester.quest_interaction_ready(other,self.target))
        self.client.zone_name.return_value = 'OtherRoom'
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))

    async def test_verified_count_progress_releases_failure_but_dialogue_close_alone_does_not(self):
        self.nearby(100)
        state = await self.failure()
        before = await self.quester._dungeon_quest_snapshot(self.client)
        self.dialogue = True
        self.after_sleep = lambda _delay: setattr(self,'dialogue',False)
        self.assertFalse(await self.quester.handle_npc_talking_quests(
            self.client,[self.client],direction_search=False,before_progress=before))
        self.assertIs(self.client._quest_tp_rejected,state)
        self.dialogue = True
        def advance(_delay):
            self.dialogue = False
            self.objective = 'Manage Bolus (1 of 1)'
        self.after_sleep = advance
        self.assertTrue(await self.quester.handle_npc_talking_quests(
            self.client,[self.client],direction_search=False,before_progress=before))
        self.assertIsNone(self.client._quest_tp_rejected)

    async def test_wrapper_cancellation_drains_movement_and_watcher_without_x(self):
        self.nearby(216)
        await self.failure()
        started, released = asyncio.Event(), asyncio.Event()
        async def pending(*_args,**_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        with patch('src.questing.collision_tp',pending):
            task=asyncio.create_task(self.quester.move_until_quest_interaction(self.client,self.target))
            await asyncio.wait_for(started.wait(),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task,1)
        self.assertTrue(released.is_set())
        self.assertNotIn(Keycode.X,self.keys)
        self.assertFalse(self.held)

    async def test_exhausted_approach_defers_without_disabling_client(self):
        self.nearby(216)
        await self.failure(3)
        with patch('src.questing.collision_tp', AsyncMock(return_value='exhausted')):
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'exhausted')
        self.assertTrue(self.client.questing_status)
        self.assertNotIn(Keycode.X, self.keys)
        self.assertFalse(self.held)

    async def test_task_change_during_exhausted_result_never_stops_new_goal(self):
        self.nearby(216)
        await self.failure(3)
        async def advance(*_args,**_kwargs):
            self.goal +=1
            return 'exhausted'
        with patch('src.questing.collision_tp',advance):
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client,self.target),'changed')
        self.assertTrue(self.client.questing_status)


class CollisionResultTests(unittest.IsolatedAsyncioTestCase):
    setUp = collision.WalkNavmapFallbackTests.setUp
    sleep = collision.WalkNavmapFallbackTests.sleep
    solve = collision.WalkNavmapFallbackTests.solve
    plan = collision.WalkNavmapFallbackTests.plan
    teleport = collision.WalkNavmapFallbackTests.teleport
    goto = collision.WalkNavmapFallbackTests.goto

    async def test_rejected_with_failed_walk_reports_failure_and_keeps_record(self):
        self.path=None
        self.assertEqual(await movement.collision_tp(self.client,self.target),'failed')
        task=await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(self.client._quest_tp_rejected)
        self.assertFalse(await movement._movement_failure_blocks_interaction(self.client,self.target,task))

    async def test_rejected_with_successful_walk_reports_arrival_separately_from_progress(self):
        self.avoid=[]
        self.assertEqual(await movement.collision_tp(self.client,self.target),'ready')
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertEqual(self.quest_id,141863388597850472)
        self.assertEqual(self.goal_id,2559431)

    async def test_task_change_during_walk_is_reported_as_changed(self):
        self.avoid=[]
        self.on_walk=lambda: setattr(self,'goal_id',self.goal_id+1)
        self.assertEqual(await movement.collision_tp(self.client,self.target),'changed')
        self.assertEqual(sum(e[0]=='walk' for e in self.events),1)

    async def test_small_external_valid_approach_releases_old_exhaustion_without_new_tp(self):
        self.target=self.quest_target=XYZ(200,0,0)
        self.position=XYZ(0,0,0)
        task=await movement._movement_task_snapshot(self.client)
        key=(self.zone,(task[0],task[1],tuple(task[2]),task[3]),tuple(self.target))
        self.client._quest_tp_rejected=dict(key=key,walks=3,anchor=self.position,origin=self.position,
                                           announced=True,ready=False)
        self.position=XYZ(90,0,0)  # Less than the existing general 120u movement reset.
        with patch.object(movement,'is_visible_by_path',AsyncMock(side_effect=lambda _,p:p==npc_range_path)):
            self.assertEqual(await movement.collision_tp(self.client,self.target),'ready')
        self.assertFalse(self.events)


class ClosestApproachTests(unittest.IsolatedAsyncioTestCase):
    setUp = walks.WaypointExecutionTests.setUp
    read_position = walks.WaypointExecutionTests.read_position
    goto = walks.WaypointExecutionTests.goto
    press = walks.WaypointExecutionTests.press
    walk = walks.WaypointExecutionTests.walk
    metrics = {}
    async def test_snapped_endpoint_does_not_walk_away_when_nearer_safe_node_exists(self):
        self.target = XYZ(200,0,20)
        self.refine = False
        self.path = [(0,0,0),(-50,100,0),(0,200,0)]
        self.shapes = [walks.box(100,-50,300,50)]
        self.grid.to_world = lambda q,r: (0,0)
        self.grid.walk_z = lambda q,r: 0 if (q,r)==(0,0) else None
        self.grid.find_walk_path = lambda start,end,**kw: self.path if end.y==0 and end.x==200 else [(0,0,0)]
        self.assertFalse(await self.walk())  # Physical approach is not completion.
        self.assertGreater(self.position.x, 0)
        self.assertLess(self.position.x,75)
        self.assertLess(movement.calc_Distance(self.position,self.target),200)
        self.assertTrue(all(abs(y)<.001 for x,y in self.calls))


class ExpiredEntranceTests(transitions.PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_expiry_consumed_once_preserving_all_other_group_state(self):
        session = await self.split_after_capture()
        self.h.quest_party_probe_pending = True
        self.h.quest_recovery_owner = 'other-operation'
        session.exited.add(12345)
        before = (session.confirmed, dict(session.entry_zones), set(session.exited), dict(session.potion_areas))
        self.clock +=31
        with patch('src.questing.logger.debug') as log:
            for _ in range(3):
                self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assertIsNone(session.transition)
        self.assertEqual(sum('field=deadline' in str(c.args[0]) for c in log.call_args_list),1)
        self.assertEqual(before,(session.confirmed,dict(session.entry_zones),set(session.exited),dict(session.potion_areas)))
        self.assertTrue(self.h.quest_party_probe_pending)
        self.assertEqual(self.h.quest_recovery_owner,'other-operation')
        self.assert_no_replay()

    async def test_deadline_during_operation_drains_only_its_own_movement(self):
        session = await self.split_after_capture()
        started, released = asyncio.Event(), asyncio.Event()
        async def wait(*_args,**_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        self.reader.teleport_to_quest_target=wait
        task=asyncio.create_task(self.reader.recover_dungeon_transition())
        await asyncio.wait_for(started.wait(),1)
        self.clock +=31
        self.assertFalse(await asyncio.wait_for(task,1))
        self.assertTrue(released.is_set())
        self.assertIsNone(session.transition)
        self.assertIs(self.h.quest_party_dungeon,session)
        self.h.send_key.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
