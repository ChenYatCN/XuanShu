"""Ordinary auto quest TP reuses manual navmap TP and verifies real feedback."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from shapely.geometry import box
from wizwalker import XYZ
from src import collision_math as cm, questing as q, teleport_math as tm
from src.automation_ownership import get_client_automation_ownership

REAL_VERIFIED_TELEPORT = tm._teleport_once_verified
REAL_SLEEP = asyncio.sleep


class QuestFinalApproachTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000, 0, 0)
        self.position = XYZ(0, 0, 0)
        self.zone = 'World/Zone'
        self.quest = 42
        self.goal = 7
        self.objective = 'Go to the door'
        self.loading = False
        self.battle = False
        self.dialogue = False
        self.interaction = False
        self.strict_position = XYZ(865, 0, 0)
        self.client = SimpleNamespace(title='p1', questing_status=True,
            refilling_potions=False, quest_recovery_owner=None,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.quest),
            goal_id=AsyncMock(side_effect=lambda: self.goal),
            is_loading=AsyncMock(side_effect=lambda: self.loading),
            in_battle=AsyncMock(side_effect=lambda: self.battle),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=AsyncMock(), send_key=AsyncMock())
        async def walk(x, y):
            self.position = XYZ(x, y, 0)
        self.client.goto = AsyncMock(side_effect=walk)
        self.quester = q.Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(
            side_effect=lambda client: (self.quest, self.goal, self.objective))
        self.quester.quest_interaction_ready = AsyncMock(side_effect=lambda *a: self.interaction)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        free = lambda _: not (self.loading or self.battle or self.dialogue)
        self.stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(side_effect=free)))
        self.stack.enter_context(patch.object(tm, 'is_free', AsyncMock(side_effect=free)))
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'')))
        self.stack.enter_context(patch('src.collision.CollisionWorld', Mock()))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        self.stack.enter_context(patch.object(tm, '_resolve_player_radius', AsyncMock(return_value=45)))
        self.solve = self.stack.enter_context(patch.object(cm, 'find_walkable_teleport_point',
            side_effect=lambda *a, **kw: (self.strict_position, 'strict') if kw['strict']
            else (self.target, 'target_clear')))
        self.tp = self.stack.enter_context(patch.object(tm, '_teleport_once_verified',
                                                    AsyncMock(side_effect=self.land)))
        self.stack.enter_context(patch.object(tm, '_retreat_toward', AsyncMock(return_value=False)))
        self.blockers = self.stack.enter_context(patch.object(cm, 'blocking_volumes_at',
                                                             return_value=[box(930, -40, 1100, 40)]))
        self.grid = Mock()
        self.grid.to_hex.return_value = (0, 0)
        self.grid.walk_z.return_value = 0
        self.grid.find_walk_path.side_effect = lambda *a: [
            (self.position.x, self.position.y, self.position.z), (self.target.x, self.target.y, self.target.z)]
        self.stack.enter_context(patch.object(cm, 'get_walk_grid', return_value=self.grid))
        self.navmap = self.stack.enter_context(patch.object(q, 'navmap_tp', AsyncMock(wraps=tm.navmap_tp)))
        async def teleport(point):
            self.position = point
        self.client.teleport.side_effect = teleport
        self.wad = self.stack.enter_context(patch.object(tm, 'load_wad', AsyncMock(
            return_value=SimpleNamespace(get_file=AsyncMock(return_value=b'nav')))))
        self.vertices = [XYZ(700, 0, 0), XYZ(800, 100, 0), XYZ(800, -100, 0)]
        self.navdata = self.stack.enter_context(patch.object(tm, 'parse_nav_data',
            return_value=(self.vertices, [(0, 1), (0, 2)])))
        self.stack.enter_context(patch.object(tm, '_WALK_TIMEOUT', .02))
        async def tick(seconds):
            await REAL_SLEEP(0)
        self.stack.enter_context(patch.object(tm.asyncio, 'sleep', side_effect=tick))
        self.log = self.stack.enter_context(patch.object(q, 'logger'))

    async def land(self, client, dest, *args):
        if dest is self.target:
            return False
        self.position = dest
        return True

    async def move(self):
        await self.quester.move_until_quest_interaction(self.client, self.target)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_direct_auto_tp_calls_the_exact_manual_navmap_implementation(self):
        await self.move()
        self.navmap.assert_awaited_once_with(self.client, self.target)
        self.client.teleport.assert_awaited_once_with(self.target)
        self.client.goto.assert_not_awaited()
        self.tp.assert_not_awaited()
        self.assertLessEqual(q.calc_Distance(self.position, self.target), tm._QUEST_POINT_TOLERANCE)

    async def test_rejected_direct_tp_uses_navmap_landing_then_existing_walk(self):
        async def teleport(point):
            if point != self.target:
                self.position = point
        self.client.teleport.side_effect = teleport
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)
        self.tp.assert_not_awaited()
        self.assertLessEqual(q.calc_Distance(self.position, self.target), tm._QUEST_POINT_TOLERANCE)

    async def test_missing_navdata_uses_the_real_manual_spiral_fallback(self):
        self.wad.side_effect = ValueError('no nav')
        attempts = []
        async def teleport(point, **kwargs):
            attempts.append(point)
            if len(attempts) > 1:
                self.position = point
        self.client.teleport.side_effect = teleport
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(len(attempts), 2)
        self.client.send_key.assert_any_await(q.Keycode.A, .05)
        self.client.send_key.assert_any_await(q.Keycode.D, .05)
        self.tp.assert_not_awaited()

    async def test_104u_real_safe_landing_is_observed_without_duplicate_approach(self):
        async def teleport(point):
            self.position = XYZ(point.x - 104, point.y, point.z)
        self.client.teleport.side_effect = teleport
        for _ in range(3):
            self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.navmap.assert_awaited_once_with(self.client, self.target)
        self.client.goto.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assertIn(id(self.client), self.quester._quest_movement_waiting)

    async def test_1u_near_point_uses_finite_feedback_wait_without_repeat_tp(self):
        self.position = XYZ(999, 0, 0)
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        for _ in range(2):
            self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.navmap.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_6u_valid_x_is_a_handoff_without_exact_coordinate_requirement(self):
        self.position = XYZ(994, 0, 0)
        self.interaction = True
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.navmap.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.assertEqual(self.quester._quest_movement_waiting, {})

    async def test_far_unchanged_position_is_failure_despite_normal_return(self):
        self.navmap.side_effect = None
        self.navmap.return_value = None
        for _ in range(3):
            self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.navmap.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.log.warning.assert_called_once()

    async def test_real_rejected_nav_landings_use_manual_spiral_and_validate_failure(self):
        self.client.teleport.side_effect = None
        with patch.object(tm, 'fallback_spiral_tp', AsyncMock()) as spiral:
            self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.client.teleport.await_count, 3)
        spiral.assert_awaited_once_with(self.client, self.target)
        self.client.goto.assert_not_awaited()

    async def test_real_navmap_walk_that_stays_at_104u_is_not_success_or_walked_twice(self):
        async def teleport(point):
            if point != self.target:
                self.position = XYZ(self.target.x - 104, 0, 0)
        self.client.teleport.side_effect = teleport
        self.client.goto.side_effect = None
        self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)
        self.navmap.assert_awaited_once_with(self.client, self.target)

    async def test_new_progress_and_coordinates_release_the_old_waiting_marker(self):
        self.navmap.side_effect = None
        self.navmap.return_value = None
        await self.move()
        self.objective = 'Go to the door (1/2)'
        self.target = XYZ(2000, 0, 0)
        self.navmap.side_effect = tm.navmap_tp
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.navmap.await_count, 2)
        self.assertEqual(self.position, self.target)

    async def test_state_or_target_change_cancels_old_navmap_and_releases_owner(self):
        for event in ('quest', 'goal', 'progress', 'target', 'zone', 'loading', 'battle',
                      'dialogue', 'interaction', 'probe', 'stop', 'sync', 'recovery',
                      'postcombat', 'mainline', 'rescue', 'restart'):
            with self.subTest(event=event):
                self.position = XYZ(0, 0, 0)
                self.zone, self.quest, self.goal = 'World/Zone', 42, 7
                self.objective = 'Go to the door'
                self.loading = self.battle = self.dialogue = self.interaction = False
                self.client.questing_status = True
                self.client.quest_party_hitters = []
                self.client.quest_party_probe_pending = False
                self.client.quest_party_target_sync_active = False
                self.client.quest_recovery_owner = None
                self.client.post_combat_movement_active = False
                self.client.mainline_chain_retry_active = False
                self.client.quest_party_battle_rescue_active = False
                self.client.quest_party_quest_worker_restart_requested = False
                self.target = XYZ(1000, 0, 0)
                self.quester._quest_movement_waiting.clear()
                cancelled = []
                async def pending(client, xyz):
                    if event == 'quest': self.quest = 99
                    elif event == 'goal': self.goal = 8
                    elif event == 'progress': self.objective = 'Go to the door (1/2)'
                    elif event == 'target': self.target = XYZ(2000, 0, 0)
                    elif event == 'zone': self.zone = 'World/Next'
                    elif event == 'loading': self.loading = True
                    elif event == 'battle': self.battle = True
                    elif event == 'dialogue': self.dialogue = True
                    elif event == 'interaction': self.interaction = True
                    elif event == 'probe':
                        self.client.quest_party_hitters = [object()]
                        self.client.quest_party_probe_pending = True
                    elif event == 'stop': self.client.questing_status = False
                    elif event == 'sync': self.client.quest_party_target_sync_active = True
                    elif event == 'recovery': self.client.quest_recovery_owner = 'existing-recovery'
                    elif event == 'postcombat': self.client.post_combat_movement_active = True
                    elif event == 'mainline': self.client.mainline_chain_retry_active = True
                    elif event == 'rescue': self.client.quest_party_battle_rescue_active = True
                    elif event == 'restart': self.client.quest_party_quest_worker_restart_requested = True
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.append(True)
                self.navmap.side_effect = pending
                await self.move()
                self.assertEqual(cancelled, [True])
                self.client.goto.assert_not_awaited()
                self.client.send_key.assert_not_awaited()

    async def test_changes_during_hud_read_or_input_lock_prevent_old_navmap(self):
        for event in ('hud', 'lock'):
            with self.subTest(event=event):
                self.goal = 7
                self.navmap.reset_mock()
                async def change(*args):
                    self.goal = 8
                    return (self.quest, self.goal, self.objective)
                target_method = '_dungeon_quest_snapshot' if event == 'hud' else '_note_quest_x_lock_wait'
                with patch.object(self.quester, target_method, AsyncMock(side_effect=change)):
                    self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
                self.navmap.assert_not_awaited()

    async def test_movement_timeout_is_bounded_and_does_not_restart_same_move(self):
        cancelled = []
        async def pending(*args):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        real_timeout = asyncio.timeout
        self.navmap.side_effect = pending
        with patch.object(q.asyncio, 'timeout', side_effect=lambda seconds: real_timeout(.02)):
            self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(cancelled, [True])
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.navmap.assert_awaited_once()

    async def test_normal_cancellation_propagates_and_drains_navmap(self):
        started = asyncio.Event()
        cancelled = []
        async def pending(*args):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        self.navmap.side_effect = pending
        task = asyncio.create_task(self.move())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(cancelled, [True])
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_existing_collision_helper_still_records_special_scene_near_arrival(self):
        self.position = XYZ(999, 0, 0)
        result = {}
        await tm.collision_tp(self.client, self.target, approach_result=result)
        self.assertTrue(result['landed'])
        self.assertFalse(result['walk_attempted'])
        self.navmap.assert_not_awaited()

    async def test_manual_and_automatic_rejected_tp_take_the_same_native_fallback(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'navmap_teleport')
        ns = dict(q.__dict__, wizwalker=__import__('wizwalker'), gather_owned=q.gather_owned)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), ns)
        paths = []
        async def teleport(point):
            if point != self.target:
                self.position = point
        self.client.teleport.side_effect = teleport
        for move in (lambda: ns['navmap_teleport'](self.client, []),
                     lambda: self.quester.teleport_to_quest_target(self.client, self.target)):
            self.position = XYZ(0, 0, 0)
            self.client.teleport.reset_mock()
            self.client.goto.reset_mock()
            await move()
            paths.append(([(call.args[0].x, call.args[0].y, call.args[0].z)
                           for call in self.client.teleport.await_args_list],
                          [call.args for call in self.client.goto.await_args_list]))
        self.assertEqual(paths[0], paths[1])
        self.assertLessEqual(q.calc_Distance(self.position, self.target), tm._QUEST_POINT_TOLERANCE)
