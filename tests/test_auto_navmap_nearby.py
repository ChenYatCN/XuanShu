"""Automatic navigation reuses finite nearby landing search before walking."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src import teleport_math as movement
import tests.test_walk_navmap_fallback as fixtures

class AutoNavmapNearbyTests(unittest.IsolatedAsyncioTestCase):
    sleep = fixtures.WalkNavmapFallbackTests.sleep
    solve = fixtures.WalkNavmapFallbackTests.solve
    plan = fixtures.WalkNavmapFallbackTests.plan
    teleport = fixtures.WalkNavmapFallbackTests.teleport
    goto = fixtures.WalkNavmapFallbackTests.goto

    def setUp(self):
        fixtures.WalkNavmapFallbackTests.setUp(self)
        self.position = XYZ(0, 0, 0)
        self.target = self.quest_target = self.safe = self.first_solve = XYZ(7300, 0, 0)
        self.zone = 'Novus/NV_Z01_Conatus'
        self.near = XYZ(6919, 0, 0)
        self.walk_gaps = []
        self.stack.enter_context(patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=AsyncMock(return_value=b'nav')))))
        self.stack.enter_context(patch('src.teleport_math.parse_nav_data', return_value=([XYZ(6538, 0, 0)], [])))

        async def walk(*args, **kwargs):
            self.walk_gaps.append(movement.calc_Distance(self.position, self.target))
            self.position = self.target
            return True
        self.real_walk = movement._walk_remaining_to_target
        self.walk = self.stack.enter_context(patch('src.teleport_math._walk_remaining_to_target', AsyncMock(side_effect=walk)))
        self.accept_near = True
        self.partial_far = False

        async def teleport(dest):
            self.events.append(('tp', dest))
            if self.accept_near and movement.calc_Distance(dest, self.near) < 1:
                self.position = dest
            elif self.partial_far:
                self.position = XYZ(1000, 0, 0)
        self.client.teleport = teleport

    async def test_far_rejection_reuses_navmap_candidate_then_short_walk(self):
        result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.walk_gaps, [381])
        self.assertEqual(result, 'ready')
        self.assertTrue(any(('[navmap_tp]' in m for m in self.messages)))
        self.assertTrue(any((kind == 'tp' and movement.calc_Distance(point, self.near) < 1 for kind, point in self.events)))

    async def test_all_landings_rejected_exits_once_without_long_walk(self):
        self.accept_near = False
        result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'failed')
        self.walk.assert_not_awaited()
        self.assertTrue(any(('[navmap_tp]' in m for m in self.messages)))
        attempts = len(self.events)
        self.assertLessEqual(attempts, 14)
        for _ in range(2):
            await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.client._quest_navmap_recovery['attempts'], 3)
        inputs = len(self.events)
        self.assertEqual(inputs, attempts)  # Known ineffective candidates are not replayed.
        for _ in range(3):
            self.assertEqual(await movement.collision_tp(self.client, self.target), 'exhausted')
        self.assertEqual(len(self.events), inputs)
        self.walk.assert_not_awaited()

    async def test_partial_far_landing_continues_nearby_search(self):
        self.partial_far = True
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.walk_gaps, [381])

    async def test_missing_interaction_identity_does_not_disable_fallback(self):
        with patch('src.teleport_math._movement_interaction_signature', AsyncMock(return_value=None)):
            await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.walk_gaps, [381])

    async def test_task_zone_and_state_changes_stop_old_nav_candidate(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle', 'dialogue', 'control'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.messages.clear()
                self.walk.reset_mock()
                self.client._quest_tp_rejected = self.client._quest_tp_landing = None
                self.client._quest_navmap_recovery = None
                self.position = XYZ(0, 0, 0)
                self.quest_target = self.target
                self.zone = 'Novus/NV_Z01_Conatus'
                self.quest_id, self.goal_id = (12, 34)
                self.loading = self.battle = self.dialogue = False
                changed_control = False
                actual_control = movement._movement_control_changed

                async def control(client, expected):
                    return changed_control or await actual_control(client, expected)

                async def read(_):
                    nonlocal changed_control
                    if cause == 'quest':
                        self.quest_id += 1
                    elif cause == 'goal':
                        self.goal_id += 1
                    elif cause == 'target':
                        self.quest_target = XYZ(7400, 0, 0)
                    elif cause == 'zone':
                        self.zone = 'Novus/NV_Z02_StoneOfHeaven'
                    elif cause == 'control':
                        changed_control = True
                    else:
                        setattr(self, cause, True)
                    return b'nav'
                with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=read))), patch('src.teleport_math._movement_control_changed', side_effect=control):
                    result = await movement.collision_tp(self.client, self.target)
                self.assertEqual(result, 'changed' if cause in ('quest', 'goal', 'target', 'zone') else 'blocked')
                self.assertFalse(any((kind == 'tp' and movement.calc_Distance(point, self.near) < 1 for kind, point in self.events)))
                self.walk.assert_not_awaited()

    async def test_cancel_during_nav_read_stops_old_movement(self):
        started = asyncio.Event()

        async def read(_):
            started.set()
            await asyncio.Event().wait()
        with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=read))):
            task = asyncio.create_task(movement.collision_tp(self.client, self.target))
            try:
                await asyncio.wait_for(started.wait(), 0.3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.walk.assert_not_awaited()
        self.assertFalse(any((kind == 'tp' and movement.calc_Distance(point, self.near) < 1 for kind, point in self.events)))

    async def test_manual_navmap_keeps_partial_landing_behavior(self):
        self.accept_near = False
        self.partial_far = True
        await movement.navmap_tp(self.client, self.target)
        self.assertEqual(self.walk_gaps, [6300])
        self.assertEqual(len(self.events), 1)

    async def test_real_final_walk_uses_actual_near_landing(self):
        self.walk.side_effect = self.real_walk
        self.avoid = []
        self.path = [tuple(self.near), (7200, 0, 0), tuple(self.target)]
        result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'ready')
        self.assertLessEqual(movement.calc_Distance(self.position, self.target), movement._WALK_GAP_MIN)
        walked = [point for kind, point in self.events if kind == 'walk']
        self.assertTrue(walked)
        for x, y in walked:
            self.assertGreaterEqual(x, self.near.x)
            self.assertLessEqual(x, self.target.x)
            self.assertEqual(y, 0)
        self.assertEqual(walked[-1], (self.target.x, self.target.y))

    async def test_original_task_guard_is_kept_across_navmap_entry(self):
        actual = movement.navmap_tp

        async def switch(*args, **kwargs):
            self.goal_id += 1
            return await actual(*args, **kwargs)
        with patch('src.teleport_math.navmap_tp', AsyncMock(side_effect=switch)):
            result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'changed')
        self.assertEqual(len(self.events), 6)
        self.walk.assert_not_awaited()

    async def test_old_exhausted_far_failure_still_gets_one_navmap_search(self):
        task = await movement._movement_task_snapshot(self.client)
        self.client._quest_tp_rejected = dict(key=(self.zone, (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)), walks=3, anchor=self.position, origin=self.position, ready=False, announced=False, rejected=True, attempted=True, origin_signature=None, control=None)
        result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'ready')
        self.assertEqual(self.walk_gaps, [381])
        self.assertEqual(len(self.events), 2)

    async def test_xy_near_wrong_floor_does_not_end_automatic_search(self):
        async def wrong_floor(dest):
            self.events.append(('tp', dest))
            self.position = XYZ(self.target.x, self.target.y, self.position.z + 100)
        self.position = XYZ(0, 0, 900)
        self.client.teleport = wrong_floor
        result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(result, 'failed')
        self.walk.assert_not_awaited()
        self.assertEqual(len(self.events), 9)
        self.assertGreaterEqual(movement.calc_Distance(self.position, self.target), 750)
