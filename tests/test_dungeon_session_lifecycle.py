"""Dungeon session generation, quester-out and exact-session retirement."""
import unittest

from wizwalker import XYZ
from src import quest_party as party
from tests.test_dungeon_local_tp import DungeonFixture, client


class DungeonSessionLifecycleTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def all_out(self):
        self.q.zone, self.h.zone = 'Outside', 'Outside'
        self.q.area_id = self.h.area_id = 100
        await party.observe_dungeon_session(self.q)
        await party.observe_dungeon_session(self.h)

    async def test_new_legitimate_entry_gets_a_new_generation(self):
        first = await self.enter()
        generation = first.generation
        await self.all_out()
        self.assertIsNone(party.dungeon_session_for(self.q))
        second = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.assertIsNotNone(second)
        self.assertIsNot(second, first)
        self.assertGreater(second.generation, generation)
        self.assertIs(party.dungeon_session_for(self.q), second)

    async def test_stale_old_cleanup_cannot_clear_new_session(self):
        old = await self.enter()
        await self.all_out()
        new = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.assertFalse(party.clear_dungeon_session(self.q, expected=old))
        self.assertIs(party.dungeon_session_for(self.q), new)
        self.assertEqual(new.phase, 'active')

    async def test_quester_out_authorizes_party_releasing(self):
        session = await self.enter()
        self.q.zone = 'Outside'
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.quester_out)
        self.assertEqual(session.phase, 'quester-out')
        self.assertIn(id(self.q), session.exited)
        self.assertTrue(party.mark_dungeon_party_releasing(session))
        self.assertEqual(session.phase, 'party-releasing')
        self.assertIs(party.dungeon_session_for(self.h), session)

    async def test_hitter_out_alone_does_not_release_or_retire(self):
        session = await self.enter()
        self.h.zone = 'Outside'
        await party.observe_dungeon_session(self.h)
        self.assertIn(id(self.h), session.exited)
        self.assertFalse(session.quester_out)
        self.assertEqual(session.phase, 'active')
        self.assertIs(party.dungeon_session_for(self.q), session)
        self.assertFalse(party.mark_dungeon_party_releasing(session))

    async def test_all_members_out_retire_exactly_once(self):
        session = await self.enter()
        await self.all_out()
        self.assertEqual(session.phase, 'retired')
        self.assertIsNone(getattr(self.q, 'quest_party_dungeon', None))
        self.assertIsNone(getattr(self.h, 'quest_party_dungeon', None))
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertFalse(party.retire_dungeon_session(session))

    async def test_internal_room_change_is_not_an_exit(self):
        session = await self.enter()
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.q.area_id = self.h.area_id = 201
        await party.observe_dungeon_session(self.q)
        await party.observe_dungeon_session(self.h)
        self.assertEqual(session.exited, set())
        self.assertEqual(session.phase, 'active')
        self.assertIs(party.dungeon_session_for(self.q), session)
        self.assertTrue(party.dungeon_member_inside(self.h))

    async def test_active_session_is_not_duplicated_by_entry(self):
        session = await self.enter()
        again = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.assertIs(again, session)

    async def test_two_group_sessions_retire_independently(self):
        q3 = client(3, XYZ(1, 2, 0))
        h3 = client(4, XYZ(3, 4, 0))
        q3.quest_party_hitters = [h3]
        h3.quest_party_status_session = object()
        second = await party.begin_dungeon_entry(q3, [q3, h3], evidence=True)
        q3.zone = h3.zone = 'Dungeon/Room1'
        q3.area_id = h3.area_id = 200
        await party.observe_dungeon_session(q3)
        self.assertTrue(second.confirmed)
        first = await self.enter()
        await self.all_out()
        self.assertIs(party.dungeon_session_for(q3), second)
        self.assertEqual(second.phase, 'active')
        self.assertIsNone(party.dungeon_session_for(self.q))

    async def test_idempotent_retire_does_not_clear_new_session_state(self):
        old = await self.enter()
        await self.all_out()
        new = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        marker = (new, 'keep')
        self.q._quest_party_safe_return = marker
        self.assertFalse(party.retire_dungeon_session(old))
        self.assertIs(party.dungeon_session_for(self.q), new)
        self.assertIs(self.q._quest_party_safe_return, marker)


if __name__ == '__main__':
    unittest.main()
