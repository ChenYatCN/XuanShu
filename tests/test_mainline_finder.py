import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.mainline_progress import quest_rows
from src.paths import quest_buttons_parent_path
from src.questing import Quester
from wizwalker import Keycode


def window(text=''):
    return SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        maybe_text=AsyncMock(return_value=text),
    )


class MainlineFinderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            mainline_finder_enabled=True,
            entity_detect_combat_status=False,
            is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Empyrea/Area'),
            send_key=AsyncMock(), root_window=object(),
        )
        self.quester = Quester(self.client, [self.client], None)

    async def test_disabled_does_not_read_or_interrupt_side_quest(self):
        self.client.mainline_finder_enabled = False
        self.client.mainline_finder_offer_guard = True
        self.quester._mainline_identity = AsyncMock()
        self.quester._run_mainline_finder = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._mainline_identity.assert_not_awaited()
        self.quester._run_mainline_finder.assert_not_awaited()
        self.assertFalse(self.client.mainline_finder_offer_guard)

    async def test_normal_mainline_does_not_trigger(self):
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'QuestTitle_162472', 'Extra Life', row, True))
        self.quester._run_mainline_finder = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_not_awaited()

    async def test_game_confirmed_mainline_is_not_recovered_on_index_miss(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'NewMainlineKey', 'New Mainline', None, True))
        self.quester._run_mainline_finder = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_not_awaited()

    async def test_confirmed_side_quest_waits_three_reads_then_claims_lock(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'SideQuest', 'Side Quest', None, False))
        self.quester._run_mainline_finder = AsyncMock(return_value=True)
        now = [0.0]
        with (patch('src.questing.time', SimpleNamespace(monotonic=lambda: now[0])),
              patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)),
              patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False))):
            for point in (0.0, 0.4):
                now[0] = point
                self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
                self.quester._run_mainline_finder.assert_not_awaited()
            now[0] = 0.9
            self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_awaited_once_with(self.client)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.mainline_finder_offer_guard)

    async def test_brief_identity_read_failure_does_not_resume_side_quest(self):
        self.quester._mainline_finder_observations[id(self.client)] = {
            'snapshot': (42, 'SideQuest', 'Side Quest', 'Empyrea/Area'),
            'count': 2, 'since': 0,
        }
        self.quester._mainline_identity = AsyncMock(return_value=None)
        self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.assertEqual(self.quester._mainline_finder_observations[id(self.client)]['count'], 0)

    async def test_zero_quest_id_needs_longer_stability_after_handoff(self):
        self.quester._mainline_identity = AsyncMock(return_value=(0, '', '', None, None))
        self.quester._run_mainline_finder = AsyncMock(return_value=False)
        now = [0.0]
        with (patch('src.questing.time', SimpleNamespace(monotonic=lambda: now[0])),
              patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)),
              patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False))):
            for point in (0.0, 1.0, 2.0):
                now[0] = point
                self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
                self.quester._run_mainline_finder.assert_not_awaited()
            now[0] = 3.1
            self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_awaited_once()
        self.assertTrue(self.client.mainline_finder_offer_guard)

    async def test_questbook_finds_card_and_named_right_button_under_parent(self):
        menu, card, title, goal, right = (window() for _ in range(5))
        title.maybe_text.return_value = '任务搜寻'
        goal.maybe_text.return_value = '在附近找一个可以接的任务'
        nodes = [
            (menu, tuple(quest_buttons_parent_path)),
            (card, (*quest_buttons_parent_path, 'wndQuestInfo2')),
            (title, (*quest_buttons_parent_path, 'wndQuestInfo2', 'txtTitle')),
            (goal, (*quest_buttons_parent_path, 'wndQuestInfo2', 'txtGoal')),
            (right, (*quest_buttons_parent_path, 'QuestLogRightButton')),
        ]
        self.quester._visible_window_nodes = AsyncMock(return_value=nodes)
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            signature, finder, next_page = await self.quester._questbook_page(self.client)
        self.assertEqual(len(signature), 1)
        self.assertIs(finder[0], goal)
        self.assertIs(next_page[0], right)
        self.assertEqual(list(next_page[1]), [*quest_buttons_parent_path, 'QuestLogRightButton'])

    async def test_questbook_refuses_ambiguous_right_button(self):
        menu, card, title, first, second = (window() for _ in range(5))
        title.maybe_text.return_value = '普通任务'
        nodes = [
            (menu, tuple(quest_buttons_parent_path)),
            (card, (*quest_buttons_parent_path, 'wndQuestInfo1')),
            (title, (*quest_buttons_parent_path, 'wndQuestInfo1', 'txtTitle')),
            (first, (*quest_buttons_parent_path, 'btnRight')),
            (second, (*quest_buttons_parent_path, 'NextPageButton')),
        ]
        self.quester._visible_window_nodes = AsyncMock(return_value=nodes)
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, finder, next_page = await self.quester._questbook_page(self.client)
        self.assertIsNone(finder)
        self.assertIsNone(next_page)

    async def test_flip_until_finder_without_fixed_page_count(self):
        page = [0]
        selected = [False]
        right, finder = object(), object()

        async def scan(_client):
            return ((f'page {page[0]}',),
                    (finder, ('WorldView', 'DeckConfiguration', 'wndQuestList', 'wndQuestInfo1'))
                    if page[0] == 2 else None,
                    (right, ('WorldView', 'DeckConfiguration', 'wndQuestList', 'btnRight')))

        async def click(_client, target):
            if target is right:
                page[0] += 1
            else:
                selected[0] = True

        async def identity(_client):
            return (99, 'Quest Finder', '', None, False) if selected[0] else None

        self.quester._questbook_page = scan
        self.quester._click_ui_window = click
        self.quester._mainline_identity = identity
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)):
            self.assertTrue(await self.quester._select_quest_finder(self.client))
        self.assertEqual(page[0], 2)
        self.assertTrue(selected[0])
        self.assertEqual(self.client.send_key.await_args_list[-1].args[0], Keycode.Q)

    async def test_repeated_page_stops_and_closes_menu(self):
        right = object()
        first = (('first page',), None, (right, ('btnRight',)))
        second = (('second page',), None, (right, ('btnRight',)))
        self.quester._questbook_page = AsyncMock(
            side_effect=(first, second, second, first, first))
        self.quester._click_ui_window = AsyncMock()
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)):
            with self.assertRaisesRegex(RuntimeError, '循环'):
                await self.quester._select_quest_finder(self.client)
        self.assertEqual(self.quester._click_ui_window.await_count, 2)
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_offer_requires_title_and_verified_mainline_quest_data(self):
        dialog, title = window(), window('额外生命')
        nodes = [(dialog, ('WorldView', 'wndDialogMain')),
                 (title, ('WorldView', 'wndDialogMain', 'txtQuestTitle'))]
        self.quester._visible_window_nodes = AsyncMock(return_value=nodes)
        quest = SimpleNamespace(
            mainline=AsyncMock(return_value=True),
            name_lang_key=AsyncMock(return_value='QuestTitle_162472'))
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))
        self.client.quest_manager = AsyncMock(return_value=manager)
        self.client.cache_handler = SimpleNamespace(
            get_langcode_name=AsyncMock(return_value='Extra Life'))
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=dialog)):
            candidate = await self.quester._mainline_offer_candidate(self.client)
            self.assertEqual(candidate[0], 42)
            quest.mainline.return_value = False
            self.assertIsNone(await self.quester._mainline_offer_candidate(self.client))
            quest.mainline.return_value = True
            self.quester._visible_window_nodes.return_value = [
                (dialog, ('WorldView', 'wndDialogMain')),
                (title, ('WorldView', 'wndDialogMain', 'txtMessage')),
            ]
            self.assertIsNone(await self.quester._mainline_offer_candidate(self.client))

    async def test_unverified_offer_is_declined(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(99, 'Quest Finder', '', None, False))
        self.quester._mainline_offer_candidate = AsyncMock(return_value=None)
        async def visible(_client, path):
            return path[-1] == 'btnLeft'
        with patch('src.questing.is_visible_by_path', side_effect=visible):
            self.assertFalse(await self.quester._run_mainline_finder(self.client))
        self.client.send_key.assert_awaited_once_with(Keycode.ESC, 0.1)

    async def test_unknown_dialogue_is_not_advanced_blindly(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(99, 'Quest Finder', '', None, False))
        ticks = [0]
        def tick():
            ticks[0] += 1
            return ticks[0]
        async def visible(_client, path):
            return path[-1] == 'btnRight'
        with (patch('src.questing.time', SimpleNamespace(monotonic=tick)),
              patch('src.questing.is_visible_by_path', side_effect=visible)):
            self.assertFalse(await self.quester._run_mainline_finder(self.client))
        self.client.send_key.assert_awaited_once_with(Keycode.ESC, 0.1)

    async def test_verified_offer_requires_matching_new_tracked_quest(self):
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        accepted = [False]
        self.quester._mainline_identity = AsyncMock(side_effect=lambda _client: (
            (42, 'QuestTitle_162472', 'Extra Life', row, True)
            if accepted[0] else (99, 'Quest Finder', '', None, False)))
        self.quester._mainline_offer_candidate = AsyncMock(return_value=(42, row))
        async def visible(_client, path):
            return path[-1] == 'btnLeft' and not accepted[0]
        async def click(_client, _path):
            accepted[0] = True
        with (patch('src.questing.is_visible_by_path', side_effect=visible),
              patch('src.questing.click_window_by_path', side_effect=click),
              patch('src.mainline_progress.log_mainline_progress', new=AsyncMock()) as progress):
            self.assertTrue(await self.quester._run_mainline_finder(self.client))
        progress.assert_awaited_once_with(self.client)
        self.assertIsNone(self.client._xuanshu_mainline_id)


if __name__ == '__main__':
    unittest.main()
