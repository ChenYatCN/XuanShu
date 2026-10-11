"""Historical photo inputs and operation-local closed-dungeon recall failure."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src import utils, script_popups
from src.questing import Quester


class PhotoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.zone = 'World/Area'
        self.goal = 'Photomance the tree'
        self.goal_id = 7
        self.quest_id = 42
        self.prompt = 'Photomance 巨美大桶'
        self.language_key = 'QuestTitle_19BE7D'
        self.discard = SimpleNamespace(value='丢弃照片', is_visible=AsyncMock(return_value=True),
            maybe_read_type_name=AsyncMock(return_value='ControlButton'))
        self.keep = SimpleNamespace(value='保留照片', is_visible=AsyncMock(return_value=True),
            maybe_read_type_name=AsyncMock(return_value='ControlButton'))
        self.camera = SimpleNamespace(update_orientation=AsyncMock(
            side_effect=lambda o: self.events.append(('camera', o))))
        quest = SimpleNamespace(name_lang_key=AsyncMock(side_effect=lambda: self.language_key))
        manager = SimpleNamespace(quest_data=AsyncMock(side_effect=lambda: {self.quest_id: quest}))
        self.client = SimpleNamespace(title='p1', questing_status=True,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.quest_id),
            goal_id=AsyncMock(side_effect=lambda: self.goal_id),
            quest_manager=AsyncMock(return_value=manager),
            send_key=AsyncMock(side_effect=lambda k, t: self.events.append(('key', k))),
            teleport=AsyncMock(side_effect=lambda p: self.events.append(('tp', p))),
            body=SimpleNamespace(write_orientation=AsyncMock(side_effect=lambda o: self.events.append(('body', o)))),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
            mouse_handler=AsyncMock(),
            root_window=SimpleNamespace(get_windows_with_predicate=AsyncMock(
                side_effect=lambda _: [self.discard, self.keep] if self.keep.is_visible.return_value else [])))
        self.client.mouse_handler.click_window.side_effect = lambda b: self.events.append(('discard', b))
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.goal)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sleep = self.stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
        self.stack.enter_context(patch('src.script_popups.read_control_text', AsyncMock(side_effect=lambda w: w.value)))

    async def shoot(self):
        await self.quester.take_photomancy_photo(self.client, self.goal)

    def keys(self):
        return [e[1] for e in self.events if e[0] == 'key']

    async def test_ordinary_s_zz_then_actual_discard(self):
        await self.shoot()
        self.assertEqual(self.events, [('key', Keycode.S), ('key', Keycode.Z), ('key', Keycode.Z), ('discard', self.discard)])
        self.assertEqual([c.args[1] for c in self.client.send_key.await_args_list], [.2, .1, .1])

    async def test_delayed_result_waits_for_actual_buttons(self):
        self.keep.is_visible.return_value = False
        async def appear(_):
            self.keep.is_visible.return_value = True
        self.sleep.side_effect = appear
        await self.shoot()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.discard)

    async def test_no_result_does_not_click_unrelated_ui(self):
        self.keep.is_visible.return_value = False
        await self.shoot()
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.assertEqual(self.sleep.await_count, 10)

    async def test_gummy_exact_position_body_camera_and_a_s_zz(self):
        self.zone = Quester.GUMMY_WORMS_PHOTO_ZONE
        self.goal = 'Photomance Gummy Worms'
        await self.shoot()
        self.assertEqual(tuple(self.events[0][1]), (1258.114, 2119.140, 2.000))
        self.assertEqual(self.events[1], ('key', Keycode.A))
        self.assertEqual(tuple(self.events[2][1]), (0, 0, 1.590))
        self.assertEqual(self.events[3], ('camera', self.events[2][1]))
        self.assertEqual(self.keys(), [Keycode.A, Keycode.S, Keycode.Z, Keycode.Z])

    async def test_gummy_other_zone_uses_ordinary_photo(self):
        self.goal = 'Photomance Gummy Worms'
        await self.shoot()
        self.client.teleport.assert_not_awaited()
        self.assertEqual(self.keys(), [Keycode.S, Keycode.Z, Keycode.Z])

    async def test_giant_two_historical_views_and_discard_each_result(self):
        self.goal = self.prompt
        await self.shoot()
        self.assertEqual(tuple(self.events[0][1]), (48.393, 187.929, 2))
        self.assertEqual([tuple(c.args[0]) for c in self.client.body.write_orientation.await_args_list],
                         [(0, 0, 3.855), (0, 0, 3.146)])
        self.assertEqual(self.camera.update_orientation.await_args_list, self.client.body.write_orientation.await_args_list)
        self.assertEqual(self.keys(), [Keycode.A, Keycode.S, Keycode.Z, Keycode.Z, Keycode.Z, Keycode.Z])
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 2)

    async def test_giant_progress_or_unreadable_skips_second_view(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                self.setUp()
                self.goal = self.prompt
                async def result(_):
                    self.goal_id += 1
                    if unreadable:
                        self.quest_id = 0
                self.client.mouse_handler.click_window.side_effect = result
                await self.shoot()
                self.assertEqual(self.camera.update_orientation.await_count, 1)
                self.assertEqual(self.client.send_key.await_count, 4)

    async def test_giant_changed_prompt_never_sends_photo_keys(self):
        self.goal = self.prompt
        self.prompt = 'Press X to Use'
        await self.shoot()
        self.client.send_key.assert_not_awaited()

    async def test_mayor_servant_and_harp_exact_views(self):
        self.zone = Quester.DARKMOOR_MAYOR_PHOTO_ZONE
        for name, point, yaw in [('半死不活的附庸', (531.067, -473.838, .999), 2.469),
                                  ('预兆竖琴', (-1203.329, 3168.999, .999), 1.490)]:
            with self.subTest(name=name):
                self.events.clear()
                self.goal = f'Photomance {name} 地点：Mortal Plain'
                await self.shoot()
                self.assertEqual(tuple(self.events[0][1]), point)
                self.assertEqual(tuple(self.events[1][1]), (0, 0, yaw))
                self.assertEqual(self.events[2], ('camera', self.events[1][1]))
                self.assertEqual(self.keys(), [Keycode.S, Keycode.Z, Keycode.Z])
                self.assertEqual(self.events[-1], ('discard', self.discard))

    async def test_mayor_wrong_quest_does_not_fall_through_to_generic_z(self):
        self.zone = Quester.DARKMOOR_MAYOR_PHOTO_ZONE
        self.goal = 'Photomance 半死不活的附庸 地点：Mortal Plain'
        self.language_key = 'OtherQuest'
        await self.shoot()
        self.assertEqual(self.events, [])

    async def test_easton_ritual_only_at_historical_quest_stage(self):
        self.zone = Quester.EASTON_DAY_RITUAL_ZONE
        self.goal = 'Photomance 仪式'
        await self.shoot()
        self.assertEqual(self.events, [])
        self.quest_id = Quester.EASTON_DAY_QUEST_ID
        await self.shoot()
        self.assertEqual(tuple(self.events[0][1]), (-6454.599, 2026.960, 851.999))
        self.assertEqual(tuple(self.events[1][1]), (0, 0, 1.573))
        self.assertEqual(self.events[2], ('camera', self.events[1][1]))
        self.assertEqual(self.keys(), [Keycode.S, Keycode.Z, Keycode.Z])

    async def test_stop_loading_battle_or_zone_change_between_keys(self):
        for change in ('stop', 'loading', 'battle', 'zone'):
            with self.subTest(change=change):
                self.events.clear()
                self.client.questing_status = True
                self.client.is_loading.return_value = False
                self.client.in_battle.return_value = False
                self.zone = 'World/Area'
                async def input_changed(key, _):
                    self.events.append(('key', key))
                    if change == 'stop': self.client.questing_status = False
                    if change == 'loading': self.client.is_loading.return_value = True
                    if change == 'battle': self.client.in_battle.return_value = True
                    if change == 'zone': self.zone = 'Other'
                self.client.send_key.side_effect = input_changed
                await self.shoot()
                self.assertEqual(self.keys(), [Keycode.S])
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_discard_rechecks_stop_battle_loading_and_buttons_after_mouse_wait(self):
        for change in ('stop', 'battle', 'loading', 'buttons'):
            with self.subTest(change=change):
                self.client.questing_status = True
                self.client.in_battle.return_value = self.client.is_loading.return_value = False
                self.keep.is_visible.return_value = True
                async def replaced():
                    if change == 'stop': self.client.questing_status = False
                    if change == 'battle': self.client.in_battle.return_value = True
                    if change == 'loading': self.client.is_loading.return_value = True
                    if change == 'buttons': self.keep.is_visible.return_value = False
                self.client.mouse_handler.__aenter__.side_effect = replaced
                self.assertFalse(await script_popups.discard_photo_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_task_popup_guard_discards_photo_in_task_mode(self):
        with patch('src.script_popups.closed_dungeon_popup', AsyncMock(return_value=False)):
            self.assertTrue(await script_popups.close_automation_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.discard)
        self.assertTrue(self.client.questing_status)


class ClosedDungeonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.title = SimpleNamespace(value='信息', is_visible=AsyncMock(return_value=True))
        self.caption = SimpleNamespace(value='区域加载失败，地下城已关闭。', is_visible=AsyncMock(return_value=True))
        self.ok = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.other = SimpleNamespace(is_visible=AsyncMock(return_value=False))
        self.modal = SimpleNamespace(is_visible=AsyncMock(return_value=True), get_windows_with_name=AsyncMock(
            side_effect=lambda name: [self.title] if name == 'TitleText' else [self.caption] if name == 'CaptionText' else []))
        self.client = SimpleNamespace(title='p1', questing_status=True, send_key=AsyncMock(),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[self.modal])),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Shop'), mouse_handler=AsyncMock())
        async def clicked(_): self.modal.is_visible.return_value = False
        self.client.mouse_handler.click_window.side_effect = clicked
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.utils.read_control_text', AsyncMock(side_effect=lambda w: w.value)))
        self.paths = self.stack.enter_context(patch('src.utils.get_window_from_path', AsyncMock(
            side_effect=lambda root, path: self.ok if path[-1] == 'centerButton' else self.other)))
        self.stack.enter_context(patch('src.utils.asyncio.sleep', AsyncMock()))
        self.timer = self.stack.enter_context(patch('src.utils.wait_for_teleport_mark_timer', AsyncMock(return_value=True)))

    async def test_exact_chinese_scoped_ok_keeps_task_on_no_keys_or_sticky_flag(self):
        self.assertTrue(await script_popups.close_automation_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.ok)
        self.assertTrue(self.client.questing_status)
        self.client.send_key.assert_not_awaited()
        self.assertFalse(hasattr(self.client, '_xuanshu_dungeon_closed'))
        for call in self.paths.await_args_list:
            self.assertIs(call.args[0], self.modal)
            self.assertEqual(call.args[1][:-1], ['messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout'])

    async def test_unrelated_information_and_wrong_title_are_untouched(self):
        for title, text in [('信息', '地下城已关闭。'), ('信息', '区域文件正在下载。'),
                            ('确认', '区域加载失败，地下城已关闭。'), ('信息', '购买物品？')]:
            self.title.value, self.caption.value = title, text
            self.assertFalse(await utils.closed_dungeon_popup(self.client, dismiss=True))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_existing_english_closed_instance_is_supported(self):
        self.title.value = 'Information'
        self.caption.value = 'Failed to load area. The instance is closed.'
        self.assertTrue(await utils.closed_dungeon_popup(self.client, dismiss=True))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.ok)

    async def test_multiple_buttons_or_replaced_modal_are_not_clicked(self):
        self.other.is_visible.return_value = True
        self.assertTrue(await utils.closed_dungeon_popup(self.client, dismiss=True))
        self.other.is_visible.return_value = False
        async def replaced(): self.caption.value = '购买物品？'
        self.client.mouse_handler.__aenter__.side_effect = replaced
        self.assertTrue(await utils.closed_dungeon_popup(self.client, dismiss=True))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_visible_error_while_loading_still_abandons_recall(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await utils.recall_to_teleport_mark(self.client, expected_zone='Dungeon'))
        self.client.send_key.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.assertFalse(hasattr(self.client, '_teleport_mark_return'))

    async def test_error_before_keys_returns_false_and_next_operation_is_allowed(self):
        self.assertFalse(await utils.recall_to_teleport_mark(self.client, expected_zone='Dungeon'))
        self.client.send_key.assert_not_awaited()
        self.assertFalse(hasattr(self.client, '_teleport_mark_return'))
        self.client.zone_name.side_effect = ['Shop', 'Dungeon', 'Dungeon']
        self.assertTrue(await utils.recall_to_teleport_mark(self.client, expected_zone='Dungeon'))
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_error_between_keys_abandons_without_second_key_or_retry(self):
        self.modal.is_visible.return_value = False
        async def reveal(*_): self.modal.is_visible.return_value = True
        self.client.send_key.side_effect = reveal
        self.assertFalse(await utils.recall_to_teleport_mark(self.client, expected_zone='Dungeon'))
        self.assertEqual(self.client.send_key.await_count, 1)
        self.assertTrue(self.client.questing_status)

    async def test_separate_watcher_closes_during_loading_cannot_report_success(self):
        self.modal.is_visible.return_value = False
        async def key(*_):
            if self.client.send_key.await_count == 2:
                self.client.is_loading.return_value = True
        self.client.send_key.side_effect = key
        async def loading(_):
            self.modal.is_visible.return_value = True
            await script_popups.close_automation_popup(self.client)
            self.client.is_loading.return_value = False
            self.client.zone_name.return_value = 'Dungeon'
        with patch('src.utils.asyncio.sleep', AsyncMock(side_effect=loading)):
            self.assertFalse(await utils.recall_to_teleport_mark(self.client, expected_zone='Dungeon'))
        self.assertEqual(self.client.send_key.await_count, 2)
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.ok)
        self.assertFalse(hasattr(self.client, '_teleport_mark_return'))
        self.assertTrue(self.client.questing_status)

    async def test_task_stop_or_battle_between_keys_does_not_repeat_input(self):
        self.modal.is_visible.return_value = False
        for stop in (True, False):
            self.client.questing_status = True
            self.client.in_battle.return_value = False
            self.client.send_key.reset_mock()
            async def stopped(*_):
                if stop: self.client.questing_status = False
                else: self.client.in_battle.return_value = True
            self.client.send_key.side_effect = stopped
            self.assertFalse(await utils.recall_to_teleport_mark(self.client))
            self.assertEqual(self.client.send_key.await_count, 1)

    async def test_cancellation_cleans_only_current_operation(self):
        self.modal.is_visible.return_value = False
        previous = {'unrelated': True}
        self.client._teleport_mark_return = previous
        async def cancelled(_): raise asyncio.CancelledError
        self.timer.side_effect = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await utils.recall_to_teleport_mark(self.client)
        self.assertIs(self.client._teleport_mark_return, previous)


class PhotoCallerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_full_chain_415_restore import FullTaskPathTests
        FullTaskPathTests.setUp(self)
        self.quester._finish_photomancy_photo = AsyncMock(return_value=True)

    async def test_legacy_group_uses_photo_method_for_each_quest_client(self):
        self.range_visible = False
        self.objective = 'Photomance the tree'
        follower = SimpleNamespace(questing_status=True, is_loading=AsyncMock(return_value=False))
        self.quester.clients.append(follower)
        self.quester.take_photomancy_photo = AsyncMock()
        self.quester.leader_wait_for_free = AsyncMock()
        with patch('src.questing.safe_click_window', AsyncMock()), patch(
                'src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
            await self.quester.handle_normal_quests([follower], False)
        self.assertEqual([c.args[0] for c in self.quester.take_photomancy_photo.await_args_list], [self.client, follower])

    async def test_giant_popup_routes_before_x_in_solo_and_legacy(self):
        self.prompt = 'Photomance 巨美大桶'
        self.quester.take_photomancy_photo = AsyncMock()
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_not_awaited()
        self.quester.take_photomancy_photo.assert_awaited_once_with(self.client, self.prompt)
        self.quester.take_photomancy_photo.reset_mock()
        self.quester.leader_wait_for_free = AsyncMock()
        with patch('src.questing.safe_click_window', AsyncMock()), patch(
                'src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
            await self.quester.handle_normal_quests([], False)
        self.client.send_key.assert_not_awaited()
        self.quester.take_photomancy_photo.assert_awaited_once_with(self.client, self.prompt)

    async def test_delayed_closed_popup_leaves_missing_retry_wait_in_solo_and_legacy(self):
        from src.paths import missing_area_path, missing_area_retry_path
        self.range_visible = False
        for group in (False, True):
            with self.subTest(group=group), patch('src.questing.is_visible_by_path', AsyncMock(
                    side_effect=lambda _, path: path == missing_area_path)), patch(
                    'src.questing.closed_dungeon_popup', AsyncMock(return_value=True)) as closed:
                if group:
                    self.quester.leader_wait_for_free = AsyncMock()
                    with patch('src.questing.safe_click_window', AsyncMock()), patch(
                            'src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
                        await self.quester.handle_normal_quests([], False)
                else:
                    await self.quester.auto_quest_solo()
                closed.assert_awaited_once_with(self.client, dismiss=True)
                self.assertTrue(self.client.questing_status)
