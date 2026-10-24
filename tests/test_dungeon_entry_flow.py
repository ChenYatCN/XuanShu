"""Trello 73: ready entrance flows and finite shared-X lifecycle; no live input."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import quest_party as party, utils
from loguru import logger
from src.teleport_math import _movement_task_snapshot
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.paths import team_up_wait_path
from tests.test_party_dungeon_entry_sync import EntrySyncTests as Base
from tests.test_dungeon_local_tp import client

REAL_TIMEOUT = asyncio.timeout


class EntryFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.q.title_text = self.h.title_text = 'Cannon Ledge'

    async def test_initial_ready_hitter_task_token_jitter_still_sends_shared_x(self):
        from itertools import count
        first = True
        for prepared in (False, True):
            for jitter in ('task-change', 'task-unreadable', 'token-change', 'token-unreadable'):
                with self.subTest(prepared=prepared, jitter=jitter):
                    if not first:
                        self.stack.close()
                        self.setUp()
                    first = False
                    if prepared:
                        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
                    if jitter.startswith('task'):
                        changes = count(8)
                        self.h.goal_id.side_effect = (lambda: next(changes)) if jitter == 'task-change' else ValueError('transient task read')
                    else:
                        original = utils._party_area_token
                        changes = count(10)
                        async def token(member):
                            value = await original(member)
                            if member is self.h:
                                if jitter == 'token-unreadable':
                                    raise ValueError('transient token read')
                                return (*value[:3], next(changes))
                            return value
                        for name in ('src.utils._party_area_token', 'src.questing._party_area_token'):
                            self.stack.enter_context(patch(name, token))
                    lines = []
                    sink = logger.add(lambda message: lines.append(str(message)), level='DEBUG')
                    try:
                        members = await self.reader.prepare_party_dungeon_entry()
                        self.assertEqual(members, [self.q, self.h])
                        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
                    finally:
                        logger.remove(sink)
                    self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
                    self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
                    self.assertTrue(any("phase=shared-x members=['p1', 'p2']" in line for line in lines))

    def setup_caller(self):
        for name in ('src.mainline_progress.log_mainline_progress', 'src.questing.close_npc_quest_menu',
                     'src.questing.close_automation_popup', 'src.questing.is_spiral_door_open',
                     'src.questing.is_potion_needed', 'src.mainline_recovery.maybe_recover_mainline'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.reader._ensure_quest_pet_mode = AsyncMock(return_value=True)
        self.reader._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Enter Cannon Ledge')))
        self.reader.read_popup = AsyncMock(side_effect=lambda c: '点击 进入' if c.prompt else '')
        self.reader.read_quest_txt = AsyncMock(return_value='Enter Cannon Ledge')

    def fast_arrival(self):
        for c in (self.q, self.h):
            async def enter(key, seconds, c=c):
                if key == Keycode.X:
                    c.zone, c.area_id, c.prompt = 'Dungeon/Room1', 200, False
            c.send_key.side_effect = enter

    async def send(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.assertEqual(members, [self.q, self.h])
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        return party.dungeon_session_for(self.q)

    async def test_initial_entry_missing_hitter_waits_then_shared_x_after_ready(self):
        self.h.prompt = self.h.teamup = False
        self.h.position = self.target
        self.on_approach = lambda c: None
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.assertEqual(self.resets(), [self.h])
        self.q.teleport.assert_not_awaited()
        self.h.prompt = self.h.teamup = True
        await self.send()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_initial_entry_distant_hitter_prepares_without_quester_x(self):
        self.h.prompt = self.h.teamup = False
        self.h.position = XYZ(0, 0, -10000)
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.assertEqual(self.resets(), [self.h])
        self.q.teleport.assert_not_awaited()

    async def test_initial_entry_follower_loading_blocks_quester(self):
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.h.prompt = self.h.teamup = False
        self.h.is_loading.return_value = True
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.h.is_loading.return_value = False
        self.h.prompt = self.h.teamup = True
        await self.send()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_initial_entry_unreadable_hitter_arrow_still_requires_its_prompt(self):
        self.h.prompt = self.h.teamup = False
        self.h.quest_position.position.side_effect = ValueError('position unavailable')
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.h.prompt = self.h.teamup = True
        await self.send()
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_initial_entry_mixed_sentinel_and_valid_xyz_waits_for_every_hitter(self):
        h2 = client(3, XYZ(3, 4, 0))
        h2.prompt = h2.teamup = False
        h2.position = self.target
        h2.quest_party_quester = self.q
        h2.quest_party_status_session = self.h.quest_party_status_session
        h2.hotkey_quest_clients = self.q.hotkey_quest_clients = [self.q, self.h, h2]
        self.q.quest_party_hitters = [self.h, h2]
        self.h.prompt = self.h.teamup = False
        self.h.position = XYZ(0, 0, -10000)
        self.on_approach = lambda c: None
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        for member in (self.q, self.h, h2):
            member.send_key.assert_not_awaited()
        self.assertEqual(self.resets(), [self.h])
        h2.teleport.assert_awaited_once()
        self.assertEqual(tuple(h2.teleport.await_args.args[0]), (0, 0, -10000))
        self.q.teleport.assert_not_awaited()

    async def test_ready_cannon_caller_is_not_requalified_by_movement_cache(self):
        await self.reader.prepare_party_dungeon_entry()
        self.q.position = XYZ(self.target.x + 247, self.target.y, self.target.z)
        self.q._quest_target_first_move = dict(task=await _movement_task_snapshot(self.q),
            target=self.target, shared=None, pending=True, active=False)
        self.setup_caller()
        self.fast_arrival()
        await self.reader.auto_quest_solo()
        self.assertTrue(party.dungeon_session_for(self.q).confirmed)
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(self.events)

    async def test_untracked_hitter_can_join_verified_entrance(self):
        self.h.quest_id_value = 0
        await self.send()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(self.events)

    async def test_unreadable_snapshot_is_not_cached_as_permanent_disagreement(self):
        self.h.quest_position.position.side_effect = [ValueError('temporarily unreadable'), self.target]
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.h.quest_position.position.side_effect = None
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.assertFalse(self.events)

    async def test_hitters_own_arrow_does_not_reject_questers_verified_entrance(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.h.quest_position.position.return_value = XYZ(9000, 12000, 0)
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_ready_first_x_is_not_limited_by_missing_prompt_recovery_deadline(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.clock += 31
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        self.assertGreater(party.dungeon_session_for(self.q).entry_wait['deadline'], self.clock)

    async def test_same_task_entry_owner_is_reentrant(self):
        members = await self.reader.prepare_party_dungeon_entry()
        async with automation_owner(self.q, 'dungeon-entry'):
            self.assertTrue(await self.reader._send_dungeon_entry_x(members))
            self.assertTrue(get_client_automation_ownership(self.q).locked)
        self.assertFalse(get_client_automation_ownership(self.q).locked)

    async def test_replaced_unsent_source_is_recaptured_without_moving_ready_members(self):
        await self.reader.prepare_party_dungeon_entry()
        session = party.dungeon_session_for(self.q)
        session.transition = None
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.assertIs(self.q._quest_party_entry['source'], session.transition)
        self.assertFalse(self.events)

    async def test_no_arrival_ends_round_without_automatic_second_x(self):
        session = await self.send()
        self.assertTrue(await party.dungeon_entry_waiting(self.q))
        self.clock += 31
        self.assertFalse(await party.dungeon_entry_waiting(self.q))
        self.assertIsNone(session.entry_wait)
        self.assertIsNone(self.q._quest_party_entry)
        self.assertTrue(session.transition.get('entry_ended'))
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q, self.h]))
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)
        self.assertFalse(self.events)

    async def test_cancel_during_shared_x_ends_round_and_releases_own_input(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.q.send_key.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.reader._send_dungeon_entry_x(members)
        session = party.dungeon_session_for(self.q)
        self.assertIsNone(session.entry_wait)
        self.assertIsNone(self.q._quest_party_entry)
        self.assertTrue(session.transition.get('entry_ended'))
        self.assertFalse(await self.reader._send_dungeon_entry_x(members))
        self.assertTrue(all(not get_client_automation_ownership(c).locked for c in (self.q, self.h)))

    async def test_timeout_during_shared_x_does_not_leave_active_wait(self):
        members = await self.reader.prepare_party_dungeon_entry()
        async def blocked_input(*_):
            await asyncio.Event().wait()
        self.q.send_key.side_effect = blocked_input
        with patch('src.questing.asyncio.timeout', side_effect=lambda _: REAL_TIMEOUT(.02)):
            self.assertFalse(await self.reader._send_dungeon_entry_x(members))
        session = party.dungeon_session_for(self.q)
        self.assertIsNone(session.entry_wait)
        self.assertIsNone(self.q._quest_party_entry)
        self.assertTrue(session.transition.get('entry_ended'))

    async def test_unreadable_wait_ends_after_deadline_without_repeating_x(self):
        session = await self.send()
        self.clock += 31
        with patch('src.utils.observe_party_area', AsyncMock(side_effect=ValueError('unreadable'))):
            self.assertFalse(await party.dungeon_entry_waiting(self.q))
        self.assertIsNone(session.entry_wait)
        self.assertIsNone(self.q._quest_party_entry)
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q, self.h]))
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_confirmed_arrival_does_not_block_the_next_ordinary_task(self):
        await self.send()
        for c in (self.q, self.h):
            c.zone, c.area_id, c.prompt = 'Dungeon/Room1', 200, False
        await party.observe_dungeon_session(self.q)
        self.assertTrue(party.dungeon_session_for(self.q).confirmed)
        self.assertIsNone(await self.reader._prepare_sigil_entry())

    async def test_ready_enter_without_teamup_does_not_teleport_ready_hitter(self):
        self.q.teamup = self.h.teamup = False
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [self.q, self.h])
        self.assertFalse(self.events)

    async def test_generic_enter_rechecks_both_prompts_before_shared_x(self):
        self.q.teamup = self.h.teamup = False
        members = await self.reader.prepare_party_dungeon_entry()
        session = await party.begin_dungeon_entry(self.q, members, evidence=True)
        await party.remember_dungeon_transition(self.q, self.target)
        self.h.prompt = False
        self.assertFalse(await self.reader._send_dungeon_entry_x(members))
        self.assertFalse(session.transition.get('entry_sent'))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_generic_peer_read_cannot_leave_an_earlier_popup_stale(self):
        self.q.teamup = self.h.teamup = False
        members = await self.reader.prepare_party_dungeon_entry()
        await party.begin_dungeon_entry(self.q, members, evidence=True)
        await party.remember_dungeon_transition(self.q, self.target)
        async def read(c):
            if c is self.h:
                self.q.prompt = False
            return 'Press X to Enter'
        self.reader.read_popup = AsyncMock(side_effect=read)
        self.assertFalse(await self.reader._send_dungeon_entry_x(members))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_generic_full_caller_never_replays_one_x_lifecycle(self):
        self.q.teamup = self.h.teamup = False
        self.setup_caller()
        for _ in range(2):
            await self.reader.auto_quest_solo()
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)

    async def test_failed_entry_retires_after_real_source_area_change(self):
        await self.send()
        self.assertTrue(await party.dungeon_entry_waiting(self.q))
        self.clock += 31
        self.assertFalse(await party.dungeon_entry_waiting(self.q))
        for c in (self.q, self.h):
            c.area_id, c.prompt = 900, False
        self.setup_caller()
        self.on_approach = lambda c: None  # The new area exposes no new entrance.
        # The existing movement boundary is enough to observe resumed ordinary work.
        await self.reader.auto_quest_solo()
        self.reader.teleport_to_quest_target.assert_awaited_once()
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_confirmed_dungeon_new_task_is_not_blocked_by_previous_internal_x(self):
        await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        session = party.dungeon_session_for(self.q)
        session.transition['entry_sent'] = True
        self.q.goal_id.return_value = 8
        self.q.prompt = self.h.prompt = False
        self.assertIsNone(await self.reader._prepare_sigil_entry())

    async def test_peer_countdown_is_checked_before_another_members_area_read(self):
        session = await self.send()
        self.clock += 31
        with patch('src.utils.observe_party_area', AsyncMock(side_effect=ValueError('area unreadable'))), \
             patch('src.utils.is_visible_by_path', AsyncMock(
                 side_effect=lambda c, path: c is self.h and path == team_up_wait_path)):
            self.assertTrue(await party.dungeon_entry_waiting(self.q))
        self.assertIsNotNone(session.entry_wait)

    async def test_real_loading_does_not_retire_sent_round_on_task_change(self):
        session = await self.send()
        self.q.goal_id.return_value = 8
        self.q.is_loading.return_value = True
        self.assertEqual(await self.reader._prepare_sigil_entry(), [])
        self.assertIs(party.dungeon_session_for(self.q), session)
        self.assertIsNotNone(session.entry_wait)
        session.entry_wait = None  # A cancelled send may have already cleared its own wait.
        self.assertEqual(await self.reader._prepare_sigil_entry(), [])
        self.assertIs(party.dungeon_session_for(self.q), session)


del Base


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(EntryFlowTests(name) for name in EntryFlowTests.__dict__ if name.startswith('test_'))
