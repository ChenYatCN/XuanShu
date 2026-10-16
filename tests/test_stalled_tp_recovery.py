"""No-op solved points must not refresh finite recovery budgets."""
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src import teleport_math as movement
from src.questing import Quester
import tests.test_walk_navmap_fallback as fixtures


class StalledTPRecoveryTests(unittest.IsolatedAsyncioTestCase):
    sleep = fixtures.WalkNavmapFallbackTests.sleep
    solve = fixtures.WalkNavmapFallbackTests.solve
    plan = fixtures.WalkNavmapFallbackTests.plan
    goto = fixtures.WalkNavmapFallbackTests.goto

    def setUp(self):
        fixtures.WalkNavmapFallbackTests.setUp(self)
        self.client.questing_status = True
        self.client.title = 'p1'
        self.zone = 'Arcanum/Interiors/AR_Z01_MusicologyOffice'
        self.origin = self.position = XYZ(50, 86.603, 2.897)
        self.target = self.quest_target = self.first_solve = XYZ(4.963, 180.626, 32.331)
        self.safe = self.origin
        self.avoid = []
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)))
        self.clock = 0
        self.stack.enter_context(patch('src.questing.time.monotonic', lambda: self.clock))
        self.stack.enter_context(patch('src.teleport_math.load_wad', AsyncMock(side_effect=RuntimeError('no nav'))))
        self.client.teleport = self.teleport
        self.reader = Quester(self.client, [self.client], None)
        self.reader.read_quest_txt = AsyncMock(return_value='unchanged objective')
        self.reader._npc_dialogue_visible = AsyncMock(return_value=False)

    async def teleport(self, destination):
        self.events.append(('tp', destination))  # Rejected: position remains unchanged.

    async def test_no_displacement_is_not_a_confirmed_landing_even_at_solved_origin(self):
        record = {}
        self.assertFalse(await movement._arrived(self.client, self.origin, self.origin, self.zone,
                                                 validation_state=record))
        self.assertFalse(record['confirmed'])
        self.assertTrue(record['rejected'])

    async def test_short_rejected_tp_reuses_navmap_and_never_reports_task_ready(self):
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            result = await movement.collision_tp(self.client, self.target)
        self.assertEqual(nav.await_count, 1)
        self.assertIn(result, ('failed', 'exhausted'))
        self.assertLessEqual(movement.calc_Distance(self.position, self.origin), 1)
        self.assertIsNotNone(self.client._quest_tp_rejected)

    async def test_no_progress_budget_is_three_across_calls_and_worker_restarts(self):
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(3):
                await self.reader.move_until_quest_interaction(self.client, self.target)
                self.clock += 20
                self.reader = Quester(self.client, [self.client], None)
                self.reader.read_quest_txt = AsyncMock(return_value='unchanged objective')
                self.reader._npc_dialogue_visible = AsyncMock(return_value=False)
            self.assertEqual(nav.await_count, 3)
            inputs = len(self.events)
            for _ in range(5):
                self.clock += 20
                self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
            self.assertEqual(nav.await_count, 3)
            self.assertEqual(len(self.events), inputs)
        self.assertIn('3', self.client._quest_internal_block_reason)
        self.assertTrue(self.client.questing_status)

    async def test_goal_change_gets_a_new_budget(self):
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(3):
                await movement.collision_tp(self.client, self.target)
            self.goal_id += 1
            await movement.collision_tp(self.client, self.target)
            self.assertEqual(nav.await_count, 4)

    async def test_measured_progress_resets_budget_without_claiming_task_success(self):
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(3):
                await movement.collision_tp(self.client, self.target)
            self.position = XYZ(30, 130, 2.897)  # Actual >5u move toward the same goal.
            result = await movement.collision_tp(self.client, self.target)
            self.assertNotEqual(result, 'exhausted')
            self.assertLess(self.client._quest_navmap_recovery['attempts'], 3)
            self.assertGreaterEqual(nav.await_count, 3)

    async def test_controls_and_state_changes_never_trigger_old_fallback(self):
        for cause in ('stop', 'loading', 'battle', 'dialogue', 'quest', 'goal', 'target', 'zone', 'control'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.client._quest_tp_rejected = self.client._quest_tp_landing = None
                self.client._quest_navmap_recovery = None
                self.client.questing_status = True
                self.quest_id, self.goal_id = 1, 2
                self.quest_target = self.target
                self.zone = 'Arcanum/Interiors/AR_Z01_MusicologyOffice'
                self.loading = self.battle = self.forced_dialogue = False
                changed_control = False
                actual = movement._movement_control_changed
                async def control(client, expected):
                    return changed_control or await actual(client, expected)
                async def teleport(destination):
                    nonlocal changed_control
                    self.events.append(('tp', destination))
                    if cause == 'stop': self.client.questing_status = False
                    elif cause == 'quest': self.quest_id += 1
                    elif cause == 'goal': self.goal_id += 1
                    elif cause == 'target': self.quest_target = XYZ(200, 300, 3)
                    elif cause == 'zone': self.zone = 'Other/Zone'
                    elif cause == 'control': changed_control = True
                    elif cause == 'dialogue': self.forced_dialogue = True
                    else: setattr(self, cause, True)
                self.client.teleport = teleport
                with patch('src.teleport_math._movement_control_changed', side_effect=control), \
                     patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
                    await movement.collision_tp(self.client, self.target)
                nav.assert_not_awaited()
                self.assertEqual(len(self.events), 1)

    async def test_third_attempt_with_actual_progress_and_prompt_returns_to_handler(self):
        actual = movement.navmap_tp
        attempts = 0
        async def nav(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 3:
                async def accepted(destination):
                    self.events.append(('tp', destination))
                    self.position = destination
                self.client.teleport = accepted
                # The last attempt must use a fresh candidate, not replay a
                # destination already proven ineffective for this task.
                from types import SimpleNamespace
                with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(
                        get_file=AsyncMock(return_value=b'nav')))), \
                     patch('src.teleport_math.parse_nav_data', return_value=(
                         [XYZ(self.target.x + 25, self.target.y + 25, self.target.z)], [])):
                    return await actual(*args, **kwargs)
            return await actual(*args, **kwargs)
        from src.paths import npc_range_path
        with patch('src.teleport_math.navmap_tp', side_effect=nav), \
             patch('src.teleport_math.is_visible_by_path', AsyncMock(side_effect=lambda _, path:
                 path == npc_range_path and not movement._sent_back(self.position, self.origin))):
            for _ in range(2):
                self.assertEqual(await movement.collision_tp(self.client, self.target), 'failed')
            self.assertEqual(await movement.collision_tp(self.client, self.target), 'ready')
        self.assertEqual(attempts, 3)
        self.assertEqual(self.client._quest_navmap_recovery['attempts'], 0)

    async def test_retiring_landing_record_does_not_renew_budget(self):
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(3):
                await movement.collision_tp(self.client, self.target)
            self.client._quest_tp_rejected = self.client._quest_tp_landing = None
            inputs = len(self.events)
            self.assertEqual(await movement.collision_tp(self.client, self.target), 'exhausted')
            self.assertEqual(nav.await_count, 3)
            self.assertEqual(len(self.events), inputs)

    async def test_same_xy_wrong_height_and_noop_solution_recover_finitely(self):
        self.origin = self.position = self.safe = self.first_solve = XYZ(50, 86.603, 2.897)
        self.target = self.quest_target = XYZ(50, 86.603, 282)
        with patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            for _ in range(3):
                result = await movement.collision_tp(self.client, self.target)
            self.assertEqual(result, 'exhausted')
            self.assertEqual(nav.await_count, 3)
        self.assertEqual(self.position, self.origin)

    async def test_confirmed_shared_goal_change_renews_stale_hitters_budget_at_same_xyz(self):
        from types import SimpleNamespace
        task = await movement._movement_task_snapshot(self.client)
        session = SimpleNamespace(transition={'movement_task': task})
        with patch('src.questing.dungeon_session_for', return_value=session), \
             patch('src.teleport_math.navmap_tp', wraps=movement.navmap_tp) as nav:
            await self.reader._quest_target_first_move_state(self.client, self.target, self.client)
            for _ in range(3):
                await movement.collision_tp(self.client, self.target)
            self.assertEqual(self.client._quest_navmap_recovery['attempts'], 3)
            self.client._quest_tp_rejected['pending'] = self.origin, self.origin, self.zone
            session.transition['movement_task'] = (task[0], task[1]+1, task[2], task[3])
            state = await self.reader._quest_target_first_move_state(self.client, self.target, self.client)
            self.assertTrue(state['pending'])
            self.assertEqual((await movement._movement_task_snapshot(self.client))[:2], task[:2])
            self.assertEqual(await movement.collision_tp(self.client, self.target), 'failed')
            self.assertEqual(nav.await_count, 4)
            self.assertEqual(self.client._quest_navmap_recovery['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
