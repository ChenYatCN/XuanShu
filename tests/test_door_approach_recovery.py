"""Local doorway steps through existing task and source-entry callers."""
import asyncio
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Client, Keycode, XYZ, utils as wizutils
from src import quest_party as party
from src.questing import Quester
from src.paths import advance_dialog_path, npc_range_path
from src.automation_ownership import get_client_automation_ownership
from test_dungeon_party_transition import PartyTransitionFixture, REAL_SLEEP


class DoorApproachTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.target = XYZ(500, 600, 0)
        self.q.quest_position.position.return_value = self.target
        self.destination = 'Dungeon/Room2'
        self.prompt, self.dialogue = False, False
        self.steps, self.heading = [], 0.0
        self.w_move = 25.0
        self.on_w = lambda: None
        for member in (self.q, self.h):
            goal = SimpleNamespace(goal_destination_zone=AsyncMock(side_effect=lambda: self.destination))
            quest = SimpleNamespace(goal_data=AsyncMock(return_value={7: goal}))
            member.quest_manager = AsyncMock(return_value=SimpleNamespace(
                quest_data=AsyncMock(return_value={42: quest})))
            member.body.write_yaw = AsyncMock(side_effect=self.yaw)
            member.client_object.speed_multiplier = AsyncMock(return_value=0)
            async def send(key, seconds, c=member):
                await self.press(c, key, seconds)
            member.send_key = AsyncMock(side_effect=send)
        self.visible.side_effect = lambda c, path: ((path == npc_range_path and self.prompt)
            or (path == advance_dialog_path and self.dialogue and c is self.q))
        self.title.return_value = 'Door'
        self.stack.enter_context(patch.object(Quester, 'read_quest_txt', AsyncMock(return_value='Use Door')))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Use Door')))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        for name in ('src.mainline_recovery.maybe_recover_mainline', 'src.questing.is_potion_needed',
                     'src.questing.close_npc_quest_menu', 'src.questing.close_automation_popup',
                     'src.questing.is_spiral_door_open'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation',
                                             AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, '_dungeon_quest_snapshot',
                                             AsyncMock(return_value=(42, 7, 'Enter room'))))
        self.stack.enter_context(patch.object(Quester, '_recover_quest_x_direction',
                                             AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, '_maybe_photo_giant_vat',
                                             AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, 'read_popup', AsyncMock(return_value='Press X to Open')))
        # Keep the actual task TP wrapper; simulate only its ordinary TP/walk.
        async def land(c, target, **_kwargs):
            c.position = XYZ(target.x - 10, target.y, target.z)
        self.original = self.stack.enter_context(patch.object(
            Quester, 'move_until_quest_interaction', AsyncMock(side_effect=land)))
        self.caller = Quester(self.q, [self.q], None)
        self.solo()

    def solo(self):
        self.q.quest_party_hitters = []

    async def yaw(self, value):
        self.heading = value

    async def press(self, member, key, seconds):
        self.steps.append((member, key, seconds))
        if key == Keycode.W:
            p = member.position
            member.position = XYZ(p.x - math.sin(self.heading) * self.w_move,
                                  p.y - math.cos(self.heading) * self.w_move, p.z)
            self.on_w()

    def w_keys(self):
        return [step for step in self.steps if step[1] == Keycode.W]

    async def test_near_tp_without_trigger_runs_one_short_measured_step(self):
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        self.assertLessEqual(self.w_keys()[0][2], .1)
        self.assertEqual(self.original.await_count, 1)
        self.q.body.write_yaw.assert_awaited_once()

    async def test_step_log_reports_observed_motion_without_claiming_success(self):
        with patch('src.questing.logger.debug') as log:
            await self.caller.auto_quest_solo()
        messages = [call.args[0] for call in log.call_args_list if '[door_approach]' in call.args[0]]
        self.assertTrue(any('actual-position-to-target' in msg and "'displacement': 25.0" in msg
                            and 'measured-step-without-progress' in msg for msg in messages))

    async def test_speed_adjustment_keeps_pulse_within_thirty_units(self):
        self.q.client_object.speed_multiplier.return_value = 900
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        self.assertLessEqual(self.w_keys()[0][2] * 5800, 30)

    async def test_ordinary_task_point_without_entry_evidence_never_presses_w(self):
        self.destination = 'Outside'
        await self.caller.auto_quest_solo()
        self.assertEqual(self.w_keys(), [])

    async def test_coincident_target_without_prior_direction_does_not_guess(self):
        self.q.position = self.target
        self.original.side_effect = lambda c, target, **_kw: setattr(c, 'position', target)
        await self.caller.auto_quest_solo()
        self.assertEqual(self.w_keys(), [])
        self.q.body.write_yaw.assert_not_awaited()

    async def test_coincident_landing_uses_valid_pre_tp_approach_direction(self):
        self.q.position = XYZ(450, 550, 0)
        self.original.side_effect = lambda c, target, **_kw: setattr(c, 'position', target)
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        yaw = self.q.body.write_yaw.await_args.args[0]
        self.assertAlmostEqual(yaw, math.atan2(-50, -50))

    async def test_remote_tp_origin_is_not_an_entry_direction_for_coincident_landing(self):
        self.original.side_effect = lambda c, target, **_kw: setattr(c, 'position', target)
        await self.caller.auto_quest_solo()
        self.assertEqual(self.w_keys(), [])
        self.q.body.write_yaw.assert_not_awaited()

    async def test_walking_door_loading_ends_without_another_step(self):
        self.on_w = lambda: self.q.is_loading.configure_mock(return_value=True)
        await self.caller.auto_quest_solo()
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        self.q.send_key.assert_awaited_once()

    async def test_x_door_prompt_after_step_returns_to_existing_x_path(self):
        self.on_w = lambda: setattr(self, 'prompt', True)
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        await self.caller.auto_quest_solo()
        self.assertTrue(any(key == Keycode.X for _c, key, _t in self.steps))

    async def test_failed_original_x_can_step_closer_instead_of_waiting_on_prompt(self):
        self.prompt = True
        await self.caller.auto_quest_solo()
        self.assertEqual([key for _c, key, _t in self.steps], [Keycode.X, Keycode.W])

    async def test_no_displacement_stops_repeated_wall_pulses(self):
        self.w_move = 0.0
        for _ in range(4):
            await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)

    async def test_measured_motion_without_progress_is_limited_to_two_steps(self):
        for _ in range(8):
            await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 2)
        self.assertEqual(self.q._quest_door_approach['reported'],
                         'approach-budget-exhausted-without-progress')

    async def test_small_target_read_jitter_does_not_reset_wall_pulse_budget(self):
        self.w_move = 0.0
        for i in range(5):
            self.q.quest_position.position.return_value = XYZ(500 + i * .1, 600, 0)
            await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)

    async def test_task_change_before_fallback_prevents_w(self):
        async def update(c, target, **_kw):
            c.position = XYZ(target.x - 10, target.y, 0)
            c.goal_id.return_value = 8
        self.original.side_effect = update
        await self.caller.auto_quest_solo()
        self.assertEqual(self.w_keys(), [])

    async def test_read_failed_speed_never_sends_unbounded_pulse(self):
        self.q.client_object.speed_multiplier.side_effect = RuntimeError('speed unreadable')
        await self.caller.auto_quest_solo()
        self.assertEqual(self.w_keys(), [])

    async def test_unreadable_progress_is_not_reported_as_task_progress(self):
        self.q.position = XYZ(490, 600, 0)
        await self.caller._remember_door_approach(self.q, self.target)
        self.caller._dungeon_quest_snapshot = AsyncMock(return_value=None)
        self.assertFalse(await self.caller._recover_door_approach(self.q, self.target))
        self.assertEqual(self.q._quest_door_approach['reported'], 'task-unreadable')
        self.assertEqual(self.w_keys(), [])

    async def test_safety_states_block_door_step(self):
        for flag in ('refilling_potions', 'quest_recovery_owner', 'entity_detect_combat_status',
                     'quest_party_battle_rescue_active',
                     'post_combat_movement_active', 'quest_party_safe_return_active',
                     '_quest_dialogue_update_pending'):
            with self.subTest(flag=flag):
                self.q.position = XYZ(490, 600, 0)
                await self.caller._remember_door_approach(self.q, self.target)
                setattr(self.q, flag, True)
                self.assertFalse(await self.caller._recover_door_approach(self.q, self.target))
                setattr(self.q, flag, False)
        self.assertEqual(self.w_keys(), [])

    async def test_quester_first_each_member_confirms_its_own_walk_trigger(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        self.h.position = XYZ(490, 600, 0)
        for member in (self.q, self.h):
            async def cross(key, seconds, c=member):
                await self.press(c, key, seconds)
                if key == Keycode.W:
                    c.zone, c.area_id = 'Dungeon/Room2', 201
            member.send_key.side_effect = cross
        await self.caller.auto_quest_solo()
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.transition['arrivals'])
        self.assertNotIn(id(self.h), session.transition['arrivals'])
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        await self.reader.recover_dungeon_transition()
        await party.observe_dungeon_session(self.h)
        self.assertIn(id(self.h), session.transition['arrivals'])
        self.assertEqual([c for c, _k, _t in self.w_keys()], [self.q, self.h])
        self.assertEqual((self.original.await_args.args[1].x,
                          self.original.await_args.args[1].y), (500, 600))

    async def test_hitter_first_quester_keeps_valid_old_source_step(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        self.q.position = XYZ(490, 600, 0)
        async def move(c, target, **_kw):
            c.position = XYZ(target.x - 10, target.y, 0)
            if c is self.h:
                c.zone, c.area_id = 'Dungeon/Room2', 201
                c.quest_position.position.return_value = XYZ(20000, 30000, 0)
        self.original.side_effect = move
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        await self.reader.move_dungeon_member()
        await party.observe_dungeon_session(self.h)
        self.assertIn(id(self.h), session.transition['arrivals'])
        async def cross(key, seconds):
            await self.press(self.q, key, seconds)
            if key == Keycode.W:
                self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.send_key.side_effect = cross
        await self.caller.auto_quest_solo()
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.transition['arrivals'])
        self.assertEqual([c for c, _k, _t in self.w_keys()], [self.q])
        self.assertEqual((self.original.await_args.args[1].x,
                          self.original.await_args.args[1].y), (500, 600))

    async def test_source_member_steps_using_old_entry_not_peers_new_coordinates(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.quest_position.position.return_value = XYZ(20000, 30000, 0)
        await party.observe_dungeon_session(self.q)
        self.h.position = XYZ(490, 600, 0)
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        self.reader._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Enter room'))
        await self.reader.recover_dungeon_transition()
        self.assertEqual(len(self.w_keys()), 1)
        move = self.original.await_args
        self.assertEqual((move.args[1].x, move.args[1].y), (500, 600))
        self.assertIs(self.w_keys()[0][0], self.h)
        self.assertNotIn(id(self.h), session.transition['arrivals'])

    async def test_arrived_peer_loading_does_not_revoke_other_members_old_step(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.q)
        self.q.is_loading.return_value = True
        self.h.position = XYZ(490, 600, 0)
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        self.assertTrue(await self.reader.recover_dungeon_transition())
        self.assertEqual([c for c, _k, _t in self.w_keys()], [self.h])
        self.assertEqual(self.h._quest_door_approach['reported'], 'measured-step-without-progress')
        self.assertNotIn(id(self.h), session.transition['arrivals'])

    async def test_existing_x_body_entry_keeps_valid_last_approach_when_target_is_coincident(self):
        self.q.quest_party_hitters = [self.h]
        await self.enter()
        self.prompt = True
        async def land(c, target, **_kw):
            c.position = XYZ(490, 600, 0)
        self.original.side_effect = land
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        await self.reader.move_dungeon_member()
        async def cross(key, seconds):
            await self.press(self.q, key, seconds)
            if key == Keycode.W:
                self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.send_key.side_effect = cross
        await self.caller.auto_quest_solo()
        session = await party.observe_dungeon_session(self.q)
        self.assertEqual(tuple(session.transition['target']), (490, 600, 0))
        await self.reader.recover_dungeon_transition()
        self.assertEqual([c for c, _k, _t in self.w_keys()], [self.q, self.h])
        self.assertEqual(self.h._quest_door_approach['direction'], (1.0, 0.0))

    async def test_tp_transition_does_not_recapture_new_room_as_old_door_context(self):
        async def cross(c, target, **_kw):
            c.position = XYZ(target.x - 10, target.y, 0)
            c.zone, c.area_id = 'Dungeon/Room2', 201
        self.original.side_effect = cross
        await self.caller.auto_quest_solo()
        self.assertEqual(self.q._quest_door_approach['task'][3], 'Outside')
        self.assertEqual(self.w_keys(), [])

    async def test_confirmed_quester_exit_keeps_laggers_old_stair_step_without_friend_tp(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        session.source_zones = {id(c): 'Dungeon/Room2' for c in session.members}
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.goal_id.return_value = 8
        self.q._quest_dialogue_update_pending = True
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.exited)
        self.destination = 'Dungeon/Room1'  # Laggers do not read the quester's new goal.
        self.h.position = XYZ(378, 600, 0)
        self.original.side_effect = lambda *_a, **_kw: None
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        async def cross(key, seconds):
            await self.press(self.h, key, seconds)
            if key == Keycode.W:
                self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        self.h.send_key.side_effect = cross
        self.assertTrue(await self.reader.recover_dungeon_transition())
        self.assertEqual([c for c, _k, _t in self.w_keys()], [self.h])
        self.assertEqual(self.h.zone, 'Dungeon/Room2')
        self.assertEqual(tuple(self.original.await_args.args[1]), tuple(self.target))
        self.assertFalse(get_client_automation_ownership(self.h).locked)

    async def test_current_evidenced_stair_at_122_units_does_not_require_expired_shared_release(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.clock += 31
        self.q.position = XYZ(378, 600, 0)
        await self.caller._remember_door_approach(self.q, self.target)
        self.assertTrue(await self.caller._recover_door_approach(self.q, self.target))
        self.assertEqual(len(self.w_keys()), 1)
        self.assertLessEqual(self.w_keys()[0][2], .1)
        self.assertEqual(session.transition['attempts'], {})

    async def test_live_entry_prompt_with_no_shared_release_still_requires_whole_group_ready(self):
        self.q.quest_party_hitters = [self.h]
        await self.enter()
        self.prompt = True
        self.q.position = XYZ(490, 600, 0)
        await self.caller._remember_door_approach(self.q, self.target)
        self.q._quest_dialogue_update_pending = True
        self.assertFalse(await self.caller._recover_door_approach(self.q, self.target, x_attempted=True))
        self.assertEqual(self.w_keys(), [])
        self.q._quest_dialogue_update_pending = False
        self.assertTrue(await self.caller._recover_door_approach(self.q, self.target, x_attempted=True))
        self.assertEqual(len(self.w_keys()), 1)

    async def test_missing_entry_evidence_log_identifies_goal_and_destination_rejection(self):
        self.destination = 'Outside'
        self.q.position = XYZ(490, 600, 0)
        await self.caller._remember_door_approach(self.q, self.target)
        with patch('src.questing.logger.debug') as log:
            self.assertFalse(await self.caller._recover_door_approach(self.q, self.target))
        message = next(c.args[0] for c in log.call_args_list if 'no-entry-evidence' in c.args[0])
        for field in ('quest_id', 'goal_id', 'source_zone', 'destination', 'same-source-zone', 'prompt_kind'):
            self.assertIn(field, message)
        self.assertEqual(self.w_keys(), [])

    async def test_invalid_recorded_source_logs_exact_field_and_does_not_use_current_goal(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.position = XYZ(490, 600, 0)
        await self.caller._remember_door_approach(self.q, self.target)
        session.transition['source_tasks'][id(self.q)] = (42, 99)
        with patch('src.questing.logger.debug') as log:
            self.assertFalse(await self.caller._recover_door_approach(
                self.q, self.target, source_entry=session.transition))
        message = next(c.args[0] for c in log.call_args_list if 'source-entry-invalid' in c.args[0])
        self.assertIn('source_task', message)
        self.assertIn('99', message)
        self.assertEqual(self.w_keys(), [])

    async def test_expired_replay_cannot_be_renewed_by_current_goal_evidence(self):
        self.q.quest_party_hitters = [self.h]
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.q)
        self.h.position = XYZ(490, 600, 0)
        self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
        self.clock += 31
        self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assertEqual(self.w_keys(), [])

    async def test_observed_headquarters_stair_uses_exact_route_when_goal_zone_is_missing(self):
        self.q.zone = 'Lemuria/Interiors/LM_Z06_Headquarters'
        self.target = XYZ(-681.4431762695312, 7998.80078125, -.00012207029794808477)
        self.q.quest_position.position.return_value = self.target
        self.q.position = XYZ(-650, 7880.8310546875, 1)
        self.destination = ''
        self.original.side_effect = lambda *_a, **_kw: None
        await self.caller.auto_quest_solo()
        self.assertEqual(len(self.w_keys()), 1)
        self.assertEqual(self.q.zone, 'Lemuria/Interiors/LM_Z06_Headquarters')
        self.assertEqual(self.q._quest_door_approach['attempts'], 1)

    async def test_observed_stair_route_does_not_classify_other_targets_or_zones(self):
        entrance = XYZ(-681.4431762695312, 7998.80078125, -.00012207029794808477)
        self.destination = ''
        for zone, target in (
                ('Lemuria/Interiors/LM_Z06_Headquarters', XYZ(entrance.x + 20, entrance.y, entrance.z)),
                ('Other/Interiors/Headquarters', entrance)):
            with self.subTest(zone=zone, target=target):
                self.q.zone = zone
                self.q.quest_position.position.return_value = target
                self.q.position = XYZ(target.x - 10, target.y, target.z)
                await self.caller._remember_door_approach(self.q, target)
                self.assertFalse(await self.caller._recover_door_approach(self.q, target))
        self.assertEqual(self.w_keys(), [])

    async def test_hitter_cannot_start_next_goal_even_when_group_is_ready(self):
        self.q.quest_party_hitters = [self.h]
        await self.enter()
        self.h.position = XYZ(490, 600, 0)
        await self.reader._remember_door_approach(self.h, self.target)
        self.assertFalse(await self.reader._recover_door_approach(self.h, self.target))
        self.assertEqual(self.w_keys(), [])

    async def test_replay_stops_on_local_task_or_area_change_during_original_tp(self):
        self.q.quest_party_hitters = [self.h]
        for change in ('task', 'area', 'loading', 'battle'):
            with self.subTest(change=change):
                session = await self.enter()
                await party.remember_dungeon_transition(self.q, self.target)
                self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
                await party.observe_dungeon_session(self.q)
                self.h.position = XYZ(378, 600, 0)
                def invalidate(*_args, **_kwargs):
                    if change == 'task':
                        self.h.goal_id.return_value = 9
                    elif change == 'area':
                        self.h.area_id += 1
                    elif change == 'loading':
                        self.h.is_loading.return_value = True
                    else:
                        self.h.in_battle.return_value = True
                self.original.side_effect = invalidate
                self.reader.teleport_to_quest_target = Quester.teleport_to_quest_target.__get__(self.reader)
                self.assertFalse(await self.reader.recover_dungeon_transition())
                self.assertFalse(get_client_automation_ownership(self.h).locked)
                self.assertEqual(self.w_keys(), [])
                party.clear_dungeon_session(self.h)
                self.q.zone = self.h.zone = 'Outside'
                self.q.area_id = self.h.area_id = 100
                self.h.goal_id.return_value = 7
                self.h.is_loading.return_value = False
                self.h.in_battle.return_value = False

    async def test_replay_invalid_source_token_logs_saved_and_actual_before_movement(self):
        self.q.quest_party_hitters = [self.h]
        await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.q)
        self.h.area_id += 1
        with patch('src.questing.logger.debug') as log:
            self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assertTrue(any('source_token' in c.args[0] and 'saved' in c.args[0]
                            and 'actual' in c.args[0] for c in log.call_args_list))
        self.assert_no_replay()

    async def test_peer_arrival_does_not_authorize_a_different_current_target(self):
        self.q.quest_party_hitters = [self.h]
        await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.h)
        target = XYZ(1500, 600, 0)
        self.q.quest_position.position.return_value = target
        self.q.position = XYZ(1490, 600, 0)
        self.destination = 'Dungeon/Room1'
        await self.caller._remember_door_approach(self.q, target)
        self.assertFalse(await self.caller._recover_door_approach(self.q, target))
        self.assertEqual(self.w_keys(), [])

    async def test_cancelled_w_releases_input_and_ownership(self):
        entered, released = asyncio.Event(), asyncio.Event()
        held = set()
        self.q.window_handle = 1
        async def send(key, seconds):
            await Client.send_key(self.q, key, seconds)
        self.q.send_key.side_effect = send
        def keydown(_window, key):
            held.add(key)
            entered.set()
        def keyup(_window, key):
            held.discard(key)
            released.set()
        # Execute actual send_key/timed_send_key cleanup, simulating only OS events.
        self.stack.enter_context(patch.object(wizutils, '_send_keydown', keydown))
        self.stack.enter_context(patch.object(wizutils, '_send_keyup', keyup))
        self.stack.enter_context(patch.object(wizutils, 'asyncio', SimpleNamespace(
            create_task=asyncio.create_task, sleep=REAL_SLEEP, CancelledError=asyncio.CancelledError)))
        task = asyncio.create_task(self.caller.auto_quest_solo())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(released.is_set())
            self.assertFalse(held)
            self.assertFalse(get_client_automation_ownership(self.q).locked)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
