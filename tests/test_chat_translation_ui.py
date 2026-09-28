import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import unittest
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.gui.chat_translation_dialog import ChatTranslationDialog
from src.gui.commands import GUICommandType


class ChatTranslationUITests(unittest.TestCase):
    def test_window_is_independent_and_defaults_to_safe_receive_only(self):
        app = QApplication.instance() or QApplication([])
        sent = queue.Queue()
        ctx = SimpleNamespace(bg_color='#171721', alt_bg='#202030',
                              text_color='#eeeeff', stroke_color='#78ddee',
                              icon_btn_style='QPushButton { border: none; }',
                              titlebar_svg_icon=lambda _svg, _size: QIcon())
        dialog = ChatTranslationDialog(sent, ctx)
        dialog.set_available_clients(["p1", "p2"])
        self.assertIsNone(dialog.parentWidget())
        self.assertTrue(dialog.isWindow())
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(dialog.auto_reply.isChecked())
        dialog.open_or_raise()
        command = sent.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.ConfigureChatTranslation)
        self.assertEqual(command.data, {
            "enabled": True, "selected_title": None, "auto_reply": False,
        })
        dialog.handle_event({"kind": "message", "title": "p2",
                             "sender_gid": 42, "message": "Hi"})
        self.assertIn("[p2] GID 42: Hi", dialog.messages.toPlainText())
        dialog.clients.setCurrentIndex(dialog.clients.findData("p2"))
        dialog.auto_reply.setChecked(True)
        dialog.set_available_clients(["p1"])
        self.assertFalse(dialog.auto_reply.isChecked())
        dialog.close_button.click()
        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog.auto_reply.isChecked())
        commands = []
        while not sent.empty():
            commands.append(sent.get_nowait())
        self.assertFalse(commands[-1].data["enabled"])
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
