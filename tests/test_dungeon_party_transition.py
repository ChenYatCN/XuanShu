"""Shared dungeon goals and bounded recovery of the recorded source entrance."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import quest_party as party, utils
from src.paths import npc_range_path, team_up_button_path
from src.questing import Quester
from tests.test_dungeon_local_tp import DungeonFixture, client


REAL_SLEEP = asyncio.sleep


class PartyTransitionFixture(DungeonFixture):
    def setUp(self):
        super().setUp()
        self.h.quest_party_quester = self.q
        self.stack.enter_context(patch('src.questing.clients_share_live_area', self.proof))
        self.visible = self.stack.enter_context(patch(
            'src.questing.is_visible_by_path', AsyncMock(
                side_effect=lambda _client, path: path == npc_range_path)))
        self.title = self.stack.enter_context(patch(
            'src.questing.get_popup_title', AsyncMock(return_value='Door')))
        self.stack.enter_context(patch.object(Quester, 'read_popup', AsyncMock(return_value='进入')))
        self.stack.enter_context(patch.object(Quester, 'handle_questing_zone_change', AsyncMock()))
        self.clock = 100.0
        clock = SimpleNamespace(monotonic=lambda: self.clock)
        self.stack.enter_context(patch.object(party, 'time', clock, create=True))
        self.stack.enter_context(patch('src.questing.time', clock))
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        self.stack.enter_context(patch('src.questing.asyncio.sleep', tick))
        self.reader = Quester(self.h, [self.h], None)
        self.reader.teleport_to_quest_target = AsyncMock(side_effect=self.land)
        self.reader.auto_collect_rewrite = AsyncMock()

    async def land(self, moving, target, **_kwargs):
        moving.position = target

    async def split_after_capture(self):
        session = await self.enter()
        self.source_target = XYZ(500, 600, 0)
        self.q.quest_position.position.return_value = self.source_target
        await party.remember_dungeon_transition(self.q, self.source_target)
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.quest_position.position.return_value = XYZ(19000, 29000, 0)
        await party.observe_dungeon_session(self.q)
        return session

    def assert_no_replay(self):
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    def assert_shared_move(self, reader, hitter, quester):
        self.assertEqual(reader.teleport_to_quest_target.await_count, 1)
        call = reader.teleport_to_quest_target.await_args
        self.assertIs(call.args[0], hitter)
        target = quester.quest_position.position.return_value
        self.assertEqual((call.args[1].x, call.args[1].y, call.args[1].z),
                         (target.x, target.y, target.z))
        self.assertIs(call.kwargs.get('leader_client'), quester)


class SharedGoalTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_same_area_uses_assigned_quester_goal_even_when_hitter_arrow_differs(self):
        await self.enter()
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assert_shared_move(self.reader, self.h, self.q)
        self.h.quest_position.position.assert_not_awaited()

    async def test_split_rooms_never_move_to_quester_destination_or_hitter_arrow(self):
        await self.enter()
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.reader.auto_collect_rewrite.assert_not_awaited()

    async def test_same_zone_without_instance_proof_rejects_shared_movement(self):
        await self.enter()
        self.proof.return_value = False
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_quester_potion_excursion_does_not_supply_a_shop_target(self):
        session = await self.enter()
        session.potion_areas[id(self.q)] = (self.q.zone, self.q.area_id)
        self.q.zone, self.q.area_id = 'PotionShop', 300
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.reader.auto_collect_rewrite.assert_not_awaited()

    async def test_one_unproven_member_blocks_the_group_shared_move(self):
        third = client(3, XYZ(1, 2, 0))
        third.quest_party_quester = self.q
        third.quest_party_status_session = self.owner
        self.q.quest_party_hitters.append(third)
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h, third], evidence=True)
        for member in (self.q, self.h, third):
            member.zone, member.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        self.proof.side_effect = lambda first, second: second is not third
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_groups_use_only_their_own_quester(self):
        await self.enter()
        other_q, other_h = client(3, XYZ(3333, 4444, 0)), client(4, XYZ(1, 2, 0))
        other_q.quest_party_hitters = [other_h]
        other_h.quest_party_quester = other_q
        other_h.quest_party_status_session = object()
        other = await party.begin_dungeon_entry(other_q, [other_q, other_h], evidence=True)
        for member in (other_q, other_h):
            member.zone, member.area_id = 'Dungeon/Room1', 700
        await party.observe_dungeon_session(other_q)
        self.assertTrue(other.confirmed)
        other_reader = Quester(other_h, [other_h], None)
        other_reader.teleport_to_quest_target = AsyncMock()
        self.assertTrue(await other_reader.move_dungeon_member())
        self.assertTrue(await self.reader.move_dungeon_member())
        self.assert_shared_move(other_reader, other_h, other_q)
        self.assert_shared_move(self.reader, self.h, self.q)

    async def test_movement_is_cancelled_when_shared_goal_or_control_changes(self):
        for change in ('goal', 'target', 'room', 'assignment', 'session', 'recovery', 'stop'):
            with self.subTest(change=change):
                self.setUp()
                await self.enter()
                entered, cancelled = asyncio.Event(), asyncio.Event()

                async def moving(*_args, **_kwargs):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

                self.reader.teleport_to_quest_target.side_effect = moving
                worker = asyncio.create_task(self.reader.move_dungeon_member())
                try:
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    if change == 'goal':
                        self.q.goal_id.return_value = 8
                    elif change == 'target':
                        self.q.quest_position.position.return_value = XYZ(999, 222, 0)
                    elif change == 'room':
                        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
                    elif change == 'assignment':
                        self.q.quest_party_hitters = []
                    elif change == 'session':
                        party.clear_dungeon_session(self.h)
                    elif change == 'recovery':
                        self.h.quest_recovery_owner = 'another-recovery'
                    else:
                        self.h.questing_status = False
                    await asyncio.wait_for(worker, timeout=1)
                    self.assertTrue(cancelled.is_set(), 'obsolete movement task was left alive')
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)


class SourceEntranceTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_valid_source_room_replays_only_recorded_entrance_and_hitter_x(self):
        session = await self.split_after_capture()
        self.assertTrue(await self.reader.recover_dungeon_transition())
        self.reader.teleport_to_quest_target.assert_awaited_once()
        move = self.reader.teleport_to_quest_target.await_args
        self.assertIs(move.args[0], self.h)
        self.assertEqual((move.args[1].x, move.args[1].y, move.args[1].z),
                         (self.source_target.x, self.source_target.y, self.source_target.z))
        self.assertTrue(move.kwargs.get('leader_client') is None
                        or move.kwargs.get('leader_client') is self.h)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.q.send_key.assert_not_awaited()
        self.assertIs(party.dungeon_session_for(self.h), session)
        self.assertNotIn(id(self.h), session.exited)

    async def test_source_quest_goal_area_or_session_changes_reject_replay(self):
        for change in ('source_area', 'source_room', 'hitter_quest', 'hitter_goal',
                       'assignment', 'owner', 'stop'):
            with self.subTest(change=change):
                self.setUp()
                session = await self.split_after_capture()
                if change == 'source_area':
                    self.h.area_id += 1
                elif change == 'source_room':
                    self.h.zone = 'Dungeon/OtherRoom'
                elif change == 'hitter_quest':
                    self.h.quest_id_value += 1
                elif change == 'hitter_goal':
                    self.h.goal_id.return_value += 1
                elif change == 'assignment':
                    self.q.quest_party_hitters = []
                elif change == 'owner':
                    session.owner = object()
                else:
                    self.h.questing_status = False
                self.assertFalse(await self.reader.recover_dungeon_transition())
                self.assert_no_replay()

    async def test_loading_generation_invalidates_source_token_even_after_return(self):
        await self.split_after_capture()
        self.h.is_loading.return_value = True
        await utils.observe_party_area(self.h)
        self.h.is_loading.return_value = False
        await utils.observe_party_area(self.h)
        self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assert_no_replay()

    async def test_source_movement_cancels_when_its_task_or_instance_changes(self):
        for change in ('goal', 'area', 'stop'):
            with self.subTest(change=change):
                self.setUp()
                await self.split_after_capture()
                entered, cancelled = asyncio.Event(), asyncio.Event()

                async def moving(*_args, **_kwargs):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

                self.reader.teleport_to_quest_target.side_effect = moving
                worker = asyncio.create_task(self.reader.recover_dungeon_transition())
                try:
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    if change == 'goal':
                        self.h.goal_id.return_value = 8
                    elif change == 'area':
                        self.h.area_id += 1
                    else:
                        self.h.questing_status = False
                    await asyncio.wait_for(worker, timeout=1)
                    self.assertTrue(cancelled.is_set())
                    self.h.send_key.assert_not_awaited()
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    async def test_capture_requires_instance_proof_and_does_not_guess_after_split(self):
        await self.enter()
        self.proof.return_value = False
        await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0))
        self.q.zone = 'Dungeon/Room2'
        self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assert_no_replay()

    async def test_nonfinite_entrance_is_never_retained_for_replay(self):
        for coordinate in (float('nan'), float('inf')):
            with self.subTest(coordinate=coordinate):
                self.setUp()
                await self.enter()
                await party.remember_dungeon_transition(self.q, XYZ(coordinate, 600, 0))
                self.q.zone = 'Dungeon/Room2'
                self.assertFalse(await self.reader.recover_dungeon_transition())
                self.assert_no_replay()

    async def test_unreadable_numeric_source_tasks_do_not_enable_replay(self):
        for field in ('quester_quest', 'hitter_quest', 'hitter_goal'):
            with self.subTest(field=field):
                self.setUp()
                await self.enter()
                if field == 'quester_quest':
                    self.q.quest_id_value = None
                elif field == 'hitter_quest':
                    self.h.quest_id_value = None
                else:
                    self.h.goal_id.return_value = None
                await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0))
                self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
                await self.reader.recover_dungeon_transition()
                self.assert_no_replay()

    async def test_unreadable_source_area_address_does_not_preserve_replay_proof(self):
        await self.enter()
        area = await self.h.client_object.client_zone()
        area.read_base_address.side_effect = None
        area.read_base_address.return_value = None
        await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0))
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await self.reader.recover_dungeon_transition()
        self.assert_no_replay()

    async def test_world_gate_never_receives_hitter_replay_x(self):
        await self.split_after_capture()
        self.title.return_value = '世界之门'
        await self.reader.recover_dungeon_transition()
        self.h.send_key.assert_not_awaited()
        self.q.send_key.assert_not_awaited()

    async def test_expired_window_never_restarts_old_entrance_movement(self):
        await self.split_after_capture()
        self.clock += 31
        self.assertFalse(await self.reader.recover_dungeon_transition())
        self.assert_no_replay()

    async def test_two_attempt_limit_and_cooldown_bound_source_replay(self):
        await self.split_after_capture()
        await self.reader.recover_dungeon_transition()
        await self.reader.recover_dungeon_transition()
        self.assertEqual(self.h.send_key.await_count, 1)
        self.clock += 2
        await self.reader.recover_dungeon_transition()
        self.assertEqual(self.h.send_key.await_count, 2)
        self.clock += 2
        await self.reader.recover_dungeon_transition()
        self.assertEqual(self.h.send_key.await_count, 2)
        self.assertEqual(self.reader.teleport_to_quest_target.await_count, 2)

    async def test_arrived_member_does_not_replay_source_entrance(self):
        await self.split_after_capture()
        self.h.zone, self.h.area_id = self.q.zone, self.q.area_id
        await self.reader.recover_dungeon_transition()
        self.assert_no_replay()

    async def test_other_group_cannot_consume_retained_source_record(self):
        await self.split_after_capture()
        stranger = client(5, XYZ(50, 50, 0))
        stranger.zone, stranger.area_id = self.h.zone, self.h.area_id
        stranger.quest_party_quester = self.q
        stranger.quest_party_dungeon = self.h.quest_party_dungeon
        reader = Quester(stranger, [stranger], None)
        reader.teleport_to_quest_target = AsyncMock()
        self.assertFalse(await reader.recover_dungeon_transition())
        reader.teleport_to_quest_target.assert_not_awaited()
        stranger.send_key.assert_not_awaited()


class FullEntryTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    async def test_ready_group_is_not_blocked_by_entrance_label_matching(self):
        await self.enter()
        self.title.side_effect = lambda member: 'Untranslated Door' if member is self.q else '另一名称'
        reader = Quester(self.q, [self.q], None)
        members = await reader.prepare_party_dungeon_entry()
        self.assertEqual(list(map(id, members)), [id(self.q), id(self.h)])

    async def test_busy_or_missing_hitter_does_not_produce_partial_entry_group(self):
        for blocked in ('loading', 'battle', 'refill', 'recovery', 'stopped', 'other_room', 'instance'):
            with self.subTest(blocked=blocked):
                self.setUp()
                await self.enter()
                if blocked == 'loading':
                    self.h.is_loading.return_value = True
                elif blocked == 'battle':
                    self.h.in_battle.return_value = True
                    self.stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=False)))
                elif blocked == 'refill':
                    self.h.refilling_potions = True
                elif blocked == 'recovery':
                    self.h.quest_recovery_owner = 'existing-recovery'
                elif blocked == 'stopped':
                    self.h.questing_status = False
                elif blocked == 'other_room':
                    self.h.zone = 'Dungeon/Room2'
                else:
                    self.proof.return_value = False
                reader = Quester(self.q, [self.q], None)
                self.assertEqual(await reader.prepare_party_dungeon_entry(), [])
                self.q.send_key.assert_not_awaited()
                self.h.send_key.assert_not_awaited()

    async def test_rejected_entrance_landing_does_not_grant_group_x(self):
        await self.enter()
        self.h.position = XYZ(10000, 20000, 0)
        self.h.teleport.side_effect = None
        reader = Quester(self.q, [self.q], None)
        self.assertEqual(await reader.prepare_party_dungeon_entry(), [])

    async def test_entrance_distance_is_rechecked_after_preparation_wait(self):
        await self.enter()
        async def displaced(delay):
            self.clock += delay
            if delay == .5:
                self.h.position = XYZ(20000, 20000, 0)
            await REAL_SLEEP(0)
        reader = Quester(self.q, [self.q], None)
        with patch('src.questing.asyncio.sleep', displaced):
            members = await reader.prepare_party_dungeon_entry()
        self.assertEqual(len(members), 0)


class ActualCallerTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.stack.enter_context(patch('src.mainline_recovery.maybe_recover_mainline', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.close_automation_popup', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_potion_needed', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)))
        self.q.quest_position.position.return_value = XYZ(500, 600, 0)
        self.caller = Quester(self.q, [self.q], None)
        self.caller.teleport_to_quest_target = AsyncMock(side_effect=self.land)
        self.caller._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.caller.read_popup = AsyncMock(return_value='Press X to Use')
        self.caller._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Use Lever'))
        self.caller._quest_interaction_signature = AsyncMock(return_value=('entry',))
        self.caller._recover_quest_x_direction = AsyncMock(return_value=True)

    async def test_actual_caller_captures_source_before_automatic_room_movement(self):
        session = await self.enter()
        async def switched(member, target):
            self.assertIsNotNone(session.transition)
            self.assertEqual(session.transition['source_zone'], 'Dungeon/Room1')
            saved = session.transition['target']
            self.assertEqual((saved.x, saved.y, saved.z), (target.x, target.y, target.z))
            member.zone, member.area_id = 'Dungeon/Room2', 201
        self.caller.teleport_to_quest_target.side_effect = switched
        await self.caller.auto_quest_solo()
        self.assertEqual(self.caller.teleport_to_quest_target.await_count, 1)
        self.assertTrue(self.q.quest_party_probe_pending)
        self.q.send_key.assert_not_awaited()

    async def test_failed_real_capture_does_not_start_old_quester_movement(self):
        await self.enter()
        self.h.goal_id.side_effect = (7, 8)
        async def switched(member, _target):
            member.zone, member.area_id = 'Dungeon/Room2', 201
        self.caller.teleport_to_quest_target.side_effect = switched
        await self.caller.auto_quest_solo()
        self.assertEqual(self.caller.teleport_to_quest_target.await_count, 0)

    async def test_failed_generic_x_recapture_does_not_send_group_input(self):
        await self.enter()
        calls = 0
        remember = party.remember_dungeon_transition
        async def capture(quester, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.h.is_loading.return_value = True
            return await remember(quester, target)
        with patch('src.questing.remember_dungeon_transition', capture):
            await self.caller.auto_quest_solo()
        self.assertEqual(calls, 2)
        self.assertEqual(self.q.send_key.await_count, 0)
        self.assertEqual(self.h.send_key.await_count, 0)

    async def test_actual_generic_world_gate_input_is_quester_only(self):
        await self.enter()
        self.title.return_value = '世界之门'
        await self.caller.auto_quest_solo()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_not_awaited()

    async def test_failed_initial_entry_capture_closes_pending_session_without_x(self):
        self.caller.read_popup.return_value = 'Press X to Enter'
        self.visible.side_effect = lambda _client, path: path in (npc_range_path, team_up_button_path)
        self.h.goal_id.side_effect = (7, 8)
        await self.caller.auto_quest_solo()
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_initial_entry_loading_wait_returns_with_unconfirmed_session(self):
        self.caller.read_popup.return_value = 'Press X to Enter'
        self.visible.side_effect = lambda _client, path: path in (npc_range_path, team_up_button_path)
        async def entered(member, key, _seconds):
            if key == Keycode.X:
                member.zone, member.area_id = 'Dungeon/Room1', 200
                member.is_loading.return_value = True
        async def q_entered(key, seconds):
            await entered(self.q, key, seconds)
        async def h_entered(key, seconds):
            await entered(self.h, key, seconds)
        self.q.send_key.side_effect, self.h.send_key.side_effect = q_entered, h_entered
        started = self.clock
        await asyncio.wait_for(self.caller.auto_quest_solo(), timeout=1)
        self.assertGreaterEqual(self.clock - started, 15)
        self.assertLess(self.clock - started, 20)
        session = party.dungeon_session_for(self.q)
        self.assertIsNotNone(session)
        self.assertFalse(session.confirmed)
        self.assertEqual(session.exited, set())
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)


if __name__ == '__main__':
    unittest.main()
