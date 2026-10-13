"""Guarded approach walks after rejected quest teleports."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from shapely.geometry import box
from shapely.geometry import LineString
from wizwalker import XYZ
from src import teleport_math as movement
from src.paths import advance_dialog_path, dialog_text_path


REAL_SLEEP = asyncio.sleep


class WalkNavmapFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1318.848, -16964.529, -1078.356)
        self.safe = XYZ(1100.0, -16800.893, -1154.886)
        self.position = XYZ(1000.157, -16635.018, -1176.275)
        self.quest_target = self.target
        self.zone = 'Polaris/PL_Z04_UrvilleStation'
        self.quest_id = 141863388597850472
        self.goal_id = 2559431
        self.loading = self.battle = self.dialogue = self.forced_dialogue = False
        self.reject_first = True
        self.first_solve = self.target
        self.goto_moves = True
        self.fail_navmap = False
        self.path = [(self.safe.x, self.safe.y, self.safe.z),
                     (1150, -16850, -1150), (1200, -16900, -1120),
                     (1250, -16950, -1100), (self.target.x, self.target.y, self.target.z)]
        self.avoid = [box(1120, -17010, 1380, -16810)]
        self.events = []
        self.messages = []
        self.on_plan = lambda: None
        self.on_walk = lambda: None
        self.client = SimpleNamespace(title='p3',
            is_loading=AsyncMock(side_effect=lambda: self.loading),
            in_battle=AsyncMock(side_effect=lambda: self.battle),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.quest_id),
            goal_id=AsyncMock(side_effect=lambda: self.goal_id),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.quest_target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=self.teleport, goto=self.goto)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        sink = logger.add(lambda msg: self.messages.append(str(msg)), level='DEBUG', format='{message}')
        self.addCleanup(logger.remove, sink)
        self.stack.enter_context(patch('src.teleport_math.asyncio.sleep', self.sleep))
        self.stack.enter_context(patch('src.utils.is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: (path == advance_dialog_path and self.dialogue)
            or (path == dialog_text_path and self.forced_dialogue))))
        self.stack.enter_context(patch('src.teleport_math.is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: path == dialog_text_path and self.forced_dialogue)))
        self.stack.enter_context(patch('src.teleport_math._resolve_player_radius', AsyncMock(return_value=55)))
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'fixture')))
        self.stack.enter_context(patch('src.collision.CollisionWorld',
                                      return_value=SimpleNamespace(load=lambda _: None)))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        self.stack.enter_context(patch('src.collision_math.find_walkable_teleport_point',
                                      side_effect=self.solve))
        self.stack.enter_context(patch('src.collision_math.blocking_volumes_at', side_effect=lambda *_: self.avoid))
        grid = SimpleNamespace(to_hex=lambda *_: (0, 0), walk_z=lambda *_: -1100,
                               find_walk_path=self.plan)
        self.grid = grid
        self.stack.enter_context(patch('src.collision_math.get_walk_grid', return_value=grid))
        self.stack.enter_context(patch('src.collision_math._ZoneWalkGrid', return_value=grid))
        self.stack.enter_context(patch('src.collision_math.straight_path_walkable', return_value=True))

    async def sleep(self, delay):
        await REAL_SLEEP(0)

    def solve(self, *args, strict=False, **kwargs):
        return (self.safe if strict else self.first_solve), ('grid_node_strict' if strict else 'target_clear')

    def plan(self, *args):
        self.on_plan()
        return self.path

    async def teleport(self, dest):
        self.events.append(('tp', dest))
        if self.fail_navmap and len(self.events) == 3:
            raise RuntimeError('navmap input failed')
        if not (self.reject_first and len(self.events) == 1):
            self.position = dest

    async def goto(self, x, y):
        self.events.append(('walk', (x, y)))
        if self.goto_moves:
            self.position = XYZ(x, y, self.target.z)
        self.on_walk()

    async def test_blocked_path_does_not_retry_rejected_target_tp(self):
        await movement.collision_tp(self.client, self.target)
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))
        self.assertEqual(self.events, [('tp', self.target), ('tp', self.safe)])

    async def test_normal_collision_tp_does_not_add_navmap_attempt(self):
        self.reject_first = False
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.events, [('tp', self.target)])
        self.assertEqual(movement.calc_Distance(self.position, self.target), 0)
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))

    async def test_normal_remaining_walk_reaches_target_without_navmap(self):
        self.reject_first = False
        self.first_solve = self.safe
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(movement.calc_Distance(self.position, self.target), 0)
        self.assertEqual([e for e in self.events if e[0] == 'tp'], [('tp', self.safe)])
        self.assertTrue(any(e[0] == 'walk' for e in self.events))
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))

    async def test_no_path_stops_without_retrying_rejected_tp(self):
        self.path = None
        await movement.collision_tp(self.client, self.target)
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))
        self.assertEqual(self.events, [('tp', self.target), ('tp', self.safe)])

    async def test_target_not_walkable_walks_to_reachable_approach_point(self):
        self.grid.walk_z = lambda *_: None
        self.avoid = []
        endpoint = XYZ(self.target.x - 150, self.target.y, self.target.z)
        self.path = [(self.safe.x, self.safe.y, self.safe.z), (endpoint.x, endpoint.y, endpoint.z)]
        await movement.collision_tp(self.client, self.target)
        from wizwalker.constants import WIZARD_SPEED
        self.assertLessEqual(movement.calc_Distance(self.position, endpoint), WIZARD_SPEED * .05)
        self.assertEqual([e for e in self.events if e[0] == 'tp'], [('tp', self.target), ('tp', self.safe)])
        self.assertNotIn(('walk', (self.target.x, self.target.y)), self.events)

    async def test_walk_goto_returns_without_moving_stops_without_navmap(self):
        self.reject_first = False
        self.first_solve = self.safe
        self.goto_moves = False
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(movement.calc_Distance(self.position, self.safe), 0)
        self.assertEqual([e for e in self.events if e[0] == 'tp'], [('tp', self.safe)])

    async def test_path_crossing_rejected_collider_does_not_fall_back_to_target_tp(self):
        self.avoid = [box(1240, -17010, 1380, -16910)]
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(len([e for e in self.events if e[0] == 'tp']), 2)
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))

    async def test_task_zone_loading_battle_and_real_dialogue_changes_prevent_fallback(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle', 'dialogue', 'forced_dialogue'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.messages.clear()
                self.client._quest_tp_rejected = None
                self.position = XYZ(1000.157, -16635.018, -1176.275)
                self.quest_target = self.target
                self.zone = 'Polaris/PL_Z04_UrvilleStation'
                self.quest_id, self.goal_id = 141863388597850472, 2559431
                self.loading = self.battle = self.dialogue = self.forced_dialogue = False
                def interrupt():
                    if cause == 'quest':
                        self.quest_id += 1
                    elif cause == 'goal':
                        self.goal_id += 1
                    elif cause == 'target':
                        self.quest_target = XYZ(2000, -17000, -1000)
                    elif cause == 'zone':
                        self.zone = 'NextRoom'
                    else:
                        setattr(self, cause, True)
                self.on_plan = interrupt
                await movement.collision_tp(self.client, self.target)
                self.assertEqual(self.events, [('tp', self.target), ('tp', self.safe)])
                self.assertFalse(any('[navmap_tp]' in m for m in self.messages))

    async def test_zone_change_during_final_task_read_prevents_old_target_tp(self):
        reads = []
        def read_target():
            reads.append(True)
            if len(reads) == 2:
                self.zone = 'NextRoom'
            return self.target
        self.client.quest_position.position.side_effect = read_target
        await movement.collision_tp(self.client, self.target)
        self.assertFalse(self.events)

    async def test_walk_ended_already_within_existing_radius_needs_no_x_or_navmap(self):
        self.path = None
        self.on_plan = lambda: setattr(self, 'position', XYZ(self.target.x + 80, self.target.y, self.target.z))
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.events, [('tp', self.target), ('tp', self.safe)])
        self.assertFalse(any('[navmap_tp]' in m for m in self.messages))

    async def test_repeated_failure_has_a_finite_walk_budget_and_no_new_tp(self):
        self.path = None
        for _ in range(10):
            await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.events, [('tp', self.target), ('tp', self.safe)])
        self.assertLessEqual(sum('no on-foot path' in m for m in self.messages), 3)
        self.assertTrue(any('suppressed' in m for m in self.messages))

    async def test_task_or_zone_change_during_walk_stops_before_next_step(self):
        self.avoid = []
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle', 'dialogue', 'forced_dialogue'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.position = self.safe
                self.quest_target = self.target
                self.zone = 'Polaris/PL_Z04_UrvilleStation'
                self.quest_id, self.goal_id = 141863388597850472, 2559431
                self.loading = self.battle = self.dialogue = self.forced_dialogue = False
                def interrupt():
                    if cause == 'quest':
                        self.quest_id += 1
                    elif cause == 'goal':
                        self.goal_id += 1
                    elif cause == 'target':
                        self.quest_target = XYZ(2000, -17000, -1000)
                    elif cause == 'zone':
                        self.zone = 'NextRoom'
                    else:
                        setattr(self, cause, True)
                self.on_walk = interrupt
                await movement._walk_remaining_to_target(self.client, self.target, object(), self.zone, 55)
                self.assertEqual(len([e for e in self.events if e[0] == 'walk']), 1)

    async def test_new_goal_clears_failed_tp_suppression(self):
        self.path = None
        await movement.collision_tp(self.client, self.target)
        self.goal_id += 1
        self.events.clear()
        self.reject_first = False
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(self.events, [('tp', self.target)])

    async def test_navmap_partial_landing_is_not_target_arrival(self):
        partial = XYZ(self.target.x - 350, self.target.y, self.target.z)
        async def partial_tp(dest):
            self.events.append(('tp', dest))
            self.position = partial
        self.client.teleport = partial_tp
        self.path = None
        with patch('src.teleport_math.load_wad', AsyncMock(side_effect=RuntimeError('no nav data'))), \
             patch('src.teleport_math.fallback_spiral_tp', AsyncMock()) as spiral:
            await movement.navmap_tp(self.client, self.target)
        self.assertTrue(any('not at target' in m for m in self.messages))
        self.assertNotEqual(movement.calc_Distance(self.position, self.target), 0)
        self.assertFalse(any(e[0] == 'walk' for e in self.events))

    async def test_navmap_average_landing_uses_short_guarded_path(self):
        self.position = XYZ(0, 0, 0)
        self.target = self.quest_target = XYZ(1000, 0, 0)
        self.path = [(300, 0, 0), (600, 0, 0), (1000, 0, 0)]
        self.avoid = []
        async def teleport(dest):
            self.events.append(('tp', dest))
            if len(self.events) == 3:
                self.position = dest
        self.client.teleport = teleport
        with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=AsyncMock(return_value=b'nav')))), \
             patch('src.teleport_math.parse_nav_data', return_value=([XYZ(0,0,0), XYZ(300,0,0), XYZ(600,0,0)], [(0,1),(1,0),(1,2),(2,1)])):
            await movement.navmap_tp(self.client, self.target)
        self.assertEqual([e[1].x for e in self.events if e[0] == 'tp'], [1000, 650, 300])
        self.assertEqual(movement.calc_Distance(self.position, self.target), 0)
        self.assertGreater(len([e for e in self.events if e[0] == 'walk']), 3)

    async def test_interaction_prompt_stops_walk_for_existing_handler(self):
        self.avoid = []
        self.position = self.safe
        from src.paths import npc_range_path
        with patch('src.teleport_math.is_visible_by_path', AsyncMock(side_effect=lambda _, path: path == npc_range_path)):
            self.assertFalse(await movement._walk_remaining_to_target(self.client, self.target, object(), self.zone, 55))
        self.assertFalse(self.events)

    async def test_leader_goal_change_stops_hitter_approach(self):
        self.avoid = []
        self.position = self.safe
        leader_goal = [42]
        leader = SimpleNamespace(quest_id=AsyncMock(return_value=12),
            goal_id=AsyncMock(side_effect=lambda: leader_goal[0]),
            zone_name=AsyncMock(return_value=self.zone),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)))
        self.on_walk = lambda: leader_goal.__setitem__(0, 43)
        await movement._walk_remaining_to_target(self.client, self.target, object(), self.zone, 55,
                                                 task_client=leader)
        self.assertEqual(len(self.events), 1)

    async def test_cancellation_drains_only_the_current_short_walk(self):
        self.avoid = []
        self.position = self.safe
        started, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def goto(x, y):
            from wizwalker.constants import WIZARD_SPEED
            self.assertLessEqual(((x-self.position.x)**2 + (y-self.position.y)**2)**.5,
                                 WIZARD_SPEED * .1 + .001)
            started.set()
            await release.wait()
            drained.set()
        self.client.goto = goto
        task = asyncio.create_task(movement._walk_remaining_to_target(self.client, self.target, object(), self.zone, 55))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await REAL_SLEEP(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(drained.is_set())

    async def test_unreadable_fallback_snapshot_does_not_block_normal_collision_success(self):
        self.reject_first = False
        self.client.quest_id.side_effect = RuntimeError('unreadable task snapshot')
        await movement.collision_tp(self.client, self.target)
        self.assertEqual(movement.calc_Distance(self.position, self.target), 0)
        self.assertEqual(self.events, [('tp', self.target)])


class RealWalkGridApproachTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_grid_routes_around_solids_to_unstandable_target(self):
        from src.collision import CollisionFlag, ProxyType
        from src.collision_math import _ZoneWalkGrid
        floor = SimpleNamespace(proxy=ProxyType.MESH, category_flags=CollisionFlag.WALKABLE,
            vertices=[(-1000,-1000,0),(1000,-1000,0),(1000,1000,0),(-1000,1000,0)],
            faces=[(0,1,2),(0,2,3)], rotation=(1,0,0,0,1,0,0,0,1), location=(0,0,0), scale=1)
        world = SimpleNamespace(objects=[floor])
        target = XYZ(0,0,0)
        shapes = [box(-80,-80,80,80), box(-320,-180,-220,180)]
        grid = _ZoneWalkGrid(world, None, shapes, 25)
        self.assertIsNone(grid.walk_z(*grid.to_hex(target.x, target.y)))
        position = [XYZ(-500,0,0)]
        segments = []
        async def goto(x, y):
            previous = position[0]
            segments.append(LineString([(previous.x, previous.y),(x,y)]))
            position[0] = XYZ(x,y,0)
        client = SimpleNamespace(title='p1',
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: position[0])), goto=goto,
            quest_id=AsyncMock(return_value=1), goal_id=AsyncMock(return_value=2),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=target)),
            zone_name=AsyncMock(return_value='Fixture/Approach'))
        with patch('src.teleport_math.is_free', AsyncMock(return_value=True)), \
             patch('src.teleport_math.is_visible_by_path', AsyncMock(return_value=False)), \
             patch('src.entity_collision.build_zone_static_shapes', return_value=shapes), \
             patch('src.collision_math.get_walk_grid', return_value=grid):
            result = await movement._walk_remaining_to_target(client, target, world, 'Fixture/Approach', 25)
        self.assertFalse(result)  # Reaching an approach endpoint is not quest progress.
        self.assertGreater(len(segments), 0)
        self.assertLessEqual(movement.calc_Distance(position[0], target), 300)
        self.assertNotEqual(movement.calc_Distance(position[0], target), 0)
        for segment in segments:
            from wizwalker.constants import WIZARD_SPEED
            self.assertLessEqual(segment.length, WIZARD_SPEED * .1 + .001)
            self.assertFalse(any(segment.intersects(s.buffer(25)) for s in shapes))


if __name__ == '__main__':
    unittest.main()
