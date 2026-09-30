import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.automation_ownership import automation_owner
from src.paths import missing_area_path, missing_area_retry_path
from src.script_popups import close_script_popup, popup_kind, run_with_script_popups


class ScriptPopupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        terminal = patch('src.script_popups.closed_dungeon_popup', AsyncMock(return_value=False))
        terminal.start()
        self.addCleanup(terminal.stop)
        pet = patch('src.script_popups.close_pet_level_popup', AsyncMock(return_value=False))
        pet.start()
        self.addCleanup(pet.stop)
        self.title = SimpleNamespace(value='You have been reported!', is_visible=AsyncMock(return_value=True))
        self.caption = SimpleNamespace(value='Remember, the use of foul language...', is_visible=AsyncMock(return_value=True))
        self.confirm = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.accept = SimpleNamespace(is_visible=AsyncMock(return_value=False))
        self.reject = SimpleNamespace(is_visible=AsyncMock(return_value=False))
        self.window = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_windows_with_name=AsyncMock(side_effect=lambda name: {
                'TitleText': [self.title], 'CaptionText': [self.caption],
                'centerButton': [self.confirm], 'rightButton': [self.reject],
            }.get(name, [])))
        self.client = SimpleNamespace(title='p1', is_loading=AsyncMock(return_value=False),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[self.window])),
            mouse_handler=AsyncMock())
        self.endorsement = patch('src.script_popups.close_endorsement_window', AsyncMock(return_value=False))
        self.close_endorsement = self.endorsement.start()
        self.addCleanup(self.endorsement.stop)
        text = patch('src.script_popups.read_control_text', AsyncMock(side_effect=lambda w: w.value))
        text.start()
        self.addCleanup(text.stop)
        async def resolve(window, path):
            self.assertIs(window, self.window)
            self.assertEqual(path[:-1], ['messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout'])
            return {
                'leftButton': self.accept,
                'centerButton': self.confirm,
                'rightButton': self.reject,
            }.get(path[-1], False)
        paths = patch('src.script_popups.get_window_from_path', AsyncMock(side_effect=resolve))
        self.resolve_path = paths.start()
        self.addCleanup(paths.stop)

    async def test_report_notice_acknowledged(self):
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.confirm)

    async def test_friend_request_declined(self):
        self.reject.is_visible.return_value = True
        self.title.value = 'Friends'
        self.caption.value = 'Accept<br>Amber Level 100<br>as your friend?'
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.reject)

    async def test_group_invite_declined(self):
        self.confirm.is_visible.return_value = False
        self.accept.is_visible.return_value = True
        self.reject.is_visible.return_value = True
        self.title.value = 'Join a group?'
        self.caption.value = 'Emma has invited you to join a group.  Would you like to join?'
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.reject)

    async def test_group_invite_requires_verified_yes_no_buttons(self):
        self.confirm.is_visible.return_value = False
        self.accept.is_visible.return_value = False
        self.reject.is_visible.return_value = True
        self.title.value = 'Join a group?'
        self.caption.value = 'Emma has invited you to join a group.  Would you like to join?'
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_screenshot_report_uses_right_button_and_spaced_title(self):
        self.title.value = '你已经被举报 ！'
        self.confirm.is_visible.return_value = False
        self.reject.is_visible.return_value = True
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.reject)

    async def test_photomancy_discards_only_with_both_photo_buttons(self):
        self.window.is_visible.return_value = False
        def button(label):
            return SimpleNamespace(value=label, is_visible=AsyncMock(return_value=True),
                maybe_read_type_name=AsyncMock(return_value='ControlButton'))
        discard, keep = button('丢弃照片'), button('保留照片')
        visible = [discard]
        async def find(predicate):
            return [window for window in visible if await predicate(window)]
        self.client.root_window.get_windows_with_predicate = AsyncMock(side_effect=find)
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

        visible.append(keep)
        self.client._xuanshu_photo_scan_at = 0
        self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(discard)

    async def test_photomancy_does_not_click_after_buttons_change(self):
        self.window.is_visible.return_value = False
        def button(label):
            return SimpleNamespace(value=label, is_visible=AsyncMock(return_value=True),
                maybe_read_type_name=AsyncMock(return_value='ControlButton'))
        discard, keep = button('Trash Picture'), button('Keep Picture')
        visible = [discard, keep]
        async def find(predicate):
            return [window for window in visible if await predicate(window)]
        async def replace():
            visible.clear()
        self.client.root_window.get_windows_with_predicate = AsyncMock(side_effect=find)
        self.client.mouse_handler.__aenter__.side_effect = replace
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_matching_text_without_correct_ui_path_is_not_clicked(self):
        self.resolve_path.side_effect = None
        self.resolve_path.return_value = False
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_multi_button_dialog_is_not_treated_as_report_acknowledgement(self):
        self.reject.is_visible.return_value = True
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_buttons_changed_while_waiting_for_mouse(self):
        async def replace():
            self.reject.is_visible.return_value = True
        self.client.mouse_handler.__aenter__.side_effect = replace
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_endorsement_uses_existing_closer(self):
        self.close_endorsement.return_value = True
        self.assertTrue(await close_script_popup(self.client))
        self.close_endorsement.assert_awaited_once_with(self.client)
        self.client.root_window.get_windows_with_name.assert_not_awaited()

    async def test_purchase_and_teleport_confirmations_are_untouched(self):
        self.title.value = 'Confirm'
        for text in ('Buy this item?', 'Do you want to go to your friend?', 'Are you sure you want to leave this dungeon?'):
            self.caption.value = text
            self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_popup_changed_while_waiting_for_mouse(self):
        async def replace():
            self.title.value = 'Confirm Purchase'
            self.caption.value = 'Buy this item?'
        self.client.mouse_handler.__aenter__.side_effect = replace
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_and_hidden_windows_are_untouched(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await close_script_popup(self.client))
        self.close_endorsement.assert_not_awaited()
        self.client.is_loading.return_value = False
        self.window.is_visible.return_value = False
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_popup_guard_waits_for_combat_owner(self):
        async with automation_owner(self.client, 'auto-combat-round'):
            close_task = asyncio.create_task(close_script_popup(self.client))
            done, _ = await asyncio.wait({close_task}, timeout=0.05)
            self.assertFalse(done)
            self.client.mouse_handler.click_window.assert_not_awaited()

        self.assertTrue(await asyncio.wait_for(close_task, 1))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.confirm)

    async def test_exit_dialog_does_not_start_area_recovery(self):
        self.client._xuanshu_zone_retry_users = 1
        self.title.value = '退出'
        self.caption.value = '你确定你要现在离开吗？'
        self.resolve_path.side_effect = lambda _root, path: (
            self.window if path == missing_area_path else False
        )
        with patch('src.script_popups.asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertFalse(await close_script_popup(self.client))
            sleep.assert_not_awaited()
        self.assertIsNone(getattr(self.client, '_xuanshu_missing_area_retry', None))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_stale_recovery_releases_exit_dialog_before_retry_or_timeout(self):
        self.client._xuanshu_zone_retry_users = 1
        self.client._xuanshu_missing_area_retry = dict(
            attempts=5, next_retry_at=float('inf'), clear_since=None,
            warned=True, started_at=0)
        self.title.value = '退出'
        self.caption.value = '你确定你要现在离开吗？'
        self.resolve_path.side_effect = lambda _root, path: (
            self.window if path == missing_area_path else False
        )
        with patch('src.script_popups.asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertFalse(await close_script_popup(self.client))
            sleep.assert_not_awaited()
        self.assertIsNone(self.client._xuanshu_missing_area_retry)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_exit_dialog_reaches_existing_dungeon_handler(self):
        from src.questing import Quester
        self.client._xuanshu_zone_retry_users = 1
        self.title.value = '退出'
        self.caption.value = '你确定你要现在离开吗？'
        self.resolve_path.side_effect = lambda _root, path: (
            self.window if path == missing_area_path else False
        )
        quester = SimpleNamespace(
            client=self.client,
            _quest_dialogue_blocks_movement=AsyncMock(return_value=False),
            handle_pending_dungeon_confirmation=AsyncMock(return_value=True))
        with patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)):
            await Quester.auto_quest_solo(quester)
        quester.handle_pending_dungeon_confirmation.assert_awaited_once()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_hidden_retry_button_does_not_start_recovery(self):
        self.client._xuanshu_zone_retry_users = 1
        retry = SimpleNamespace(is_visible=AsyncMock(return_value=False))
        self.title.value = 'Confirm'
        self.caption.value = 'Do you want to go to your friend?'
        self.resolve_path.side_effect = lambda _root, path: (
            self.window if path == missing_area_path else
            retry if path == missing_area_retry_path else False
        )
        self.assertFalse(await close_script_popup(self.client))
        self.assertIsNone(getattr(self.client, '_xuanshu_missing_area_retry', None))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_dialog_changed_while_waiting_for_mouse_releases_recovery(self):
        self.client._xuanshu_zone_retry_users = 1
        retry = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.title.value = '退出'
        self.caption.value = '你确定你要现在离开吗？'
        self.resolve_path.side_effect = lambda _root, path: (
            self.window if path == missing_area_path else
            retry if path == missing_area_retry_path else False
        )
        async def replace():
            retry.is_visible.return_value = False
        self.client.mouse_handler.__aenter__.side_effect = replace
        with patch('src.script_popups.asyncio.sleep', new=AsyncMock()) as sleep:
            self.assertFalse(await close_script_popup(self.client))
            sleep.assert_not_awaited()
        self.assertIsNone(self.client._xuanshu_missing_area_retry)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_missing_area_retry_is_automation_only_and_never_closes(self):
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        retry = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.title.value = 'Wait'
        self.caption.value = 'Area files are loading'
        self.resolve_path.side_effect = lambda _root, path: (
            modal if path == missing_area_path else
            retry if path == missing_area_retry_path else False
        )
        self.assertFalse(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

        self.client._xuanshu_zone_retry_users = 1
        async def dismiss(_):
            modal.is_visible.return_value = False
        self.client.mouse_handler.click_window.side_effect = dismiss
        with patch('src.script_popups._MISSING_AREA_STABLE_SECONDS', 0), patch(
            'src.script_popups.asyncio.sleep', new=AsyncMock()
        ):
            self.assertTrue(await close_script_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(retry)
        self.assertIsNot(retry, self.reject)

    async def test_missing_area_retry_is_throttled_limited_and_resets_after_stability(self):
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        retry = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.client._xuanshu_zone_retry_users = 1
        self.resolve_path.side_effect = lambda _root, path: (
            modal if path == missing_area_path else
            retry if path == missing_area_retry_path else False
        )
        clicked = asyncio.Event()
        async def note_click(_):
            clicked.set()
        self.client.mouse_handler.click_window.side_effect = note_click
        with patch('src.script_popups._MISSING_AREA_RETRY_INTERVAL', .05), patch(
            'src.script_popups._MISSING_AREA_STABLE_SECONDS', 0
        ), patch('src.script_popups.logger.warning') as warning:
            first = asyncio.create_task(close_script_popup(self.client))
            second = asyncio.create_task(close_script_popup(self.client))
            await clicked.wait()
            await asyncio.sleep(.01)
            self.assertEqual(self.client.mouse_handler.click_window.await_count, 1)
            await asyncio.gather(first, second)
            self.assertEqual(self.client.mouse_handler.click_window.await_count, 5)
            warning.assert_called_once()

            modal.is_visible.return_value = False
            await close_script_popup(self.client)
            self.assertIsNone(self.client._xuanshu_missing_area_retry)

    async def test_missing_area_clients_keep_independent_retry_limits(self):
        modals = [SimpleNamespace(is_visible=AsyncMock(return_value=True)) for _ in range(2)]
        retries = [SimpleNamespace(is_visible=AsyncMock(return_value=True)) for _ in range(2)]
        self.client._xuanshu_zone_retry_users = 1
        other = SimpleNamespace(title='p2', is_loading=AsyncMock(return_value=False),
            root_window=SimpleNamespace(), mouse_handler=AsyncMock(),
            _xuanshu_zone_retry_users=1)
        def resolve(root, path):
            index = 0 if root is self.client.root_window else 1
            return modals[index] if path == missing_area_path else (
                retries[index] if path == missing_area_retry_path else False
            )
        self.resolve_path.side_effect = resolve
        async def dismiss_first(_):
            modals[0].is_visible.return_value = False
        async def dismiss_other(_):
            modals[1].is_visible.return_value = False
        self.client.mouse_handler.click_window.side_effect = dismiss_first
        other.mouse_handler.click_window.side_effect = dismiss_other
        with patch('src.script_popups._MISSING_AREA_STABLE_SECONDS', 0), patch(
            'src.script_popups.asyncio.sleep', new=AsyncMock()
        ):
            await close_script_popup(self.client)
            await close_script_popup(other)
        self.client.mouse_handler.click_window.assert_awaited_once_with(retries[0])
        other.mouse_handler.click_window.assert_awaited_once_with(retries[1])
        self.assertIsNone(self.client._xuanshu_missing_area_retry)
        self.assertIsNone(other._xuanshu_missing_area_retry)

    async def test_retry_holds_client_input_owner_until_loading_settles(self):
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        retry = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.resolve_path.side_effect = lambda _root, path: (
            modal if path == missing_area_path else
            retry if path == missing_area_retry_path else False
        )
        self.client._xuanshu_zone_retry_users = 1
        clicked, resume, acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        real_sleep = asyncio.sleep
        async def begin_loading(_):
            modal.is_visible.return_value = False
            self.client.is_loading.return_value = True
            clicked.set()
        async def wait_for_game(_):
            await resume.wait()
        async def next_input():
            async with automation_owner(self.client, 'script-vm'):
                acquired.set()
        self.client.mouse_handler.click_window.side_effect = begin_loading
        with patch('src.script_popups._MISSING_AREA_STABLE_SECONDS', 0), patch(
            'src.script_popups.asyncio.sleep', side_effect=wait_for_game
        ):
            recovery = asyncio.create_task(close_script_popup(self.client))
            await clicked.wait()
            next_command = asyncio.create_task(next_input())
            await real_sleep(0)
            self.assertFalse(acquired.is_set())
            self.client.is_loading.return_value = False
            resume.set()
            await recovery
            await next_command
        self.assertTrue(acquired.is_set())

    def test_verified_bilingual_text_and_unrelated_report(self):
        self.assertEqual(popup_kind('你已经被举报！', ''), 'reported')
        self.assertEqual(popup_kind('', '是否接受<br>某人 等级 100<br>成为你的好友？'), 'friend_request')
        self.assertEqual(popup_kind('', '你和 某人 成为了好友！'), 'friend_added')
        self.assertEqual(
            popup_kind('加入一个队伍？', '艾玛 邀请你加入一个队伍，是否同意？'),
            'group_invite',
        )
        self.assertEqual(
            popup_kind('Join a group?', 'Emma has invited you to join a group. Would you like to join?'),
            'group_invite',
        )
        self.assertIsNone(popup_kind('举报玩家', '你确定要举报此玩家吗？'))
        self.assertIsNone(popup_kind('', '该玩家未被禁言，但已被举报.'))
        self.assertIsNone(popup_kind('Join a group?', 'This is not a group invitation.'))


class QuestPopupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_solo_handles_notice_before_dungeon_confirmation(self):
        from src.questing import Quester
        client = SimpleNamespace(title='p1')
        quester = SimpleNamespace(
            client=client,
            _quest_dialogue_blocks_movement=AsyncMock(return_value=False),
            handle_pending_dungeon_confirmation=AsyncMock())
        with patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), patch(
            'src.questing.close_automation_popup', AsyncMock(return_value=True)
        ) as close:
            await Quester.auto_quest_solo(quester)
        close.assert_awaited_once_with(client)
        quester.handle_pending_dungeon_confirmation.assert_not_awaited()

    async def test_group_checks_each_quest_client(self):
        from src.questing import Quester
        clients = [SimpleNamespace(title='p1'), SimpleNamespace(title='p2')]
        quester = SimpleNamespace(current_leader_client=clients[0], clients=clients)
        with patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), patch(
            'src.questing.close_automation_popup', AsyncMock(side_effect=[False, True])
        ) as close:
            await Quester.handle_normal_quests(quester, clients[1:], False)
        self.assertEqual([call.args[0] for call in close.await_args_list], clients)


class ScriptPopupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_zone_retry_is_enabled_only_for_guard_lifetime(self):
        client = SimpleNamespace(title='p1')
        async def script():
            self.assertEqual(client._xuanshu_zone_retry_users, 1)
            return 42
        with patch('src.script_popups.watch_script_popups', new=AsyncMock()):
            self.assertEqual(await run_with_script_popups(script, [client], zone_retry=True), 42)
        self.assertEqual(client._xuanshu_zone_retry_users, 0)

    async def test_watchers_run_during_wait_and_stop_on_cancel(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        first, other = SimpleNamespace(title='p1'), SimpleNamespace(title='p2')
        async def watch(client):
            self.assertIs(client, first)
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        async def script():
            await asyncio.Future()
        with patch('src.script_popups.watch_script_popups', side_effect=watch) as watcher:
            task = asyncio.create_task(run_with_script_popups(script, [first]))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(watcher.call_count, 1)

    async def test_normal_completion_and_script_failure_drain_watchers(self):
        for fail in (False, True):
            started, stopped = asyncio.Event(), asyncio.Event()
            async def watch(_):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    stopped.set()
            async def script():
                await started.wait()
                if fail:
                    raise ValueError('script failed')
                return 42
            with patch('src.script_popups.watch_script_popups', side_effect=watch):
                if fail:
                    with self.assertRaises(ValueError):
                        await run_with_script_popups(script, [object()])
                else:
                    self.assertEqual(await run_with_script_popups(script, [object()]), 42)
            self.assertTrue(stopped.is_set())
