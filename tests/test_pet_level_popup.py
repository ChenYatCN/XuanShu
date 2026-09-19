import unittest
from unittest.mock import AsyncMock, patch
from src.script_popups import close_pet_level_popup


class PetLevelPopupTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_visible_scoped_close_button(self):
        for panel_visible, button_visible, loading, expected in (
            (True, True, False, True), (False, True, False, False),
            (True, False, False, False), (True, True, True, False),
        ):
            client, panel, button = AsyncMock(), AsyncMock(), AsyncMock()
            client.root_window.get_windows_with_name.return_value = [panel]
            client.is_loading.return_value = loading
            panel.is_visible.return_value = panel_visible
            button.is_visible.return_value = button_visible
            with patch('src.script_popups.get_window_from_path', new=AsyncMock(return_value=button)) as resolve:
                self.assertIs(await close_pet_level_popup(client), expected)
                if expected:
                    resolve.assert_awaited_once_with(panel, ['wndPetLevelBkg', 'btnPetLevelClose'])
                    client.mouse_handler.click_window.assert_awaited_once_with(button)
                else:
                    client.mouse_handler.click_window.assert_not_awaited()
            client.send_key.assert_not_awaited()
