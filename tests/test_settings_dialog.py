import os
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

from src.gui.settings_dialog import show_settings_dialog
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME


class _Settings:
    def get_settings(self):
        return dict(DEFAULT_SETTINGS)

    def get_theme(self):
        return dict(DEFAULT_THEME)


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
        with patch.object(QDialog, "exec", return_value=0):
            show_settings_dialog(ctx)


if __name__ == "__main__":
    unittest.main()
