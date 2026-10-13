"""The first member crossing must not strand the remaining source member."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import quest_party as party
from src.questing import Quester
from src.automation_ownership import get_client_automation_ownership
import test_dungeon_party_transition as transitions
from test_dungeon_local_tp import client


class ExitDeadlockTests(transitions.PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        for name in ('src.mainline_progress.log_mainline_progress',):
            self.stack.enter_context(patch(name, AsyncMock()))
        for name in ('src.mainline_recovery.maybe_recover_mainline',
                     'src.questing.close_npc_quest_menu', 'src.questing.close_automation_popup',
                     'src.questing.is_spiral_door_open', 'src.questing.is_potion_needed'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation',
                                             AsyncMock(return_value=False)))
        self.q.quest_position.position.return_value = XYZ(500, 600, 0)
        self.caller = Quester(self.q, [self.q], None)
        self.caller.teleport_to_quest_target = AsyncMock(side_effect=self.land)
        self.caller._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.caller.read_popup = AsyncMock(return_value='Press X to Use')
        self.caller._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Use Lever'))
        self.caller._quest_interaction_signature = AsyncMock(return_value=('entry',))
        self.caller._recover_quest_x_direction = AsyncMock(return_value=True)

    async def capture(self):
        session = await self.enter()
        self.assertTrue(await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0)))
        return session

    async def test_hitter_exit_does_not_block_actual_quester_tp_or_x(self):
        session = await self.enter()
        self.h.zone, self.h.area_id = 'Outside', 100
        await party.observe_dungeon_session(self.h)
        self.q.quest_party_probe_pending = True
        await self.caller.auto_quest_solo()
        self.caller.teleport_to_quest_target.assert_awaited_once()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_not_awaited()
        self.h.teleport.assert_not_awaited()
        self.assertIn(id(self.h), session.exited)
        self.assertNotIn(id(self.q), session.exited)

    async def test_exited_hitter_excluded_but_remaining_hitter_still_required(self):
        third = client(3, XYZ(1, 2, 0))
        third.quest_party_status_session = self.owner
        self.q.quest_party_hitters.append(third)
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h, third], evidence=True)
        for c in session.members:
            c.zone, c.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.h.zone, self.h.area_id = 'Outside', 100
        await party.observe_dungeon_session(self.h)
        self.assertTrue(await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0)))
        self.assertEqual(await self.caller.prepare_party_dungeon_entry(), [self.q, third])
        third.is_loading.return_value = True
        self.assertFalse(await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0)))
        self.assertEqual(await self.caller.prepare_party_dungeon_entry(), [])

    async def test_exited_hitter_old_postcombat_flag_does_not_block_remaining_roster(self):
        third = client(3, XYZ(1, 2, 0))
        third.quest_party_status_session = self.owner
        self.q.quest_party_hitters.append(third)
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h, third], evidence=True)
        for c in session.members:
            c.zone, c.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.h.zone, self.h.area_id = 'Outside', 100
        await party.observe_dungeon_session(self.h)
        self.h.post_combat_movement_active = True
        self.assertFalse(await self.caller._quest_party_probe_blocks_movement())
        self.assertTrue(self.h.post_combat_movement_active, 'Do not clear another flow flag')
        third.post_combat_movement_active = True
        self.assertTrue(await self.caller._quest_party_probe_blocks_movement())

    async def test_last_quester_loading_still_blocks_without_clearing_probe(self):
        await self.enter()
        self.h.zone = 'Outside'
        await party.observe_dungeon_session(self.h)
        self.q.quest_party_probe_pending = True
        self.q.is_loading.return_value = True
        self.assertTrue(await self.caller._quest_party_probe_blocks_movement())
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_hitter_first_movement_retains_source_before_it_crosses(self):
        session = await self.enter()
        async def cross(moving, target, **kwargs):
            self.assertIsNotNone(session.transition)
            self.assertEqual(session.transition['source_zone'], 'Dungeon/Room1')
            moving.zone, moving.area_id = 'Dungeon/Room2', 201
        self.reader.teleport_to_quest_target.side_effect = cross
        await self.reader.move_dungeon_member()

    async def test_interrupted_last_step_drains_then_resumes_only_unchanged_source(self):
        session = await self.enter()
        entered, drained = asyncio.Event(), asyncio.Event()
        async def last_step(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.reader.teleport_to_quest_target.side_effect = last_step
        worker = asyncio.create_task(self.reader.move_dungeon_member())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
            self.q.goal_id.return_value = 8
            self.q.quest_position.position.return_value = XYZ(20000, 30000, 0)
            self.assertFalse(await asyncio.wait_for(worker, 1))
            self.assertTrue(drained.is_set())
            self.assertFalse(get_client_automation_ownership(self.h).locked)
            self.reader.teleport_to_quest_target.side_effect = self.land
            self.assertTrue(await self.reader.recover_dungeon_transition())
            target = self.reader.teleport_to_quest_target.await_args.args[1]
            self.assertEqual((target.x, target.y, target.z), (500, 600, 0))
            self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
            self.q.send_key.assert_not_awaited()
            self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
            await party.observe_dungeon_session(self.h)
            self.assertTrue(await party.quest_party_members_ready(self.q))
            self.assertEqual(session.exited, set())
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_hitter_first_room_change_replays_quester_in_actual_worker(self):
        session = await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        self.h.goal_id.return_value = 8  # First arrival can already advance its own task.
        await party.observe_dungeon_session(self.h)
        self.q.quest_party_probe_pending = True
        async def cross(moving, target, **kwargs):
            self.assertIs(moving, self.q)
            self.assertEqual((target.x, target.y, target.z), (500, 600, 0))
            moving.position = target
        self.caller.teleport_to_quest_target.side_effect = cross
        await self.caller.auto_quest_solo()
        self.caller.teleport_to_quest_target.assert_awaited_once()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertTrue(self.q.quest_party_probe_pending, 'Sending TP/X is not reunion proof')
        self.assertEqual(session.exited, set())

    async def test_quester_first_goal_change_does_not_invalidate_unchanged_hitter_source(self):
        for change in ('quest', 'goal'):
            with self.subTest(change=change):
                self.setUp()
                session = await self.split_after_capture()
                if change == 'quest':
                    self.q.quest_id_value += 1
                else:
                    self.q.goal_id.return_value = 8
                self.q.quest_position.position.return_value = XYZ(20000, 30000, 0)
                self.assertTrue(await self.reader.recover_dungeon_transition())
                moved = self.reader.teleport_to_quest_target.await_args.args[1]
                self.assertEqual((moved.x, moved.y, moved.z), (500, 600, 0))
                self.assertNotIn(id(self.h), session.exited)

    async def test_hitter_first_source_safety_and_attempt_window_are_preserved(self):
        for block in ('loading', 'battle', 'dialogue', 'refill', 'recovery',
                      'area', 'goal', 'stop', 'expired'):
            with self.subTest(block=block):
                self.setUp()
                await self.capture()
                self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
                if block == 'loading':
                    self.q.is_loading.return_value = True
                elif block == 'battle':
                    self.q.in_battle.return_value = True
                elif block == 'dialogue':
                    self.caller._npc_dialogue_visible = AsyncMock(return_value=True)
                elif block == 'refill':
                    self.q.refilling_potions = True
                elif block == 'recovery':
                    self.q.quest_recovery_owner = 'manual-operation'
                elif block == 'area':
                    self.q.area_id += 1
                elif block == 'goal':
                    self.q.goal_id.return_value += 1
                elif block == 'stop':
                    self.q.questing_status = False
                else:
                    self.clock += 31
                self.assertFalse(await self.caller.recover_dungeon_transition())
                self.caller.teleport_to_quest_target.assert_not_awaited()
                self.q.send_key.assert_not_awaited()

    async def test_hitter_first_attempts_are_bounded_without_false_success_or_group_release(self):
        session = await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        self.q.quest_party_probe_pending = True
        await self.caller._quest_party_probe_blocks_movement()
        await self.caller._quest_party_probe_blocks_movement()
        self.assertEqual(self.q.send_key.await_count, 1)
        self.clock += 2
        await self.caller._quest_party_probe_blocks_movement()
        self.clock += 2
        await self.caller._quest_party_probe_blocks_movement()
        self.assertEqual(self.q.send_key.await_count, 2)
        self.assertEqual(session.transition['attempts'][id(self.q)], 2)
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_unreadable_source_area_fails_closed_without_stopping_worker(self):
        await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        area = await self.q.client_object.client_zone()
        area.read_base_address.side_effect = RuntimeError('area unavailable')
        self.assertFalse(await self.caller.recover_dungeon_transition())
        self.caller.teleport_to_quest_target.assert_not_awaited()
        self.q.send_key.assert_not_awaited()

    async def test_cancel_while_waiting_for_manual_ownership_does_not_release_manual_owner(self):
        await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        from src.automation_ownership import automation_owner
        held, release = asyncio.Event(), asyncio.Event()
        async def manual():
            async with automation_owner(self.q, 'manual'):
                held.set()
                await release.wait()
        manual_task = asyncio.create_task(manual())
        worker = None
        try:
            await asyncio.wait_for(held.wait(), 1)
            worker = asyncio.create_task(self.caller.recover_dungeon_transition())
            await transitions.REAL_SLEEP(0)
            self.q.questing_status = False
            await asyncio.wait_for(worker, 1)
            owner = get_client_automation_ownership(self.q)
            self.assertTrue(owner.locked)
            self.assertEqual(owner.owner_label, 'manual')
            self.caller.teleport_to_quest_target.assert_not_awaited()
            self.q.send_key.assert_not_awaited()
        finally:
            release.set()
            if worker is not None:
                worker.cancel()
            await asyncio.gather(*(t for t in (worker, manual_task) if t), return_exceptions=True)

    async def test_final_arrival_requires_instance_proof_before_shared_move(self):
        session = await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        await self.caller.recover_dungeon_transition()
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.q)
        await party.observe_dungeon_session(self.h)
        self.proof.return_value = False
        self.assertFalse(await party.quest_party_members_ready(self.q))
        self.reader.teleport_to_quest_target.reset_mock()
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.proof.return_value = True
        self.assertTrue(await party.quest_party_members_ready(self.q))
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assertEqual(session.exited, set())

    async def test_hitter_first_initial_entry_can_finish_quester_source_step(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.assertTrue(await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0)))
        self.h.zone, self.h.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.h)
        self.q.quest_party_probe_pending = True
        await self.caller.auto_quest_solo()
        self.caller.teleport_to_quest_target.assert_awaited_once()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(session.confirmed)

    async def test_internal_room_without_exit_evidence_keeps_cross_area_sync(self):
        session = await self.enter()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.h)
        self.assertNotIn(id(self.h), session.exited)
        self.assertTrue(party.dungeon_member_inside(self.h))

    async def test_transition_completion_is_recorded_per_client_only_after_actual_crossing(self):
        session = await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.h)
        self.assertIn(id(self.h), session.transition['arrivals'])
        self.assertNotIn(id(self.q), session.transition['arrivals'])
        await self.caller.recover_dungeon_transition()
        self.assertNotIn(id(self.q), session.transition['arrivals'], 'Input alone is not arrival')
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.is_loading.return_value = True
        await party.observe_dungeon_session(self.q)
        self.assertNotIn(id(self.q), session.transition['arrivals'])
        self.q.is_loading.return_value = False
        await party.observe_dungeon_session(self.q)
        self.assertEqual(session.transition['arrivals'][id(self.q)][0], 'Dungeon/Room2')
        self.assertEqual(session.exited, set())

    async def test_replay_cancel_drains_only_its_movement_and_ownership(self):
        await self.capture()
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        entered, released = asyncio.Event(), asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                released.set()
        self.caller.teleport_to_quest_target.side_effect = pending
        worker = asyncio.create_task(self.caller.recover_dungeon_transition())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.q.questing_status = False
            await asyncio.wait_for(worker, 1)
            self.assertTrue(released.is_set())
            self.assertFalse(get_client_automation_ownership(self.q).locked)
            self.q.send_key.assert_not_awaited()
            self.h.send_key.assert_not_awaited()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
