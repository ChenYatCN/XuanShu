"""Workbook-declared parallel mainlines must survive tracked-quest changes."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from loguru import logger
from src import mainline_progress as progress
from tests.test_mainline_recovery import MainlineClient, MainlineRecoveryTests as BookFixture, Window


ROWS = progress.quest_rows()
TAWAREST_ID = 129197014662125044  # User-provided live identity, not a production override.
QUEST_IDS = {27: TAWAREST_ID, 29: 1029, 31: 1031, 33: 1033, 34: 1034, 13: 1013}


def selenopolis(number):
    return next(row for row in ROWS if row['world'] == 'selenopolis' and row['number'] == number)


class SelenopolisMatcherTests(unittest.IsolatedAsyncioTestCase):
    runtime = BookFixture.runtime
    tick = BookFixture.tick

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = 0
        self.client = MainlineClient('p3')
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/Area'
        self.client.tracked = TAWAREST_ID
        self.client.quests = {
            qid: SimpleNamespace(name_lang_key=AsyncMock(return_value=selenopolis(n)['keys'][0]))
            for n, qid in QUEST_IDS.items()
        }
        self.client.quests[999] = SimpleNamespace(name_lang_key=AsyncMock(return_value='Quest Finder'))
        titles = {selenopolis(n)['keys'][0]: selenopolis(n)['english'] for n in QUEST_IDS}
        self.client.cache_handler.get_langcode_name.side_effect = lambda key: titles.get(key, key)
        self.stack.enter_context(patch.object(progress, 'installed_catalog', return_value=None))
        self.last(33)
        self.logs = []
        sink = logger.add(lambda message: self.logs.append(str(message)), level='DEBUG')
        self.addCleanup(logger.remove, sink)

    def last(self, number):
        row = selenopolis(number)
        progress.record_mainline_success(self.client, dict(quest_id=QUEST_IDS[number], goal_id=7,
            code=row['keys'][0], title=row['english'], row=row))

    async def test_live_key_already_resolves_tawarest_without_a_quest_id_override(self):
        identity = await progress.read_mainline_identity(self.client)
        self.assertEqual(identity['quest_id'], TAWAREST_ID)
        self.assertEqual(identity['code'], 'QuestTitle_00002055')
        self.assertEqual((identity['row']['world'], identity['row']['number']), ('selenopolis', 27))
        self.assertEqual(identity['row']['quest_ids'], [])

    async def test_ra_to_tawarest_is_an_expected_parallel_mainline_while_ra_is_owned(self):
        identity = await progress.read_mainline_identity(self.client)
        last = self.client._xuanshu_mainline_last_success
        self.assertTrue(progress.mainline_consistent(identity, last))
        self.assertIsNotNone(self.runtime().expected_identity(last, identity))
        expected = {r['number'] for r in progress.expected_mainline_rows(last)}
        self.assertEqual(expected, {27, 29, 31, 33, 34})
        self.assertIn(QUEST_IDS[33], identity['owned'])

    async def test_parallel_roots_can_be_tracked_in_either_order(self):
        for previous in (27, 29, 31, 33):
            for current in (27, 29, 31, 33):
                with self.subTest(previous=previous, current=current):
                    self.last(previous)
                    self.client.tracked = QUEST_IDS[current]
                    identity = await progress.read_mainline_identity(self.client)
                    self.assertTrue(progress.mainline_consistent(identity,
                        self.client._xuanshu_mainline_last_success))

    async def test_return_to_previous_branch_keeps_parallel_roots_expected(self):
        self.last(34)  # The Sun Undone explicitly returns to Ra is War.
        for current in (27, 33):
            with self.subTest(current=current):
                self.client.tracked = QUEST_IDS[current]
                identity = await progress.read_mainline_identity(self.client)
                self.assertTrue(progress.mainline_consistent(identity, self.client._xuanshu_mainline_last_success))
                self.assertIsNotNone(self.runtime().expected_identity(self.client._xuanshu_mainline_last_success, identity))

    def test_all_39_selenopolis_keys_keep_their_original_identity_rows(self):
        for row in (r for r in ROWS if r['world'] == 'selenopolis'):
            for code in row['keys']:
                with self.subTest(number=row['number'], code=code):
                    self.assertIs(progress.match_quest(ROWS, None, code, ''), row)

    async def test_pending_recovery_accepts_stable_parallel_identity_without_finder(self):
        self.client._xuanshu_mainline_recovery = dict(phase='observe', attempts=0, observed=None)
        await progress.log_mainline_progress(self.client)
        self.assertIn('主线 27/39', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
        self.assertIsNone(self.client._xuanshu_mainline_recovery)
        self.assertFalse(self.client.book_open)

    async def test_stable_tawarest_stays_recognized_without_opening_recovery_q(self):
        recovery = self.runtime()
        for _ in range(3):
            await progress.log_mainline_progress(self.client)
            self.assertEqual(self.client._xuanshu_mainline_progress, 'p3 · selenopolis 主线 27/39')
            self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertFalse(any(event[0] == 'key' for event in self.client.events))

    async def test_ra_is_war_does_not_regress_when_switching_back(self):
        recovery = self.runtime()
        for number in (27, 33, 27, 33):
            self.client.tracked = QUEST_IDS[number]
            await progress.log_mainline_progress(self.client)
            self.assertIn(f'主线 {number}/39', self.client._xuanshu_mainline_progress)
            self.assertFalse(await recovery.maybe_recover_mainline(self.client))

    async def test_unrelated_known_mainline_stays_non_target_and_keeps_recovery_blocked(self):
        self.client.tracked = QUEST_IDS[13]
        identity = await progress.read_mainline_identity(self.client)
        last = self.client._xuanshu_mainline_last_success
        self.assertFalse(progress.mainline_consistent(identity, last))
        self.assertIsNone(self.runtime().expected_identity(last, identity))
        await progress.log_mainline_progress(self.client)
        self.assertIn('未匹配', self.client._xuanshu_mainline_progress)
        self.assertTrue(await self.runtime().maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 33)

    async def test_finder_system_identity_is_never_a_normal_mainline(self):
        for code, title in (('Quest Finder', 'Quest Finder'), ('', '任务搜寻')):
            self.assertIsNone(progress.match_quest(ROWS, 999, code, title))
        self.client.tracked = 999
        identity = await progress.read_mainline_identity(self.client)
        self.assertFalse(progress.mainline_consistent(identity, self.client._xuanshu_mainline_last_success))
        self.assertIsNone(self.runtime().expected_identity(self.client._xuanshu_mainline_last_success, identity))

    async def test_recovery_can_select_a_parallel_card_beyond_the_first_two_expected_rows(self):
        recovery = self.runtime()
        self.client.tracked = 999
        self.client.pages = [['Tawarest in Peace', '任务搜寻']]
        def cards():
            output = []
            for i, (title, qid) in enumerate((('Tawarest in Peace', TAWAREST_ID), ('任务搜寻', 999))):
                goal = Window('txtGoal', self.client, text='Talk to NPC',
                    action=lambda qid=qid: setattr(self.client, 'tracked', qid))
                output.append(Window(f'wndQuestInfo{i}', self.client, children=[
                    Window('txtQuestTitle', self.client, text=title), goal]))
            return output
        self.client.page_nodes = cards
        with patch.object(recovery, 'confirm_finder_click', AsyncMock(return_value={})):
            result = await recovery.select_from_book(self.client, self.client._xuanshu_mainline_last_success)
        self.assertEqual(result, 'restore')
        self.assertEqual(self.client.tracked, TAWAREST_ID)
        self.assertTrue(any('source=quest-book' in line and 'Tawarest in Peace' in line
                            and 'match=True' in line for line in self.logs))
        self.assertTrue(any('quest-finder-system-card' in line and 'match=False' in line for line in self.logs))

    async def test_unmatched_log_explains_identity_and_expected_index_once(self):
        self.client.tracked = QUEST_IDS[13]
        await progress.log_mainline_progress(self.client)
        await progress.log_mainline_progress(self.client)
        lines = [line for line in self.logs if '[mainline_match]' in line and 'source=tracked' in line]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        for value in ('client=p3', 'QuestID=1013', 'QuestTitle_19454A', 'normalized_title=',
                      'expected_title=', 'Ra is War', 'expected_index=', '33',
                      'index_match=True', 'match=False', 'reason=prior-mainline-still-owned'):
            self.assertIn(value, line)

    async def test_unindexed_identity_log_is_distinct_from_sequence_rejection(self):
        self.client.tracked = 8080
        self.client.quests[8080] = SimpleNamespace(name_lang_key=AsyncMock(return_value='UnknownTitle'))
        await progress.log_mainline_progress(self.client)
        lines = [line for line in self.logs if '[mainline_match]' in line]
        self.assertTrue(any('index_match=False' in line and 'reason=title-not-in-index' in line for line in lines))

    def test_missing_diagnostic_reason_cannot_authorize_an_unmatched_identity(self):
        for reason in (None, ''):
            identity = dict(row=None, quest_id=8080, match_reason=reason)
            self.assertFalse(progress.mainline_consistent(identity, self.client._xuanshu_mainline_last_success))

    async def test_active_recovery_keeps_unexpected_known_future_row_blocked(self):
        row = selenopolis(35)
        self.client.quests.pop(QUEST_IDS[33])
        self.client.quests[1035] = SimpleNamespace(name_lang_key=AsyncMock(return_value=row['keys'][0]))
        self.client.tracked = 1035
        self.client._xuanshu_mainline_recovery = dict(phase='observe', attempts=0, observed=None)
        await progress.log_mainline_progress(self.client)
        self.assertIn('未匹配', self.client._xuanshu_mainline_progress)
        self.assertTrue(await self.runtime().maybe_recover_mainline(self.client))
        self.assertTrue(any('match=False' in line and 'reason=outside-recovery-expected' in line for line in self.logs))

    def test_branch_mapping_uses_mutual_unique_same_world_metadata(self):
        def row(number, title, note='', world='Example'):
            return dict(world=world, number=number, english=title, note=note,
                keys=[], quest_ids=[], chinese=[], aliases=[])
        first = row(1, 'One', 'interchangeable with "Three" and "Missing"')
        second = row(2, 'Two')
        third = row(3, 'Three', 'interchangeable with "One"')
        last = dict(world='Example', number=1, row=first)
        with patch.object(progress, 'quest_rows', return_value=[first, second, third]):
            self.assertEqual([r['number'] for r in progress.expected_mainline_rows(last)], [1, 2, 3])
        for bad in (dict(third, note=''), dict(third, world='Other')):
            with patch.object(progress, 'quest_rows', return_value=[first, second, bad]):
                self.assertEqual([r['number'] for r in progress.expected_mainline_rows(last)], [1, 2])
        with patch.object(progress, 'quest_rows', return_value=[first, second, third, dict(third, number=4)]):
            self.assertEqual([r['number'] for r in progress.expected_mainline_rows(last)], [1, 2])


del BookFixture
