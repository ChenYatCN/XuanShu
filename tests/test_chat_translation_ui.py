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
    def test_manual_sender_default_no_duplicate_or_fallback(self):
        app = QApplication.instance() or QApplication([])
        sent = queue.Queue()
        ctx = SimpleNamespace(bg_color='#171721', alt_bg='#202030',
                              text_color='#eeeeff', stroke_color='#78ddee',
                              icon_btn_style='', titlebar_svg_icon=lambda *_args: QIcon())
        dialog = ChatTranslationDialog(sent, ctx)
        dialog.set_available_clients(['p2'])
        self.assertEqual(dialog.sender.currentData(), 'p1')
        self.assertFalse(dialog.send_button.isEnabled())
        self.assertEqual(dialog.send_text.text(), 'hello')
        self.assertFalse(dialog.auto_reply.isEnabled())
        dialog.set_available_clients(['p1', 'p2'])
        dialog.send_button.click()
        dialog._send_once()
        self.assertEqual(sent.qsize(), 1)
        command = sent.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.SendNearbyChatTest)
        self.assertEqual(command.data, {'title': 'p1', 'text': 'hello'})
        self.assertFalse(dialog.send_button.isEnabled())
        dialog.handle_event({'kind': 'manual_send', 'title': 'p1', 'done': True,
                             'invoked': True, 'local_echo': False,
                             'peer_receipts': ['p2'], 'listener_capture': ['p2']})
        self.assertTrue(dialog.send_button.isEnabled())
        self.assertIn('p2', dialog.send_status.text())
        dialog.close()
        app.processEvents()

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
        dialog.handle_event({'kind': 'message', 'source': 'chat_log', 'title': 'p2',
                             'channel': '队伍', 'sender_name': '你', 'message': 'team hi'})
        self.assertIn('[p2][队伍] [你]: team hi', dialog.messages.toPlainText())
        dialog.handle_event({'kind': 'status', 'source': 'chat_log', 'title': 'p2',
                             'status': '聊天记录：监听中'})
        dialog.handle_event({'kind': 'status', 'title': 'p2', 'status': '私聊 Hook 失败'})
        self.assertIn('聊天记录：监听中', dialog.status.text())
        self.assertIn('私聊 Hook 失败', dialog.status.text())
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
