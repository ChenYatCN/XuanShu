import os
import queue
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QPushButton, QWidget

from src.gui.settings_dialog import show_settings_dialog
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME, XuanShuSettings
from src.gui.commands import GUICommandType
from src.lang import load_lang


class _Settings:
    def get_settings(self):
        return dict(DEFAULT_SETTINGS)

    def get_theme(self):
        return dict(DEFAULT_THEME)


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            cls.app.setFont(QFont(families[0], 10))

    def test_opening_settings_with_hooked_clients_does_not_raise(self):
        ctx = SimpleNamespace(
            tl=lambda key: key,
            settings=_Settings(),
            window=QWidget(),
            bg_color="#20202e",
            text_color="#ffffff",
            alt_bg="#20202e",
            btn_color_hex="#80deea",
            btn_style="",
            icon_btn_style="",
            svgs={"reset": "", "import": ""},
            titlebar_svg_icon=lambda svg, size: QIcon(),
            exports={
                "launcher": {
                    "last_hooked_data": {
                        "hooked": [
                            {
                                "title": "p1",
                                "account_nick": "main",
                                "stable_id": "account:main",
                            },
                            {
                                "title": "p2",
                                "account_nick": "hitter",
                                "stable_id": "account:hitter",
                            },
                        ]
                    }
                }
            },
            send_queue=queue.Queue(),
            gui_font="Segoe UI",
            gui_font_size=9,
        )
        def inspect_dialog(dialog):
            checkbox = next(
                box for box in dialog.findChildren(QCheckBox)
                if box.text() == "setting_mainline_finder_enabled"
            )
            self.assertFalse(checkbox.isChecked())
            return 0

        with patch.object(QDialog, "exec", inspect_dialog):
            show_settings_dialog(ctx)

    def test_fusion_choice_is_saved_reloaded_and_cancelled_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'settings.json')
            settings = XuanShuSettings(path)
            ctx = SimpleNamespace(
                tl=load_lang('zh'), settings=settings, window=QWidget(),
                bg_color="#20202e", text_color="#ffffff", alt_bg="#20202e",
                btn_color_hex="#80deea", btn_style="", icon_btn_style="",
                svgs={"reset": "", "import": ""},
                titlebar_svg_icon=lambda *_: QIcon(), exports={},
                send_queue=queue.Queue(), gui_font="Segoe UI", gui_font_size=9)

            def save_left(dialog):
                choice = dialog.findChild(QComboBox, 'fusionResultSide')
                self.assertEqual(choice.currentData(), 'right')
                self.assertEqual([choice.itemText(i) for i in range(choice.count())], ['左侧', '右侧'])
                choice.setCurrentIndex(choice.findData('left'))
                dialog.show()
                self.app.processEvents()
                # Show the relevant part of the existing scrollable settings.
                from PyQt6.QtWidgets import QScrollArea
                dialog.findChild(QScrollArea).ensureWidgetVisible(choice)
                self.app.processEvents()
                dialog.grab().save('artifacts/FUSION_SETTINGS_20261002.png')
                next(button for button in dialog.findChildren(QPushButton)
                     if button.text() == ctx.tl('settings_save')).click()
                return 0

            with patch.object(settings, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', save_left):
                show_settings_dialog(ctx)
            self.assertEqual(XuanShuSettings(path).get_setting('fusion_result_side'), 'left')
            command = ctx.send_queue.get_nowait()
            self.assertEqual(command.com_type, GUICommandType.UpdateSettings)
            self.assertEqual(command.data, {'fusion_result_side': 'left'})

            def cancel_right(dialog):
                choice = dialog.findChild(QComboBox, 'fusionResultSide')
                self.assertEqual(choice.currentData(), 'left')
                choice.setCurrentIndex(choice.findData('right'))
                next(button for button in dialog.findChildren(QPushButton)
                     if button.text() == ctx.tl('settings_cancel')).click()
                return 0

            ctx.settings = XuanShuSettings(path)
            with patch.object(ctx.settings, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', cancel_right):
                show_settings_dialog(ctx)
            self.assertEqual(XuanShuSettings(path).get_setting('fusion_result_side'), 'left')
            self.assertTrue(ctx.send_queue.empty())
            ctx.window.close()


if __name__ == "__main__":
    unittest.main()
