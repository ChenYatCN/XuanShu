import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.mainline_progress import quest_rows
from src.paths import quest_buttons_parent_path, all_quests_sort_button_path
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
            send_key=AsyncMock(), root_window=SimpleNamespace(children=AsyncMock(return_value=[])),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester._restore_owned_mainline = AsyncMock(return_value=False)
        async def close_book(client):
            await client.send_key(Keycode.Q)
        self.quester._close_questbook = AsyncMock(side_effect=close_book)

    async def test_disabled_does_not_read_or_interrupt_side_quest(self):
        self.client.mainline_finder_enabled = False
        self.client.mainline_finder_offer_guard = True
        self.client.mainline_chain_retry_active = True
        self.client.mainline_last_turn_in_snapshot = ('old',)
        self.client.outback_story_pending = True
        self.quester._mainline_identity = AsyncMock()
        self.quester._run_mainline_finder = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._mainline_identity.assert_not_awaited()
        self.quester._run_mainline_finder.assert_not_awaited()
        self.assertFalse(self.client.mainline_finder_offer_guard)
        self.assertFalse(self.client.mainline_chain_retry_active)
        self.assertIsNone(self.client.mainline_last_turn_in_snapshot)

    async def test_normal_mainline_does_not_trigger(self):
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'QuestTitle_162472', 'Extra Life', row, True))
        self.quester._run_mainline_finder = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_not_awaited()

    async def test_live_elephant_march_is_recognized_and_does_not_seek_another_quest(self):
        quest_id = 121315715316472848
        self.client.zone_name.return_value = 'Zafaria/ZF_Z07_Stone_Town'
        self.client.quest_id = AsyncMock(return_value=quest_id)
        self.client.quest_manager = AsyncMock(return_value=SimpleNamespace(
            quest_data=AsyncMock(return_value={quest_id: SimpleNamespace(
                name_lang_key=AsyncMock(return_value='QuestTitle_80B3E'),
                mainline=AsyncMock(return_value=True))})))
        self.client.cache_handler = SimpleNamespace(
            get_langcode_name=AsyncMock(return_value='Elephant March'))
        identity = await self.quester._mainline_identity(self.client)
        self.assertEqual(identity[3]['number'], 84)
        self.quester._run_mainline_finder = AsyncMock()
        self.quester._mainline_finder_retry_at[id(self.client)] = 60
        self.client.mainline_finder_offer_guard = True
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)):
            self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_not_awaited()
        self.quester._restore_owned_mainline.assert_not_awaited()
        self.assertFalse(self.client.mainline_finder_offer_guard)
        self.assertNotIn(id(self.client), self.quester._mainline_finder_retry_at)

    async def test_unindexed_mainline_runs_finder_after_stable_wait(self):
        for flag in (True, None):
            self.quester._mainline_identity = AsyncMock(
                return_value=(142707813505353448, 'QuestTitle_71CCD', 'Of Unknown Origin', None, flag))
            self.quester._run_mainline_finder = AsyncMock(return_value=True)
            self.quester._mainline_finder_blocked = AsyncMock(return_value=False)
            now = [0.0]
            with (patch('src.questing.time.monotonic', side_effect=lambda: now[0]),
                  patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False))):
                for point in (0.0, 0.4):
                    now[0] = point
                    self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
                    self.quester._run_mainline_finder.assert_not_awaited()
                now[0] = 3.1
                self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
            self.quester._run_mainline_finder.assert_awaited_once_with(self.client)
            self.assertIsNone(self.client.quest_recovery_owner)


    async def test_known_cave_does_not_redirect_explicit_finder_to_dungeon_reselection(self):
        self.client.zone_name.return_value = 'Lemuria/Interiors/LM_Z05_I02_Cave'
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        self.quester._mainline_identity = AsyncMock(return_value=(42, 'QuestTitle_162472', 'Extra Life', row, True))
        self.quester._run_mainline_finder = AsyncMock()
        self.quester._maybe_refresh_stalled_dungeon_quest = AsyncMock()
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)):
            self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.quester._mainline_identity.assert_awaited_once_with(self.client)
        self.quester._run_mainline_finder.assert_not_awaited()
        self.quester._maybe_refresh_stalled_dungeon_quest.assert_not_awaited()
        self.assertIsNone(getattr(self.client, 'quest_dungeon_recovery', None))

    async def test_stable_previous_mainline_can_seek_specific_next_id(self):
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'QuestTitle_162472', 'Extra Life', row, True))
        self.quester._run_mainline_finder = AsyncMock(return_value=False)
        now = [0.0]
        with (patch('src.questing.time.monotonic', side_effect=lambda: now[0]),
              patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)),
              patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False))):
            for point in (0, 1.5, 3.1):
                now[0] = point
                self.assertTrue(await self.quester._maybe_recover_mainline(self.client, expected_id=43))
        self.quester._run_mainline_finder.assert_awaited_once_with(self.client, expected_id=43)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_npc_range_does_not_permanently_block_finder(self):
        from src.paths import npc_range_path
        with (patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)),
              patch('src.questing.is_visible_by_path', new=AsyncMock(
                  side_effect=lambda c, path: path == npc_range_path))):
            self.assertFalse(await self.quester._mainline_finder_blocked(self.client))

    async def test_dialogue_settles_latest_quest_even_with_finder_disabled(self):
        self.client.mainline_finder_enabled = False
        self.client.quest_id = AsyncMock(return_value=100)
        self.client.goal_id = AsyncMock(return_value=1)
        now = [0.0]
        free = AsyncMock(return_value=False)
        with (patch('src.questing.is_free_leader_questing', new=free),
              patch('src.questing.time.monotonic', side_effect=lambda: now[0])):
            self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
            free.return_value = True
            self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
            now[0] = 2.0
            self.client.quest_id.return_value = 200
            self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
            now[0] = 2.2
            self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
            now[0] = 2.31
            self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertIsNone(self.client.quest_dialogue_settle)

    async def test_game_flag_does_not_allow_index_miss_movement(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(42, 'NewMainlineKey', 'New Mainline', None, True))
        self.quester._run_mainline_finder = AsyncMock()
        self.quester._mainline_finder_blocked = AsyncMock(return_value=False)
        self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_not_awaited()

    async def test_confirmed_side_quest_waits_three_reads_then_claims_lock(self):
        # Old dungeon metadata must not suppress Finder after leaving the instance.
        self.client.quest_dungeon_recovery = {'zone': 'Dungeon/OldRoom'}
        self.client.quest_party_group_dungeon_zone = 'Dungeon/OldRoom'
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
            now[0] = 3.1
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

    async def test_questbook_only_matches_visible_starred_cards(self):
        menu, first, second, title1, title2, star = (window() for _ in range(6))
        title1.maybe_text.return_value = 'Monkey Business'
        title2.maybe_text.return_value = 'Side Quest'
        first_path = (*quest_buttons_parent_path, 'wndQuestInfo3')
        second_path = (*quest_buttons_parent_path, 'wndQuestInfo1')
        nodes = [(menu, tuple(quest_buttons_parent_path)),
                 (first, first_path), (title1, (*first_path, 'txtTitle')),
                 (second, second_path), (title2, (*second_path, 'txtTitle'))]
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            for name in ('LeftMainline', 'RightMainline'):
                self.quester._visible_window_nodes = AsyncMock(return_value=nodes + [
                    (star, (*first_path, 'questInfoWindow', 'wndQuestInfo', name))])
                cards = []
                await self.quester._questbook_page(self.client, mainlines=cards)
                self.assertEqual([title for title, _ in cards], ['Monkey Business'])
            self.quester._visible_window_nodes.return_value = nodes
            cards = []
            await self.quester._questbook_page(self.client, mainlines=cards)
            self.assertEqual(cards, [])

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
        all_quests_click = AsyncMock()

        async def scan(_client, *, backwards=False):
            all_quests_click.assert_awaited_once_with(_client, all_quests_sort_button_path)
            self.assertTrue(backwards)
            return ((f'page {page[0]}',),
                    (finder, ('WorldView', 'DeckConfiguration', 'wndQuestList', 'wndQuestInfo1'))
                    if page[0] == 2 else None,
                    (right, ('WorldView', 'DeckConfiguration', 'wndQuestList', 'btnPrevPage')))

        async def click(_client, target):
            if target is right:
                page[0] += 1
            else:
                selected[0] = True

        async def identity(_client):
            return (60216001, '任务搜寻', '', None, False) if selected[0] else None

        self.quester._questbook_page = scan
        self.quester._click_ui_window = click
        self.quester._mainline_identity = identity
        with (patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)),
              patch('src.questing.click_window_by_path', new=all_quests_click)):
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
        with (patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)),
              patch('src.questing.click_window_by_path', new=AsyncMock()) as all_quests_click):
            with self.assertRaisesRegex(RuntimeError, '循环'):
                await self.quester._select_quest_finder(self.client)
        all_quests_click.assert_awaited_once_with(self.client, all_quests_sort_button_path)
        self.assertEqual(self.quester._click_ui_window.await_count, 2)
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_missing_all_quests_button_stops_finder_before_scan(self):
        self.quester._questbook_page = AsyncMock()
        ticks = iter(range(20))
        with (patch('src.questing.time', SimpleNamespace(monotonic=lambda: next(ticks))),
              patch('src.questing.asyncio.sleep', new=AsyncMock()),
              patch('src.questing.is_visible_by_path', new=AsyncMock(
                  side_effect=lambda client, path: path == quest_buttons_parent_path)),
              patch('src.questing.click_window_by_path', new=AsyncMock()) as all_quests_click):
            with self.assertRaisesRegex(RuntimeError, '任务菜单未稳定打开'):
                await self.quester._select_quest_finder(self.client)
        all_quests_click.assert_not_awaited()
        self.quester._questbook_page.assert_not_awaited()
        self.quester._close_questbook.assert_awaited_once_with(self.client)

    async def test_finder_scan_uses_left_button_and_ignores_card_children(self):
        menu, card, title, left, right, star = (window() for _ in range(6))
        title.maybe_text.return_value = '普通任务'
        card_path = (*quest_buttons_parent_path, 'wndQuestInfo1')
        self.quester._visible_window_nodes = AsyncMock(return_value=[
            (menu, tuple(quest_buttons_parent_path)), (card, card_path),
            (title, (*card_path, 'txtTitle')), (star, (*card_path, 'LeftMainline')),
            (left, (*quest_buttons_parent_path, 'btnPrevPage')),
            (right, (*quest_buttons_parent_path, 'btnNextPage')),
        ])
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, _, previous = await self.quester._questbook_page(self.client, backwards=True)
            _, _, following = await self.quester._questbook_page(self.client)
        self.assertIs(previous[0], left)
        self.assertIs(following[0], right)

    async def test_known_page_buttons_take_priority_over_backgrounds_and_arrow_children(self):
        menu, card, left, right = (window() for _ in range(4))
        card_path = (*quest_buttons_parent_path, 'wndQuestInfo1')
        self.quester._visible_window_nodes = AsyncMock(return_value=[
            (menu, tuple(quest_buttons_parent_path)), (card, card_path),
            (left, (*quest_buttons_parent_path, 'btnPrevPage')),
            (right, (*quest_buttons_parent_path, 'btnNextPage')),
            (window(), (*quest_buttons_parent_path, 'LogToggleButtonBackground')),
            (window(), (*quest_buttons_parent_path, 'QuestLogButtonsBackground')),
            (window(), (*quest_buttons_parent_path, 'RightPageBackground')),
            (window(), (*quest_buttons_parent_path, 'btnPrevPage', 'LeftArrow')),
            (window(), (*quest_buttons_parent_path, 'btnNextPage', 'RightArrow')),
        ])
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, _, previous = await self.quester._questbook_page(self.client, backwards=True)
            _, _, following = await self.quester._questbook_page(self.client)
        self.assertIsNotNone(previous)
        self.assertIsNotNone(following)
        self.assertIs(previous[0], left)
        self.assertIs(following[0], right)

    async def test_fallback_page_button_ignores_backgrounds(self):
        menu, card, left = (window() for _ in range(3))
        self.quester._visible_window_nodes = AsyncMock(return_value=[
            (menu, tuple(quest_buttons_parent_path)),
            (card, (*quest_buttons_parent_path, 'wndQuestInfo1')),
            (left, (*quest_buttons_parent_path, 'btnBack')),
            (window(), (*quest_buttons_parent_path, 'QuestLogButtonsBackground')),
        ])
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, _, previous = await self.quester._questbook_page(self.client, backwards=True)
        self.assertIsNotNone(previous)
        self.assertIs(previous[0], left)

    async def test_background_alone_is_not_a_page_button(self):
        menu, card = window(), window()
        self.quester._visible_window_nodes = AsyncMock(return_value=[
            (menu, tuple(quest_buttons_parent_path)),
            (card, (*quest_buttons_parent_path, 'wndQuestInfo1')),
            (window(), (*quest_buttons_parent_path, 'QuestLogButtonsBackground')),
        ])
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, _, previous = await self.quester._questbook_page(self.client, backwards=True)
        self.assertIsNone(previous)

    async def test_ambiguous_real_page_buttons_are_still_rejected(self):
        menu, card = window(), window()
        self.quester._visible_window_nodes = AsyncMock(return_value=[
            (menu, tuple(quest_buttons_parent_path)),
            (card, (*quest_buttons_parent_path, 'wndQuestInfo1')),
            (window(), (*quest_buttons_parent_path, 'btnPrevPage')),
            (window(), (*quest_buttons_parent_path, 'OtherPanel', 'btnPrevPage')),
        ])
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            _, _, previous = await self.quester._questbook_page(self.client, backwards=True)
        self.assertIsNone(previous)

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
            if accepted[0] else (60216001, '任务搜寻', '', None, False)))
        self.quester._select_quest_finder = AsyncMock(return_value=False)
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
        self.quester._select_quest_finder.assert_not_awaited()
        self.assertIsNone(self.client._xuanshu_mainline_id)

    async def test_finder_guided_npc_can_offer_quest_not_yet_owned(self):
        from wizwalker import XYZ
        from src.paths import npc_range_path, advance_dialog_path, decline_quest_path
        row = next(r for r in quest_rows() if r['english'] == 'Extra Life')
        stage = [-1]
        async def select(c):
            self.quester._restore_owned_mainline.assert_awaited_once_with(c, expected_id=None)
            stage[0] = 0
            return True
        self.quester._select_quest_finder = AsyncMock(side_effect=select)
        self.client.quest_position = SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 100, 0)))
        self.client.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 100, 0)))
        self.quester.read_popup = AsyncMock(return_value='talk')
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c:
            (42, 'QuestTitle_162472', 'Extra Life', row, True) if stage[0] == 3
            else (100, 'SideQuest', '', None, False) if stage[0] == -1
            else (60216001, '任务搜寻', '', None, False))
        self.quester._mainline_offer_candidate = AsyncMock(return_value=None)
        async def key(key, *args):
            self.assertEqual(key, Keycode.X)
            stage[0] = 1
        self.client.send_key.side_effect = key
        async def visible(c, path):
            return (path == npc_range_path and stage[0] == 0
                    or path == advance_dialog_path and stage[0] in (1, 2)
                    or path == decline_quest_path and stage[0] == 2)
        async def click(c, path):
            self.assertEqual(path, advance_dialog_path)
            stage[0] += 1
        with (patch('src.questing.is_visible_by_path', side_effect=visible),
              patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
              patch('src.questing.interaction_kind', return_value='talk'),
              patch('src.questing.click_window_by_path', side_effect=click),
              patch('src.questing.asyncio.sleep', new=AsyncMock()),
              patch('src.mainline_progress.log_mainline_progress', new=AsyncMock())):
            self.assertTrue(await self.quester._run_mainline_finder(self.client))
        self.assertEqual(stage[0], 3)
        self.quester._select_quest_finder.assert_awaited_once_with(self.client)

    async def test_owned_matching_mainline_skips_finder_and_acceptance(self):
        self.quester._mainline_identity = AsyncMock(
            return_value=(100, 'SideQuest', '', None, False))
        self.quester._restore_owned_mainline.return_value = True
        self.quester._select_quest_finder = AsyncMock()
        self.quester._mainline_offer_candidate = AsyncMock()
        self.assertTrue(await self.quester._run_mainline_finder(self.client))
        self.quester._select_quest_finder.assert_not_awaited()
        self.quester._mainline_offer_candidate.assert_not_awaited()
        self.client.send_key.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
