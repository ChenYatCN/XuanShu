"""Exit confirmations use the scoped Yes button and observed UI outcomes."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.game_text_catalog import TEXT_RECORDS
from src.questing import Quester
import tests.test_dungeon_local_tp as fixtures

REAL_SLEEP = asyncio.sleep


class Window:
    def __init__(self, name, *children, text='', visible=True):
        self.name = AsyncMock(return_value=name)
        self.children = AsyncMock(return_value=list(children))
        self.visible = visible
        self.is_visible = AsyncMock(side_effect=lambda: self.visible)
        self.text = text

    async def get_windows_with_name(self, name):
        result = []
        for child in await self.children():
            if await child.name() == name:
                result.append(child)
            result.extend(await child.get_windows_with_name(name))
        return result


class ExitConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = fixtures.client(1, None)
        self.caption = Window('CaptionText', text=TEXT_RECORDS['GUI_InstanceExit'][1])
        self.yes = Window('leftButton')
        self.no = Window('rightButton')
        self.center = Window('centerButton', visible=False)
        self.modal = Window('MessageBoxModalWindow', Window('messageBoxBG',
            Window('messageBoxLayout', Window('TextArea', self.caption),
                Window('AdjustmentWindow', Window('Layout', self.center, self.yes, self.no)))))
        self.client.root_window = Window('root', self.modal)
        self.reader = Quester(self.client, [self.client], None)
        self.clock = 0.0
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        for target, value in (
            ('src.questing.read_control_text', AsyncMock(side_effect=lambda w: w.text)),
            ('src.questing.asyncio.sleep', tick),
            ('src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    async def test_bilingual_exit_without_session_clicks_yes_and_observes_closed_modal(self):
        for caption in (*TEXT_RECORDS['GUI_InstanceExit'], '<string;GUI_InstanceExit>'):
            with self.subTest(caption=caption):
                self.caption.text, self.modal.visible = caption, True
                self.client.mouse_handler.click_window.reset_mock()
                async def click(button):
                    self.assertIs(button, self.yes)
                    self.modal.visible = False
                self.client.mouse_handler.click_window.side_effect = click
                self.assertTrue(await self.reader.handle_pending_dungeon_confirmation())
                self.client.mouse_handler.click_window.assert_awaited_once_with(self.yes)
        self.client.quest_id.assert_not_awaited()
        self.client.goal_id.assert_not_awaited()
        self.client.quest_position.position.assert_not_awaited()
        self.client.body.position.assert_not_awaited()

    async def test_click_without_ui_progress_times_out_as_unconfirmed(self):
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 3)
        self.assertTrue(all(call.args == (self.yes,)
                            for call in self.client.mouse_handler.click_window.await_args_list))
        self.assertTrue(self.modal.visible)
        self.assertGreaterEqual(self.clock, 15)

    async def test_exit_center_button_also_requires_observed_ui_progress(self):
        self.yes.visible = self.no.visible = False
        self.center.visible = True
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 3)

    async def test_hidden_button_does_not_count_as_a_closed_modal(self):
        async def click(_):
            self.yes.visible = False
        self.client.mouse_handler.click_window.side_effect = click
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.assertTrue(self.modal.visible)

    async def test_loading_is_observed_even_if_zone_has_not_changed_yet(self):
        async def click(_):
            self.client.is_loading.return_value = True
        self.client.mouse_handler.click_window.side_effect = click
        self.assertTrue(await self.reader.handle_pending_dungeon_confirmation())
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.yes)

    async def test_exit_confirmation_retries_until_modal_disappears(self):
        async def click(button):
            self.assertIs(button, self.yes)
            if self.client.mouse_handler.click_window.await_count == 2:
                self.modal.visible = False
        self.client.mouse_handler.click_window.side_effect = click
        self.assertTrue(await self.reader.handle_pending_dungeon_confirmation())
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 2)

    async def test_exit_confirmation_retries_are_bounded(self):
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 3)

    async def test_generic_yes_no_and_hidden_exit_modal_are_not_confirmed(self):
        self.caption.text = '是否购买物品？'
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.caption.text = TEXT_RECORDS['GUI_InstanceExit'][1]
        self.modal.visible = False
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_yes_button_is_resolved_inside_exit_modal(self):
        self.yes.visible = False
        self.client.root_window.children.return_value.append(Window('leftButton'))
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_replacement_while_waiting_for_mouse_is_not_clicked(self):
        async def replace():
            self.caption.text = '是否购买物品？'
        self.client.mouse_handler.__aenter__.side_effect = replace
        self.assertFalse(await self.reader.handle_pending_dungeon_confirmation())
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.client.mouse_handler.__aexit__.assert_awaited_once()

    async def test_cancellation_during_click_releases_mouse_and_is_not_success(self):
        self.client.mouse_handler.click_window.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.reader.handle_pending_dungeon_confirmation()
        self.client.mouse_handler.__aexit__.assert_awaited_once()

    async def test_cancellation_during_observation_propagates_without_recording_exit(self):
        with patch('src.questing.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)), patch(
                'src.questing.record_dungeon_exit') as record:
            with self.assertRaises(asyncio.CancelledError):
                await self.reader.handle_pending_dungeon_confirmation()
        record.assert_not_called()
        self.client.mouse_handler.__aexit__.assert_awaited_once()

    async def test_existing_center_button_confirmation_is_preserved(self):
        self.caption.text = '是否进入下一个房间？'
        self.yes.visible = self.no.visible = False
        self.center.visible = True
        async def click(button):
            self.assertIs(button, self.center)
            self.client.zone = 'Dungeon/Room2'
        self.client.mouse_handler.click_window.side_effect = click
        self.assertTrue(await self.reader.handle_pending_dungeon_confirmation())
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.center)

    async def test_group_normal_flow_uses_same_confirmation_handler(self):
        self.reader.current_leader_client = self.client
        self.reader.leader_wait_for_free = AsyncMock()
        with patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), patch(
                'src.questing.close_automation_popup', AsyncMock(return_value=False)), patch(
                'src.questing.is_spiral_door_open', AsyncMock(return_value=False)), patch(
                'src.questing.safe_click_window', AsyncMock()), patch(
                'src.questing.is_free_leader_questing', AsyncMock(return_value=False)), patch.object(
                Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=True)) as confirm:
            await self.reader.handle_normal_quests([], False)
        confirm.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
