"""Shared entry input remains coordinated; missed members never replay X."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src import quest_party as party
from src.questing import Quester
from src.paths import team_up_wait_path
from src.automation_ownership import get_client_automation_ownership, automation_owner
from tests.test_dungeon_local_tp import REAL_SLEEP, client
from tests.test_party_dungeon_entry_sync import EntrySyncTests as Base


class EntryWaitTests(Base):

    async def send(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        return party.dungeon_session_for(self.q)

    async def test_shared_x_publishes_wait_before_any_member_input(self):
        observed = []
        self.q.send_key.side_effect = lambda *_: observed.append(
            getattr(party.dungeon_session_for(self.q), 'entry_wait', None))
        await self.send()
        self.assertTrue(observed[0])

    async def test_split_member_never_replays_old_entrance(self):
        session = await self.send()
        self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.clock += 1
        self.h.send_key.reset_mock()
        other = Quester(self.h, [self.h], None)
        other.teleport_to_quest_target = AsyncMock()
        await other.recover_dungeon_transition()
        other.teleport_to_quest_target.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_repeated_prepare_cannot_send_another_entry_x(self):
        await self.send()
        self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)

    async def test_entry_wait_blocks_loading_and_known_wait_ui(self):
        await self.send()
        self.clock += 31
        self.h.is_loading.return_value = True
        self.assertTrue(await party.dungeon_entry_waiting(self.h))
        self.h.is_loading.return_value = False
        with patch('src.utils.is_visible_by_path', AsyncMock(
                side_effect=lambda c, p: p == team_up_wait_path)):
            self.assertTrue(await party.dungeon_entry_waiting(self.h))

    async def test_partial_arrival_releases_to_follow_only_after_stable_deadline(self):
        await self.send()
        self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
        self.assertTrue(await party.dungeon_entry_waiting(self.h))
        self.clock += 31
        self.assertFalse(await party.dungeon_entry_waiting(self.h))
        self.assertFalse(self.events)

    async def test_all_arrived_requires_same_instance_then_releases_movement(self):
        session = await self.send()
        for c in (self.q, self.h): c.zone, c.area_id = 'Dungeon/Room1', 200
        self.proof.return_value = False
        self.assertTrue(await party.dungeon_entry_waiting(self.h))
        self.proof.return_value = True
        self.assertFalse(await party.dungeon_entry_waiting(self.h))
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)

    async def test_confirmed_exit_invalidates_only_its_entrance_and_budgets(self):
        session = await self.enter()
        self.q._quest_party_entry = dict(session=session)
        self.q._quest_door_approach = dict(session=session, ended=False)
        session.transition = {'source_zone': self.q.zone}
        session.follow_attempts[id(self.h)] = 2
        owner = object()
        self.q.quest_recovery_owner = owner
        party.record_dungeon_exit(self.q, self.q.zone, 'Outside')
        self.assertIsNone(session.transition)
        self.assertIsNone(self.q._quest_party_entry)
        self.assertTrue(self.q._quest_door_approach['ended'])
        self.assertFalse(session.follow_attempts)
        self.assertIs(self.q.quest_recovery_owner, owner)

    async def test_potion_excursion_does_not_invalidate_entry_state(self):
        session = await self.enter()
        session.transition = state = {'source_zone': self.q.zone}
        session.potion_areas[id(self.q)] = (self.q.zone, self.q.area_id)
        party.record_dungeon_exit(self.q, self.q.zone, 'PotionShop')
        self.assertIs(session.transition, state)

    async def test_sending_x_waits_for_active_movement_without_cancelling_its_worker(self):
        members = await self.reader.prepare_party_dungeon_entry()
        release = asyncio.Event()
        moving = asyncio.create_task(release.wait())
        self.h._quest_active_movement = moving
        await REAL_SLEEP(0)
        try:
            self.assertFalse(await self.reader._send_dungeon_entry_x(members))
            self.assertFalse(moving.done())
            self.h.send_key.assert_not_awaited()
            self.q.send_key.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(moving, return_exceptions=True)

    async def test_cancelled_shared_x_releases_only_its_wait_and_input(self):
        members = await self.reader.prepare_party_dungeon_entry()
        self.q.send_key.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.reader._send_dungeon_entry_x(members)
        session = party.dungeon_session_for(self.q)
        self.assertIsNone(session.entry_wait)
        self.assertFalse(self.q.quest_party_entry_active)
        self.assertFalse(get_client_automation_ownership(self.q).locked)
        self.assertFalse(get_client_automation_ownership(self.h).locked)

    async def test_another_groups_entry_state_is_not_blocked_or_cleared(self):
        session = await self.send()
        q, h = client(3, self.target), client(4, self.target)
        q.quest_party_hitters = [h]
        other = await party.begin_dungeon_entry(q, [q,h], evidence=True)
        self.assertFalse(await party.dungeon_entry_waiting(h))
        self.assertTrue(await party.dungeon_entry_waiting(self.h))
        party.clear_dungeon_session(h)
        self.assertIs(party.dungeon_session_for(self.h), session)
        self.assertIsNotNone(session.entry_wait)

    async def test_generic_group_entry_cannot_bypass_an_existing_input_owner(self):
        await self.enter()
        await party.remember_dungeon_transition(self.q, self.target)
        self.q._quest_party_entry = None
        async with automation_owner(self.h, 'potion'):
            self.assertFalse(await self.reader._send_dungeon_entry_x([self.q,self.h]))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()


del Base


def load_tests(loader, tests, pattern):
    # Reuse the existing fixture without rerunning its inherited test cases.
    return unittest.TestSuite(EntryWaitTests(name) for name in EntryWaitTests.__dict__
        if name.startswith('test_'))
