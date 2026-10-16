"""Waypoint input, bounded stall confirmation and original task-operation handoff."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Client, XYZ, Keycode
from wizwalker import utils as native_input
from src import teleport_math as movement
from src.questing import Quester
from src.automation_ownership import ClientAutomationOwnership, get_client_automation_ownership
from tests import test_walk_navmap_fallback as fixtures
from tests import test_interaction_a_search as interactions
from src.paths import npc_range_path

REAL_SLEEP = fixtures.REAL_SLEEP


class WalkGotoTests(unittest.IsolatedAsyncioTestCase):
    sleep = fixtures.WalkNavmapFallbackTests.sleep
    solve = fixtures.WalkNavmapFallbackTests.solve
    plan = fixtures.WalkNavmapFallbackTests.plan
    teleport = fixtures.WalkNavmapFallbackTests.teleport
    goto = fixtures.WalkNavmapFallbackTests.goto

    def setUp(self):
        fixtures.WalkNavmapFallbackTests.setUp(self)
        self.position = self.safe
        self.reject_first = False
        self.first_solve = self.safe
        self.avoid = []
        self.client.questing_status = True

    async def walk(self):
        return await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55)

    async def test_walk_uses_safe_waypoint_instead_of_two_tick_microsteps(self):
        origin = self.position
        self.assertTrue(await self.walk())
        walks = [point for kind, point in self.events if kind == 'walk']
        self.assertEqual(walks, [(self.target.x, self.target.y)])
        self.assertGreater(movement.calc_Distance(origin, self.position), 120)

    async def test_delayed_position_publication_is_observed_without_new_input(self):
        clock = 0
        pending = None
        async def sleep(delay):
            nonlocal clock
            clock += delay
            await REAL_SLEEP(0)
        async def goto(x, y):
            nonlocal pending
            self.events.append(('walk', (x, y)))
            pending = (clock + .2, XYZ(x, y, self.target.z))
        def position():
            if pending and clock >= pending[0]:
                self.position = pending[1]
            return self.position
        self.client.goto = goto
        self.client.body.position.side_effect = position
        with patch('src.teleport_math.asyncio.sleep', sleep):
            self.assertTrue(await self.walk())
        self.assertEqual(sum(kind == 'walk' for kind, _ in self.events), 1)

    async def test_suspected_stall_has_one_waypoint_recovery_and_continues_on_progress(self):
        original = self.client.goto
        first = True
        async def goto(x, y):
            nonlocal first
            if first:
                first = False
                self.events.append(('suspect', (x, y)))
            else:
                await original(x, y)
        self.client.goto = goto
        self.assertTrue(await self.walk())
        self.assertEqual(len(self.events), 2)

    async def test_two_confirmed_no_progress_inputs_stop_with_finite_observation(self):
        self.goto_moves = False
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            await REAL_SLEEP(0)
        with patch('src.teleport_math.asyncio.sleep', sleep):
            self.assertFalse(await self.walk())
        self.assertEqual(len(self.events), 2)
        self.assertLessEqual(sum(sleeps), 1.1)
        self.assertTrue(all(delay <= .1 for delay in sleeps))

    async def test_running_waypoint_safety_changes_cancel_and_release_input(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle',
                      'dialogue', 'stop', 'control', 'owner', 'owner_replaced', 'refill'):
            with self.subTest(cause=cause):
                self.position = self.safe
                self.quest_target = self.target
                self.quest_id, self.goal_id = 141863388597850472, 2559431
                self.zone = 'Polaris/PL_Z04_UrvilleStation'
                self.loading = self.battle = self.forced_dialogue = False
                self.client.questing_status = True
                self.client.refilling_potions = False
                owner = get_client_automation_ownership(self.client)
                owner._owner_task = None
                owner._owner_labels = []
                started, released = asyncio.Event(), asyncio.Event()
                input_tasks = []
                controlled = False
                actual = movement._movement_control_changed
                async def control(client, expected):
                    return controlled or await actual(client, expected)
                async def goto(x, y):
                    input_tasks.append(asyncio.current_task())
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        released.set()
                self.client.goto = goto
                with patch('src.teleport_math._movement_control_changed', side_effect=control):
                    task = asyncio.create_task(self.walk())
                    try:
                        await asyncio.wait_for(started.wait(), 1)
                        if cause == 'quest': self.quest_id += 1
                        elif cause == 'goal': self.goal_id += 1
                        elif cause == 'target': self.quest_target = XYZ(2000, 0, 0)
                        elif cause == 'zone': self.zone = 'Other/Area'
                        elif cause == 'stop': self.client.questing_status = False
                        elif cause == 'control': controlled = True
                        elif cause == 'owner': owner._owner_task = object()
                        elif cause == 'owner_replaced':
                            self.client._xuanshu_automation_ownership = ClientAutomationOwnership()
                        elif cause == 'refill': self.client.refilling_potions = True
                        elif cause == 'dialogue': self.forced_dialogue = True
                        else: setattr(self, cause, True)
                        self.assertFalse(await asyncio.wait_for(asyncio.shield(task), .4))
                        self.assertTrue(released.is_set())
                    finally:
                        owner._owner_task = None
                        self.client._xuanshu_automation_ownership = owner
                        task.cancel()
                        # Old shielding drains a finite input. Unblock this fixture
                        # rather than leave a stalled task alive after a red test.
                        for child in input_tasks:
                            child.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_releases_running_input_before_return(self):
        started, released = asyncio.Event(), asyncio.Event()
        input_tasks = []
        async def goto(x, y):
            input_tasks.append(asyncio.current_task())
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        self.client.goto = goto
        task = asyncio.create_task(self.walk())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), .4)
            self.assertTrue(released.is_set())
        finally:
            for child in input_tasks:
                child.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_overall_walk_timeout_releases_input_and_returns_failure(self):
        timeout = asyncio.timeout
        released = asyncio.Event()
        async def goto(x, y):
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        self.client.goto = goto
        with patch('src.teleport_math.asyncio.timeout', side_effect=lambda seconds: timeout(.12)) as limit:
            self.assertFalse(await self.walk())
        limit.assert_called_once_with(30)
        self.assertTrue(released.is_set())

    async def test_confirmed_stall_reuses_existing_navmap_budget_across_questers(self):
        self.goto_moves = False
        with patch('src.teleport_math.navmap_tp', AsyncMock()) as nav:
            for _ in range(6):
                Quester(self.client, [self.client], None)  # Worker reconstruction.
                result = await movement.collision_tp(self.client, self.target)
            self.assertEqual(nav.await_count, 3)
            self.assertEqual(result, 'exhausted')
            self.assertEqual(self.client._quest_navmap_recovery['attempts'], 3)

    async def test_native_goto_emits_longer_input_and_waits_for_delayed_position(self):
        """Real installed goto/timer; native send functions are replaced, no game input."""
        self.client.window_handle = 0
        self.client.client_object = SimpleNamespace(
            actor_body=AsyncMock(return_value=self.client.body),
            speed_multiplier=AsyncMock(return_value=0))
        self.client.body.write_yaw = AsyncMock()
        keys = []
        pending = None
        destination = None
        def keydown(handle, key):
            keys.append(key)
        def keyup(handle, key):
            nonlocal pending
            if len(keys) >= 3:
                pending = (asyncio.get_running_loop().time() + .2, destination)
        def position():
            if pending and asyncio.get_running_loop().time() >= pending[0]:
                self.position = pending[1]
            return self.position
        async def goto(x, y):
            nonlocal destination
            destination = XYZ(x, y, self.target.z)
            await Client.goto(self.client, x, y)
        self.client.goto = goto
        self.client.body.position.side_effect = position
        with patch('asyncio.sleep', REAL_SLEEP), \
             patch.object(native_input, '_send_keydown', side_effect=keydown), \
             patch.object(native_input, '_send_keyup', side_effect=keyup):
            self.assertTrue(await self.walk())
        self.assertGreaterEqual(len(keys), 3)
        self.assertTrue(all(key == Keycode.W for key in keys))

    async def test_recovery_does_not_repeat_identical_rejected_navmap_candidates(self):
        self.goto_moves = False
        self.client.teleport = AsyncMock(side_effect=lambda dest: self.events.append(('tp', dest)))
        with patch('src.collision_math.find_walkable_teleport_point', return_value=(None, 'unavailable')), \
             patch('src.teleport_math.load_wad', AsyncMock(side_effect=RuntimeError('no nav'))), \
             patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(7):
                result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'exhausted')
        self.assertEqual(nav.await_count, 3)
        points = [tuple(point) for kind, point in self.events if kind == 'tp']
        self.assertEqual(len(points), len(set(points)))

    async def test_obstructed_landing_switches_to_fresh_navmap_point_then_walks(self):
        self.position = XYZ(1000.157, -16635.018, -1176.275)
        original = self.client.goto
        async def goto(x, y):
            if self.position.x >= 1790:
                await original(x, y)
            else:
                self.events.append(('obstructed', (x, y)))
        async def teleport(point):
            self.events.append(('tp', point))
            if movement.calc_Distance(point, self.target) > 5:
                self.position = point
        self.client.goto, self.client.teleport = goto, teleport
        with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(
                get_file=AsyncMock(return_value=b'nav')))), \
             patch('src.teleport_math.parse_nav_data', return_value=(
                 [XYZ(1800, self.target.y, self.target.z)], [])), \
             patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(10):
                result = await movement.collision_tp(self.client, self.target)
                if result == 'ready':
                    break
        self.assertEqual(result, 'ready')
        self.assertLessEqual(nav.await_count, 3)
        self.assertEqual(tuple(self.position), tuple(self.target))
        self.assertIsNone(self.client._quest_tp_rejected)
        points = [tuple(point) for kind, point in self.events if kind == 'tp']
        self.assertEqual(len(points), len(set(points)))

    async def test_real_native_timer_releases_w_on_safety_stop(self):
        self.client.window_handle = 0
        self.client.client_object = SimpleNamespace(
            actor_body=AsyncMock(return_value=self.client.body),
            speed_multiplier=AsyncMock(return_value=0))
        self.client.body.write_yaw = AsyncMock()
        events = []
        def keydown(handle, key):
            events.append(('down', key))
            self.loading = True
        self.client.goto = lambda x, y: Client.goto(self.client, x, y)
        with patch.object(native_input, '_send_keydown', side_effect=keydown), \
             patch.object(native_input, '_send_keyup', side_effect=lambda h, k: events.append(('up', k))):
            self.assertFalse(await self.walk())
        self.assertEqual(events[-1], ('up', Keycode.W))
        self.assertEqual(sum(kind == 'up' for kind, _ in events), 1)


class TaskOperationTests(unittest.IsolatedAsyncioTestCase):
    sleep = interactions.InteractionASearchTests.sleep
    send_key = interactions.InteractionASearchTests.send_key

    def setUp(self):
        interactions.InteractionASearchTests.setUp(self)
        self.position = XYZ(300, 2000, 3)
        self.safe = XYZ(650, 2000, 3)
        self.client.body.position.side_effect = lambda: self.position
        self.movement_events = []
        self.goto_moves = True
        async def teleport(point):
            self.movement_events.append(('tp', point))
            self.position = point
        async def goto(x, y):
            self.movement_events.append(('walk', XYZ(x, y, 3)))
            if self.goto_moves:
                self.position = XYZ(x, y, 3)
        self.client.teleport, self.client.goto = teleport, goto
        self.stack.enter_context(patch('src.questing.collision_tp', movement.collision_tp))
        self.stack.enter_context(patch('src.teleport_math.is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.teleport_math.is_visible_by_path', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.utils.is_visible_by_path', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.teleport_math._resolve_player_radius', AsyncMock(return_value=55)))
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'fixture')))
        self.stack.enter_context(patch('src.collision.CollisionWorld', return_value=SimpleNamespace(load=lambda _: None)))
        self.stack.enter_context(patch('src.collision_math.find_walkable_teleport_point', return_value=(self.safe, 'fixture')))
        self.stack.enter_context(patch('src.collision_math.blocking_volumes_at', return_value=[]))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        grid = SimpleNamespace(to_hex=lambda *_: (0, 0), walk_z=lambda *_: 3,
            find_walk_path=lambda *_: [tuple(self.safe), tuple(self.target)])
        self.stack.enter_context(patch('src.collision_math.get_walk_grid', return_value=grid))
        self.stack.enter_context(patch('src.collision_math.straight_path_walkable', return_value=True))
        self.quester._recover_door_approach = AsyncMock()

    async def test_tp_then_walk_enters_original_photo_branch_and_only_operation_advances_goal(self):
        self.objective = 'Photomance the tree'
        self.prompt = '按下Z来拍照'
        old_goal = self.goal
        async def photo(client, objective):
            self.assertEqual(tuple(self.position), tuple(self.target))
            self.assertEqual(self.goal, old_goal)  # Coordinates did not complete the task.
            self.movement_events.append(('photo', self.position))
            self.goal += 1
        self.quester.take_photomancy_photo = AsyncMock(side_effect=photo)
        with patch('src.questing.get_quest_name', AsyncMock(return_value=self.objective)):
            await self.quester.auto_quest_solo()
        self.assertEqual([kind for kind, _ in self.movement_events], ['tp', 'walk', 'photo'])
        self.assertEqual(self.goal, old_goal + 1)
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertIsNone(getattr(self.client, '_quest_internal_failure_wait', None))
        self.assertNotIn(Keycode.Z, self.keys)  # No early photo workaround in movement.

    async def test_success_after_failed_walk_clears_matching_failure_and_enters_photo(self):
        self.objective = 'Photomance the tree'
        self.goto_moves = False
        with patch('src.questing.get_quest_name', AsyncMock(return_value=self.objective)), \
             patch('src.teleport_math.navmap_tp', AsyncMock()):
            await self.quester.auto_quest_solo()
            self.assertIsNotNone(self.client._quest_internal_failure_wait)
            self.goto_moves = True
            self.clock += 3
            self.quester.take_photomancy_photo = AsyncMock()
            await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_awaited_once()
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertIsNone(self.client._quest_internal_failure_wait)
        self.assertEqual(self.client._quest_internal_block_reason, '')

    async def test_tp_then_walk_enters_original_x_and_existing_progress_check(self):
        self.objective = 'Use Lever'
        self.prompt = 'Press X to Use'
        def visible(client, path):
            return path == npc_range_path and movement.calc_Distance(self.position, self.target) <= 3
        self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=visible)), \
             patch('src.teleport_math.is_visible_by_path', AsyncMock(side_effect=visible)):
            await self.quester.auto_quest_solo()
        self.assertEqual([kind for kind, _ in self.movement_events], ['tp', 'walk'])
        self.assertEqual(self.keys, [Keycode.X])
        self.assertEqual(self.goal, 8)
        self.assertFalse(self.held)


if __name__ == '__main__':
    unittest.main()
