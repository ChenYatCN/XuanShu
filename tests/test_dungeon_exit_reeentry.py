"""An exited dungeon lifecycle cannot consume the same stale entrance again."""
import unittest

from src import quest_party as party
from tests.test_dungeon_entry_receipt_lifecycle import EntryReceiptLifecycleTests


class ExitReentryTests(EntryReceiptLifecycleTests):
    async def enter_and_exit(self):
        await self.interact()
        session = party.dungeon_session_for(self.q)
        self.assertIsNotNone(session)
        session.confirmed = True
        for c in (self.q, self.h):
            c.zone, c.area_id, c.prompt = 'Outside', 100, True
            party.record_dungeon_exit(c, 'Dungeon/Room1', 'Outside')
        self.assertIsNone(party.dungeon_session_for(self.q))
        # The quester is back at the entrance and the old Enter popup is
        # still readable; this is the observed post-exit state.
        return session

    async def test_b1_exit_handoff_does_not_reenter_from_stale_popup(self):
        old = await self.enter_and_exit()
        for _ in range(3):
            await self.interact()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)
        self.assertEqual(self.q._deimos_quest_x['session'], old)

    async def test_b1_full_quest_loop_does_not_reenter_from_stale_popup(self):
        await self.enter_and_exit()
        for _ in range(2):
            await self.reader.auto_quest_solo()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)

    async def test_b2_task_refresh_returns_to_normal_quest_flow(self):
        await self.enter_and_exit()
        self.q.goal_id.return_value = 8
        self.q.prompt = self.h.prompt = False
        await self.interact()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_b3_new_task_lifecycle_can_enter_same_dungeon(self):
        old = await self.enter_and_exit()
        self.q.goal_id.return_value = 8
        await self.interact()
        new = party.dungeon_session_for(self.q)
        self.assertIsNotNone(new)
        self.assertGreater(new.generation, old.generation)
        self.assertEqual(self.q.send_key.await_count, 2)
        self.assertEqual(self.h.send_key.await_count, 2)

    async def test_b4_first_entry_does_not_require_movement_provenance(self):
        await self.interact()
        self.assertIsNotNone(party.dungeon_session_for(self.q))
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)

    async def test_b5_active_session_repeated_tick_does_not_spam_x(self):
        await self.interact()
        session = party.dungeon_session_for(self.q)
        for _ in range(3):
            await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), session)
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.h.send_key.await_count, 1)

    async def test_b6_stale_popup_stays_blocked_across_ticks(self):
        await self.enter_and_exit()
        for _ in range(5):
            await self.interact()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(self.q.send_key.await_count, 1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ExitReentryTests(name)
        for name in ExitReentryTests.__dict__ if name.startswith('test_'))
