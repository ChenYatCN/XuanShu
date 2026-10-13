"""Recorded dungeon progress is distinct from an external mainline mismatch."""
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src import quest_party as party
from src.mainline_progress import log_mainline_progress
import test_mainline_recovery as fixtures


class MainlineDungeonRecoveryTests(unittest.IsolatedAsyncioTestCase):
    initialize = fixtures.MainlineRecoveryTests.initialize
    runtime = fixtures.MainlineRecoveryTests.runtime
    recover = fixtures.MainlineRecoveryTests.recover
    tick = fixtures.MainlineRecoveryTests.tick

    def setUp(self):
        fixtures.MainlineRecoveryTests.setUp(self)
        self.client.title = 'p3'
        self.hitter = fixtures.MainlineClient('p4')
        self.client.quest_party_hitters = [self.hitter]
        for member in (self.client, self.hitter):
            member.zone = 'Outside'
            member.zone_name = AsyncMock(side_effect=lambda member=member: member.zone)
        self.stack.enter_context(patch('src.utils.clients_share_live_area', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.utils.observe_party_area', AsyncMock()))
        self.stack.enter_context(patch('src.utils._party_area_token', AsyncMock(return_value=('zone', 200))))

    async def enter(self, *, evidence=True, confirm=True):
        await self.initialize()
        session = await party.begin_dungeon_entry(self.client, [self.client, self.hitter], evidence=evidence)
        if confirm:
            for member in (self.client, self.hitter):
                member.zone = 'Dungeon/Room1'
            await party.observe_dungeon_session(self.client)
            self.assertTrue(session.confirmed)
        self.client.tracked = 77
        return session

    def assert_no_recovery_book(self):
        self.assertNotIn(('key', Keycode.Q), self.client.events)

    async def test_confirmed_dungeon_mismatch_continues_current_task(self):
        await self.enter()
        last = self.client._xuanshu_mainline_last_success
        self.assertFalse(await self.recover(times=4))
        self.assertEqual(self.client.tracked, 77)
        self.assertIs(self.client._xuanshu_mainline_last_success, last)
        self.assert_no_recovery_book()

    async def test_entry_evidence_wait_does_not_open_recovery_book(self):
        await self.enter(confirm=False)
        self.assertFalse(await self.recover(times=4))
        self.assert_no_recovery_book()

    async def test_unproven_pending_entry_does_not_suppress_normal_recovery(self):
        await self.enter(evidence=False, confirm=False)
        await self.recover(times=4)
        self.assertIn(('key', Keycode.Q), self.client.events)
        self.assertEqual(self.client.tracked, 42)

    async def test_internal_room_changes_keep_dungeon_task_active(self):
        await self.enter()
        self.client.zone = 'Dungeon/UnknownRoom2'
        await party.observe_dungeon_session(self.client)
        self.assertFalse(await self.recover(times=4))
        self.assertEqual(self.client.tracked, 77)
        self.assert_no_recovery_book()

    async def test_exit_flag_without_positive_exit_evidence_does_not_start_recovery(self):
        session = await self.enter()
        session.exited.add(id(self.client))
        self.assertFalse(await self.recover(times=4))
        self.assert_no_recovery_book()

    async def test_potion_away_and_return_preserve_session_and_record(self):
        session = await self.enter()
        last = self.client._xuanshu_mainline_last_success
        session.potion_areas[id(self.client)] = ('Dungeon/Room1', 200)
        self.client.refilling_potions = True
        self.client.zone = 'PotionShop'
        self.assertFalse(await self.recover(times=2))
        self.client.refilling_potions = False
        self.assertFalse(await self.recover(times=2))
        self.client.zone = 'Dungeon/Room1'
        party.resume_dungeon_member(self.client)
        self.assertFalse(await self.recover(times=2))
        self.assertIs(self.client._xuanshu_mainline_last_success, last)
        self.assert_no_recovery_book()

    async def test_dungeon_ui_never_relabels_previous_number_as_current(self):
        await self.enter()
        await log_mainline_progress(self.client)
        self.assertIn('地牢任务进行中', self.client._xuanshu_mainline_progress)
        self.assertNotIn('未匹配', self.client._xuanshu_mainline_progress)
        self.assertNotIn('5/10', self.client._xuanshu_mainline_progress)

    async def test_matched_internal_task_does_not_replace_external_last_success(self):
        await self.enter()
        last = self.client._xuanshu_mainline_last_success
        self.client.tracked = 43
        await log_mainline_progress(self.client)
        self.assertFalse(await self.recover(times=2))
        self.assertIs(self.client._xuanshu_mainline_last_success, last)
        self.assertIn('地牢任务进行中', self.client._xuanshu_mainline_progress)

    async def test_unreadable_internal_identity_keeps_dungeon_ui_and_task_flow(self):
        await self.enter()
        self.client.quest_id.side_effect = RuntimeError('not ready')
        await log_mainline_progress(self.client)
        self.assertFalse(await self.recover(times=2))
        self.assertIn('地牢任务进行中', self.client._xuanshu_mainline_progress)
        self.assert_no_recovery_book()

    async def exit_all(self):
        # No log/recovery polling between entry and complete group exit: the
        # recorded entry hook must retain the evidence after the session clears.
        session = await self.enter()
        for member in (self.client, self.hitter):
            member.zone = 'Outside'
            party.record_dungeon_exit(member, 'Dungeon/Room1', 'Outside')
        self.assertIsNone(party.dungeon_session_for(self.client))
        self.assertIn(id(self.client), session.exit_evidence)
        return session

    async def test_actual_exit_waits_for_loading_and_stable_task_before_q(self):
        await self.exit_all()
        self.client.loading = True
        await self.recover(times=2)
        self.assert_no_recovery_book()
        self.client.loading = False
        self.client.quest_id.side_effect = RuntimeError('post-load task not ready')
        await self.recover(times=2)
        self.assert_no_recovery_book()
        self.client.quest_id.side_effect = lambda: self.client.tracked
        await self.recover(times=1)
        self.assert_no_recovery_book()
        self.client.goal_id.return_value = 8
        await self.recover(times=1)
        self.assert_no_recovery_book()
        await self.recover(times=4)
        self.assertIn(('key', Keycode.Q), self.client.events)
        self.assertEqual(self.client.tracked, 42)

    async def test_recovery_ui_guard_rejects_entry_during_existing_book_pass(self):
        await self.enter()
        recovery = self.runtime()
        with self.assertRaises(RuntimeError):
            await recovery.guard(self.client, self.client.zone)

    async def test_partial_group_exit_settles_once_and_returns_to_normal_ui(self):
        session = await self.enter()
        self.client.zone = 'Outside'
        party.record_dungeon_exit(self.client, 'Dungeon/Room1', 'Outside')
        self.assertIs(party.dungeon_session_for(self.client), session)
        await self.recover(times=4)
        self.assertEqual(self.client.tracked, 42)
        await log_mainline_progress(self.client)
        self.assertIn('主线 5/10', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.recover(times=2))

    async def test_new_session_exit_does_not_reuse_previous_stable_observation(self):
        await self.exit_all()
        await self.recover(times=4)
        await self.exit_all()
        self.client.events.clear()
        await self.recover(times=1)
        self.assert_no_recovery_book()


if __name__ == '__main__':
    unittest.main()
