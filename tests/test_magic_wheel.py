import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src.script_popups import skip_magic_wheel_tutorial


class MagicWheelTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_tutorial_then_scoped_confirmation(self):
        title = SimpleNamespace(text='THE MAGIC WHEEL', is_visible=AsyncMock(return_value=True))
        skip = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        panel = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_windows_with_name=AsyncMock(return_value=[title]))
        yes = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        no = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        windows = {'TutorialWindow': [panel]}
        client = SimpleNamespace(questing_status=True,
            zone_name=AsyncMock(return_value='Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove'),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(side_effect=lambda name: windows.get(name, []))),
            mouse_handler=AsyncMock())
        async def path(window, parts):
            if window is panel:
                self.assertEqual(parts[0], 'DialogWindow')
            return {'TitleText': title, 'SkipButton': skip, 'leftButton': yes, 'rightButton': no}.get(parts[-1])
        with patch('src.script_popups.get_window_from_path', AsyncMock(side_effect=path)), \
             patch('src.script_popups.read_control_text', AsyncMock(side_effect=lambda w: w.text)):
            self.assertTrue(await skip_magic_wheel_tutorial(client))
            client.mouse_handler.click_window.assert_awaited_once_with(skip)
            windows.clear()
            windows['MessageBoxModalWindow'] = [modal]
            title.text = '确定要跳过新手教程吗？'
            self.assertTrue(await skip_magic_wheel_tutorial(client))
            self.assertIs(client.mouse_handler.click_window.await_args.args[0], yes)
            client.mouse_handler.click_window.reset_mock()
            client._magic_wheel_skip_until = float('inf')
            title.text = '是否购买这个物品？'
            self.assertFalse(await skip_magic_wheel_tutorial(client))
            client.mouse_handler.click_window.assert_not_awaited()
            client.questing_status = False
            self.assertFalse(await skip_magic_wheel_tutorial(client))
