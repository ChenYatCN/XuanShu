import unittest
from src.paths import npc_range_path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.paths import all_quests_sort_button_path, quest_buttons_parent_path
from src.questing import Quester
from src.mainline_progress import quest_rows


class DungeonQuestRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.title = "p1"
        self.client.questing_status = True
        self.client.quest_dungeon_recovery = None
        # Explicitly represent the real non-refilling flag, not a truthy mock.
        self.client.refilling_potions = False
        self.client.quest_recovery_owner = None
        for name in (
            "quest_party_probe_pending", "quest_party_battle_rescue_active",
            "quest_party_quest_worker_restart_requested", "post_combat_movement_active",
            "auto_dialogue_running",
        ):
            setattr(self.client, name, False)
        self.client.zone_name.return_value = "Dungeon/RoomA"
        self.client.quest_id.return_value = 42
        self.client.goal_id.return_value = 7
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(return_value="Defeat 0/3")
        self.now = 0.0
        self.menu_open = False
        self.page = 0
        self.row = next(row for row in quest_rows() if row['english'] == 'Extra Life')
        self.pages = [[('潜入海底', ('wrong', ('card0',))),
                       ('艰难的开始', ('child', ('card1',))),
                       ('Extra Life', ('current', ('card2',)))]]
        self.quests = {42: SimpleNamespace(name_lang_key=AsyncMock(return_value='DungeonQuest')),
                       99: SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_162472'))}
        self.client.quest_manager.return_value = SimpleNamespace(
            quest_data=AsyncMock(return_value=self.quests))
        self.client.cache_handler.get_langcode_name.side_effect = lambda code: {
            'DungeonQuest': '艰难的开始', 'OtherQuest': '潜入海底',
            'QuestTitle_162472': 'Extra Life',
        }[code]
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            (99, 'QuestTitle_162472', 'Extra Life', self.row, True)
            if c.quest_id.return_value == 99 else (42, 'DungeonQuest', '艰难的开始', None, False)))

        async def scan(_client, mainlines=None, *, cards=None, backwards=False):
            if cards is not None:
                cards.extend(self.pages[self.page])
            if mainlines is not None:
                mainlines.extend(self.pages[self.page])
            return (tuple(title for title, _ in self.pages[self.page]), None,
                    ('right', ('btnNextPage',)) if len(self.pages) > 1 else None)

        async def turn(_client, target):
            self.assertEqual(target, 'right')
            self.page = (self.page + 1) % len(self.pages)

        async def sleep(seconds):
            self.now += seconds

        self.quester._questbook_page = AsyncMock(side_effect=scan)
        self.quester._click_ui_window = AsyncMock(side_effect=turn)
        self.card_click = self.client.mouse_handler.click_window
        self.card_click.side_effect = lambda w: setattr(self.client.quest_id, 'return_value', 99)

        async def send_key(key, *args, **kwargs):
            if key == Keycode.Q:
                self.menu_open = not self.menu_open

        async def visible(_client, path):
            return self.menu_open and (
                path == all_quests_sort_button_path or path == quest_buttons_parent_path
            )

        self.client.send_key.side_effect = send_key
        self.visible = patch("src.questing.is_visible_by_path", side_effect=visible)
        self.free = patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True))
        self.spiral = patch("src.questing.is_spiral_door_open", new=AsyncMock(return_value=False))
        self.click = patch("src.questing.click_window_by_path", new=AsyncMock())
        self.clock = patch("src.questing.time", SimpleNamespace(monotonic=lambda: self.now))
        self.sleep = patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep))
        self.visible.start()
        self.free.start()
        self.spiral.start()
        self.clicked = self.click.start()
        self.clock.start()
        self.sleep.start()
        for item in (self.sleep, self.clock, self.click, self.spiral, self.free, self.visible):
            self.addCleanup(item.stop)

    async def arm(self):
        await self.quester._confirm_dungeon_entry(self.client, "World/Entrance", mainline_id=99)
        self.assertEqual(self.client.quest_dungeon_recovery["zone"], "Dungeon/RoomA")
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))

    async def test_confirmed_entry_and_three_minute_threshold(self):
        await self.quester._confirm_dungeon_entry(self.client, "Dungeon/RoomA")
        self.assertIsNone(self.client.quest_dungeon_recovery)
        await self.arm()
        self.now = 179.9
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()
        self.now = 180.0
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 2)
        self.clicked.assert_awaited_once_with(self.client, all_quests_sort_button_path)
        self.card_click.assert_awaited_once_with('current')
        self.assertFalse(self.menu_open)
        self.now = 181.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 2)
        self.now = self.client.quest_dungeon_recovery['since'] + 180.0
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 4)

    async def test_goal_count_progress_resets_timer(self):
        await self.arm()
        self.now = 179.0
        self.quester.read_quest_txt.return_value = "Defeat 1/3"
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.now = 180.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_shared_dungeon_refreshes_once_then_waits_for_manual_progress(self):
        self.client.quest_party_hitters = [SimpleNamespace()]
        self.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        await self.arm()
        self.now = 180
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.card_click.assert_awaited_once()
        self.now = self.client.quest_dungeon_recovery['since'] + 180
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertTrue(self.client.quest_dungeon_recovery['manual_wait'])
        self.now += 1000
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.card_click.assert_awaited_once()
        self.quester.read_quest_txt.return_value = 'Defeat 1/3'
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertFalse(self.client.quest_dungeon_recovery['manual_wait'])

    async def test_shared_room_auto_arms_even_with_finder_disabled(self):
        self.client.mainline_finder_enabled = False
        self.client.quest_party_hitters = [SimpleNamespace()]
        self.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.quest_dungeon_recovery['zone'], 'Dungeon/RoomA')
        self.now = 180
        self.quester._refresh_dungeon_quest = AsyncMock(return_value=True)
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.quester._refresh_dungeon_quest.assert_awaited_once()

    async def test_shared_refresh_failure_waits_immediately_without_repeat(self):
        self.client.quest_party_hitters = [SimpleNamespace()]
        self.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        await self.arm()
        self.quester._refresh_dungeon_quest = AsyncMock(return_value=False)
        self.now = 180
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.now += 1
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.now += 1000
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.quester._refresh_dungeon_quest.assert_awaited_once()
        self.assertTrue(self.client.quest_dungeon_recovery['manual_wait'])

    async def test_unknown_object_prompt_does_not_disable_shared_long_stall_recovery(self):
        self.client.quest_party_hitters = [SimpleNamespace()]
        self.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        self.quester.read_popup = AsyncMock(return_value='按X 未知动作')
        await self.arm()
        self.quester._refresh_dungeon_quest = AsyncMock(return_value=True)
        self.now = 180
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda client, path: path == npc_range_path)):
            self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.quester._refresh_dungeon_quest.assert_awaited_once()

    async def test_confirmed_dungeon_card_recovery_preempts_mainline_finder(self):
        self.client.mainline_finder_enabled = True
        self.client.mainline_finder_offer_guard = True
        self.quester._run_mainline_finder = AsyncMock()
        self.quester._mainline_finder_observations[id(self.client)] = {'count': 3}
        self.quester._mainline_finder_retry_at[id(self.client)] = 900
        await self.arm()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.assertFalse(self.client.mainline_finder_offer_guard)
        self.assertNotIn(id(self.client), self.quester._mainline_finder_observations)
        self.assertNotIn(id(self.client), self.quester._mainline_finder_retry_at)
        self.now = 180.0
        self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.card_click.assert_awaited_once_with('current')
        self.quester._mainline_identity.assert_awaited()
        self.quester._run_mainline_finder.assert_not_awaited()

    async def test_party_confirmed_dungeon_arms_card_recovery_before_finder(self):
        self.client.mainline_finder_enabled = True
        self.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        self.quester._mainline_identity = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.assertEqual(self.client.quest_dungeon_recovery['zone'], 'Dungeon/RoomA')
        self.assertEqual(self.client.quest_dungeon_recovery['since'], 0.0)
        self.quester._mainline_identity.assert_not_awaited()

    async def test_busy_state_defers_then_rechecks_progress(self):
        await self.arm()
        self.now = 181.0
        self.client.quest_party_probe_pending = True
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()
        self.client.quest_party_probe_pending = False
        self.quester.read_quest_txt.return_value = "Defeat 1/3"
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_unconfirmed_new_zone_disarms_recovery(self):
        await self.arm()
        self.client.zone_name.return_value = "World/Town"
        self.now = 300.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertIsNone(self.client.quest_dungeon_recovery)
        self.client.send_key.assert_not_awaited()

    async def test_party_confirmed_new_room_resets_timer(self):
        await self.arm()
        self.client.zone_name.return_value = "Dungeon/RoomB"
        self.client.quest_party_group_dungeon_zone = "Dungeon/RoomB"
        self.now = 300.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.quest_dungeon_recovery["zone"], "Dungeon/RoomB")
        self.assertIsNone(self.client.quest_dungeon_recovery["since"])
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_priority_state_after_open_closes_without_click(self):
        await self.arm()
        self.now = 180.0
        with patch.object(
            self.quester, "_dungeon_recovery_blocked_for_open_menu",
            new=AsyncMock(return_value=True),
        ):
            self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.clicked.assert_not_awaited()
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)
        self.assertEqual(self.client.send_key.await_count, 0)

    async def test_zero_quest_position_still_checks_dungeon_recovery(self):
        self.client.quest_position.position.return_value = XYZ(0.0, 0.0, 0.0)
        self.client.use_potions = False
        self.client.auto_pet_status = False
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        self.quester._maybe_recover_mainline = AsyncMock(return_value=False)
        self.quester._maybe_recover_nightmare = AsyncMock(return_value=False)
        self.quester._maybe_refresh_stalled_dungeon_quest = AsyncMock(return_value=True)
        with (patch("src.questing.close_npc_quest_menu", new=AsyncMock(return_value=False)),
              patch("src.questing.close_automation_popup", new=AsyncMock(return_value=False)),
              patch("src.questing.is_free", new=AsyncMock(return_value=True)),
              patch("src.questing.is_potion_needed", new=AsyncMock(return_value=False)),
              patch("src.mainline_progress.log_mainline_progress", new=AsyncMock())):
            await self.quester.auto_quest_solo()
        self.quester._maybe_refresh_stalled_dungeon_quest.assert_awaited_once_with(self.client)

    async def test_later_page_restores_mainline_not_current_dungeon_child(self):
        self.pages = [[('潜入海底', ('wrong', ('card0',)))],
                      [('艰难的开始', ('child', ('card1',)))],
                      [('Extra Life', ('current', ('card2',)))]]
        await self.arm()
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.assertEqual(self.quester._click_ui_window.await_count, 2)
        self.card_click.assert_awaited_once_with('current')
        self.assertFalse(self.menu_open)

    async def test_missing_target_scans_pages_without_selecting_any_card(self):
        self.pages = [[('潜入海底', ('wrong', ('card0',)))],
                      [('另一个任务', ('other', ('card1',)))]]
        await self.arm()
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.assertEqual(self.quester._click_ui_window.await_count, 2)
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)

    async def test_owned_same_title_ids_refuse_selection(self):
        self.quests[100] = SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_162472'))
        self.client.zone_name.return_value = self.row['world'] + '/Dungeon'
        with self.assertRaises(RuntimeError):
            await self.quester._refresh_dungeon_quest(self.client)
        self.card_click.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_duplicate_matching_cards_refuse_selection(self):
        await self.arm()
        self.pages[0].append(('Extra Life', ('duplicate', ('card3',))))
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)

    async def test_missing_owned_mainline_never_selects_dungeon_child(self):
        await self.arm()
        self.quests.clear()
        with self.assertRaises(RuntimeError):
            await self.quester._refresh_dungeon_quest(self.client)
        self.card_click.assert_not_awaited()

    async def test_unreadable_title_never_clicks_other_task(self):
        await self.arm()
        self.quests[99].name_lang_key.side_effect = RuntimeError('identity unavailable')
        self.client.cache_handler.get_langcode_name.side_effect = RuntimeError('title unavailable')
        self.now = 180
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.card_click.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_no_response_to_page_turn_is_bounded(self):
        self.pages = [[('潜入海底', ('wrong', ('card0',)))],
                      [('Extra Life', ('current', ('card1',)))]]
        await self.arm()
        self.quester._click_ui_window.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
            await self.quester._refresh_dungeon_quest(self.client)
        self.quester._click_ui_window.assert_awaited_once()
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)
        self.assertLess(self.now, 4)

    async def test_quest_change_while_scanning_never_clicks_stale_card(self):
        await self.arm()
        scan = self.quester._questbook_page.side_effect

        async def changed(*args, **kwargs):
            result = await scan(*args, **kwargs)
            self.client.quest_id.return_value = 99
            return result

        self.quester._questbook_page.side_effect = changed
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)

    async def test_page_change_while_waiting_for_mouse_never_clicks_stale_card(self):
        await self.arm()
        async def changed():
            self.pages = [[('潜入海底', ('wrong', ('card0',)))]]

        self.client.mouse_handler.__aenter__.side_effect = changed
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)

    async def test_refill_while_waiting_for_mouse_prevents_click(self):
        await self.arm()
        async def changed():
            self.client.refilling_potions = True

        self.client.mouse_handler.__aenter__.side_effect = changed
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_not_awaited()
        self.assertFalse(self.menu_open)

    async def test_post_click_wrong_tracking_id_is_not_success(self):
        await self.arm()
        self.card_click.side_effect = lambda w: setattr(self.client.quest_id, 'return_value', 43)
        with self.assertRaisesRegex(RuntimeError, '重新追踪尚未确认'):
            await self.quester._refresh_dungeon_quest(self.client)
        self.card_click.assert_awaited_once_with('current')
        self.assertFalse(self.menu_open)

    async def test_changed_goal_before_scan_does_not_reselect_old_stage(self):
        snapshot = await self.quester._dungeon_quest_snapshot(self.client)
        self.client.goal_id.return_value = 8
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client, snapshot))
        self.client.send_key.assert_not_awaited()
        self.card_click.assert_not_awaited()

    async def test_page_scanner_includes_unstarred_cards_without_changing_mainline_filter(self):
        menu, card, title, goal = (AsyncMock() for _ in range(4))
        path = (*quest_buttons_parent_path, 'wndQuestInfo1')
        nodes = [(menu, tuple(quest_buttons_parent_path)), (card, path),
                 (title, (*path, 'txtTitle')), (goal, (*path, 'txtGoal'))]
        self.quester._visible_window_nodes = AsyncMock(return_value=nodes)
        self.quester._window_text = AsyncMock(side_effect=lambda w: {
            id(title): '艰难的开始', id(goal): 'Defeat 0/3',
        }.get(id(w), ''))
        cards, mainlines = [], []
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=menu)):
            await Quester._questbook_page(self.quester, self.client, mainlines, cards=cards)
        self.assertEqual(mainlines, [])
        self.assertEqual(cards, [('艰难的开始', (goal, (*path, 'txtGoal')))])

    async def test_without_entry_record_uses_existing_current_world_mainline_matching(self):
        self.client.zone_name.return_value = self.row['world'] + '/Dungeon'
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')
        self.assertEqual(self.client.quest_id.return_value, 99)

    async def test_entry_mainline_id_wins_over_another_earlier_owned_mainline(self):
        await self.arm()
        other = next(row for row in quest_rows() if row['world'] == self.row['world']
                     and row['number'] < self.row['number'] and row['keys'])
        self.quests[100] = SimpleNamespace(name_lang_key=AsyncMock(return_value=other['keys'][0]))
        original = self.client.cache_handler.get_langcode_name.side_effect
        self.client.cache_handler.get_langcode_name.side_effect = lambda code: (
            other['english'] if code == other['keys'][0] else original(code))
        self.pages[0].insert(0, (other['english'], ('other-mainline', ('other-card',))))
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')
        self.assertEqual(self.client.quest_dungeon_recovery['mainline_id'], 99)

    async def test_verified_current_mainline_supersedes_old_entry_record(self):
        await self.arm()
        self.client.quest_dungeon_recovery['mainline_id'] = 100
        self.client.quest_id.return_value = 99
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')

    def use_darkmoor_indexed_child(self, flag=False):
        parent_id, child_id = 148618788036648672, 2460372771438335459
        parent_row = next(row for row in quest_rows() if row['english'] == 'Glam Rock')
        child_row = next(row for row in quest_rows() if row['english'] == 'Breaking Branch')
        self.client.zone_name.return_value = 'Darkmoor/Interiors/DM_Z01I12_DarkHallway'
        self.client.quest_id.return_value = child_id
        self.quester.read_quest_txt.return_value = '击败 火焰女帝 Empress of Flames 地点：Graveholm'
        self.quests.clear()
        self.quests.update({
            parent_id: SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_00002167')),
            child_id: SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_00002168')),
        })
        self.client.cache_handler.get_langcode_name.side_effect = lambda code: {
            'QuestTitle_00002167': 'Glam Rock', 'QuestTitle_00002168': 'Breaking Branch'}[code]
        self.pages = [[('华丽摇滚', ('current', ('card0',))),
                       ('潜入海底', ('wrong', ('card1',))),
                       ('未知起源', ('unindexed', ('card2',)))]]
        self.quester._mainline_identity.side_effect = lambda c: (
            (parent_id, 'QuestTitle_00002167', 'Glam Rock', parent_row, True)
            if c.quest_id.return_value == parent_id else
            (child_id, 'QuestTitle_00002168', 'Breaking Branch', child_row, flag))
        self.card_click.side_effect = lambda w: setattr(self.client.quest_id, 'return_value', parent_id)
        return parent_id

    async def test_indexed_false_mainline_child_cannot_replace_glam_rock_entry_target(self):
        parent_id = self.use_darkmoor_indexed_child()
        await self.quester._confirm_dungeon_entry(
            self.client, 'Darkmoor/DM_Z01_Graveholm', mainline_id=parent_id)
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')
        self.quester._click_ui_window.assert_not_awaited()  # No page turn.
        self.assertEqual(self.client.quest_id.return_value, parent_id)
        self.assertEqual(self.client.quest_dungeon_recovery['mainline_id'], parent_id)
        self.assertFalse(self.menu_open)

    async def test_indexed_child_without_entry_record_uses_starred_current_world_parent(self):
        parent_id = self.use_darkmoor_indexed_child()
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')
        self.quester._click_ui_window.assert_not_awaited()
        self.assertEqual(self.client.quest_id.return_value, parent_id)

    async def test_unreadable_mainline_flag_does_not_replace_confirmed_entry(self):
        parent_id = self.use_darkmoor_indexed_child(flag=None)
        await self.quester._confirm_dungeon_entry(
            self.client, 'Darkmoor/DM_Z01_Graveholm', mainline_id=parent_id)
        self.assertTrue(await self.quester._refresh_dungeon_quest(self.client))
        self.card_click.assert_awaited_once_with('current')

    async def test_stopped_after_selection_never_reports_restore_success(self):
        await self.arm()
        def click(window):
            self.client.quest_id.return_value = 99
            self.client.questing_status = False
        self.card_click.side_effect = click
        self.assertFalse(await self.quester._refresh_dungeon_quest(self.client))
        self.assertFalse(self.menu_open)

    def test_mainline_capture_precedes_entry_x_in_solo_and_legacy_workers(self):
        import ast
        import inspect
        import textwrap
        for worker in (Quester.enter_party_dungeon, Quester.handle_normal_quests):
            tree = ast.parse(textwrap.dedent(inspect.getsource(worker)))
            branch = tree if worker == Quester.enter_party_dungeon else next(
                node for node in ast.walk(tree) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Await) and isinstance(node.test.value, ast.Call)
                and isinstance(node.test.value.func, ast.Attribute)
                and node.test.value.func.attr == 'party_dungeon_entry_visible')
            capture = next(node for node in ast.walk(branch) if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == 'entry_mainline'
                                   for target in node.targets))
            press = min(node.lineno for node in ast.walk(branch) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == 'send_key')
            self.assertLess(capture.lineno, press)

    def test_entry_capture_requires_real_mainline_flag_in_both_workers(self):
        import ast
        import inspect
        import textwrap
        for worker in (Quester.enter_party_dungeon, Quester.handle_normal_quests):
            tree = ast.parse(textwrap.dedent(inspect.getsource(worker)))
            assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == 'entry_mainline_id'
                                      for target in node.targets))
            for flag in (True, False, None):
                with self.subTest(worker=worker.__name__, flag=flag):
                    namespace = {'entry_mainline': (42, 'key', 'title', self.row, flag)}
                    exec(compile(ast.Module(body=[assignment], type_ignores=[]),
                                 'src/questing.py', 'exec'), namespace)
                    self.assertEqual(namespace['entry_mainline_id'], 42 if flag is True else None)


if __name__ == "__main__":
    unittest.main()
