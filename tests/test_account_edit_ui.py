import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLineEdit, QPushButton, QWidget

from src.gui.commands import GUICommandType
from src.gui.tab_launcher import build_launcher_tab


class AccountEditUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.ctx = SimpleNamespace(
            window=self.window, tl=lambda key: key, send_queue=Mock(),
            theme='dark', stroke_color='#80d8e8', btn_style='', icon_btn_style='',
            svgs=defaultdict(lambda: '<svg xmlns="http://www.w3.org/2000/svg"/>'),
            titlebar_svg_icon=lambda *_: QIcon(), widget_tags={}, exports={},
            settings=SimpleNamespace(get_setting=lambda *_: None),
            registry=SimpleNamespace(action_icon_btn=lambda *_: QPushButton()),
        )
        self.tab = build_launcher_tab(self.ctx)
        self.ctx.exports['launcher']['populate_account_list']([
            {'nick': 'Old', 'steam': False, 'error': None},
        ])
        self.ctx.send_queue.reset_mock()

    def tearDown(self):
        self.tab.close()
        self.window.close()
        self.app.processEvents()

    def edit_button(self):
        account_list = self.ctx.exports['launcher']['account_list']
        row = account_list.itemWidget(account_list.item(0))
        return next(button for button in row.findChildren(QPushButton)
                    if button.toolTip() == 'edit_account')

    def test_edit_nickname_and_credentials_command(self):
        def interact():
            dialog = self.app.activeModalWidget()
            self.assertIsInstance(dialog, QDialog)
            self.assertEqual(dialog.findChild(QLineEdit).text(), 'Old')
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            self.assertTrue(buttons['update_account_credentials'].isVisible())
            buttons['update_account_credentials'].click()
            dialog.findChild(QLineEdit).setText('New')
            buttons['save_account'].click()

        QTimer.singleShot(0, interact)
        self.edit_button().click()
        commands = [call.args[0] for call in self.ctx.send_queue.put.call_args_list]
        self.assertEqual([command.com_type for command in commands], [
            GUICommandType.UpdateAccountCredentials, GUICommandType.UpdateAccount,
        ])
        self.assertEqual(commands[0].data, 'Old')
        self.assertEqual(commands[1].data, ('Old', 'New', False))

    def test_steam_hides_credentials_and_cancel_does_not_save(self):
        def interact():
            dialog = self.app.activeModalWidget()
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            dialog.findChild(QCheckBox).setChecked(True)
            self.assertFalse(buttons['update_account_credentials'].isVisible())
            buttons['cancel'].click()

        QTimer.singleShot(0, interact)
        self.edit_button().click()
        self.ctx.send_queue.put.assert_not_called()


if __name__ == '__main__':
    unittest.main()
