"""Unknown object X needs a completed current-target route and real task progress."""
import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from wizwalker.errors import MemoryInvalidated, ExceptionalTimeout
from src import teleport_math as movement
from src import current_target_probe as probe
from src.paths import npc_range_path, team_up_button_path
from tests.test_stale_interaction_movement import Fixture


class GenericProbeTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective = 'Calibrate the array'
        self.title, self.prompt = 'Alignment Console', 'Press X to Use'
        self.nearby(273)
        self.range_visible = True
        self.windows = [SimpleNamespace(is_visible=AsyncMock(return_value=True),
            read_base_address=AsyncMock(return_value=4100))]
        self.client.root_window.get_windows_with_name = AsyncMock(side_effect=lambda _: self.windows)
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        for mod in ('src.questing', 'src.teleport_math', 'src.current_target_probe'):
            self.stack.enter_context(patch(mod+'.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(probe, 'time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', XYZ(*point)))
        self.stack.enter_context(patch('src.collision.CollisionWorld', return_value=Mock()))
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'')))
        self.stack.enter_context(patch.object(movement, '_resolve_player_radius', AsyncMock(return_value=45)))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        self.stack.enter_context(patch('src.collision_math.blocking_volumes_at', return_value=[]))
        self.landing_gap = 150
        self.stack.enter_context(patch('src.collision_math.find_walkable_teleport_point',
            side_effect=lambda *_a, **_k: (XYZ(self.target.x-self.landing_gap, self.target.y, self.target.z), 'grid')))
        grid = SimpleNamespace(to_hex=lambda *_: (0, 0), walk_z=lambda *_: None,
            find_walk_path=lambda *_a, **_k: [(self.target.x-getattr(self, 'endpoint_gap', self.landing_gap),
                                               self.target.y, self.target.z)])
        self.stack.enter_context(patch('src.collision_math.get_walk_grid', return_value=grid))
        self.stack.enter_context(patch('src.collision_math._ZoneWalkGrid', return_value=grid))
        self.stack.enter_context(patch('src.collision_math.straight_path_walkable',
            side_effect=lambda _w, _z, _a, b, **_k: b.x <= self.target.x-getattr(
                self, 'endpoint_gap', self.landing_gap)+1e-5))
        self.stack.enter_context(patch('src.questing.collision_tp', movement.collision_tp))
        self.quester.handle_questing_zone_change = AsyncMock()
        self.quester._recover_door_approach = AsyncMock()
        self.quester._recover_quest_x_direction = AsyncMock()
        self.quester.take_photomancy_photo = AsyncMock()  # Existing dedicated handler is outside this probe.

    async def test_a_unknown_object_at_completed_current_target_probes_then_acknowledges_progress(self):
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertAlmostEqual(movement.calc_Distance(self.position, self.target), 150)

    async def test_short_navigation_cutoff_alone_is_not_proof_but_verified_nearest_point_is(self):
        self.landing_gap = 70
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertAlmostEqual(movement.calc_Distance(self.position, self.target), 70)
        self.assertEqual(self.client._quest_interaction_retry['probe']['status'], 'success')

    async def test_short_cutoff_with_closer_safe_plan_does_not_probe(self):
        self.landing_gap = 70
        with patch('src.collision_math.straight_path_walkable', return_value=True):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])
        self.assertIsNone(self.client._quest_probe_navigation['terminal'])

    async def test_optional_short_endpoint_read_failure_preserves_original_navigation_return(self):
        self.position = XYZ(self.target.x-70, self.target.y, self.target.z)
        task = await movement._movement_task_snapshot(self.client)
        probe.start_navigation(self.client, self.target, task,
            XYZ(self.target.x-273, self.target.y, self.target.z), None)
        with patch('src.entity_collision.build_zone_static_shapes', side_effect=RuntimeError('geometry unavailable')):
            result = await movement._walk_remaining_to_target(self.client, self.target, object(), task[3], 45)
        self.assertTrue(result)
        self.assertIsNone(self.client._quest_probe_navigation['terminal'])
        self.assertEqual(self.keys, [])

    async def test_optional_endpoint_position_read_cannot_replace_exhausted_navigation_result(self):
        task = await movement._movement_task_snapshot(self.client)
        probe.start_navigation(self.client, self.target, task, self.position, None)
        self.client._quest_navmap_recovery = dict(task=task, shared=None, target=self.target,
            anchor=self.position, control=None, attempts=3)
        original = movement._movement_position
        async def position(client):
            if inspect.currentframe().f_back.f_code.co_name in ('result', 'record_endpoint'):
                raise ValueError('optional evidence position unreadable')
            return await original(client)
        with patch.object(movement, '_movement_position', position), patch.object(probe, '_movement_position', position):
            try:
                outcome = await movement.collision_tp(self.client, self.target)
            except ValueError:
                outcome = 'unhandled-optional-read'
        self.assertEqual(outcome, 'exhausted')
        self.assertIsNone(self.client._quest_probe_navigation['terminal'])

    async def test_navmap_exact_arrival_uses_the_same_bounded_probe(self):
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        with patch('src.collision_math.find_walkable_teleport_point', side_effect=RuntimeError('collision unavailable')):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertEqual(self.client._quest_interaction_retry['probe']['terminal']['kind'], 'target')

    async def test_c_roadside_x_without_any_actual_current_target_movement_is_rejected(self):
        self.client.teleport.side_effect = None
        with patch.object(movement, 'load_wad', AsyncMock(side_effect=RuntimeError('no nav'))):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_d_progress_without_completed_nearest_endpoint_cannot_probe(self):
        async def partial(*_a, **_k):
            self.position = XYZ(self.target.x-90, self.target.y, self.target.z)
            return 'failed'
        with patch('src.questing.collision_tp', partial):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_e_dedicated_handlers_do_not_use_generic_unknown_object_fallback(self):
        for title, action, objective in (
            ('Unknown NPC', 'Press X to Talk', 'Calibrate the array'),
            ('Unknown Door', 'Press X to Enter', 'Calibrate the array'),
            ('Unknown Item', 'Press X to Collect', 'Calibrate the array'),
            ('Unknown Camera', 'Press Z to Take a Photo', 'Calibrate the array'),
            ('Alignment Console', 'Press X to Use', 'Defeat Sentinel'),
            ('Alignment Console', 'Press X to Use', 'Collect Gears (0/3)'),
            ('Alignment Console', 'Press X to Use', 'Press Z to Take a Photo'),
            ('Alignment Console', 'Press X to Use', 'Talk to the Archivist'),
            ('Alignment Console', 'Press X to Use', '拜访 档案管理员'),
            ('Alignment Console', 'Press X to Use', 'Enter the Sanctum'),
            ('Alignment Console', 'Press X to Use', '进入圣堂'),
            ('Alignment Console', 'Press X to Use', '骑乘小船'),
            ('Alignment Console', 'Press X to Use', '攀爬绳子'),
            ('Alignment Console', 'Press X to Use', 'Do not use the console')):
            with self.subTest(action=action, objective=objective):
                self.setUp()
                self.title, self.prompt, self.objective = title, action, objective
                self.on_x = lambda: setattr(self, 'goal', self.goal+1)
                await self.quester.auto_quest_solo()
                retry = getattr(self.client, '_quest_interaction_retry', {})
                self.assertFalse(retry.get('probe', {}).get('attempts', 0))
                self.stack.close()

    async def test_existing_chinese_x_binding_is_an_object_prompt_without_new_synonyms(self):
        self.objective = '校准古老装置'
        self.title, self.prompt = '排列控制台', '按下 &Icons_XKey& 使用'
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_semantic_match_keeps_priority_over_probe(self):
        self.objective = 'Use Alignment Console'
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertFalse(getattr(self.client, '_quest_interaction_retry', {}).get('probe', {}).get('attempts', 0))

    async def test_actual_planned_endpoint_with_safe_closer_continuation_is_not_terminal(self):
        async def walk(x, y):
            self.position = XYZ(x, y, self.target.z)
        self.client.goto = walk
        with patch('src.collision_math.straight_path_walkable', return_value=True):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])
        self.assertIsNone(self.client._quest_probe_navigation['terminal'])

    async def test_overshoot_past_planned_endpoint_records_terminal_and_probes(self):
        self.landing_gap, self.endpoint_gap = 138, 76
        async def walk(_x, y):
            self.position = XYZ(self.target.x-69, y, self.target.z)
        self.client.goto = walk
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        state = self.client._quest_interaction_retry['probe']
        self.assertEqual(state['terminal']['kind'], 'approach-limit')
        self.assertAlmostEqual(state['terminal']['position'].x, self.target.x-69)
        self.assertAlmostEqual(movement.calc_Distance(self.position, self.target), 69)

    async def test_stopping_short_of_planned_endpoint_is_not_terminal(self):
        self.landing_gap, self.endpoint_gap = 138, 76
        async def walk(_x, y):
            self.position = XYZ(self.target.x-90, y, self.target.z)
        self.client.goto = walk
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])
        self.assertIsNone(self.client._quest_probe_navigation['terminal'])

    async def test_catalog_place_prompt_with_overshoot_is_a_generic_object_probe(self):
        self.objective = '放置 石头 地点：提洛斯'
        self.title, self.prompt = 'Stone Door', '按下 &Icons_XKey& 放置'
        self.range_visible = False
        def arrive(point):
            self.position, self.range_visible = XYZ(*point), True
        self.client.teleport.side_effect = arrive
        self.landing_gap, self.endpoint_gap = 138, 76
        async def walk(_x, y):
            self.position = XYZ(self.target.x-69, y, self.target.z)
        self.client.goto = walk
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    def test_place_object_action_uses_catalog_records_only(self):
        from src.interaction_prompts import task_object_prompt
        self.assertTrue(task_object_prompt('Stone Door', '按下 &Icons_XKey& 放置'))
        self.assertTrue(task_object_prompt('Stone Door', 'Press X to Place'))
        self.assertFalse(task_object_prompt('Stone Door', '按下 安放'))
        self.assertFalse(task_object_prompt('Stone Door', 'Press X to Deploy'))
        self.assertFalse(task_object_prompt(
            'Stone Door',
            'Press &InputBindings_NPCInteract& or &Icons_LeftMouseClick& to Place/Collect'))

    async def test_closed_navigation_scope_cannot_be_rearmed(self):
        self.assertEqual(await self.quester.move_until_quest_interaction(
            self.client, self.target), 'probe-ready')
        nav = self.client._quest_probe_navigation
        self.assertIsNotNone(nav['terminal'])
        self.assertFalse(nav['active'])
        task = await movement._movement_task_snapshot(self.client)
        self.assertFalse(await probe.offer_probe(self.quester, self.target, task))

    async def test_team_up_control_excludes_generic_probe(self):
        visible = AsyncMock(side_effect=lambda _c, path: path in (npc_range_path, team_up_button_path))
        with patch.object(probe, 'is_visible_by_path', visible):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_unknown_action_or_missing_x_binding_is_not_assumed_object_style(self):
        for action in ('Press X to UnclassifiedAction', 'Use', '按下 Z 使用'):
            with self.subTest(action=action):
                self.setUp()
                self.prompt = action
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_f_task_change_during_movement_cancels_probe(self):
        async def changed(point):
            self.position = XYZ(*point)
            self.goal += 1
        self.client.teleport.side_effect = changed
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_g_multiple_visible_uncertain_prompts_reject_probe(self):
        self.windows.append(SimpleNamespace(is_visible=AsyncMock(return_value=True),
            read_base_address=AsyncMock(return_value=4200)))
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])

    async def test_strict_object_path_keeps_priority_and_its_five_unit_limit(self):
        self.title, self.prompt = 'Unknown Mechanism', 'Press X to Use'
        self.landing_gap = 0
        self.range_visible = False
        def arrive(point):
            self.position, self.range_visible = XYZ(*point), True
        self.client.teleport.side_effect = arrive
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertFalse(self.client._quest_interaction_retry.get('probe', {}).get('attempts', 0))

    async def test_b_no_progress_retries_once_then_blocks_across_worker_recreation(self):
        from src.questing import Quester
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        state = self.client._quest_interaction_retry['probe']
        self.assertEqual(state['status'], 'blocked')
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.objective)
        for _ in range(3):
            self.clock += 20
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])

    async def test_retry_can_acknowledge_progress_but_initial_x_is_not_success(self):
        def progress():
            state = self.client._quest_interaction_retry['probe']
            self.assertNotEqual(state['status'], 'success')
            if len(self.keys) == 2:
                self.objective = 'Calibrate the array (1/2)'
        self.on_x = progress
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertEqual(self.client._quest_interaction_retry['probe']['status'], 'success')

    async def test_finished_probe_cannot_swallow_later_existing_dungeon_entry(self):
        self.on_x = lambda: setattr(self, 'objective', 'Calibrate the array (1/2)')
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.objective, self.title, self.prompt = 'Enter Next Room', 'Next Room', 'Press X to Enter'
        self.on_x = lambda: setattr(self.client.is_loading, 'return_value', True)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertEqual(self.client._quest_interaction_retry['probe']['attempts'], 1)
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_blocked_probe_does_not_block_a_new_semantic_match(self):
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.objective = 'Use Alignment Console'
        self.on_x = lambda: setattr(self, 'goal', self.goal+1)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X, Keycode.X])

    async def test_popup_disappearance_without_quest_progress_is_not_ack(self):
        self.on_x = lambda: setattr(self, 'range_visible', False)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertEqual(self.client._quest_interaction_retry['probe']['status'], 'cancelled')

    async def test_cancel_after_input_never_replays_in_the_same_context(self):
        def cancel():
            raise asyncio.CancelledError()
        self.on_x = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.auto_quest_solo()
        self.on_x = lambda: None
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_cancel_before_input_retires_old_prepared_probe(self):
        self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'probe-ready')
        task = await movement._movement_task_snapshot(self.client)
        async def cancel(_delay):
            raise asyncio.CancelledError()
        with patch.object(probe.asyncio, 'sleep', cancel):
            with self.assertRaises(asyncio.CancelledError):
                await probe.run_probe(self.quester, self.target, task)
        await probe.run_probe(self.quester, self.target, task)
        self.assertEqual(self.keys, [])

    async def test_deallocated_or_timed_out_popup_cancels_without_input(self):
        for error in (MemoryInvalidated('popup removed'), ExceptionalTimeout('popup timed out')):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'probe-ready')
                task = await movement._movement_task_snapshot(self.client)
                with patch.object(probe, '_unique_popup', AsyncMock(side_effect=error)):
                    try:
                        outcome = await probe.run_probe(self.quester, self.target, task)
                    except (MemoryInvalidated, ExceptionalTimeout):
                        outcome = 'unhandled-ui-read'
                self.assertEqual(outcome, 'cancelled')
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_concurrent_probe_callers_share_one_budget_and_owner(self):
        self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'probe-ready')
        task = await movement._movement_task_snapshot(self.client)
        await asyncio.gather(probe.run_probe(self.quester, self.target, task),
                             probe.run_probe(self.quester, self.target, task))
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertEqual(self.client._quest_interaction_retry['probe']['status'], 'blocked')

    async def test_context_changes_after_input_never_reuse_old_probe(self):
        for cause in ('target', 'body', 'reference', 'prompt', 'loading', 'battle', 'owner', 'stop', 'multiple'):
            with self.subTest(cause=cause):
                self.setUp()
                session = [None]
                self.stack.enter_context(patch('src.quest_party.dungeon_session_for', lambda _: session[0]))
                def changed():
                    if cause == 'target': self.target = XYZ(self.target.x+50, self.target.y, self.target.z)
                    if cause == 'body': self.client.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position))
                    if cause == 'reference': session[0] = SimpleNamespace(transition={})
                    if cause == 'prompt': self.title = 'Other Console'
                    if cause == 'loading': self.client.is_loading.return_value = True
                    if cause == 'battle': self.client.in_battle.return_value = True
                    if cause == 'owner': self.client.quest_recovery_owner = 'other'
                    if cause == 'stop': self.client.questing_status = False
                    if cause == 'multiple': self.windows.append(self.windows[0])
                self.on_x = changed
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X])
                self.assertEqual(self.client._quest_interaction_retry['probe']['status'], 'cancelled')
                self.stack.close()

    async def test_final_popup_window_read_cannot_send_old_goal_or_changed_endpoint(self):
        for cause in ('goal', 'quest', 'target', 'position', 'control'):
            with self.subTest(cause=cause):
                self.setUp()
                self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'probe-ready')
                task = await movement._movement_task_snapshot(self.client)
                original, reads = probe._unique_popup, []
                async def window(client):
                    result = await original(client)
                    reads.append(1)
                    # Two complete qualifications, then the final second frame's last window read.
                    if len(reads) == 8:
                        if cause == 'goal': self.goal += 1
                        if cause == 'quest': self.client.quest_id.return_value = 99
                        if cause == 'target': self.target = XYZ(self.target.x+50, self.target.y, self.target.z)
                        if cause == 'position': self.position = XYZ(self.position.x-20, self.position.y, self.position.z)
                        if cause == 'control':
                            parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                global_id_full=AsyncMock(return_value=210))
                            pet = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position),
                                parent_client_object=AsyncMock(return_value=parent))
                            self.stack.enter_context(patch.object(movement, 'get_controlled_body',
                                AsyncMock(return_value=(pet, 200))))
                    return result
                with patch.object(probe, '_unique_popup', window):
                    await probe.run_probe(self.quester, self.target, task)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_control_switch_during_final_position_read_cannot_send_old_probe(self):
        self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'probe-ready')
        task = await movement._movement_task_snapshot(self.client)
        original, reads = probe._movement_position, []
        async def position(client):
            reads.append(1)
            if len(reads) == 8:
                parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                    global_id_full=AsyncMock(return_value=210))
                pet = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position),
                    parent_client_object=AsyncMock(return_value=parent))
                self.stack.enter_context(patch.object(movement, 'get_controlled_body',
                    AsyncMock(return_value=(pet, 200))))
            return await original(client)
        with patch.object(probe, '_movement_position', position):
            await probe.run_probe(self.quester, self.target, task)
        self.assertEqual(self.keys, [])


del Fixture
