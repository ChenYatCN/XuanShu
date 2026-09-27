import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import unittest

from PyQt6.QtWidgets import QApplication

from src.gui.chat_translation_dialog import ChatTranslationDialog
from src.gui.commands import GUICommandType


class ChatTranslationUITests(unittest.TestCase):
    def test_window_is_independent_and_defaults_to_safe_receive_only(self):
        app = QApplication.instance() or QApplication([])
        sent = queue.Queue()
        dialog = ChatTranslationDialog(sent)
        dialog.set_available_clients(["p1", "p2"])
        self.assertIsNone(dialog.parentWidget())
        self.assertTrue(dialog.isWindow())
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
        dialog.close()
        self.assertFalse(dialog.auto_reply.isChecked())
        commands = []
        while not sent.empty():
            commands.append(sent.get_nowait())
        self.assertFalse(commands[-1].data["enabled"])
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
