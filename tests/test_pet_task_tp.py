"""Dual-body task movement: simulated Hook delivery is not a live TP proof."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, MemoryReadError
from src import teleport_math as movement
from src.questing import Quester
import tests.test_walk_navmap_fallback as fixtures
import tests.test_walk_waypoint_execution as waypoints
import tests.test_door_approach_recovery as doors


class PetTaskTPTests(unittest.IsolatedAsyncioTestCase):
    setUp_base = fixtures.WalkNavmapFallbackTests.setUp
    solve = fixtures.WalkNavmapFallbackTests.solve
    plan = fixtures.WalkNavmapFallbackTests.plan
    goto = fixtures.WalkNavmapFallbackTests.goto

    def setUp(self):
        self.setUp_base()
        self.pet_mode = self.pet_available = True
        self.pet_address = 200
        self.player_position = XYZ(-1000, -1000, 0)
        self.origin = self.position
        self.move_pet = True
        self.pet_id = 210
        self.on_settle = lambda: None
        self.client.body.position = AsyncMock(side_effect=lambda: self.player_position)
        self.client.body.read_base_address = AsyncMock(return_value=100)
        self.pet_body = SimpleNamespace(
            position=AsyncMock(side_effect=lambda: self.position),
            read_base_address=AsyncMock(side_effect=lambda: self.pet_address),
            write_yaw=AsyncMock())
        self.parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
            global_id_full=AsyncMock(side_effect=lambda: self.pet_id))
        self.pet_body.parent_client_object = AsyncMock(return_value=self.parent)
        self.pet_object = SimpleNamespace(
            object_name=AsyncMock(side_effect=lambda: 'PetObject' if self.pet_mode else 'Player Object'),
            actor_body=AsyncMock(side_effect=lambda: self.pet_body if self.pet_available else None))
        self.camera = SimpleNamespace(read_base_address=AsyncMock(return_value=300),
            attached_client_object=AsyncMock(return_value=self.pet_object))
        self.client.game_client = SimpleNamespace(
            elastic_camera_controller=AsyncMock(return_value=self.camera),
            selected_camera_controller=AsyncMock(return_value=self.camera))
        self.client.client_object = SimpleNamespace(actor_body=AsyncMock(return_value=self.pet_body),
            speed_multiplier=AsyncMock(return_value=0))
        self.reader = Quester(self.client, [self.client], None)
        self.client.questing_status = True
        self.reader._npc_dialogue_visible = AsyncMock(return_value=False)
        self.reader._dungeon_quest_snapshot = AsyncMock(return_value=(self.quest_id, self.goal_id))
        self.reader._quest_interaction_signature = AsyncMock(return_value=None)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)))

    async def teleport(self, dest):
        self.events.append(('tp', dest))
        if self.move_pet:
            self.position = dest
        else:
            self.player_position = dest

    async def sleep(self, delay):
        if delay == 1:
            self.on_settle()
        await fixtures.REAL_SLEEP(0)

    async def attempt(self):
        return await movement._teleport_once_verified(
            self.client, self.target, self.origin, self.zone)

    async def test_pet_tp_is_confirmed_when_player_did_not_move(self):
        self.assertTrue(await self.attempt())
        self.assertEqual((self.player_position.x, self.player_position.y), (-1000, -1000))

    async def test_player_only_tp_does_not_prove_pet_arrival(self):
        self.move_pet = False
        self.assertFalse(await self.attempt())

    async def test_real_pet_bounce_is_rejected_even_when_player_is_at_target(self):
        self.player_position = self.target
        self.on_settle = lambda: setattr(self, 'position', self.origin)
        self.assertFalse(await self.attempt())

    async def test_manual_navmap_uses_pet_origin_when_player_is_already_at_goal(self):
        self.player_position = self.target
        await movement.navmap_tp(self.client, self.target)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], 'tp')

    async def test_mode_switch_during_landing_cannot_confirm_old_body(self):
        self.player_position = self.target
        self.on_settle = lambda: setattr(self, 'pet_mode', False)
        self.assertFalse(await self.attempt())

    async def test_pet_replacement_during_landing_invalidates_old_attempt(self):
        self.player_position = self.target
        self.on_settle = lambda: setattr(self, 'pet_address', 201)
        self.assertFalse(await self.attempt())

    async def test_missing_pet_body_cannot_use_player_at_goal(self):
        self.player_position = self.target
        self.on_settle = lambda: setattr(self, 'pet_available', False)
        self.assertFalse(await self.attempt())

    async def test_reused_pet_body_with_new_object_id_invalidates_old_attempt(self):
        self.player_position = self.target
        self.on_settle = lambda: setattr(self, 'pet_id', 211)
        self.assertFalse(await self.attempt())

    async def test_clienthook_with_other_body_never_receives_pet_tp(self):
        self.client.client_object.actor_body.return_value = self.client.body
        self.assertFalse(await self.attempt())
        self.assertFalse(self.events)

    async def test_unconfirmed_pet_tp_record_cannot_grant_interaction(self):
        await movement._movement_position(self.client)
        task = await movement._movement_task_snapshot(self.client)
        self.client._quest_tp_rejected = dict(key=(self.zone,
            (task[0],task[1],tuple(task[2]),task[3]),tuple(self.target)),
            control=self.client._quest_movement_control, rejected=False, confirmed=False,
            anchor=self.origin, origin=self.origin, ready=False, walks=0)
        self.assertFalse(await movement._movement_interaction_handoff(
            self.client,self.target,task,range_visible=True))

    async def test_unsynced_clienthook_keeps_new_goal_first_move_and_cooldown(self):
        await self.reader._quest_target_first_move_state(self.client,self.target)
        self.goal_id += 1
        self.client.client_object.actor_body.return_value = self.client.body
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client,self.target),'failed')
        self.assertFalse(self.events)
        self.assertTrue(self.client._quest_target_first_move['pending'])
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertIsNotNone(self.client._quest_internal_failure_wait)
        self.assertTrue(self.client.questing_status)

    async def test_auto_tp_succeeds_without_player_movement(self):
        self.reject_first = False
        self.assertEqual(await movement.collision_tp(self.client, self.target), 'ready')
        self.assertEqual(len([e for e in self.events if e[0] == 'tp']), 1)
        self.assertTrue(self.client._quest_tp_landing['confirmed'])

    async def test_late_pet_bounce_revokes_interaction_permission(self):
        self.reject_first = False
        await movement.collision_tp(self.client, self.target)
        self.player_position = self.target
        self.position = self.origin
        task = await movement._movement_task_snapshot(self.client)
        self.assertFalse(await movement._movement_interaction_handoff(
            self.client, self.target, task, range_visible=True))
        self.assertTrue(self.client._quest_tp_rejected['rejected'])

    async def test_mode_change_clears_old_landing_and_wait_state(self):
        self.reject_first = False
        await movement.collision_tp(self.client, self.target)
        await self.reader._defer_internal_quest_failure(self.client, 'fixture', kind='movement',
                                                      delay=2, target=self.target)
        self.pet_mode = False
        await movement._movement_position(self.client)
        self.assertIsNone(self.client._quest_tp_landing)
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertIsNone(self.client._quest_internal_failure_wait)
        self.assertTrue(self.client.questing_status)

    async def test_body_loss_uses_existing_cooldown_without_closing_questing(self):
        self.pet_available = False
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
        self.assertTrue(self.client.questing_status)
        self.assertIsNotNone(self.client._quest_internal_failure_wait)
        self.assertFalse(self.events)

    async def test_pet_progress_releases_cooldown_while_player_stays_still(self):
        await self.reader._defer_internal_quest_failure(self.client, 'fixture', kind='movement',
                                                      delay=10, target=self.target)
        self.position = XYZ(self.position.x+10, self.position.y-10, self.position.z)
        self.assertFalse(await self.reader._wait_internal_quest_failure(self.client))
        self.assertTrue(self.client.questing_status)

    async def test_battle_during_settle_does_not_confirm_pet_landing(self):
        self.on_settle = lambda: setattr(self, 'battle', True)
        self.assertFalse(await self.attempt())

    async def test_changed_goal_still_gets_first_pet_movement_before_old_prompt(self):
        self.reject_first = False
        await self.reader._quest_target_first_move_state(self.client, self.target)
        self.position = self.target
        self.goal_id += 1
        self.target = self.quest_target = self.safe = self.first_solve = XYZ(
            self.target.x+182, self.target.y, self.target.z)
        self.player_position = self.target
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'ready')
        self.assertEqual(len([e for e in self.events if e[0] == 'tp']), 1)
        self.assertFalse(self.client._quest_target_first_move['pending'])

    async def test_confirmed_landing_with_unreadable_pet_position_blocks_interaction(self):
        self.reject_first = False
        await movement.collision_tp(self.client, self.target)
        self.pet_body.position.side_effect = MemoryReadError(1)
        task = await movement._movement_task_snapshot(self.client)
        self.assertTrue(await movement._movement_failure_blocks_interaction(self.client, self.target, task))

    async def test_nonfinite_pet_origin_is_deferred_without_input(self):
        self.position = XYZ(float('nan'), 0, 0)
        self.assertEqual(await self.reader.move_until_quest_interaction(self.client, self.target), 'blocked')
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.events)

    async def test_special_exit_stall_observes_pet_progress(self):
        self.zone = 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
        now = 0
        async def advance(*args, **kwargs):
            self.position = XYZ(self.position.x+150, self.position.y, self.position.z)
        with patch('src.questing.collision_tp', AsyncMock(side_effect=advance)), \
                patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='fixture')), \
                patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)):
            for now in (0, 5, 11, 15):
                await self.reader.teleport_to_quest_target(self.client, self.target)
        self.assertFalse(self.events)  # Pet motion cannot trigger an extra escape TP.

    async def test_special_exit_mode_switch_discards_old_stall_timer(self):
        self.zone = 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
        now = 0
        with patch('src.questing.collision_tp', AsyncMock()), \
                patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='fixture')), \
                patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)):
            for now in (0, 5):
                await self.reader.teleport_to_quest_target(self.client, self.target)
            self.pet_address += 1
            now = 11
            await self.reader.teleport_to_quest_target(self.client, self.target)
        self.assertFalse(self.events)
        self.assertEqual(self.reader._krok_exit_watch[id(self.client)]['attempts'], 1)

    async def test_special_exit_body_loss_during_tp_cannot_trigger_escape(self):
        self.zone = 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
        async def lose_body(*args, **kwargs):
            self.pet_available = False
        with patch('src.questing.collision_tp', AsyncMock(side_effect=lose_body)), \
                patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='fixture')):
            await self.reader.teleport_to_quest_target(self.client, self.target)
        self.assertFalse(self.events)
        self.assertNotIn(id(self.client), self.reader._krok_exit_watch)

    async def test_cancel_drains_pet_tp_and_retains_unconfirmed_pending_record(self):
        started = asyncio.Event()
        completed = asyncio.Event()
        async def teleport(dest):
            started.set()
            await fixtures.REAL_SLEEP(.02)
            self.position = dest
            completed.set()
        self.client.teleport = teleport
        state = {}
        task = asyncio.create_task(movement._teleport_once_verified(
            self.client, self.target, self.origin, self.zone, validation_state=state))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(completed.is_set())
        self.assertIn('pending', state)
        self.assertFalse(state.get('confirmed', False))


class PetWaypointTests(unittest.IsolatedAsyncioTestCase):
    setUp_base = waypoints.WaypointExecutionTests.setUp
    read_position = waypoints.WaypointExecutionTests.read_position
    goto = waypoints.WaypointExecutionTests.goto
    press = waypoints.WaypointExecutionTests.press
    walk = waypoints.WaypointExecutionTests.walk
    metrics = {}

    def setUp(self):
        self.setUp_base()
        self.pet_mode = True
        self.pet_body = self.client.body
        self.pet_body.read_base_address = AsyncMock(return_value=200)
        self.parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                      global_id_full=AsyncMock(return_value=210))
        self.pet_body.parent_client_object = AsyncMock(return_value=self.parent)
        self.client.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(-2000,-2000,0)),
                                          read_base_address=AsyncMock(return_value=100))
        obj = SimpleNamespace(object_name=AsyncMock(
            side_effect=lambda: 'PetObject' if self.pet_mode else 'Player Object'),
            actor_body=AsyncMock(return_value=self.pet_body))
        camera = SimpleNamespace(read_base_address=AsyncMock(return_value=300),
                                 attached_client_object=AsyncMock(return_value=obj))
        self.client.game_client = SimpleNamespace(elastic_camera_controller=AsyncMock(return_value=camera),
                                                  selected_camera_controller=AsyncMock(return_value=camera))

    async def test_pet_walk_uses_existing_goto_and_measures_pet_progress(self):
        self.assertTrue(await self.walk())
        self.assertTrue(self.yaws)
        self.assertTrue(self.calls)
        self.assertEqual(self.client.body.position.call_count, 0)

    async def test_stale_clienthook_never_sends_pet_walk(self):
        self.client.client_object.actor_body.return_value = self.client.body
        self.assertFalse(await self.walk())
        self.assertFalse(self.calls)

    async def test_mode_change_stops_before_another_waypoint_input(self):
        self.on_step = lambda: setattr(self, 'pet_mode', False)
        self.assertFalse(await self.walk())
        self.assertEqual(len(self.calls), 1)

    async def test_entering_battle_stops_before_another_pet_step(self):
        self.on_step = lambda: setattr(self, 'battle', True)
        self.assertFalse(await self.walk())
        self.assertEqual(len(self.calls), 1)

    async def test_cancel_drains_pet_native_goto_then_stops(self):
        await waypoints.WaypointExecutionTests.test_cancel_drains_only_current_native_goto_then_stops(self)


class PetDoorApproachTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Compose the existing fixture; do not inherit character-only tests.
        f = doors.DoorApproachTests('test_near_tp_without_trigger_runs_one_short_measured_step')
        f.addCleanup = self.addCleanup
        f.addAsyncCleanup = self.addAsyncCleanup
        f.setUp()
        self.fixture = f
        f.pet_mode = True
        f.pet_body = f.q.body
        f.pet_body.read_base_address = AsyncMock(return_value=200)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        f.pet_body.parent_client_object = AsyncMock(return_value=parent)
        f.q.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(-2000,-2000,0)),
                                  read_base_address=AsyncMock(return_value=100), write_yaw=AsyncMock())
        obj = SimpleNamespace(object_name=AsyncMock(
            side_effect=lambda: 'PetObject' if f.pet_mode else 'Player Object'),
            actor_body=AsyncMock(return_value=f.pet_body))
        camera = SimpleNamespace(read_base_address=AsyncMock(return_value=300),
                                 attached_client_object=AsyncMock(return_value=obj))
        f.q.game_client = SimpleNamespace(elastic_camera_controller=AsyncMock(return_value=camera),
                                         selected_camera_controller=AsyncMock(return_value=camera))

    async def test_pet_door_step_uses_pet_yaw_and_pet_displacement(self):
        f = self.fixture
        await f.caller.auto_quest_solo()
        self.assertEqual(len(f.w_keys()), 1)
        f.pet_body.write_yaw.assert_awaited_once()
        f.q.body.write_yaw.assert_not_awaited()
        self.assertFalse(f.q._quest_door_approach['ended'])

    async def test_pet_door_step_invalidates_record_on_mode_change(self):
        f = self.fixture
        f.on_w = lambda: setattr(f, 'pet_mode', False)
        with patch('src.questing.logger.debug') as log:
            await f.caller.auto_quest_solo()
        messages = [call.args[0] for call in log.call_args_list if '[door_approach]' in call.args[0]]
        self.assertTrue(any('controlled-object-changed' in msg and "'displacement': None" in msg
                            for msg in messages))
        self.assertEqual(len(f.w_keys()), 1)
        self.assertIsNone(f.q._quest_door_approach)

    async def test_pet_door_cancel_releases_actual_key_and_owner(self):
        await doors.DoorApproachTests.test_cancelled_w_releases_input_and_ownership(self.fixture)
