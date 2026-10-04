import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src.questing import Quester
from src.mainline_progress import match_quest, quest_rows
from src.paths import all_quests_sort_button_path


class OwnedMainlineTrackingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = next(row for row in quest_rows() if row['english'] == 'Extra Life')
        self.now = 0.0
        self.page = 0
        self.open = False
        self.selected = False
        quest = SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_162472'))
        self.client = SimpleNamespace(
            title='p1',
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={42: quest}))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='Extra Life')),
            zone_name=AsyncMock(return_value=self.row['world'].split('(')[0].strip() + '/Area'),
            send_key=AsyncMock(side_effect=self.key),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester._questbook_page = AsyncMock(side_effect=self.scan)
        self.quester._click_ui_window = AsyncMock(side_effect=self.click)
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            (42, 'QuestTitle_162472', 'Extra Life', self.row, True) if self.selected else None))
        self.all_quests_click = AsyncMock()
        for patcher in (
            patch('src.questing.time.monotonic', side_effect=lambda: self.now),
            patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=self.sleep)),
            patch('src.questing.is_visible_by_path', new=AsyncMock(side_effect=lambda *args: self.open)),
            patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)),
            patch('src.questing.click_window_by_path', new=self.all_quests_click),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def key(self, key):
        self.assertEqual(key, Keycode.Q)
        self.open = not self.open

    async def sleep(self, seconds):
        self.now += seconds

    async def scan(self, client, mainlines=None):
        self.all_quests_click.assert_awaited_once_with(client, all_quests_sort_button_path)
        if mainlines is not None and self.page == 1:
            mainlines.append(('Extra Life', ('mainline', ('card', 'txtGoal'))))
        return ((str(self.page),), None, ('right', ('btnRight',)))

    async def click(self, client, target):
        if target == 'right':
            self.page = (self.page + 1) % 2
        else:
            self.selected = True

    async def test_owned_mainline_on_later_page_restores_and_closes_before_return(self):
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertTrue(self.selected)
        self.assertFalse(self.open)
        self.assertGreaterEqual(self.now, 3)
        self.assertEqual([call.args[1] for call in self.quester._click_ui_window.await_args_list],
                         ['right', 'mainline'])

    async def test_target_on_current_page_never_turns_page(self):
        self.page = 1
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')
        self.assertEqual(self.page, 1)

    async def test_current_page_tracking_failure_never_rescans_or_turns_page(self):
        self.page = 1
        self.quester._mainline_identity = AsyncMock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, '重新追踪尚未确认'):
            await self.quester._restore_owned_mainline(self.client)
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')

    async def test_other_world_mainline_does_not_get_selected(self):
        self.client.zone_name.return_value = 'AnotherWorld/Area'
        self.assertFalse(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.selected)
        self.assertFalse(self.open)

    async def test_darkmoor_same_title_owned_in_two_worlds_restores_only_current_world(self):
        self.row = next(row for row in quest_rows()
                        if row['world'] == 'darkmoor' and row['english'] == 'The Survivors')
        other = next(row for row in quest_rows()
                     if row['world'] != 'darkmoor'
                     and match_quest([row], None, '', 'The Survivors') is not None)
        self.client.zone_name.return_value = 'Darkmoor/DM_Z02_MortalPlain'
        self.client.quest_manager.return_value.quest_data.return_value = {
            42: SimpleNamespace(name_lang_key=AsyncMock(return_value=self.row['keys'][0])),
            99: SimpleNamespace(name_lang_key=AsyncMock(return_value=other['keys'][0])),
        }
        self.client.cache_handler.get_langcode_name.return_value = '幸存者'
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            (42, self.row['keys'][0], '幸存者', self.row, True) if self.selected else None))

        async def scan(client, mainlines=None):
            if mainlines is not None:
                mainlines.append(('幸存者', ('mainline', ('card', 'txtGoal'))))
            return (('only-page',), None, None)

        self.quester._questbook_page = AsyncMock(side_effect=scan)
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')
        self.assertFalse(self.open)

    def use_final_act(self):
        self.row = next(row for row in quest_rows() if row['english'] == 'The Final Act')
        self.client.quest_manager.return_value.quest_data.return_value = {
            153685337617326243: SimpleNamespace(
                name_lang_key=AsyncMock(return_value='QuestTitle_00002057')),
        }
        self.client.cache_handler.get_langcode_name.return_value = 'The Final Act'
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            (153685337617326243, 'QuestTitle_00002057', 'The Final Act', self.row, True)
            if self.selected else None))

        async def scan(client, mainlines=None):
            self.all_quests_click.assert_awaited_once_with(client, all_quests_sort_button_path)
            if mainlines is not None and self.page == 0:
                mainlines.extend([
                    ('最终一幕', ('mainline', ('card', 'txtGoal'))),
                    ('潜入海底', ('side', ('card2', 'txtGoal'))),
                    ('未知起源', ('unindexed', ('card3', 'txtGoal'))),
                ])
            return ((str(self.page),), None, ('right', ('btnRight',)))

        self.quester._questbook_page.side_effect = scan

    async def test_final_act_in_selenopolis_market_restores_without_turning_page(self):
        self.use_final_act()
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')
        self.assertFalse(self.open)
        self.assertGreaterEqual(self.now, 3)

    async def test_final_act_in_selenopolis_interior_restores(self):
        self.use_final_act()
        self.client.zone_name.return_value = (
            'Krokotopia/KT_Selenopolis/Interiors/KT_Z05I03_PropDepartment')
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')
        self.assertFalse(self.open)

    async def test_selenopolis_mapping_does_not_accept_other_krokotopia_zones(self):
        self.use_final_act()
        for zone in ('Krokotopia/Area', 'Krokotopia/KT_Selenopolis_Other/Area',
                     'AnotherWorld/KT_Selenopolis/Area'):
            with self.subTest(zone=zone):
                self.page = 0
                self.client.zone_name.return_value = zone
                self.all_quests_click.reset_mock()
                self.quester._click_ui_window.reset_mock()
                self.assertFalse(await self.quester._restore_owned_mainline(self.client))
                self.assertFalse(self.selected)
                self.assertFalse(self.open)
                self.assertTrue(all(call.args[1] == 'right' for call in
                                    self.quester._click_ui_window.await_args_list))

    async def test_multiple_owned_selenopolis_branches_restore_first_indexed_stage(self):
        self.use_final_act()
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
        self.client.quest_manager.return_value.quest_data.return_value[43] = SimpleNamespace(
            name_lang_key=AsyncMock(return_value='QuestTitle_00002058'))
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, 'mainline')
        self.assertFalse(self.open)

    async def test_duplicate_owned_ids_for_same_mainline_still_require_unique_target(self):
        self.use_final_act()
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
        self.client.quest_manager.return_value.quest_data.return_value[43] = SimpleNamespace(
            name_lang_key=AsyncMock(return_value='QuestTitle_00002057'))
        with self.assertRaisesRegex(RuntimeError, '同序号的当前世界主线'):
            await self.quester._restore_owned_mainline(self.client)
        self.quester._click_ui_window.assert_not_awaited()
        self.assertFalse(self.open)

    async def test_group_expected_id_can_restore_independently_of_zone(self):
        self.client.zone_name.return_value = 'Arcanum/Area'
        self.assertTrue(await self.quester._restore_owned_mainline(self.client, expected_id=42))
        self.assertFalse(self.open)

    async def test_tracking_read_failure_never_reports_task_absent(self):
        self.quester._mainline_identity = AsyncMock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, '主线已在任务列表'):
            await self.quester._restore_owned_mainline(self.client)
        self.assertFalse(self.open)

    async def test_existing_open_menu_is_closed_after_scan(self):
        self.open = True
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.open)

    async def test_close_failure_keeps_movement_blocked(self):
        self.open = True
        self.client.send_key.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '任务菜单未成功关闭'):
            await self.quester._close_questbook(self.client)

    async def test_missing_all_quests_button_stops_before_scan_and_closes_menu(self):
        with patch('src.questing.is_visible_by_path', new=AsyncMock(
                side_effect=lambda client, path: self.open and path != all_quests_sort_button_path)):
            with self.assertRaisesRegex(RuntimeError, '任务菜单未稳定打开'):
                await self.quester._restore_owned_mainline(self.client)
        self.all_quests_click.assert_not_awaited()
        self.quester._questbook_page.assert_not_awaited()
        self.assertFalse(self.open)

    async def test_failed_page_turn_never_claims_mainline_absent(self):
        self.quester._click_ui_window = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
            await self.quester._restore_owned_mainline(self.client)
        self.assertFalse(self.open)

    async def test_special_quest_and_bad_language_key_do_not_abort_scan(self):
        quests = await (await self.client.quest_manager()).quest_data()
        quests[99] = SimpleNamespace(name_lang_key=AsyncMock(return_value='任务搜寻'))
        quests[100] = SimpleNamespace(name_lang_key=AsyncMock(return_value='Unavailable_123'),
                                      mainline=AsyncMock(return_value=False))
        async def language(code):
            if code != 'QuestTitle_162472':
                raise ValueError('No lang file named ' + code)
            return 'Extra Life'
        self.client.cache_handler.get_langcode_name.side_effect = language
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.open)
        self.assertNotIn('任务搜寻', [call.args[0] for call in
                                     self.client.cache_handler.get_langcode_name.await_args_list])

    async def test_unindexed_owned_mainline_does_not_block_matching_mainline(self):
        quests = await (await self.client.quest_manager()).quest_data()
        quests[100] = SimpleNamespace(name_lang_key=AsyncMock(return_value='NewMainline'),
                                      mainline=AsyncMock(return_value=True))
        self.client.cache_handler.get_langcode_name.return_value = 'Unindexed Quest'
        self.assertTrue(await self.quester._restore_owned_mainline(self.client))
        self.assertTrue(self.selected)
        self.assertFalse(self.open)

    async def test_only_unindexed_owned_mainline_allows_finder(self):
        self.client.quest_manager.return_value.quest_data.return_value = {
            142707813505353448: SimpleNamespace(
                name_lang_key=AsyncMock(return_value='QuestTitle_71CCD'),
                mainline=AsyncMock(return_value=True)),
        }
        self.client.cache_handler.get_langcode_name.return_value = 'Of Unknown Origin'
        async def scan(client, mainlines=None):
            if mainlines is not None:
                mainlines.append(('Of Unknown Origin', ('unindexed', ('card', 'txtGoal'))))
            return ((str(self.page),), None, ('right', ('btnRight',)))
        self.quester._questbook_page.side_effect = scan
        self.assertFalse(await self.quester._restore_owned_mainline(self.client))
        self.assertFalse(self.selected)
        self.assertFalse(self.open)
        self.assertTrue(all(call.args[1] == 'right' for call in
                            self.quester._click_ui_window.await_args_list))
