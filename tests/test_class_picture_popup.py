import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.paths import exit_zafaria_class_picture_button
from src.script_popups import close_class_picture_popup, close_automation_popup


class ClassPicturePopupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.panel = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.client = SimpleNamespace(title='p1', root_window=object(),
            mouse_handler=AsyncMock(), is_loading=AsyncMock(return_value=False), send_key=AsyncMock())
        async def resolve(root, path):
            if root is self.client.root_window and path == ['WorldView', 'ClassPicture']:
                return self.panel
            if root is self.panel and path == ['exit']:
                return self.button
            return None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.resolve = self.stack.enter_context(patch(
            'src.script_popups.get_window_from_path', AsyncMock(side_effect=resolve)))

    async def test_visible_picture_closes_only_its_exit(self):
        self.assertTrue(await close_class_picture_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.button)
        self.assertEqual(self.resolve.await_args_list[0].args[1], exit_zafaria_class_picture_button[:-1])
        self.client.send_key.assert_not_awaited()

    async def test_hidden_picture_or_exit_is_not_clicked(self):
        for target in (self.panel, self.button):
            with self.subTest(target=target):
                target.is_visible.return_value = False
                self.assertFalse(await close_class_picture_popup(self.client))
                target.is_visible.return_value = True
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_missing_picture_or_exit_is_not_clicked(self):
        for values in ([None], [self.panel, None]):
            with self.subTest(values=values):
                self.resolve.side_effect = values
                self.assertFalse(await close_class_picture_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_picture_disappears_while_waiting_for_mouse(self):
        async def vanished():
            self.panel.is_visible.return_value = False
        self.client.mouse_handler.__aenter__.side_effect = vanished
        self.assertFalse(await close_class_picture_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_begins_while_waiting_for_mouse(self):
        async def loading():
            self.client.is_loading.return_value = True
        self.client.mouse_handler.__aenter__.side_effect = loading
        self.assertFalse(await close_class_picture_popup(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_replaced_panel_uses_fresh_scoped_exit(self):
        fresh_panel = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        fresh_button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.resolve.side_effect = [self.panel, self.button, fresh_panel, fresh_button]
        self.assertTrue(await close_class_picture_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(fresh_button)

    async def test_shared_guard_closes_picture_before_unrelated_handlers(self):
        with patch('src.script_popups.closed_dungeon_popup', AsyncMock(return_value=False)), \
                patch('src.script_popups.discard_photo_popup', AsyncMock()) as photo:
            self.assertTrue(await close_automation_popup(self.client))
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.button)
        photo.assert_not_awaited()
