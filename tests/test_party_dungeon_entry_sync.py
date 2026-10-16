"""Strict sigil readiness and bounded per-member entry reset; all inputs mocked."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.questing import Quester
from src import quest_party as party
from src.paths import npc_range_path, team_up_button_path
from src.automation_ownership import get_client_automation_ownership
from tests.test_dungeon_local_tp import DungeonFixture, client, REAL_SLEEP

REAL_TIMEOUT = asyncio.timeout


class EntrySyncTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.target = XYZ(500, 600, 0)
        self.events = []
        self.clock = 100
        self.on_reset = lambda c: None
        self.on_approach = lambda c: setattr(c, 'prompt', True)
        for c in (self.q, self.h):
            c.position = self.target
            c.quest_position.position.return_value = self.target
            c.prompt, c.teamup, c.title_text = True, True, 'Dungeon A'
            async def tp(point, c=c):
                self.events.append((c, 'tp', tuple(point)))
                c.position = point
                if point.z == -10000:
                    c.prompt = False
                    self.on_reset(c)
            c.teleport = AsyncMock(side_effect=tp)
        self.q.hotkey_quest_clients = self.h.hotkey_quest_clients = [self.q, self.h]
        self.h.quest_party_quester = self.q
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        self.stack.enter_context(patch('src.questing.asyncio.sleep', tick))
        self.stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch.object(party, 'time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(
            side_effect=lambda c, path: c.prompt and path in (npc_range_path, team_up_button_path)
            and (path != team_up_button_path or c.teamup))))
        self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(side_effect=lambda c: c.title_text)))
        self.stack.enter_context(patch.object(Quester, 'read_popup', AsyncMock(
            side_effect=lambda c: 'Press X to Enter' if c.prompt else '')))
        self.stack.enter_context(patch.object(Quester, '_npc_dialogue_visible', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, '_dungeon_quest_snapshot', AsyncMock(return_value=(42,7,'Enter'))))
        self.reader = Quester(self.q, [self.q], None)
        self.reader.read_quest_txt = AsyncMock(side_effect=lambda _: 'Enter ' + self.q.title_text)
        self.stack.enter_context(patch('src.questing.get_quest_name',
            AsyncMock(side_effect=lambda _: 'Enter ' + self.q.title_text)))
        async def approach(c, target, **kwargs):
            self.events.append((c, 'approach', tuple(target)))
            c.position = target
            self.on_approach(c)
        self.reader.teleport_to_quest_target = AsyncMock(side_effect=approach)

    def resets(self):
        return [c for c, kind, point in self.events if kind == 'tp' and point[2] == -10000]

    async def test_both_ready_are_not_moved(self):
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q,self.h])
        self.assertEqual(self.events, [])

    async def test_only_missing_hitter_resets_then_approaches_before_ready(self):
        self.h.prompt = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q,self.h])
        self.assertEqual(self.resets(), [self.h])
        self.assertEqual([kind for _, kind, _ in self.events], ['tp','approach'])
        self.q.teleport.assert_not_awaited()
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_missing_quester_is_recovered_without_moving_ready_hitter(self):
        self.q.prompt = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q,self.h])
        self.assertEqual(self.resets(), [self.q])
        self.h.teleport.assert_not_awaited()

    async def test_different_entrance_title_never_grants_group_input(self):
        self.h.title_text = 'Dungeon B'
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertEqual(self.resets(), [])

    async def test_final_x_rechecks_missing_prompt_and_loading(self):
        for change in ('prompt', 'loading', 'battle', 'solo', 'task', 'area', 'stop'):
            with self.subTest(change=change):
                self.setUp()
                members = await self.reader.prepare_party_dungeon_entry()
                if change == 'prompt': self.h.prompt = False
                if change == 'loading': self.h.is_loading.return_value = True
                if change == 'battle': self.h.in_battle.return_value = True
                if change == 'solo': self.h.in_solo_zone = True
                if change == 'task': self.h.goal_id.return_value = 8
                if change == 'area': self.h.area_id = 999
                if change == 'stop': self.q.questing_status = False
                self.assertFalse(await self.reader._send_dungeon_entry_x(members))
                self.q.send_key.assert_not_awaited()
                self.h.send_key.assert_not_awaited()

    async def test_both_ready_x_is_input_only_until_each_arrival_is_proven(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
        session = party.dungeon_session_for(self.q)
        self.assertFalse(session.confirmed)
        self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.h.zone, self.h.area_id = 'Dungeon/Room1', 200
        self.proof.return_value = False
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.proof.return_value = True
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)

    async def test_exhaustion_survives_worker_recreation_and_expired_source(self):
        self.h.prompt = False
        self.on_approach = lambda c: None
        for _ in range(5):
            self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
            self.clock += 2
            self.reader = Quester(self.q, [self.q], None)
            self.reader.teleport_to_quest_target = AsyncMock(side_effect=lambda *a, **kw: None)
        self.assertEqual(self.resets(), [self.h, self.h])
        self.clock += 40
        await self.reader.prepare_party_dungeon_entry()
        self.assertEqual(self.resets(), [self.h, self.h])
        self.q.send_key.assert_not_awaited()

    async def test_one_already_entered_uses_source_recovery_without_reset(self):
        await self.reader.prepare_party_dungeon_entry()
        self.h.zone, self.h.area_id = 'Dungeon/Room1', 200
        self.reader.recover_dungeon_transition = AsyncMock(return_value=True)
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.reader.recover_dungeon_transition.assert_awaited_once()
        self.assertEqual(self.resets(), [])
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q,self.h]))

    async def test_zone_change_during_reset_stops_approach_without_old_restore(self):
        self.h.prompt = False
        self.on_reset = lambda c: setattr(c, 'zone', 'Dungeon/Room1')
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertEqual(self.resets(), [self.h])
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.assertEqual(len(self.events), 1)
        self.assertFalse(self.q.quest_party_entry_active)

    async def test_task_change_during_reset_stops_old_approach(self):
        self.h.prompt = False
        self.on_reset = lambda c: self.q.goal_id.configure_mock(return_value=8)
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.q.send_key.assert_not_awaited()

    async def test_cancel_drains_approach_and_releases_owners_with_budget_spent(self):
        self.h.prompt = False
        started, released = asyncio.Event(), asyncio.Event()
        async def movement(*a, **kw):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        self.reader.teleport_to_quest_target.side_effect = movement
        worker = asyncio.create_task(self.reader.prepare_party_dungeon_entry())
        await asyncio.wait_for(started.wait(), 2)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError): await worker
        self.assertTrue(released.is_set())
        self.assertFalse(self.q.quest_party_entry_active)
        self.assertTrue(all(not get_client_automation_ownership(c).locked for c in (self.q,self.h)))
        self.assertEqual(self.q._quest_party_entry['source']['attempts'][id(self.h)], 1)
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_no_cross_instance_reset(self):
        self.h.prompt = False
        self.proof.return_value = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertEqual(self.events, [])

    async def test_world_gate_or_npc_is_not_a_missing_dungeon_prompt(self):
        for title, action in (('世界之门','Press X to Enter'), ('NPC','Press X to Talk')):
            with self.subTest(title=title):
                self.setUp()
                self.h.title_text = title
                self.reader.read_popup = AsyncMock(side_effect=lambda c: action if c is self.h else 'Press X to Enter')
                self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
                self.assertEqual(self.resets(), [])

    async def test_underground_coordinate_with_stale_ui_never_gets_x(self):
        await self.reader.prepare_party_dungeon_entry()
        self.h.position = XYZ(0,0,-10000)
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q,self.h]))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_unproved_missing_prompt_uses_navigation_before_entry_preparation(self):
        self.q.prompt = False
        self.on_approach = lambda c: None
        for name in ('src.mainline_progress.log_mainline_progress', 'src.questing.close_npc_quest_menu',
                     'src.questing.close_automation_popup', 'src.questing.is_spiral_door_open',
                     'src.questing.is_potion_needed', 'src.mainline_recovery.maybe_recover_mainline'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.reader._ensure_quest_pet_mode = AsyncMock(return_value=True)
        await self.reader.auto_quest_solo()
        self.assertEqual(self.resets(), [])
        self.reader.teleport_to_quest_target.assert_awaited_once()
        self.h.teleport.assert_not_awaited()
        self.q.send_key.assert_not_awaited()

    async def test_recovery_may_not_consume_another_groups_member(self):
        await self.reader.prepare_party_dungeon_entry()
        stranger = client(4, self.target)
        self.q.hotkey_quest_clients = [self.q, stranger]
        self.h.prompt = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertEqual(self.resets(), [])

    async def test_earlier_member_losing_prompt_during_peer_read_blocks_x(self):
        members = await self.reader.prepare_party_dungeon_entry()
        async def read(c):
            if c is self.h:
                self.q.prompt = False
            return 'Press X to Enter'
        self.reader.read_popup = AsyncMock(side_effect=read)
        self.assertFalse(await self.reader._send_dungeon_entry_x(members))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_actual_caller_confirms_fast_arrivals_without_observed_loading(self):
        for name in ('src.mainline_progress.log_mainline_progress', 'src.questing.close_npc_quest_menu',
                     'src.questing.close_automation_popup', 'src.questing.is_spiral_door_open',
                     'src.questing.is_potion_needed', 'src.mainline_recovery.maybe_recover_mainline'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.reader._ensure_quest_pet_mode = AsyncMock(return_value=True)
        self.reader._maybe_photo_giant_vat = AsyncMock(return_value=False)
        for c in (self.q,self.h):
            async def enter(key, seconds, c=c):
                if key == Keycode.X:
                    c.zone, c.area_id, c.prompt = 'Dungeon/Room1', 200, False
            c.send_key = AsyncMock(side_effect=enter)
        before = self.clock
        await self.reader.auto_quest_solo()
        self.assertTrue(party.dungeon_session_for(self.q).confirmed)
        self.assertLess(self.clock - before, 5)
        self.assertEqual(self.events, [])
        self.q.send_key.assert_awaited_once_with(Keycode.X,.1)
        self.h.send_key.assert_awaited_once_with(Keycode.X,.1)

    async def test_both_missing_after_recorded_entry_recover_without_any_early_x(self):
        await self.reader.prepare_party_dungeon_entry()
        self.q.prompt = self.h.prompt = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q,self.h])
        self.assertEqual(self.resets(), [self.q,self.h])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_two_groups_recover_only_their_own_members(self):
        other_q, other_h = client(3,self.target), client(4,self.target)
        other_q.quest_party_hitters = [other_h]
        other_h.quest_party_quester = other_q
        other_h.quest_party_status_session = object()
        for c in (other_q,other_h):
            c.position, c.prompt, c.teamup, c.title_text = self.target, True, True, 'Dungeon A'
            c.hotkey_quest_clients = [other_q,other_h]
            async def tp(point, c=c):
                self.events.append((c,'tp',tuple(point)))
                c.position = point
                c.prompt = False
            c.teleport = AsyncMock(side_effect=tp)
        self.h.prompt = other_h.prompt = False
        other = Quester(other_q,[self.q,self.h,other_q,other_h],None)
        other.teleport_to_quest_target = self.reader.teleport_to_quest_target
        a,b = await asyncio.gather(self.reader.prepare_party_dungeon_entry(),other.prepare_party_dungeon_entry())
        self.assertEqual(a,[self.q,self.h])
        self.assertEqual(b,[other_q,other_h])
        self.assertEqual(set(map(id,self.resets())),{id(self.h),id(other_h)})
        self.q.teleport.assert_not_awaited()
        other_q.teleport.assert_not_awaited()

    async def test_cancellation_during_underground_tp_restores_own_origin(self):
        self.h.prompt = False
        origin = self.h.position
        started, finish_tp = asyncio.Event(), asyncio.Event()
        async def tp(point):
            self.h.position = point
            if point.z == -10000:
                self.h.prompt = False
                started.set()
                await finish_tp.wait()
        self.h.teleport.side_effect = tp
        worker = asyncio.create_task(self.reader.prepare_party_dungeon_entry())
        await asyncio.wait_for(started.wait(),2)
        worker.cancel()
        finish_tp.set()
        with self.assertRaises(asyncio.CancelledError): await worker
        self.assertEqual(tuple(self.h.position),tuple(origin))
        self.reader.teleport_to_quest_target.assert_not_awaited()
        self.q.send_key.assert_not_awaited()
        self.assertFalse(self.q.quest_party_entry_active)
        self.assertTrue(all(not get_client_automation_ownership(c).locked for c in (self.q,self.h)))

    async def test_owner_acquisition_race_is_bounded_for_reset_and_x(self):
        members = await self.reader.prepare_party_dungeon_entry()
        @asynccontextmanager
        async def occupied(*args):
            await asyncio.Event().wait()
            yield
        with patch('src.questing.automation_owner',occupied), patch('src.questing.asyncio.timeout',
                side_effect=lambda _:REAL_TIMEOUT(.02)):
            self.assertFalse(await asyncio.wait_for(self.reader._send_dungeon_entry_x(members),.2))
            self.h.prompt = False
            self.assertEqual(await asyncio.wait_for(self.reader.prepare_party_dungeon_entry(),.2),[])
        self.assertFalse(self.q.quest_party_entry_active)
        self.assertEqual(self.resets(),[])
        self.q.send_key.assert_not_awaited()

    async def test_actual_caller_preserves_source_budget_at_a_nearby_ready_landing(self):
        self.q.prompt = False
        async def approach(c,target,**kw):
            c.position = XYZ(target.x+2,target.y,target.z)
            c.prompt = True
        self.reader.teleport_to_quest_target.side_effect = approach
        for name in ('src.mainline_progress.log_mainline_progress', 'src.questing.close_npc_quest_menu',
                     'src.questing.close_automation_popup', 'src.questing.is_spiral_door_open',
                     'src.questing.is_potion_needed', 'src.mainline_recovery.maybe_recover_mainline'):
            self.stack.enter_context(patch(name,AsyncMock(return_value=False)))
        self.reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.reader._ensure_quest_pet_mode = AsyncMock(return_value=True)
        self.reader._maybe_photo_giant_vat = AsyncMock(return_value=False)
        checked = []
        async def enter(key,seconds):
            if key == Keycode.X:
                state = self.q._quest_party_entry
                checked.append(state['session'].transition is state['source'])
                self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
                self.q.is_loading.return_value = True
        self.q.send_key.side_effect = enter
        await self.reader.auto_quest_solo()
        self.assertEqual(checked,[True])
        state = self.q._quest_party_entry
        self.assertIs(state['session'].transition,state['source'])
        self.assertEqual(state['source']['attempts'].get(id(self.q), 0), 0)
        self.reader.teleport_to_quest_target.assert_awaited_once()  # Normal navigation, no unproved entry recovery.


if __name__ == '__main__':
    unittest.main()
