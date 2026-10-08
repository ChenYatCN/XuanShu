import asyncio
from contextlib import ExitStack
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from shapely.geometry import box, LineString, Point
from wizwalker import XYZ

from src import collision_math as cm
from src import teleport_math as tm


def make_grid(regions=((-1000, -1000, 1000, 1000, 0),), static=()):
    triangles, heights = [], []
    for left, bottom, right, top, z in regions:
        triangles.extend([
            [(left, bottom), (right, bottom), (right, top)],
            [(left, bottom), (right, top), (left, top)],
        ])
        heights.extend([z, z])
    vertices = np.array(triangles, dtype=float)
    bounds = np.column_stack((vertices.min(axis=1), vertices.max(axis=1)))
    with patch.object(cm, '_walkable_mesh', return_value=(vertices, np.array(heights), bounds)), \
            patch.object(cm, '_all_collider_solids', return_value=[]):
        return cm._ZoneWalkGrid(object(), None, static, 0)


class ApproachGeometryTests(unittest.TestCase):
    def test_walk_cache_keeps_both_floors(self):
        grid = make_grid(((-500, -500, 500, 500, 0), (-500, -500, 500, 500, 600)))
        self.assertEqual(grid.walk_z(0, 0), 0)
        self.assertEqual(grid.walk_z(0, 0, prefer_z=600), 600)
        self.assertEqual(grid.walk_z(0, 0, prefer_z=0), 0)

    def test_flat_walk_path_reaches_goal(self):
        path = make_grid().find_walk_path(XYZ(0, 0, 0), XYZ(400, 0, 0))
        self.assertEqual(path[0], (0, 0, 0))
        self.assertEqual(path[-1], (400, 0, 0))

    def test_upper_floor_path_stays_upper(self):
        grid = make_grid(((-500, -500, 500, 500, 0), (-500, -500, 500, 500, 600)))
        path = grid.find_walk_path(XYZ(0, 0, 600), XYZ(400, 0, 600))
        self.assertTrue(path)
        self.assertEqual({p[2] for p in path}, {600})

    def test_same_xy_on_other_floor_is_not_arrival(self):
        grid = make_grid(((-300, -300, 300, 300, 0), (-300, -300, 300, 300, 600)))
        self.assertIsNone(grid.find_walk_path(XYZ(0, 0, 0), XYZ(0, 0, 600), goal_radius=120))

    def test_cliff_is_not_a_connected_edge(self):
        grid = make_grid(((-200, -200, 45, 200, 0), (55, -200, 400, 200, 600)))
        self.assertIsNone(grid.find_walk_path(XYZ(0, 0, 0), XYZ(200, 0, 600)))

    def test_small_mesh_gap_between_valid_nodes_is_rejected(self):
        grid = make_grid(((-200, -200, 45, 200, 0), (55, -200, 400, 200, 0)))
        self.assertIsNone(grid.find_walk_path(XYZ(0, 0, 0), XYZ(200, 0, 0)))

    def test_routes_around_a_blocked_step_not_through_it(self):
        wall = box(140, -80, 260, 80)
        path = make_grid().find_walk_path(XYZ(0, 0, 0), XYZ(400, 0, 0), avoid=[wall])
        self.assertTrue(path)
        for start, end in zip(path, path[1:]):
            self.assertFalse(wall.intersects(LineString([start[:2], end[:2]])))

    def test_can_stop_near_a_goal_inside_an_object(self):
        grid = make_grid(static=[box(350, -100, 450, 100)])
        path = grid.find_walk_path(XYZ(0, 0, 0), XYZ(400, 0, 0), goal_radius=120)
        self.assertTrue(path)
        self.assertLessEqual(math.hypot(path[-1][0] - 400, path[-1][1]), 120)
        self.assertFalse(grid.static_prep.intersects(Point(*path[-1][:2])))

    def test_search_budget_is_respected(self):
        self.assertIsNone(make_grid().find_walk_path(XYZ(0, 0, 0), XYZ(800, 0, 0), max_nodes=1))

    def test_alternate_landings_are_separated_and_connected(self):
        excluded = [XYZ(0, 0, 0), XYZ(300, 0, 0)]
        candidates = make_grid().find_approach_paths(XYZ(0, 0, 0), excluded)
        self.assertEqual(len(candidates), 3)
        for dest, path in candidates:
            self.assertTrue(path)
            self.assertLessEqual(math.hypot(*path[-1][:2]), 120)
            self.assertTrue(all(math.hypot(dest.x - p.x, dest.y - p.y) >= 150 for p in excluded))
        for index, (dest, _) in enumerate(candidates):
            self.assertTrue(all(math.hypot(dest.x - p.x, dest.y - p.y) >= 200
                                for p, _ in candidates[index + 1:]))

    def test_wrong_floor_has_no_alternate_landing(self):
        self.assertEqual(make_grid().find_approach_paths(XYZ(0, 0, 600)), [])

    def test_forced_small_radius_refines_only_on_connected_ground(self):
        goal = XYZ(445, 0, 0)
        path = make_grid().find_walk_path(XYZ(0, 0, 0), goal, goal_radius=35)
        self.assertTrue(path)
        self.assertLessEqual(math.hypot(path[-1][0] - goal.x, path[-1][1] - goal.y), 35)
        grid = make_grid(((-1000, -1000, 418, 1000, 0), (430, -1000, 1000, 1000, 0)))
        self.assertIsNone(grid.find_walk_path(XYZ(0, 0, 0), goal, goal_radius=35))

    def test_far_reentry_landings_obey_distance_bounds(self):
        candidates = make_grid().find_approach_paths(XYZ(45, 0, 0),
            min_distance=500, max_distance=1500, goal_radius=35)
        self.assertTrue(candidates)
        for dest, path in candidates:
            self.assertGreaterEqual(math.hypot(dest.x - 45, dest.y), 500)
            self.assertLessEqual(math.hypot(dest.x - 45, dest.y), 1500)
            self.assertLessEqual(math.hypot(path[-1][0] - 45, path[-1][1]), 35)

    def test_unreachable_goal_has_no_alternate_landing(self):
        grid = make_grid(((300, -300, 800, 300, 0),))
        self.assertEqual(grid.find_approach_paths(XYZ(0, 0, 0)), [])


class ApproachRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pos = XYZ(0, 0, 0)
        self.zone = 'Test/Zone'
        self.free = True
        self.now = 10.0
        self.client = SimpleNamespace(
            title='p1', zone_name=AsyncMock(side_effect=lambda: self.zone),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.pos)),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(500, 0, 0))),
            goto=AsyncMock(side_effect=self.walk), teleport=AsyncMock(),
        )
        self.grid = Mock()
        self.grid.find_walk_path.return_value = [(0, 0, 0), (100, 0, 0), (200, 0, 0),
                                                 (300, 0, 0), (400, 0, 0)]
        self.grid.find_approach_paths.return_value = [
            (XYZ(200, 200, 0), [(200, 200, 0), (400, 0, 0)]),
            (XYZ(0, 300, 0), [(0, 300, 0), (400, 0, 0)]),
            (XYZ(-200, 200, 0), [(-200, 200, 0), (400, 0, 0)]),
        ]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tm, 'is_free', AsyncMock(side_effect=lambda _: self.free)))
        self.stack.enter_context(patch.object(tm, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        self.stack.enter_context(patch.object(tm.asyncio, 'sleep', AsyncMock()))
        self.stack.enter_context(patch.object(cm, 'get_walk_grid', return_value=self.grid))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        self.log = self.stack.enter_context(patch.object(tm, 'logger'))

    async def walk(self, x, y):
        self.pos = XYZ(x, y, self.pos.z)

    def state(self, **values):
        result = dict(zone=self.zone, target=XYZ(500, 0, 0), failures=2,
                      landings=[], blocked_steps=[], retry_after=0.0)
        result.update(values)
        return result

    async def remaining(self, **kwargs):
        return await tm._walk_remaining_to_target(
            self.client, XYZ(500, 0, 0), object(), 'Test/Zone', 55, **kwargs
        )

    def patch_collision(self):
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'')))
        self.stack.enter_context(patch('src.collision.CollisionWorld', return_value=Mock()))
        self.stack.enter_context(patch.object(tm, '_resolve_player_radius', AsyncMock(return_value=55)))
        self.solve = self.stack.enter_context(patch.object(
            cm, 'find_walkable_teleport_point', return_value=(XYZ(500, 0, 0), 'target_clear')
        ))
        self.tp = self.stack.enter_context(patch.object(tm, '_teleport_once_verified', AsyncMock(return_value=True)))
        self.foot = self.stack.enter_context(patch.object(tm, '_walk_remaining_to_target', AsyncMock(return_value=False)))
        self.recovery = self.stack.enter_context(patch.object(tm, '_recover_near_target', AsyncMock(return_value=True)))
        self.navmap = self.stack.enter_context(patch.object(tm, 'navmap_tp', AsyncMock()))

    async def test_walk_stops_in_range_without_exact_target_refinement(self):
        self.assertTrue(await self.remaining())
        self.assertEqual(self.pos.x, 400)
        self.assertNotIn((500, 0), [call.args for call in self.client.goto.call_args_list])

    async def test_forced_walk_closes_gap_previously_counted_as_arrival(self):
        self.pos = XYZ(400, 0, 0)
        self.grid.find_walk_path.return_value = [(400, 0, 0), (500, 0, 0)]
        self.assertTrue(await self.remaining(goal_radius=35))
        self.client.goto.assert_awaited_once_with(500, 0)

    async def test_last_twenty_units_are_walked_to_the_actual_quest_point(self):
        self.pos = XYZ(480, 0, 0)
        self.grid.find_walk_path.return_value = [(480, 0, 0), (500, 0, 0)]
        self.assertTrue(await self.remaining(goal_radius=5))
        self.client.goto.assert_awaited_once_with(500, 0)
        self.assertLessEqual(abs(self.pos.x - 500), 5)

    async def test_exact_point_is_not_reported_reached_without_a_final_path(self):
        self.pos = XYZ(480, 0, 0)
        self.grid.find_walk_path.return_value = None
        self.assertFalse(await self.remaining(goal_radius=5))
        self.client.goto.assert_not_awaited()

    async def test_progress_callback_stops_before_another_waypoint(self):
        stopped = AsyncMock(side_effect=lambda: self.pos.x >= 100)
        self.assertTrue(await self.remaining(goal_radius=35, stop_condition=stopped))
        self.client.goto.assert_awaited_once_with(100, 0)

    async def test_recovery_callback_stops_before_teleport(self):
        state = self.state()
        stopped = AsyncMock(return_value=True)
        self.assertTrue(await tm._recover_near_target(self.client, XYZ(500, 0, 0),
            object(), self.zone, 55, state, stop_condition=stopped))
        self.grid.find_approach_paths.assert_not_called()
        self.client.teleport.assert_not_awaited()

    async def test_real_stall_is_recorded_and_not_walked_forever(self):
        self.client.goto.side_effect = None
        blocked = []
        self.assertFalse(await self.remaining(blocked_steps=blocked))
        self.assertEqual(self.client.goto.await_count, 2)
        self.assertEqual([(p.x, p.y) for p in blocked], [(100, 0)])

    async def test_partial_timed_walk_keeps_correcting_until_arrival(self):
        self.grid.find_walk_path.return_value = [(0, 0, 0), (500, 0, 0)]
        async def partial(x, y):
            self.pos = XYZ(self.pos.x + min(80, x - self.pos.x), y, 0)
        self.client.goto.side_effect = partial
        self.assertTrue(await self.remaining(goal_radius=5))
        self.assertGreater(self.client.goto.await_count, 2)
        self.assertLessEqual(abs(self.pos.x - 500), 5)

    async def test_overshot_timed_walk_corrects_instead_of_claiming_a_wall(self):
        self.grid.find_walk_path.return_value = [(0, 0, 0), (500, 0, 0)]
        async def overshoot(x, y):
            self.pos = XYZ(self.pos.x + (x - self.pos.x) * 1.5, y, 0)
        self.client.goto.side_effect = overshoot
        blocked = []
        self.assertTrue(await self.remaining(goal_radius=5, blocked_steps=blocked))
        self.assertEqual(blocked, [])
        self.assertLessEqual(abs(self.pos.x - 500), 5)

    async def test_tp_blocking_volume_does_not_force_zero_waypoint_walk(self):
        path = self.grid.find_walk_path.return_value
        self.grid.find_walk_path.side_effect = [None, path]
        self.assertTrue(await self.remaining(avoid=[box(50, -100, 600, 100)]))
        self.assertEqual(self.grid.find_walk_path.call_count, 2)
        self.assertEqual(self.grid.find_walk_path.call_args.kwargs['avoid'], [])
        self.assertTrue(self.client.goto.await_count)

    async def test_actual_blocked_steps_are_never_relaxed(self):
        self.grid.find_walk_path.return_value = None
        self.assertFalse(await self.remaining(avoid=[box(100, -100, 600, 100)],
                                               blocked_steps=[XYZ(100, 0, 0)]))
        self.assertTrue(self.grid.find_walk_path.call_args.kwargs['avoid'][0].contains(Point(100, 0)))
        self.client.goto.assert_not_awaited()

    async def test_no_path_is_failure_not_zero_waypoint_success(self):
        self.grid.find_walk_path.return_value = None
        self.assertFalse(await self.remaining())
        self.client.goto.assert_not_awaited()

    async def test_close_xy_on_wrong_floor_is_not_success(self):
        self.pos = XYZ(500, 0, 600)
        self.grid.find_walk_path.return_value = None
        self.assertFalse(await self.remaining())

    async def test_zone_change_stops_remaining_walk(self):
        async def enter(x, y):
            self.zone = 'Test/Interior'
        self.client.goto.side_effect = enter
        self.assertTrue(await self.remaining())
        self.assertEqual(self.client.goto.await_count, 1)

    async def test_dialogue_or_battle_stops_remaining_walk(self):
        async def interrupt(x, y):
            self.free = False
        self.client.goto.side_effect = interrupt
        self.assertTrue(await self.remaining())
        self.assertEqual(self.client.goto.await_count, 1)

    async def test_walk_timeout_cancels_pending_input(self):
        cancelled = []
        async def wait_forever(x, y):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        self.client.goto.side_effect = wait_forever
        with patch.object(tm, '_WALK_TIMEOUT', 0.01):
            self.assertFalse(await self.remaining())
        self.assertEqual(cancelled, [True])

    async def test_external_cancellation_is_not_swallowed(self):
        self.client.goto.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.remaining()

    async def test_entire_walk_has_a_time_budget(self):
        self.client.goto.side_effect = self.slow_walk
        self.assertFalse(await self.remaining())
        self.assertLessEqual(self.client.goto.await_count, 2)

    async def slow_walk(self, x, y):
        self.now += 13
        await self.walk(x, y)

    async def test_small_underwalk_gets_only_two_attempts(self):
        self.grid.find_walk_path.return_value = [(0, 0, 0), (400, 0, 0)]
        async def underwalk(x, y):
            self.pos = XYZ(self.pos.x + 200, y, 0)
        self.client.goto.side_effect = underwalk
        self.assertTrue(await self.remaining())
        self.assertEqual(self.client.goto.await_count, 2)

    async def test_recovery_skips_rejected_candidates_then_walks(self):
        state = self.state()
        with patch.object(tm, '_teleport_once_verified', AsyncMock(side_effect=[False, False, True])) as tp, \
                patch.object(tm, '_walk_remaining_to_target', AsyncMock(return_value=True)) as foot:
            self.assertTrue(await tm._recover_near_target(
                self.client, state['target'], object(), self.zone, 55, state
            ))
        self.assertEqual(tp.await_count, 3)
        self.assertEqual(foot.await_count, 1)
        self.assertEqual(len(state['landings']), 3)
        self.assertEqual(state['retry_after'], 0)

    async def test_recovery_attempts_are_bounded_and_then_cool_down(self):
        state = self.state()
        with patch.object(tm, '_teleport_once_verified', AsyncMock(return_value=True)) as tp, \
                patch.object(tm, '_walk_remaining_to_target', AsyncMock(return_value=False)) as foot:
            self.assertFalse(await tm._recover_near_target(
                self.client, state['target'], object(), self.zone, 55, state
            ))
        self.assertEqual(tp.await_count, 3)
        self.assertEqual(foot.await_count, 3)
        self.assertEqual(state['retry_after'], 40)

    async def test_no_candidate_does_not_teleport_blindly(self):
        state = self.state()
        self.grid.find_approach_paths.return_value = []
        with patch.object(tm, '_teleport_once_verified', AsyncMock()) as tp:
            self.assertFalse(await tm._recover_near_target(
                self.client, state['target'], object(), self.zone, 55, state
            ))
        tp.assert_not_awaited()
        self.assertEqual(state['retry_after'], 40)

    async def test_two_failures_switch_to_alternate_approach(self):
        self.patch_collision()
        await tm.collision_tp(self.client)
        await tm.collision_tp(self.client)
        self.assertEqual(self.client._collision_tp_approach['failures'], 2)
        self.assertEqual(self.tp.await_count, 2)
        await tm.collision_tp(self.client)
        self.recovery.assert_awaited_once()
        self.assertEqual(self.tp.await_count, 2)
        self.assertIsNone(self.client._collision_tp_approach)

    async def test_strict_landing_with_no_walk_progress_counts_failure(self):
        self.patch_collision()
        self.solve.side_effect = [(XYZ(500, 0, 0), 'target_clear'), (XYZ(150, 0, 0), 'grid_node_strict')]
        self.tp.side_effect = [False, True]
        self.stack.enter_context(patch.object(cm, 'blocking_volumes_at', return_value=[]))
        await tm.collision_tp(self.client)
        self.assertEqual(self.client._collision_tp_approach['failures'], 1)
        self.assertEqual(len(self.client._collision_tp_approach['landings']), 2)

    async def test_cooldown_skips_repeated_same_target_movement(self):
        self.patch_collision()
        self.client._collision_tp_approach = self.state(retry_after=40)
        await tm.collision_tp(self.client)
        self.solve.assert_not_called()
        self.tp.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_cooldown_expiry_retries_fresh(self):
        self.patch_collision()
        self.client._collision_tp_approach = self.state(retry_after=5)
        await tm.collision_tp(self.client)
        self.recovery.assert_not_awaited()
        self.tp.assert_awaited_once()
        self.assertEqual(self.client._collision_tp_approach['failures'], 1)

    async def test_new_target_clears_old_blocked_state_immediately(self):
        self.patch_collision()
        self.client._collision_tp_approach = self.state(retry_after=40, blocked_steps=[XYZ(100, 0, 0)])
        await tm.collision_tp(self.client, XYZ(800, 0, 0))
        self.tp.assert_awaited_once()
        self.recovery.assert_not_awaited()
        self.assertEqual(self.client._collision_tp_approach['blocked_steps'], [])

    async def test_zone_change_clears_cooldown_immediately(self):
        self.patch_collision()
        self.client._collision_tp_approach = self.state(zone='Old/Zone', retry_after=40)
        await tm.collision_tp(self.client)
        self.tp.assert_awaited_once()
        self.assertEqual(self.client._collision_tp_approach['zone'], self.zone)

    async def test_normal_success_does_not_run_recovery(self):
        self.patch_collision()
        self.foot.return_value = True
        await tm.collision_tp(self.client)
        self.assertIsNone(self.client._collision_tp_approach)
        self.recovery.assert_not_awaited()
        self.assertEqual(self.foot.await_args.kwargs['goal_radius'], 5)

    async def test_missing_geometry_keeps_existing_navmap_fallback(self):
        self.patch_collision()
        self.solve.return_value = (None, 'no_mesh')
        await tm.collision_tp(self.client)
        self.navmap.assert_awaited_once_with(self.client, None, None)
        self.recovery.assert_not_awaited()

    async def test_nonfree_client_does_not_move(self):
        self.patch_collision()
        self.free = False
        self.client._collision_tp_approach = self.state()
        await tm.collision_tp(self.client)
        self.assertIsNone(self.client._collision_tp_approach)
        self.tp.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
