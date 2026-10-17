"""Visible quest-book slots may be empty, partial, real quests or system cards."""
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_mainline_recovery as fixtures


class MainlineEmptySlotTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MainlineRecoveryTests.setUp
    initialize = fixtures.MainlineRecoveryTests.initialize
    runtime = fixtures.MainlineRecoveryTests.runtime
    tick = fixtures.MainlineRecoveryTests.tick

    def slot(self, number=2, *, title=None, objective=None, reward=False, hidden=False):
        fields = []
        if title is not None:
            fields.append(fixtures.Window('txtQuestTitle', self.client, text=title))
        if objective is not None:
            fields.append(fixtures.Window('txtGoal', self.client, text=objective))
        if reward:
            fields.append(fixtures.Window('txtRewardXP', self.client, text='100'))
        inner = fixtures.Window('wndQuestInfo', self.client, children=fields)
        content = fixtures.Window('questInfoWindow', self.client, children=[inner])
        if hidden:
            content.is_visible = AsyncMock(return_value=False)
        return fixtures.Window(f'wndQuestInfo{number}', self.client, children=[content])

    async def prepare(self, titles):
        self.client.pages = [titles]
        self.client.book_open = True
        recovery = self.runtime()
        self.original_nodes = self.client.page_nodes
        return recovery

    async def test_two_quests_and_visible_totally_empty_slot_return_two_quests(self):
        recovery = await self.prepare(['原主线', '下一主线'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot()]
        _, cards, _ = await recovery.page(self.client)
        self.assertEqual([title for title, _ in cards], ['原主线', '下一主线'])

    async def test_objective_without_title_is_partial_and_stable_page_retries(self):
        recovery = await self.prepare(['原主线'])
        calls = 0
        def nodes():
            nonlocal calls
            calls += 1
            return self.original_nodes() + [self.slot(title=None if calls == 1 else '下一主线',
                                                       objective='Talk to NPC')]
        self.client.page_nodes = nodes
        _, cards, _ = await recovery.stable_page(self.client, 'Mirage/Area', 'partial fixture')
        self.assertEqual(calls, 3, 'partial scan must be discarded before two complete matching scans')
        self.assertEqual([title for title, _ in cards], ['原主线', '下一主线'])

    async def test_one_quest_with_multiple_empty_slots_is_stable(self):
        recovery = await self.prepare(['原主线'])
        calls = 0
        def nodes():
            nonlocal calls
            calls += 1
            return self.original_nodes() + [self.slot(i) for i in range(1, 5)]
        self.client.page_nodes = nodes
        _, cards, _ = await recovery.stable_page(self.client, 'Mirage/Area', 'single quest fixture')
        self.assertEqual(calls, 2)
        self.assertEqual([title for title, _ in cards], ['原主线'])

    async def test_system_card_is_not_returned_as_an_ordinary_quest(self):
        recovery = await self.prepare(['原主线', '任务搜寻'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot()]
        _, cards, _ = await recovery.page(self.client)
        self.assertEqual([title for title, _ in cards], ['原主线'])

    async def test_come_on_chameleons_reaches_mainline_match_past_empty_slot(self):
        recovery = await self.prepare(['Come On, Chameleons!'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot()]
        _, cards, _ = await recovery.stable_page(self.client, 'Mirage/Area', 'chameleons fixture')
        row = dict(english='Come On, Chameleons!', chinese=[], aliases=[])
        self.assertIsNotNone(recovery.card_for_row(cards, row))

    async def test_empty_shell_layout_changes_do_not_reset_page_stability(self):
        recovery = await self.prepare(['原主线'])
        calls = 0
        def nodes():
            nonlocal calls
            calls += 1
            return self.original_nodes() + [self.slot(calls+1, hidden=calls % 2 == 0)]
        self.client.page_nodes = nodes
        _, cards, _ = await recovery.stable_page(self.client, 'Mirage/Area', 'empty layout fixture')
        self.assertEqual(calls, 2)
        self.assertEqual(len(cards), 1)

    async def test_hidden_quest_content_without_visible_title_cannot_be_skipped(self):
        recovery = await self.prepare(['原主线'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot(objective='Talk to NPC', hidden=True)]
        with self.assertRaisesRegex(RuntimeError, '任务卡片标题不可读'):
            await recovery.page(self.client)

    async def test_reward_without_title_is_partial_not_empty(self):
        recovery = await self.prepare(['原主线'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot(reward=True)]
        with self.assertRaisesRegex(RuntimeError, '任务卡片标题不可读'):
            await recovery.page(self.client)

    async def test_empty_layout_containers_are_not_quest_content(self):
        recovery = await self.prepare(['原主线'])
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                empty = self.slot(hidden=hidden)
                for name in ('QuestTitlePane', 'QuestRewardsPane'):
                    panel = fixtures.Window(name, self.client)
                    panel.read_type_name = AsyncMock(return_value='Window')
                    empty.nodes[0].nodes[0].nodes.append(panel)
                self.client.page_nodes = lambda: self.original_nodes() + [empty]
                _, cards, _ = await recovery.page(self.client)
                self.assertEqual([title for title, _ in cards], ['原主线'])

    async def test_hidden_empty_root_becoming_visible_during_scan_retries(self):
        recovery = await self.prepare(['原主线'])
        empty = self.slot(hidden=True)
        content = empty.nodes[0]
        original_children = content.children
        async def children():
            content.is_visible.return_value = True
            return await original_children()
        content.children = children
        self.client.page_nodes = lambda: self.original_nodes() + [empty]
        with self.assertRaisesRegex(RuntimeError, '任务卡片可见状态变化'):
            await recovery.page(self.client)

    async def test_title_only_partial_card_and_blank_title_field_retry(self):
        recovery = await self.prepare(['原主线'])
        for title, objective in (('下一主线', None), ('', 'Talk to NPC'), ('', None)):
            with self.subTest(title=title, objective=objective):
                self.client.page_nodes = lambda: self.original_nodes() + [self.slot(title=title, objective=objective)]
                with self.assertRaises(RuntimeError) as caught:
                    await recovery.page(self.client)
                self.assertTrue(recovery.transient_ui_read(caught.exception))

    async def test_title_only_finder_remains_navigation_only(self):
        recovery = await self.prepare(['原主线'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot(1, title='Quest Finder'), self.slot()]
        normal_signature, cards, _ = await recovery.page(self.client)
        self.assertEqual([title for title, _ in cards], ['原主线'])
        system_signature, navigation_cards, _ = await recovery.page(self.client, include_system=True)
        self.assertEqual(normal_signature, system_signature)
        self.assertEqual([title for title, _ in navigation_cards], ['原主线', 'Quest Finder'])
        self.assertIsNone(recovery.card_for_row(navigation_cards,
            dict(english='Quest Finder', chinese=[], aliases=[])))

    async def test_after_page_turn_remaining_empty_slots_are_normal(self):
        recovery = await self.prepare(['原主线', '下一主线'])
        self.client.pages.append(['Other Mainline'])
        self.client.page_nodes = lambda: self.original_nodes() + [self.slot(3), self.slot(4)]
        signature, _, left = await recovery.stable_page(self.client, 'Mirage/Area', 'before turn')
        await recovery.turn_left(self.client, signature, left, 'Mirage/Area')
        _, cards, _ = await recovery.stable_page(self.client, 'Mirage/Area', 'after turn')
        self.assertEqual([title for title, _ in cards], ['Other Mainline'])

    async def test_empty_slots_do_not_break_existing_finder_navigation(self):
        await self.initialize()
        self.client.tracked = 77
        recovery = self.runtime()
        original = self.client.page_nodes
        self.client.pages = [['Other Mainline'], ['任务搜寻']]
        self.client.page_nodes = lambda: original() + [self.slot(2), self.slot(3)]
        result = await recovery.select_from_book(self.client, self.client._xuanshu_mainline_last_success)
        self.assertEqual(result, 'finder')
        self.assertEqual(self.client.tracked, 999)
        self.assertFalse(self.client.book_open)
