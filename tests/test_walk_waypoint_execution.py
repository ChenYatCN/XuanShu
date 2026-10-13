"""Exercise the real goto math; only its keyboard transport is simulated."""
import asyncio
from contextlib import ExitStack
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from shapely.geometry import LineString, box
from wizwalker import Client, Keycode, XYZ
from wizwalker.constants import WIZARD_SPEED
from src import teleport_math as movement
from src.collision import CollisionFlag, ProxyType
from src.collision_math import GRID_SPACING
from src.paths import npc_range_path

REAL_SLEEP = asyncio.sleep


class WaypointExecutionTests(unittest.IsolatedAsyncioTestCase):
    metrics = {}

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.position = XYZ(0, 0, 0)
        self.target = XYZ(600, 0, 0)
        self.zone = 'Fixture/' + self._testMethodName
        self.quest = self.goal = 1
        self.speed_bonus = 40
        self.path = [(0, 0, 0), (100, 50, 0), (200, 0, 0),
                     (300, 50, 0), (400, 0, 0), (500, 50, 0), (600, 0, 0)]
        self.shapes, self.calls, self.yaws, self.segments = [], [], [], []
        self.overrun = 0
        self.stale_reads = 0
        self.delay_position = False
        self.refine = True
        self.loading = self.battle = self.dialogue = self.interaction = False
        self.on_step = lambda: None
        floor = SimpleNamespace(proxy=ProxyType.MESH, category_flags=CollisionFlag.WALKABLE,
            vertices=[(-2000,-2000,0),(2000,-2000,0),(2000,2000,0),(-2000,2000,0)],
            faces=[(0,1,2),(0,2,3)], rotation=(1,0,0,0,1,0,0,0,1), location=(0,0,0), scale=1)
        self.world = SimpleNamespace(objects=[floor])
        body = SimpleNamespace(position=AsyncMock(side_effect=self.read_position),
            write_yaw=AsyncMock(side_effect=lambda yaw: self.yaws.append(yaw)))
        obj = SimpleNamespace(actor_body=AsyncMock(return_value=body),
            speed_multiplier=AsyncMock(side_effect=lambda: self.speed_bonus))
        self.client = SimpleNamespace(title='p1', window_handle=0, body=body, client_object=obj,
            quest_id=AsyncMock(side_effect=lambda: self.quest), goal_id=AsyncMock(side_effect=lambda: self.goal),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            zone_name=AsyncMock(side_effect=lambda: self.zone), goto=self.goto)
        self.grid = SimpleNamespace(spacing=GRID_SPACING, to_hex=lambda *_: (0,0),
            walk_z=lambda *_: 0 if self.refine else None,
            find_walk_path=lambda *_args, **_kwargs: self.path)
        self.stack.enter_context(patch('src.collision_math.get_walk_grid', return_value=self.grid))
        self.stack.enter_context(patch('src.collision_math._ZoneWalkGrid', return_value=self.grid))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', side_effect=lambda *_: self.shapes))
        self.stack.enter_context(patch('src.teleport_math.is_free', AsyncMock(
            side_effect=lambda _: not (self.loading or self.battle or self.dialogue))))
        self.stack.enter_context(patch('src.teleport_math.is_visible_by_path', AsyncMock(
            side_effect=lambda _c, path: path == npc_range_path and self.interaction)))
        self.stack.enter_context(patch('wizwalker.client.utils.timed_send_key', self.press))

    def read_position(self):
        if self.stale_reads:
            self.stale_reads -= 1
            return self.previous
        return self.position

    async def goto(self, x, y):
        self.destination = (x, y)
        self.calls.append(self.destination)
        await Client.goto(self.client, x, y)

    async def press(self, _handle, key, seconds):
        self.assertEqual(key, Keycode.W)
        self.assertLessEqual(seconds, .10001)
        self.previous = self.position
        dx, dy = self.destination[0] - self.position.x, self.destination[1] - self.position.y
        distance = math.hypot(dx, dy)
        moved = seconds * WIZARD_SPEED * (1 + self.speed_bonus / 100) + self.overrun
        # Game position readers return float32 coordinates, not accumulating
        # Python doubles (which can push native yaw's acos just outside [-1,1]).
        self.position = XYZ(*struct.unpack('<fff', struct.pack('<fff',
            self.position.x + dx / distance * moved, self.position.y + dy / distance * moved, 0)))
        self.segments.append(LineString([(self.previous.x,self.previous.y),
                                         (self.position.x,self.position.y)]))
        self.stale_reads = 1 if self.delay_position else 0
        self.on_step()
        await REAL_SLEEP(0)

    async def walk(self):
        result = await movement._walk_remaining_to_target(self.client, self.target, self.world, self.zone, 25)
        turns = sum(abs(math.atan2(math.sin(b-a), math.cos(b-a))) > math.radians(5)
                    for a,b in zip(self.yaws,self.yaws[1:]))
        self.metrics[self._testMethodName] = dict(calls=len(self.calls), turns=turns,
            gap=round(math.hypot(self.position.x-self.target.x,self.position.y-self.target.y),3), result=result)
        return result

    async def test_open_route_skips_hex_zigzags_and_start_alignment(self):
        self.path[0] = (-30, 0, 0)
        self.assertTrue(await self.walk())
        self.assertLessEqual(len(self.calls), math.ceil(600/(WIZARD_SPEED*1.4*.1)))
        self.assertTrue(all(x >= 0 and abs(y) < .001 for x,y in self.calls))
        self.assertEqual(self.metrics[self._testMethodName]['turns'], 0)

    async def test_corner_cannot_be_shortcut_through_solid(self):
        self.target = XYZ(400, 0, 0)
        self.shapes = [box(150,-100,250,100)]
        self.path = [(0,0,0),(0,150,0),(100,150,0),(200,150,0),
                     (300,150,0),(400,150,0),(400,0,0)]
        self.assertTrue(await self.walk())
        self.assertGreater(self.metrics[self._testMethodName]['turns'], 0)
        for segment in self.segments:
            self.assertFalse(segment.intersects(self.shapes[0].buffer(25)))

    async def test_mesh_gap_is_not_shortcut(self):
        self.target = XYZ(400,0,0)
        self.path = [(0,0,0),(0,150,0),(400,150,0),(400,0,0)]
        from src import collision_math
        hole = box(150,-100,250,100)
        original = collision_math._walkable_union
        with patch.object(collision_math,'_walkable_union', side_effect=lambda w,z: original(w,z).difference(hole)):
            self.assertTrue(await self.walk())
        for segment in self.segments:
            self.assertFalse(segment.intersects(hole))

    async def test_overshoot_does_not_reverse_to_old_node_or_claim_target(self):
        self.overrun = 6
        result = await self.walk()
        self.assertLess(len(self.calls), 20)
        self.assertEqual(self.metrics[self._testMethodName]['turns'], 0)
        self.assertFalse(result)  # A passed target outside its 3u execution tolerance is unconfirmed.

    async def test_one_stale_position_frame_rechecks_without_new_move(self):
        self.delay_position = True
        self.assertTrue(await self.walk())
        self.assertEqual(self.metrics[self._testMethodName]['turns'], 0)

    async def test_no_shortcut_across_inside_corner_after_overshoot(self):
        self.target = XYZ(400,0,0)
        self.shapes = [box(150,-100,250,100)]
        self.path = [(0,0,0),(0,150,0),(400,150,0),(400,0,0)]
        self.overrun = 6
        await self.walk()
        self.assertLess(len(self.calls), 30)
        for segment in self.segments:
            self.assertFalse(segment.intersects(self.shapes[0].buffer(25)))

    async def test_approach_endpoint_is_not_task_or_target_completion(self):
        self.refine = False
        self.path = [(0,0,0),(200,0,0),(400,0,0)]
        self.assertFalse(await self.walk())
        self.assertEqual(self.quest, 1)
        self.assertGreater(math.hypot(self.position.x-self.target.x,self.position.y-self.target.y),
                           movement._WALK_GAP_MIN)

    async def test_mid_walk_state_change_stops_before_second_call(self):
        for reason in ('quest','goal','target','zone','loading','battle','dialogue','interaction'):
            with self.subTest(reason=reason):
                self.position = XYZ(0,0,0)
                self.calls.clear()
                self.target = XYZ(600,0,0)
                self.loading = self.battle = self.dialogue = self.interaction = False
                def change():
                    if reason in ('quest','goal'): setattr(self, reason, getattr(self,reason)+1)
                    elif reason == 'target': self.target = XYZ(900,0,0)
                    elif reason == 'zone': self.zone += '/Next'
                    else: setattr(self, reason, True)
                self.on_step = change
                self.assertFalse(await self.walk())
                self.assertEqual(len(self.calls), 1)

    async def test_runtime_speed_controls_bounded_step_without_final_radius_change(self):
        self.speed_bonus = 100
        self.assertTrue(await self.walk())
        self.assertLessEqual(len(self.calls), math.ceil(600/GRID_SPACING))
        self.assertEqual(self.position.x, self.target.x)
        self.assertEqual(movement._WALK_GAP_MIN, 120)

    async def test_cancel_drains_only_current_native_goto_then_stops(self):
        started, release, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        durations = []
        async def held_press(_handle, key, seconds):
            self.assertEqual(key, Keycode.W)
            durations.append(seconds)
            started.set()
            try:
                await release.wait()
            finally:
                released.set()
        with patch('wizwalker.client.utils.timed_send_key', held_press):
            task = asyncio.create_task(self.walk())
            try:
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                await REAL_SLEEP(0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(released.is_set())
        self.assertEqual(len(self.calls), 1)
        self.assertLessEqual(durations[0], .10001)


if __name__ == '__main__':
    unittest.main()
