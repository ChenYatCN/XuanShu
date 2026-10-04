import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QIcon, QFont, QFontDatabase, QTextDocument
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QWidget

from src.gui.commands import GUICommandType
from src.gui.helpers import titlebar_svg_icon
from src.gui.popups import show_license_popup
from src.gui.tab_launcher import show_launcher_settings_dialog
from src.gui.theme import compute_styles
from src.lang import load_lang
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME


class LauncherLanguageUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            cls.app.setFont(QFont(families[0], 10))

    def setUp(self):
        self.parent = QWidget()
        self.parent.setWindowIcon(QIcon('XuanShu-logo.ico'))
        self.settings = Mock()
        self.settings.get_theme.return_value = dict(DEFAULT_THEME)
        self.settings.get_setting.side_effect = DEFAULT_SETTINGS.get
        styles = compute_styles(DEFAULT_THEME)
        self.app.setStyleSheet(styles['app_style'])
        self.ctx = SimpleNamespace(window=self.parent, settings=self.settings, tl=load_lang('zh'),
            titlebar_svg_icon=lambda svg, size: titlebar_svg_icon(self.parent, svg, size),
            btn_style=styles['btn_style'], icon_btn_style=styles['icon_btn_style'],
            svgs={'folder': '<svg xmlns="http://www.w3.org/2000/svg"/>'},
            exports={'launcher': {}}, send_queue=queue.Queue())

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def check_titlebar(self, dialog):
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
        bar = dialog.findChild(QWidget, 'dialogTitleBar')
        self.assertEqual(bar.height(), 32)
        old = dialog.pos()
        bar.mousePressEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
            globalPosition=lambda: QPointF(old.x()+12, old.y()+12)))
        bar.mouseMoveEvent(SimpleNamespace(buttons=lambda: Qt.MouseButton.LeftButton,
            globalPosition=lambda: QPointF(old.x()+32, old.y()+27)))
        bar.mouseReleaseEvent(None)
        self.assertEqual(dialog.pos(), old + QPointF(20, 15).toPoint())

    def test_launcher_default_apply_and_feedback(self):
        def inspect(dialog):
            self.check_titlebar(dialog)
            choice = dialog.findChild(QComboBox, 'gameLanguageChoice')
            apply = dialog.findChild(QPushButton, 'gameLanguageApply')
            self.assertIsNone(choice.currentData())
            self.assertFalse(apply.isEnabled())
            self.assertTrue(self.ctx.send_queue.empty())
            choice.setCurrentIndex(choice.findData('zh'))
            apply.click()
            self.assertFalse(apply.isEnabled())
            command = self.ctx.send_queue.get_nowait()
            self.assertEqual(command.com_type, GUICommandType.SetGameLanguage)
            self.assertEqual(command.data['language'], 'zh')
            self.ctx.exports['launcher']['game_language_result']({
                'ok': False, 'message_key': 'game_language_close_clients', 'detail': ''})
            self.assertTrue(apply.isEnabled())
            self.assertIn('关闭所有', dialog.findChild(QLabel, 'gameLanguageStatus').text())
            self.ctx.exports['launcher']['game_language_result']({
                'ok': True, 'message_key': 'game_language_applied', 'detail': ''})
            dialog.grab().save('artifacts/LAUNCHER_SETTINGS_20261002.png')
            choice.setCurrentIndex(0)
            self.assertTrue(apply.isEnabled())
            apply.click()
            self.assertIsNone(self.ctx.send_queue.get_nowait().data['language'])
            self.ctx.exports['launcher']['game_language_result']({
                'ok': True, 'message_key': 'game_language_preserved', 'detail': ''})
            self.assertFalse(apply.isEnabled())
            dialog.findChild(QPushButton, 'dialogTitleClose').click()
            self.assertFalse(dialog.isVisible())
            return 0
        with patch.object(QDialog, 'exec', inspect), patch(
                'src.gui.tab_launcher.installed_language_patches', return_value=['Locale_en-US-root.wad.d']):
            show_launcher_settings_dialog(self.ctx, QLineEdit('test'))
        self.assertNotIn('game_language_result', self.ctx.exports['launcher'])
        self.settings.set_setting.assert_not_called()

    def test_license_uses_main_chrome_and_keeps_five_second_timeout(self):
        with patch('src.gui.popups.QTimer.singleShot') as timer:
            dialog = show_license_popup(self.ctx, DEFAULT_THEME)
        self.check_titlebar(dialog)
        timer.assert_called_once()
        self.assertEqual(timer.call_args.args[0], 5000)
        self.assertIn(self.ctx.tl('license_text'), [label.text() for label in dialog.findChildren(QLabel)])
        dialog.grab().save('artifacts/LICENSE_DIALOG_20261002.png')
        dialog.findChild(QPushButton, 'dialogTitleClose').click()
        self.assertFalse(dialog.isVisible())

    def test_feedback_and_long_backup_path_fit_without_crowding(self):
        backup = ('D:\\Steam\\steamapps\\common\\Wizard101\\Data\\GameData\\'
                  'Locale_en-US-root.wad.xuanshu-backup-j1l9azex.bak')

        def inspect(dialog):
            self.app.setStyleSheet(compute_styles(DEFAULT_THEME, 'Microsoft YaHei', 14)['app_style'])
            dialog.setFont(QFont('Microsoft YaHei', 14))
            dialog.show()
            self.app.processEvents()
            old_height = dialog.height()
            choice = dialog.findChild(QComboBox, 'gameLanguageChoice')
            patches = dialog.findChild(QComboBox, 'gameLanguagePatch')
            self.assertEqual(patches.count(), 2)
            patches.setCurrentIndex(patches.findData('Locale_English-Root.wad.d'))
            choice.setCurrentIndex(choice.findData('zh'))
            apply = dialog.findChild(QPushButton, 'gameLanguageApply')
            apply.click()
            self.assertEqual(self.ctx.send_queue.get_nowait().data['patch_name'], 'Locale_English-Root.wad.d')
            self.ctx.exports['launcher']['game_language_result']({
                'ok': True, 'message_key': 'game_language_applied', 'detail': backup})
            self.app.processEvents()
            note = dialog.findChild(QLabel, 'gameLanguageNote')
            status = dialog.findChild(QLabel, 'gameLanguageStatus')
            self.assertGreater(dialog.height(), old_height)
            for label in (note, status):
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertFalse(label.font().bold())
                self.assertGreaterEqual(label.font().pointSizeF(), 14)
                document = QTextDocument()
                document.setDefaultFont(label.font())
                document.setHtml(label.text())
                document.setTextWidth(label.width())
                self.assertLessEqual(document.idealWidth(), label.width())
            self.assertGreaterEqual(status.geometry().top() - note.geometry().bottom(), 10)
            self.assertGreaterEqual(apply.geometry().top() - status.geometry().bottom(), 10)
            dialog.grab().save('artifacts/LAUNCHER_LANGUAGE_SPACING_20261002.png')
            dialog.reject()
            return 0

        with patch.object(QDialog, 'exec', inspect), patch(
                'src.gui.tab_launcher.installed_language_patches', return_value=[
                    'Locale_en-US-root.wad.d', 'Locale_English-Root.wad.d']):
            show_launcher_settings_dialog(self.ctx, QLineEdit('D:\\Steam\\steamapps\\common\\Wizard101'))
