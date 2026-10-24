"""Diagnostic evidence must not change source capture decisions or read order."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from loguru import logger
from wizwalker import XYZ
from wizwalker.errors import HookNotActive, MemoryReadError
from src import quest_party as party, utils
from tests.test_dungeon_local_tp import DungeonFixture


class SourceCaptureTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.events = []
        def collect(message):
            if '[dungeon_source_capture]' in message.record['message']:
                self.events.append(message.record['extra']['dungeon_source_capture'])
        sink = logger.add(collect, level='DEBUG')
        self.addCleanup(logger.remove, sink)
        self.target = XYZ(500, 600, 0)

    async def create(self):
        self.session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.assertIsNotNone(self.session)

    async def capture(self):
        return await party.remember_dungeon_transition(self.q, self.target)

    def event(self, reason):
        matches = [event for event in self.events if event['reason'] == reason]
        self.assertTrue(matches, (reason, self.events))
        return matches[-1]

    async def test_stable_capture_and_reuse_log_once_and_keep_source_budget(self):
        await self.create()
        self.assertTrue(await self.capture())
        source = self.session.transition
        self.assertTrue(await self.capture())
        self.assertIs(self.session.transition, source)
        event = self.event('stable-source-captured')
        self.assertEqual(event['decision'], 'accept')
        self.assertEqual(event['task'], (42, 7))
        self.assertEqual(event['zone'], 'Outside')
        self.assertEqual(event['token'], ('Outside', 10100, 100, 0))
        self.assertEqual(event['generation'], self.session.generation)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.q.quest_id.await_count, 4)
        self.assertEqual(self.h.quest_id.await_count, 2)

    async def test_task_changed_during_capture_keeps_original_false(self):
        await self.create()
        self.q.goal_id.side_effect = [7, 8]
        self.assertFalse(await self.capture())
        event = self.event('task-changed-during-capture')
        self.assertEqual(event['task_before'], (42, 7))
        self.assertEqual(event['task_after'], (42, 8))
        self.assertIsNone(self.session.transition)

    async def test_token_or_zone_changed_preserves_short_circuit_before_task_read(self):
        for zone_changed in (False, True):
            with self.subTest(zone_changed=zone_changed):
                if zone_changed:
                    self.q.goal_id.reset_mock()
                await self.create()
                original = utils._party_area_token
                reads = 0
                async def token(member):
                    nonlocal reads
                    value = await original(member)
                    if member is self.q:
                        reads += 1
                        if reads == 2:
                            return ('Other' if zone_changed else value[0], *value[1:3], value[3] + 1)
                    return value
                before = self.q.goal_id.await_count
                with patch.object(utils, '_party_area_token', token):
                    self.assertFalse(await self.capture())
                reason = 'zone-changed-during-capture' if zone_changed else 'token-changed-during-capture'
                self.event(reason)
                self.assertEqual(self.q.goal_id.await_count - before, 1)

    async def test_unreadable_zone_task_and_token_report_operation_and_exception_type(self):
        for field, reason in [('zone', 'zone-unreadable'), ('task', 'task-unreadable'),
                              ('token', 'area-token-unreadable')]:
            with self.subTest(field=field):
                await self.create()
                error = ValueError('must-not-log-account-or-secret')
                if field == 'zone':
                    # Fail the existing initial member-readiness zone read.
                    context = patch.object(self.q, 'zone_name', AsyncMock(side_effect=error))
                elif field == 'task':
                    context = patch.object(self.q, 'quest_id', AsyncMock(side_effect=error))
                else:
                    context = patch.object(utils, '_party_area_token', AsyncMock(side_effect=error))
                with context:
                    self.assertFalse(await self.capture())
                event = self.event(reason)
                self.assertEqual(event['exception_type'], 'ValueError')
                self.assertEqual(event['error'], 'value-error')
                self.assertNotIn('must-not-log', repr(event))

    async def test_failure_recovers_and_continuous_identical_outcomes_are_deduplicated(self):
        await self.create()
        self.q.quest_id_value = None
        self.assertFalse(await self.capture())
        self.assertFalse(await self.capture())
        self.q.quest_id_value = 42
        self.assertTrue(await self.capture())
        source = self.session.transition
        self.assertTrue(await self.capture())
        self.q.quest_id_value = None
        self.assertFalse(await self.capture())
        self.assertIs(self.session.transition, source)
        self.assertEqual([e['reason'] for e in self.events],
            ['task-invalid', 'stable-source-captured', 'task-invalid'])

    async def test_invalid_area_token_and_token_zone_mismatch_remain_rejected(self):
        await self.create()
        for token, reason in [(('Outside', 0, 100, 0), 'area-token-invalid'),
                              (('Other', 10100, 100, 0), 'zone-invalid')]:
            with self.subTest(token=token), patch.object(utils, '_party_area_token', AsyncMock(return_value=token)):
                self.assertFalse(await self.capture())
                self.event(reason)

    async def test_initial_hitter_optional_exception_and_outer_hook_error_keep_distinct_behavior(self):
        await self.create()
        with patch.object(self.h, 'quest_id', AsyncMock(side_effect=ValueError('optional'))):
            self.assertTrue(await self.capture())
        self.assertNotIn(id(self.h), self.session.transition['source_tasks'])
        with patch.object(self.h, 'quest_id', AsyncMock(side_effect=HookNotActive('player'))):
            self.assertFalse(await self.capture())
        event = self.event('task-unreadable')
        self.assertEqual(event['error'], 'hook-not-active')
        self.assertEqual(event['member'], 'p2')

    async def test_memory_read_error_stays_false_and_is_identified(self):
        await self.create()
        with patch.object(self.q, 'goal_id', AsyncMock(side_effect=MemoryReadError(123))):
            self.assertFalse(await self.capture())
        self.assertEqual(self.event('task-unreadable')['error'], 'memory-read-error')

    async def test_area_observation_error_does_not_claim_a_zone_read_failed(self):
        await self.create()
        for error in (ValueError('unreadable-loading'), HookNotActive('player')):
            with self.subTest(error=type(error).__name__), patch.object(self.q, 'is_loading',
                    AsyncMock(side_effect=[False, False, error])):
                self.assertFalse(await self.capture())
                event = self.event('area-observation-unreadable')
                self.assertEqual(event['stage'], 'observe-area')
                self.assertEqual(event['exception_type'], type(error).__name__)

    async def test_loading_battle_and_member_zone_failures_are_identified_in_existing_ready_check(self):
        await self.create()
        for field, reason in [('loading', 'loading-or-battle'), ('battle', 'loading-or-battle'),
                              ('zone', 'zone-changed-during-capture')]:
            with self.subTest(field=field):
                name = {'loading': 'is_loading', 'battle': 'in_battle', 'zone': 'zone_name'}[field]
                with patch.object(self.h, name, AsyncMock(return_value='Other' if field == 'zone' else True)):
                    self.assertFalse(await self.capture())
                event = self.event(reason)
                self.assertEqual(event['member'], 'p2')

    async def test_session_replaced_after_read_does_not_commit_to_old_session(self):
        await self.create()
        old = self.session
        original = utils._party_area_token
        async def token(member):
            value = await original(member)
            if member is self.h:
                self.q.quest_party_dungeon = None
            return value
        with patch.object(utils, '_party_area_token', token):
            self.assertFalse(await self.capture())
        self.event('session-replaced')
        self.assertIsNone(old.transition)

    async def test_create_failures_log_exact_original_rejection(self):
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q], evidence=True))
        self.assertEqual(self.event('session-create-failed')['check'], 'entry-roster-mismatch')
        self.h.is_loading.return_value = True
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True))
        self.assertEqual(self.event('session-create-failed')['check'], 'loading')
        self.h.is_loading.return_value = False
        self.h.zone = ''
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True))
        self.assertEqual(self.event('session-create-failed')['check'], 'zone-invalid')
        self.h.zone = 'Other'
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True))
        self.assertEqual(self.event('session-create-failed')['check'], 'zone-mismatch')

    async def test_diagnostics_do_not_require_titles_on_original_create_rejection(self):
        minimal = SimpleNamespace(quest_party_hitters=[])
        self.assertIsNone(await party.begin_dungeon_entry(minimal, []))

    async def test_create_rejection_does_not_consume_an_originally_short_circuited_iterable(self):
        minimal = SimpleNamespace(title='p0', quest_party_hitters=[])
        entries = iter([self.h])
        self.assertIsNone(await party.begin_dungeon_entry(minimal, entries))
        self.assertEqual(list(entries), [self.h])

    async def test_create_read_exception_is_logged_and_still_propagates(self):
        with patch.object(self.h, 'zone_name', AsyncMock(side_effect=AttributeError('private-context'))):
            with self.assertRaises(AttributeError):
                await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        event = self.event('session-create-failed')
        self.assertEqual(event['check'], 'zone-unreadable')
        self.assertEqual(event['error'], 'attribute-error')
        self.assertNotIn('private-context', repr(event))

    async def test_ready_final_eager_reads_still_visit_later_members(self):
        await self.create()
        self.q.is_loading.reset_mock()
        self.h.is_loading.reset_mock()
        self.q.is_loading.side_effect = [False, True]
        diagnostic = {}
        self.assertFalse(await party.quest_party_members_ready(self.q, _capture_diagnostic=diagnostic))
        self.assertEqual(self.h.is_loading.await_count, 2)
        self.assertEqual(diagnostic['reason'], 'loading-or-battle')
        self.assertEqual(diagnostic['member'], 'p1')


if __name__ == '__main__':
    unittest.main()
