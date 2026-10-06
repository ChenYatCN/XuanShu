import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

from src.gui.chat_translation_dialog import ChatTranslationDialog, ChatAPISettingsDialog
from src.gui.commands import GUICommandType
from src.settings_manager import DEFAULT_SETTINGS


class ChatTranslationUITests(unittest.TestCase):
    def settings(self):
        current = dict(DEFAULT_SETTINGS)
        settings = Mock()
        settings.get_settings.side_effect = lambda: dict(current)
        settings.get_setting.side_effect = current.get
        settings.set_settings.side_effect = current.update
        settings.set_setting.side_effect = lambda key, value: current.update({key: value})
        return settings

    def api_dialog(self, values=None):
        app, sent, parent = self.make_dialog()
        current = {**DEFAULT_SETTINGS, **(values or {})}
        parent.ctx.settings = Mock()
        parent.ctx.settings.get_settings.side_effect = lambda: dict(current)
        parent.ctx.settings.get_setting.side_effect = current.get
        parent.ctx.settings.set_settings.side_effect = current.update
        parent.ctx.settings.set_setting.side_effect = lambda key, value: current.update({key: value})
        child = ChatAPISettingsDialog(parent)
        self.addCleanup(child.close)
        return app, sent, parent, child, current

    def test_api_icon_opens_settings_but_return_never_opens_or_sends(self):
        app, sent, dialog = self.make_dialog()
        dialog.show()
        with patch('src.gui.chat_translation_dialog.ChatAPISettingsDialog') as factory:
            dialog.api_settings_button.click()
            factory.assert_called_once_with(dialog)
            factory.return_value.exec.assert_called_once()
            factory.reset_mock()
            self.assertFalse(dialog.api_settings_button.autoDefault())
            dialog.api_settings_button.setFocus()
            QTest.keyClick(dialog.api_settings_button, Qt.Key.Key_Return)
            app.processEvents()
            factory.assert_not_called()
        self.assertTrue(sent.empty())

    def test_settings_save_masked_key_only_protected_persisted_no_secret_in_command(self):
        app, sent, parent, child, current = self.api_dialog()
        parent.show()
        self.assertEqual(child.api_key.echoMode(), child.api_key.EchoMode.Password)
        current['chat_translation_enabled'] = True
        parent.translation_enabled.blockSignals(True)
        parent.translation_enabled.setChecked(True)
        parent.translation_enabled.blockSignals(False)
        child.api_url.setText('https://example.com/v1')
        child.model.setText('custom-model')
        child.api_key.setText('synthetic-not-real-key')
        with (patch('src.gui.chat_translation_dialog.protect_api_key', return_value='encrypted') as protect,
              patch('src.gui.chat_translation_dialog.unprotect_api_key', return_value='synthetic-not-real-key')):
            child.save_button.click()
        protect.assert_called_once_with('synthetic-not-real-key')
        self.assertTrue(current['chat_translation_enabled'])
        self.assertEqual(current['chat_translation_api_url'], 'https://example.com/v1/chat/completions')
        self.assertEqual(current['chat_translation_model'], 'custom-model')
        self.assertEqual(current['chat_translation_api_key_protected'], 'encrypted')
        self.assertNotIn('synthetic-not-real-key', str(current))
        self.assertEqual(child.api_key.text(), '')
        command = sent.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.ConfigureChatTranslation)
        self.assertNotIn('synthetic-not-real-key', str(command.data))
        self.assertFalse(command.data['auto_reply'])

    def test_settings_cancel_retains_previous_and_clears_entered_key(self):
        app, sent, parent, child, current = self.api_dialog()
        child.api_url.setText('https://other.example.com')
        child.api_key.setText('discard-this-secret')
        child.reject()
        self.assertEqual(child.api_key.text(), '')
        parent.ctx.settings.set_settings.assert_not_called()
        self.assertTrue(sent.empty())

    def test_main_toggle_persists_and_configures_immediately_without_game_send(self):
        app, sent, dialog = self.make_dialog()
        dialog.show()
        with patch('src.gui.chat_translation_dialog.ChatTranslationAPI') as api:
            dialog.translation_enabled.setChecked(True)
            api.assert_called_once()
        self.assertTrue(dialog.ctx.settings.get_setting('chat_translation_enabled'))
        command = sent.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.ConfigureChatTranslation)
        self.assertFalse(command.data['auto_reply'])
        with patch('src.gui.chat_translation_dialog.ChatTranslationAPI') as api:
            dialog.translation_enabled.setChecked(False)
            api.assert_not_called()
        self.assertFalse(dialog.ctx.settings.get_setting('chat_translation_enabled'))
        self.assertEqual(sent.get_nowait().com_type, GUICommandType.ConfigureChatTranslation)
        self.assertTrue(sent.empty())

    def test_enable_without_valid_credentials_reverts_and_does_not_submit(self):
        from src.chat_translation_api import TranslationError
        app, sent, dialog = self.make_dialog()
        dialog.show()
        with patch('src.gui.chat_translation_dialog.ChatTranslationAPI', side_effect=TranslationError('先配置接口')):
            dialog.translation_enabled.setChecked(True)
        self.assertFalse(dialog.translation_enabled.isChecked())
        self.assertFalse(dialog.ctx.settings.get_setting('chat_translation_enabled'))
        self.assertTrue(sent.empty())
        self.assertIn('先配置接口', dialog.translation_status.toolTip())

    def test_api_settings_has_no_second_enable_switch(self):
        app, sent, parent, child, current = self.api_dialog({'chat_translation_enabled': True})
        self.assertFalse(hasattr(child, 'enabled'))
        child.api_key.setText('synthetic')
        with (patch('src.gui.chat_translation_dialog.protect_api_key', return_value='encrypted'),
              patch('src.gui.chat_translation_dialog.unprotect_api_key', return_value='synthetic')):
            child._save()
        self.assertTrue(current['chat_translation_enabled'])

    def test_settings_retain_blank_key_or_delete_while_disabled(self):
        app, sent, parent, child, current = self.api_dialog({'chat_translation_api_key_protected': 'saved'})
        self.assertEqual(child.api_key.text(), '')
        child._save()
        self.assertEqual(current['chat_translation_api_key_protected'], 'saved')
        child.clear_key.setChecked(True)
        child._save()
        self.assertEqual(current['chat_translation_api_key_protected'], '')
        self.assertFalse(current['chat_translation_enabled'])

    def test_bad_url_or_missing_key_does_not_save_or_enable(self):
        app, sent, parent, child, current = self.api_dialog()
        child.api_url.setText('http://example.com')
        child._save()
        self.assertIn('HTTPS', child.error.text())
        child.api_url.setText('https://api.deepseek.com')
        current['chat_translation_enabled'] = True
        child._save()
        self.assertIn('API Key', child.error.text())
        parent.ctx.settings.set_settings.assert_not_called()
        self.assertTrue(sent.empty())

    def test_translation_paired_without_duplicate_original_diagnostics_or_late_after_clear(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event({'kind': 'message', 'message_id': 1, 'title': 'p1', 'message': 'hello'})
        dialog.handle_event({'kind': 'message', 'message_id': 2, 'title': 'p2', 'message': 'bye'})
        dialog.handle_event({'kind': 'translation', 'message_id': 1, 'translation': '你好'})
        history = dialog.messages.toPlainText()
        self.assertEqual(history.count('hello'), 1)
        self.assertLess(history.index('你好'), history.index('bye'))
        dialog.handle_event({'kind': 'translation_status', 'status': '翻译暂不可用，详情见日志'})
        self.assertEqual(dialog.messages.toPlainText(), history)
        self.assertIn('暂不可用', dialog.translation_status.text())
        dialog._clear_button.click()
        dialog.handle_event({'kind': 'translation', 'message_id': 2, 'translation': '再见'})
        self.assertEqual(dialog.messages.toPlainText(), '')

    def make_dialog(self):
        app = QApplication.instance() or QApplication([])
        self._app = app
        sent = queue.Queue()
        ctx = SimpleNamespace(bg_color='#171721', alt_bg='#202030',
                              text_color='#eeeeff', stroke_color='#78ddee',
                              icon_btn_style='', titlebar_svg_icon=lambda *_args: QIcon(), settings=self.settings())
        dialog = ChatTranslationDialog(sent, ctx)
        self.addCleanup(dialog.close)
        return app, sent, dialog

    def test_compact_two_control_rows_leave_most_height_for_history(self):
        app, sent, dialog = self.make_dialog()
        dialog.set_available_clients(['p1', 'p2', 'p3', 'p4'])
        dialog.open_or_raise()
        app.processEvents()
        self.assertLess(abs(dialog.clients.geometry().top() - dialog.allow_busy_chat.geometry().top()), 6)
        self.assertLess(dialog.clients.width(), dialog.width() / 2)
        self.assertGreater(dialog.send_text.width(), dialog.clients.width())
        self.assertGreater(dialog.messages.height(), dialog.height() / 2)
        self.assertFalse(dialog.send_status.isVisible())
        self.assertIn('4 个客户端', dialog.status.text())

    def test_return_and_keypad_enter_send_once_without_minimizing(self):
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            with self.subTest(key=key):
                app, sent, dialog = self.make_dialog()
                dialog.set_available_clients(['p1'])
                dialog.send_text.setText('hello')
                dialog.show()
                dialog.send_text.setFocus()
                app.processEvents()
                QTest.keyClick(dialog.send_text, key)
                app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertFalse(dialog.isMinimized())
                self.assertEqual(sent.qsize(), 1)
                self.assertEqual(sent.get_nowait().com_type, GUICommandType.SendNearbyChatTest)
                QTest.keyClick(dialog, key)
                self.assertTrue(sent.empty())

    def test_return_never_activates_titlebar_or_clear_buttons(self):
        app, sent, dialog = self.make_dialog()
        dialog.show()
        dialog.handle_event({'kind': 'message', 'title': 'p1', 'message': 'keep me'})
        for button in (dialog.minimize_button, dialog.close_button, dialog.send_button, dialog._clear_button):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Return)
            app.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.isMinimized())
            self.assertIn('keep me', dialog.messages.toPlainText())
        self.assertTrue(sent.empty())

    def test_chat_history_contains_only_real_message_events(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event({'kind': 'message', 'title': 'p1', 'source': 'chat_log',
                             'channel': '附近', 'sender_name': 'Alex', 'message': 'hi'})
        history = dialog.messages.toPlainText()
        dialog.handle_event({'kind': 'manual_send', 'title': 'p1', 'status': 'HWND=7 测试停止',
                             'done': True, 'invoked': False, 'peer_receipts': ['p2']})
        dialog.handle_event({'kind': 'reply', 'title': 'p1', 'sender_gid': 3})
        dialog.handle_event({'kind': 'status', 'title': 'p1', 'status': 'Chat Hook 失败'})
        self.assertEqual(dialog.messages.toPlainText(), history)
        self.assertNotIn('HWND', dialog.send_status.text())
        self.assertNotIn('Chat Hook', dialog.status.text())

    def test_outgoing_english_preview_and_translation_failure_are_visible(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event({'kind': 'manual_send', 'translation': 'Who am I?', 'done': False})
        self.assertIn('Who am I?', dialog.send_status.text())
        dialog.handle_event({'kind': 'manual_send', 'done': True, 'invoked': False,
                             'error': '接口超时；未输入中文原文。'})
        self.assertIn('未输入中文原文', dialog.send_status.text())
        self.assertNotIn('Who am I?', dialog.messages.toPlainText())

    def test_chinese_input_requests_send_and_draft_fallback_never_claims_sent(self):
        app, sent, dialog = self.make_dialog()
        dialog.set_available_clients(['p1'])
        dialog.send_text.setText('我是谁')
        dialog._send_once()
        command = sent.get_nowait()
        self.assertEqual(command.data['text'], '我是谁')
        self.assertNotIn('fill_only', command.data)
        self.assertEqual(dialog.send_button.text(), '翻译并填入')
        dialog.handle_event({'kind': 'manual_send', 'done': True, 'filled': True, 'invoked': False})
        self.assertIn('已填入游戏', dialog.send_status.text())
        self.assertIn('未发送', dialog.send_status.text())

    def test_empty_input_and_missing_sender_do_not_submit(self):
        app, sent, dialog = self.make_dialog()
        dialog.set_available_clients(['p1'])
        self.assertEqual(dialog.send_text.text(), '')
        self.assertFalse(dialog.send_button.isEnabled())
        dialog._send_once()
        self.assertTrue(sent.empty())
        dialog.send_text.setText('hello')
        dialog.set_available_clients(['p2'])
        dialog._send_once()
        self.assertTrue(sent.empty())

    def test_manual_sender_default_no_duplicate_or_fallback(self):
        app = QApplication.instance() or QApplication([])
        sent = queue.Queue()
        ctx = SimpleNamespace(bg_color='#171721', alt_bg='#202030',
                              text_color='#eeeeff', stroke_color='#78ddee',
                              icon_btn_style='', titlebar_svg_icon=lambda *_args: QIcon(), settings=self.settings())
        dialog = ChatTranslationDialog(sent, ctx)
        dialog.set_available_clients(['p2'])
        self.assertEqual(dialog.sender.currentData(), 'p1')
        self.assertFalse(dialog.send_button.isEnabled())
        self.assertEqual(dialog.send_text.text(), '')
        self.assertFalse(hasattr(dialog, 'auto_reply'))
        dialog.send_text.setText('hello')
        self.assertFalse(dialog.allow_busy_chat.isChecked())
        dialog.allow_busy_chat.setChecked(True)
        dialog.set_available_clients(['p1', 'p2'])
        dialog.send_button.click()
        dialog._send_once()
        self.assertEqual(sent.qsize(), 1)
        command = sent.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.SendNearbyChatTest)
        self.assertEqual(command.data, {'title': 'p1', 'text': 'hello', 'allow_busy': True})
        self.assertFalse(dialog.send_button.isEnabled())
        dialog.handle_event({'kind': 'manual_send', 'title': 'p1', 'done': True,
                             'invoked': False, 'filled': True, 'local_echo': False,
                             'peer_receipts': ['p2'], 'listener_capture': ['p2']})
        self.assertTrue(dialog.send_button.isEnabled())
        self.assertIn('已填入游戏', dialog.send_status.text())
        self.assertIn('频道和收件人', dialog.send_status.text())
        self.assertEqual(dialog.messages.toPlainText(), '')
        dialog.close()
        app.processEvents()

    def test_window_is_independent_and_defaults_to_safe_receive_only(self):
        app = QApplication.instance() or QApplication([])
        sent = queue.Queue()
        ctx = SimpleNamespace(bg_color='#171721', alt_bg='#202030',
                              text_color='#eeeeff', stroke_color='#78ddee',
                              icon_btn_style='QPushButton { border: none; }',
                              titlebar_svg_icon=lambda _svg, _size: QIcon(), settings=self.settings())
        dialog = ChatTranslationDialog(sent, ctx)
        dialog.set_available_clients(["p1", "p2"])
        self.assertIsNone(dialog.parentWidget())
        self.assertTrue(dialog.isWindow())
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertFalse(hasattr(dialog, 'auto_reply'))
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
        self.assertIn('监听中', dialog.status.text())
        self.assertNotIn('Hook', dialog.status.text())
        dialog.clients.setCurrentIndex(dialog.clients.findData("p2"))
        dialog.set_available_clients(["p1"])
        self.assertFalse(hasattr(dialog, 'auto_reply'))
        dialog.close_button.click()
        self.assertFalse(dialog.isVisible())
        self.assertFalse(hasattr(dialog, 'auto_reply'))
        commands = []
        while not sent.empty():
            commands.append(sent.get_nowait())
        self.assertFalse(commands[-1].data["enabled"])
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
