import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import script_popups
from src.automation_ownership import automation_owner
from src.paths import exit_minigame_sigil


def control(kind='ControlButton', value=None):
    return SimpleNamespace(is_visible=AsyncMock(return_value=True),
                           maybe_read_type_name=AsyncMock(return_value=kind), value=value)


class MinigamePickerPopupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.panel = control('Window')
        self.caption = control('ControlText', '<center>选择一个游戏</center>')
        self.buttons = {name: control() for name in ('game1', 'pict1', 'game7', 'pict7', 'exit')}
        self.children = dict(self.buttons, instructionText=self.caption)
        self.active = {}
        self.unrelated_exit = control()
        self.client = SimpleNamespace(title='p1', questing_status=True, is_foreground=False,
            root_window=SimpleNamespace(), mouse_handler=AsyncMock(), send_key=AsyncMock(),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False))

        async def resolve(root, path):
            if root is self.client.root_window:
                if path == exit_minigame_sigil[:-1]: return self.panel
                if path == ['WorldView', 'shopGUI', 'buyWindow', 'exit']: return self.unrelated_exit
                if len(path) == 2 and path[0] == 'WorldView': return self.active.get(path[1])
            if root is self.panel and len(path) == 1: return self.children.get(path[0])
            return None

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.resolve = self.stack.enter_context(patch.object(script_popups, 'get_window_from_path', AsyncMock(side_effect=resolve)))
        self.stack.enter_context(patch.object(script_popups, 'read_control_text', AsyncMock(side_effect=lambda w: w.value)))

    async def close(self):
        return await script_popups.close_minigame_picker_popup(self.client)

    async def test_picker_closes_only_its_red_exit_no_game_button_or_keys(self):
        self.assertTrue(await self.close())
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.buttons['exit'])
        self.client.send_key.assert_not_awaited()
        self.assertEqual(exit_minigame_sigil, ['WorldView', 'mainwindow', 'exit'])
        for call in self.resolve.await_args_list:
            if call.args[1] == ['exit']: self.assertIs(call.args[0], self.panel)

    async def test_verified_english_instruction_is_supported(self):
        self.caption.value = '<center>Select a game to play</center>'
        self.assertTrue(await self.close())
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.buttons['exit'])

    async def test_map_shop_pet_or_unrelated_red_x_is_not_enough(self):
        for text in ('选择一个地图', '药剂商店', '选择宠物训练', '退出小游戏？'):
            self.caption.value = text
            self.assertFalse(await self.close())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_hidden_missing_or_wrong_type_controls_prevent_click(self):
        for name in list(self.children):
            with self.subTest(name=name):
                item = self.children.pop(name)
                self.assertFalse(await self.close())
                self.children[name] = item
                item.is_visible.return_value = False
                self.assertFalse(await self.close())
                item.is_visible.return_value = True
                old = item.maybe_read_type_name.return_value
                item.maybe_read_type_name.return_value = 'ControlSprite'
                self.assertFalse(await self.close())
                item.maybe_read_type_name.return_value = old
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_missing_or_hidden_mainwindow_never_uses_another_exit(self):
        self.panel.is_visible.return_value = False
        self.assertFalse(await self.close())
        self.panel = None
        self.assertFalse(await self.close())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_active_potion_and_pet_flags_prevent_click(self):
        for flag in ('refilling_potions', 'auto_pet_status', 'feeding_pet_status', 'dance_hook_status'):
            with self.subTest(flag=flag):
                setattr(self.client, flag, True)
                self.assertFalse(await self.close())
                setattr(self.client, flag, False)
        self.resolve.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_visible_active_game_or_pet_interface_is_protected(self):
        for name in ('MinigameInterface', 'PetGameTracks', 'PetGameSplash'):
            with self.subTest(name=name):
                self.active = {name: control('Window')}
                self.assertFalse(await self.close())
                self.active[name].is_visible.return_value = False
                self.assertTrue(await self.close())
        self.assertEqual(self.client.mouse_handler.click_window.await_count, 3)

    async def test_foreground_manual_play_and_unknown_foreground_are_protected(self):
        self.client.is_foreground = True
        self.assertFalse(await self.close())
        del self.client.is_foreground
        self.assertFalse(await self.close())
        self.resolve.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_or_battle_prevents_click(self):
        for attr in ('is_loading', 'in_battle'):
            getattr(self.client, attr).return_value = True
            self.assertFalse(await self.close())
            getattr(self.client, attr).return_value = False
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_state_changes_during_mouse_wait_prevent_click(self):
        for change in ('foreground', 'potions', 'pet', 'game', 'loading', 'battle'):
            with self.subTest(change=change):
                self.client.is_foreground = self.client.refilling_potions = self.client.feeding_pet_status = False
                self.client.is_loading.return_value = self.client.in_battle.return_value = False
                self.active = {}
                async def changed():
                    if change == 'foreground': self.client.is_foreground = True
                    if change == 'potions': self.client.refilling_potions = True
                    if change == 'pet': self.client.feeding_pet_status = True
                    if change == 'game': self.active['MinigameInterface'] = control('Window')
                    if change == 'loading': self.client.is_loading.return_value = True
                    if change == 'battle': self.client.in_battle.return_value = True
                self.client.mouse_handler.__aenter__.side_effect = changed
                self.assertFalse(await self.close())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_caption_buttons_or_panel_replaced_during_mouse_wait_prevent_click(self):
        for change in ('caption', 'options', 'exit', 'panel'):
            with self.subTest(change=change):
                self.caption.value = '选择一个游戏'
                self.buttons['game7'].is_visible.return_value = self.buttons['exit'].is_visible.return_value = True
                self.panel.is_visible.return_value = True
                async def changed():
                    if change == 'caption': self.caption.value = '药剂商店'
                    if change == 'options': self.buttons['game7'].is_visible.return_value = False
                    if change == 'exit': self.buttons['exit'].is_visible.return_value = False
                    if change == 'panel': self.panel.is_visible.return_value = False
                self.client.mouse_handler.__aenter__.side_effect = changed
                self.assertFalse(await self.close())
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_existing_guard_dispatches_picker_before_other_popup_handlers(self):
        with patch.object(script_popups, 'closed_dungeon_popup', AsyncMock(return_value=False)), patch.object(
                script_popups, 'close_endorsement_window', AsyncMock()) as endorsement:
            self.assertTrue(await script_popups.close_automation_popup(self.client))
            endorsement.assert_not_awaited()
        self.assertTrue(self.client.questing_status)
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.buttons['exit'])

    async def test_existing_ownership_keeps_guard_out_of_active_script_input(self):
        with patch.object(script_popups, 'closed_dungeon_popup', AsyncMock(return_value=False)):
            async with automation_owner(self.client, 'script-vm'):
                task = asyncio.create_task(script_popups.close_automation_popup(self.client))
                await asyncio.sleep(0)
                self.client.mouse_handler.click_window.assert_not_awaited()
                self.client.feeding_pet_status = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_guard_lifetime_has_no_picker_click_after_shutdown(self):
        clicked = asyncio.Event()
        async def dismiss(_):
            self.panel.is_visible.return_value = False
            clicked.set()
        self.client.mouse_handler.click_window.side_effect = dismiss
        async def automation(): await clicked.wait()
        with patch.object(script_popups, 'closed_dungeon_popup', AsyncMock(return_value=False)):
            await asyncio.wait_for(script_popups.run_with_automation_ui_guard(automation, [self.client]), 1)
        self.panel.is_visible.return_value = True
        await asyncio.sleep(.01)
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.buttons['exit'])
