"""Only a proven incomplete collision final walk may add automatic approach input."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from shapely.geometry import box
from wizwalker import XYZ
from src import collision_math as cm, questing as q, teleport_math as tm
from src.automation_ownership import get_client_automation_ownership


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
        self.navmap = self.stack.enter_context(patch.object(q, 'navmap_tp', AsyncMock()))
        self.log = self.stack.enter_context(patch.object(q, 'logger'))

    async def land(self, client, dest, *args):
        if dest is self.target:
            return False
        self.position = dest
        return True

    async def move(self):
        await self.quester.move_until_quest_interaction(self.client, self.target)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_strict_landing_zero_waypoints_walks_from_safe_position_to_original_target(self):
        await self.move()
        self.assertEqual(self.tp.await_count, 2)
        self.client.goto.assert_awaited_once_with(1000, 0)
        self.navmap.assert_not_awaited()
        self.assertEqual(self.position.x, 1000)
        self.assertEqual(self.quester._quest_approach_failed, {})
        self.log.warning.assert_not_called()

    async def test_direct_collision_arrival_keeps_normal_path_without_added_walk_or_navmap(self):
        async def land(client, dest, *args):
            self.position = dest
            return True
        self.tp.side_effect = land
        await self.move()
        self.tp.assert_awaited_once()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.log.info.assert_not_called()

    async def test_existing_final_walk_success_does_not_add_another_walk(self):
        self.blockers.return_value = []
        await self.move()
        self.assertEqual([call.args for call in self.client.goto.await_args_list], [(1000, 0), (1000, 0)])
        self.navmap.assert_not_awaited()
        self.log.info.assert_not_called()

    async def test_near_landing_without_failed_walk_evidence_does_not_trigger(self):
        self.strict_position = XYZ(920, 0, 0)
        await self.move()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_far_failed_walk_is_not_the_near_target_fallback(self):
        self.strict_position = XYZ(100, 0, 0)
        self.grid.find_walk_path.side_effect = None
        self.grid.find_walk_path.return_value = None
        await self.move()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_truncated_walk_within_old_arrival_radius_still_approaches_exact_point(self):
        self.strict_position = XYZ(800, 0, 0)
        self.grid.find_walk_path.side_effect = lambda *a: [(800, 0, 0), (900, 0, 0), (1000, 0, 0)]
        await self.move()
        self.assertEqual([call.args for call in self.client.goto.await_args_list], [(900, 0), (1000, 0)])
        self.navmap.assert_not_awaited()

    async def test_no_path_uses_existing_approach_instead_of_new_pathfinder(self):
        self.grid.find_walk_path.side_effect = None
        self.grid.find_walk_path.return_value = None
        await self.move()
        self.client.goto.assert_awaited_once_with(1000, 0)
        self.navmap.assert_not_awaited()

    async def test_failed_goto_uses_existing_bounded_navmap_reentry_then_finishes(self):
        self.client.goto.side_effect = None
        async def navigate(client, target, *, reenter):
            self.assertIs(client, self.client)
            self.assertIs(target, self.target)
            self.assertTrue(reenter)
            self.position = target
        self.navmap.side_effect = navigate
        await self.move()
        self.client.goto.assert_awaited_once()
        self.navmap.assert_awaited_once_with(self.client, self.target, reenter=True)
        self.log.warning.assert_not_called()

    async def test_two_failed_approaches_warn_once_and_do_not_repeat_tp_for_same_target(self):
        self.client.goto.side_effect = None
        for _ in range(3):
            await self.move()
        self.assertEqual(self.tp.await_count, 2)
        self.client.goto.assert_awaited_once()
        self.navmap.assert_awaited_once()
        self.log.warning.assert_called_once()
        self.assertIn(id(self.client), self.quester._quest_approach_failed)

    async def test_progress_changes_clear_failed_target_and_allow_current_task_move(self):
        self.client.goto.side_effect = None
        await self.move()
        self.objective = 'Go to the door (1/2)'
        async def walk(x, y):
            self.position = XYZ(x, y, 0)
        self.client.goto.side_effect = walk
        await self.move()
        self.assertEqual(self.tp.await_count, 4)
        self.assertEqual(self.quester._quest_approach_failed, {})

    async def test_failure_marker_is_cleared_when_auto_quest_stops(self):
        self.client.goto.side_effect = None
        await self.move()
        self.client.questing_status = False
        await self.move()
        self.assertEqual(self.quester._quest_approach_failed, {})

    async def test_failed_target_marker_never_blocks_existing_recovery_or_shared_movement(self):
        self.client.goto.side_effect = None
        await self.move()
        self.assertIn(id(self.client), self.quester._quest_approach_failed)
        self.quester._dungeon_quest_snapshot.reset_mock()
        with patch.object(q, 'collision_tp', AsyncMock()) as collision:
            for name, value in (('quest_recovery_owner', 'existing_recovery'),
                                ('quest_party_target_sync_active', True)):
                setattr(self.client, name, value)
                await self.move()
                setattr(self.client, name, None if isinstance(value, str) else False)
            self.assertEqual(collision.await_count, 2)
            for call in collision.await_args_list:
                self.assertEqual(call.kwargs, {'leader_client': None})
        self.quester._dungeon_quest_snapshot.assert_not_awaited()

    async def test_probe_sync_and_recovery_flags_do_not_authorize_extra_approach(self):
        for name, value in (('quest_party_probe_pending', True),
                            ('quest_party_target_sync_active', True),
                            ('quest_recovery_owner', 'existing_recovery'),
                            ('potion_dungeon_returned', ('World/Zone', 1, set())),
                            ('quest_party_dungeon_interaction', {'phase': 'transition'})):
            with self.subTest(name=name):
                setattr(self.client, name, value)
                self.position = XYZ(0, 0, 0)
                await self.move()
                setattr(self.client, name, None if isinstance(value, (str, tuple, dict)) else False)
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_npc_prompt_after_collision_is_left_to_existing_interaction(self):
        async def land(client, dest, *args):
            landed = await self.land(client, dest, *args)
            if landed:
                self.interaction = True
            return landed
        self.tp.side_effect = land
        await self.move()
        self.client.goto.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_progress_without_goal_id_change_during_collision_prevents_old_point_walk(self):
        async def land(client, dest, *args):
            landed = await self.land(client, dest, *args)
            if landed:
                self.objective = 'Go to the door (1/2)'
            return landed
        self.tp.side_effect = land
        await self.move()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_unreadable_before_progress_cannot_authorize_fallback(self):
        self.quester._dungeon_quest_snapshot.return_value = None
        self.quester._dungeon_quest_snapshot.side_effect = None
        await self.move()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_follower_collision_call_and_original_source_target_are_unchanged(self):
        hitter = SimpleNamespace(title='p2', questing_status=True, refilling_potions=False,
            zone_name=AsyncMock(side_effect=lambda: self.zone))
        with patch.object(q, 'collision_tp', AsyncMock()) as collision:
            await self.quester.move_until_quest_interaction(hitter, self.target, leader_client=self.client)
        collision.assert_awaited_once_with(hitter, self.target, leader_client=self.client)
        self.navmap.assert_not_awaited()
        self.quester._dungeon_quest_snapshot.assert_not_awaited()

    async def test_normal_collision_is_still_cancelled_and_drained_on_goal_change(self):
        cancelled = []
        async def collision(*args, **kwargs):
            self.goal = 8
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        with patch.object(q, 'collision_tp', AsyncMock(side_effect=collision)):
            await self.move()
        self.assertEqual(cancelled, [True])
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()

    async def test_collision_observation_reports_zero_nodes_without_changing_default_movement(self):
        result = {}
        self.assertIsNone(await tm.collision_tp(self.client, self.target, approach_result=result))
        self.assertEqual(result['waypoints'], 0)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['walk_attempted'])
        self.assertFalse(result['walk_completed'])
        self.assertTrue(result['landed'])
        self.client.goto.assert_not_awaited()
        self.assertFalse(hasattr(self.client, '_collision_tp_approach'))
        self.position = XYZ(0, 0, 0)
        self.assertIsNone(await tm.collision_tp(self.client, self.target))
        self.assertEqual(self.tp.await_count, 4)
        self.client.goto.assert_not_awaited()

    async def test_navigation_reentry_is_cancelled_when_interaction_triggers(self):
        self.client.goto.side_effect = None
        cancelled = []
        async def navigate(*args, **kwargs):
            self.interaction = True
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        self.navmap.side_effect = navigate
        await self.move()
        self.assertEqual(cancelled, [True])
        self.navmap.assert_awaited_once_with(self.client, self.target, reenter=True)
        self.log.warning.assert_not_called()
        self.assertEqual(self.quester._quest_approach_failed, {})

    async def test_both_pending_approaches_are_bounded_cancelled_and_warn_once(self):
        cancelled = []
        async def pending(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        self.client.goto.side_effect = pending
        self.navmap.side_effect = pending
        with patch.object(tm, '_WALK_TIMEOUT', .01), patch.object(tm, '_WALK_TIME_LIMIT', .7):
            await self.move()
        self.assertEqual(cancelled, [True, True])
        self.log.warning.assert_called_once()
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_real_navmap_fallback_lands_then_uses_existing_goto(self):
        walk_calls = []
        async def walk(x, y):
            walk_calls.append((x, y))
            if len(walk_calls) > 1:
                self.position = XYZ(x, y, 0)
        async def teleport(point):
            self.position = point
        self.client.goto.side_effect = walk
        self.client.teleport.side_effect = teleport
        wad = SimpleNamespace(get_file=AsyncMock(return_value=b'nav'))
        vertices = [XYZ(700, 0, 0), XYZ(800, 100, 0), XYZ(800, -100, 0)]
        with patch.object(q, 'navmap_tp', tm.navmap_tp), \
             patch.object(tm, 'load_wad', AsyncMock(return_value=wad)), \
             patch.object(tm, 'parse_nav_data', return_value=(vertices, [(0, 1), (0, 2)])), \
             patch.object(tm, 'fallback_spiral_tp', AsyncMock()) as spiral:
            await self.move()
        self.assertEqual(walk_calls, [(1000, 0), (1000, 0)])
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.position.x, 1000)
        spiral.assert_not_awaited()
        self.log.warning.assert_not_called()

    async def test_state_change_during_goto_cancels_input_and_releases_owner(self):
        for event in ('quest', 'goal', 'objective', 'target', 'zone', 'loading', 'battle',
                      'dialogue', 'interaction', 'probe', 'stop', 'disconnect'):
            with self.subTest(event=event):
                self.position = XYZ(0, 0, 0)
                self.zone, self.quest, self.goal = 'World/Zone', 42, 7
                self.objective, self.loading, self.battle, self.dialogue = 'Go to the door', False, False, False
                self.interaction = False
                self.client.questing_status = True
                self.client.quest_party_probe_pending = False
                self.client.zone_name.side_effect = lambda: self.zone
                self.target = XYZ(1000, 0, 0)
                cancelled = []
                async def pending(x, y):
                    if event == 'quest': self.quest = 99
                    elif event == 'goal': self.goal = 8
                    elif event == 'objective': self.objective = 'Go to the door (1/2)'
                    elif event == 'target': self.target = XYZ(2000, 0, 0)
                    elif event == 'zone': self.zone = 'World/Next'
                    elif event == 'loading': self.loading = True
                    elif event == 'battle': self.battle = True
                    elif event == 'dialogue': self.dialogue = True
                    elif event == 'interaction': self.interaction = True
                    elif event == 'probe': self.client.quest_party_probe_pending = True
                    elif event == 'stop': self.client.questing_status = False
                    elif event == 'disconnect': self.client.zone_name.side_effect = RuntimeError('disconnected')
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.append(True)
                self.client.goto.side_effect = pending
                if event == 'disconnect':
                    with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                        await self.move()
                else:
                    await self.move()
                self.assertEqual(cancelled, [True])
                self.assertFalse(get_client_automation_ownership(self.client).locked)
                self.assertEqual(self.quester._quest_approach_failed, {})
        self.navmap.assert_not_awaited()

if __name__ == '__main__':
    unittest.main()
