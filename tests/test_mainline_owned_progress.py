"""Seen mainline numbers are not proof that earlier owned quests completed."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from src import mainline_progress as progress
from tests.test_mainline_recovery import MainlineClient, MainlineRecoveryTests as BookFixture, Window


INDEX = progress.quest_rows()


class MainlineOwnedProgressTests(unittest.IsolatedAsyncioTestCase):
    runtime = BookFixture.runtime
    tick = BookFixture.tick

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = 0
        self.logs = []
        sink = logger.add(lambda message: self.logs.append(str(message)), format='{message}')
        self.addCleanup(logger.remove, sink)
        self.stack.enter_context(patch.object(progress, 'installed_catalog', return_value=None))
        self.configure()

    def configure(self, *, world='Example', first=5, title='p1'):
        if world == 'Example':
            rows = [dict(world=world, number=n, total=20, english=f'Quest {n}', chinese=[],
                         keys=[f'Quest_{n}'], quest_ids=[], aliases=[], note='') for n in (1, 5, 6, 7)]
        else:
            rows = [dict(r) for r in INDEX if r['world'] == world]
        self.rows = rows + [dict(world='Other', number=1, total=10, english='Other Mainline',
                                keys=['OtherQuest'], quest_ids=[], chinese=[], aliases=[], note='')]
        self.first = first
        self.world = world
        self.client = MainlineClient(title)
        self.client.tracked = self.qid(first)
        self.client.quests = {self.qid(r['number']): SimpleNamespace(
            name_lang_key=AsyncMock(return_value=r['keys'][0]), ready_to_turn_in=AsyncMock(return_value=False))
            for r in rows}
        self.client.quests[999] = SimpleNamespace(name_lang_key=AsyncMock(return_value='Quest Finder'))
        self.client.quests[777] = SimpleNamespace(name_lang_key=AsyncMock(return_value='OtherQuest'))
        names = {r['keys'][0]: r['english'] for r in self.rows}
        self.client.cache_handler.get_langcode_name.side_effect = lambda key: names.get(key, key)
        self.stack.enter_context(patch.object(progress, 'quest_rows', return_value=self.rows))

    @staticmethod
    def qid(number):
        return 100000 + number  # Synthetic fixture IDs; production must resolve the real keys.

    async def track(self, number):
        self.client.tracked = self.qid(number)
        await progress.log_mainline_progress(self.client)

    async def identity(self, number):
        self.client.tracked = self.qid(number)
        return await progress.read_mainline_identity(self.client)

    async def test_live_57_58_57_and_6_7_6_stay_matched_without_q_or_finder(self):
        for world, first, title in (('LEMURIA', 57, 'p1'), ('darkmoor', 6, 'p3'), ('Example', 5, 'p2')):
            for later_owned in (True, False):
                with self.subTest(world=world, later_owned=later_owned):
                    self.configure(world=world, first=first, title=title)
                    for number in (first, first+1):
                        await self.track(number)
                        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
                    if not later_owned:
                        self.client.quests.pop(self.qid(first+1))
                    for number in ((first, first+1, first) if later_owned else (first, first)):
                        await self.track(number)
                        self.assertIn(f'主线 {number}/', self.client._xuanshu_mainline_progress)
                        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
                    self.assertFalse(self.client.events)
                    last = self.client._xuanshu_mainline_last_success
                    self.assertEqual(last['highest_seen'][world], first+1)
                    self.assertFalse(last['completed_boundary'].get(world))

    async def test_prior_valid_owned_quest_survives_even_when_the_later_quest_is_no_longer_owned(self):
        await self.track(5)
        await self.track(6)
        self.client.quests.pop(self.qid(6))
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
        self.assertFalse(self.client.events)
        self.assertEqual(self.client._xuanshu_mainline_last_success['highest_seen']['Example'], 6)

    async def test_pending_recovery_accepts_the_owned_previous_identity_without_q(self):
        await self.track(5)
        await self.track(6)
        self.client._xuanshu_mainline_recovery = dict(phase='observe', attempts=0, observed=None)
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
        self.assertIsNone(self.client._xuanshu_mainline_recovery)
        self.assertFalse(self.client.events)

    async def test_owned_backtrack_preserves_the_seen_forward_anchor_and_its_direct_successor(self):
        await self.track(5)
        await self.track(6)
        await self.track(5)
        self.client.quests.pop(self.qid(6))
        last = self.client._xuanshu_mainline_last_success
        identity = await self.identity(7)
        self.assertTrue(progress.mainline_consistent(identity, last))
        self.assertIsNotNone(self.runtime().expected_identity(last, identity))
        self.client._xuanshu_mainline_recovery = dict(phase='observe', attempts=0, observed=None)
        await progress.log_mainline_progress(self.client)
        self.assertIn('主线 7/20', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
        self.assertFalse(self.client.events)

    async def test_book_selects_the_still_owned_previous_mainline_instead_of_finder(self):
        await self.track(5)
        await self.track(6)
        self.client.tracked = 777
        def nodes():
            return [Window('wndQuestInfo0', self.client, children=[
                Window('txtQuestTitle', self.client, text='Quest 5'),
                Window('txtGoal', self.client, text='Talk to NPC',
                       action=lambda: setattr(self.client, 'tracked', self.qid(5)))])]
        self.client.page_nodes = nodes
        recovery = self.runtime()
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.clock += 2
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client.tracked, self.qid(5))
        self.assertFalse(self.client.book_open)
        self.assertIsNone(self.client._xuanshu_mainline_recovery)
        self.assertTrue(any('source=quest-book' in line and 'match=True' in line and 'Quest 5' in line for line in self.logs))

    async def test_ready_to_turn_in_or_a_single_absence_is_not_retirement(self):
        self.client.quests[self.qid(5)].ready_to_turn_in.return_value = True
        await self.track(5)
        await self.track(6)
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        quest = self.client.quests.pop(self.qid(5))
        await self.track(6)
        self.client.quests[self.qid(5)] = quest
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        self.assertFalse(self.client._xuanshu_mainline_last_success['completed_boundary'].get('Example'))

    async def test_confirmed_retired_quest_is_rejected_even_if_a_stale_owned_entry_returns(self):
        self.client.quests[self.qid(5)].ready_to_turn_in.return_value = True
        await self.track(5)
        old = self.client.quests.pop(self.qid(5))
        await self.track(6)
        await self.track(6)
        last = self.client._xuanshu_mainline_last_success
        self.assertEqual(last.get('completed_boundary', {}).get('Example'), 5)
        self.client.quests[self.qid(5)] = old
        identity = await self.identity(5)
        self.assertFalse(progress.mainline_consistent(identity, last))
        self.assertIsNone(self.runtime().expected_identity(last, identity))
        self.assertEqual(progress.mainline_reject_reason(identity, last), 'retired-mainline')
        await progress.log_mainline_progress(self.client)
        self.assertIn('未匹配', self.client._xuanshu_mainline_progress)
        unowned = dict(identity, owned=frozenset(), row=self.rows[0], quest_id=self.qid(1))
        self.assertEqual(progress.mainline_reject_reason(unowned, last), 'behind-completed-mainline-boundary')

    async def test_retiring_a_later_quest_does_not_retire_an_earlier_owned_hole(self):
        await self.track(5)
        self.client.quests[self.qid(6)].ready_to_turn_in.return_value = True
        await self.track(6)
        self.client.quests.pop(self.qid(6))
        await self.track(7)
        await self.track(7)
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        last = self.client._xuanshu_mainline_last_success
        self.assertEqual(last['highest_seen']['Example'], 7)
        self.assertEqual(last['completed_boundary']['Example'], 6)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))

    async def test_loading_battle_and_torn_owned_maps_do_not_confirm_retirement(self):
        for state in ('loading', 'battle', 'torn-map'):
            with self.subTest(state=state):
                self.configure()
                self.client.quests[self.qid(5)].ready_to_turn_in.return_value = True
                await self.track(5)
                previous = self.client.quests.pop(self.qid(5))
                if state == 'torn-map':
                    reads = 0
                    def owned():
                        nonlocal reads
                        reads += 1
                        return dict(self.client.quests) if reads % 2 else {**self.client.quests, self.qid(5): previous}
                    manager = await self.client.quest_manager()
                    manager.quest_data.side_effect = owned
                else:
                    setattr(self.client, state, True)
                for _ in range(4):
                    await self.track(6)
                self.assertFalse(self.client._xuanshu_mainline_last_success['completed_boundary'].get('Example'))

    async def test_positive_current_ownership_is_not_rejected_only_by_a_higher_number(self):
        await self.track(6)  # First observation starts in the middle of the stage.
        self.client.quests.pop(self.qid(6))
        await self.track(5)
        self.assertIn('主线 5/20', self.client._xuanshu_mainline_progress)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.client))
        self.assertFalse(self.client.events)

    async def test_prior_world_history_cannot_authorize_a_cross_world_old_chain(self):
        await self.track(5)
        previous = self.client.quests.pop(self.qid(5))
        self.client.tracked = 777
        await progress.log_mainline_progress(self.client)
        last = self.client._xuanshu_mainline_last_success
        self.assertEqual(last['world'], 'Other')
        self.client.quests[self.qid(5)] = previous
        self.client.quests.pop(777)
        identity = await self.identity(5)
        self.assertFalse(progress.mainline_consistent(identity, last))
        self.assertIsNone(self.runtime().expected_identity(last, identity))

    async def test_unrelated_old_world_or_unowned_history_never_bypasses_guards(self):
        await self.track(5)
        await self.track(6)
        last = self.client._xuanshu_mainline_last_success
        unrelated = await self.identity(1)
        self.assertEqual(progress.mainline_reject_reason(unrelated, last), 'prior-mainline-still-owned')
        self.client.tracked = 777
        other = await progress.read_mainline_identity(self.client)
        self.assertFalse(progress.mainline_consistent(other, last))
        previous = await self.identity(5)
        self.assertFalse(progress.mainline_consistent(dict(previous, owned=frozenset()), last))
        self.client.tracked = 999
        finder = await progress.read_mainline_identity(self.client)
        self.assertFalse(progress.mainline_consistent(finder, last))
        self.assertIsNone(self.runtime().expected_identity(last, finder))

    async def test_read_failure_does_not_infer_retirement_and_clients_keep_separate_history(self):
        self.client.quests[self.qid(5)].ready_to_turn_in.return_value = True
        await self.track(5)
        old_client = self.client
        self.client.quests.pop(self.qid(5))
        await self.track(6)
        last = self.client._xuanshu_mainline_last_success
        self.client.quest_manager.side_effect = RuntimeError('unreadable quest map')
        await progress.log_mainline_progress(self.client)
        self.assertEqual(self.client._xuanshu_mainline_last_success, last)
        self.assertFalse(last.get('completed_boundary', {}).get('Example'))
        self.configure(title='p3')
        await self.track(5)
        self.assertEqual(self.client._xuanshu_mainline_last_success.get('highest_seen', {}).get('Example'), 5)
        self.assertEqual(old_client._xuanshu_mainline_last_success.get('highest_seen', {}).get('Example'), 6)

    async def test_existing_last_success_seeds_seen_history_without_seeding_completion(self):
        await self.track(6)
        old = self.client._xuanshu_mainline_last_success
        self.client._xuanshu_mainline_last_success = {
            key: old[key] for key in ('world', 'number', 'title', 'quest_id', 'goal_id', 'code', 'row')}
        self.client.quests.pop(self.qid(6))
        await self.track(5)
        last = self.client._xuanshu_mainline_last_success
        self.assertEqual(last['highest_seen']['Example'], 6)
        self.assertFalse(last['completed_boundary'])
        self.assertTrue(progress.mainline_consistent(await self.identity(7), last))

    async def test_match_log_explains_seen_owned_and_completed_without_loop_spam(self):
        await self.track(5)
        await self.track(6)
        self.logs.clear()
        await self.track(5)
        await progress.log_mainline_progress(self.client)
        lines = [line for line in self.logs if '[mainline_match]' in line]
        self.assertTrue(lines)
        for value in ('matched_index=', 'expected_index=', 'highest_seen=', 'completed_boundary=',
                      'owned-current=True', 'match=True'):
            self.assertIn(value, lines[-1])
        count = len(lines)
        await progress.log_mainline_progress(self.client)
        self.assertEqual(len([line for line in self.logs if '[mainline_match]' in line]), count)


del BookFixture
