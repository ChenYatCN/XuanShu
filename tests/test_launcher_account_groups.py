import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import queue
from collections import defaultdict
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt, QModelIndex, QCoreApplication, QEvent, QRect
from PyQt6.QtGui import QIcon, QPen
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QPushButton, QWidget, QStyle, QStyleOptionViewItem,
)

from src.gui.commands import GUICommandType
from src.gui.tab_launcher import build_launcher_tab
from src.lang import load_lang
from src.settings_manager import XuanShuSettings


class LauncherAccountGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.qt_errors = []
        hook = patch('sys.excepthook', lambda kind, error, trace: self.qt_errors.append(error))
        hook.start()
        self.addCleanup(hook.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.settings = XuanShuSettings(self.path)
        self.settings.set_setting('account_groups', {
            'Depot': 'warehouse', 'Spare': 'other', 'Extra': 'warehouse'})
        self.window = QWidget()
        def action(svg, tooltip, callback):
            button = QPushButton()
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            return button
        self.ctx = SimpleNamespace(window=self.window, tl=load_lang('zh'),
            send_queue=queue.Queue(), theme='dark', stroke_color='#80d8e8',
            btn_style='', icon_btn_style='', settings=self.settings,
            svgs=defaultdict(lambda: '<svg xmlns="http://www.w3.org/2000/svg"/>'),
            titlebar_svg_icon=lambda *_: QIcon(), widget_tags={}, exports={},
            registry=SimpleNamespace(action_icon_btn=action,
                                     styled_btn=lambda text: QPushButton(text)))
        self.tab = build_launcher_tab(self.ctx)
        self.launcher = self.ctx.exports['launcher']
        self.accounts = self.launcher['account_list']
        self.entries = [dict(nick=nick, steam=False, error=None)
                        for nick in ('Main', 'Depot', 'Spare', 'Extra')]
        self.launcher['populate_account_list'](self.entries)
        self.ctx.send_queue.get_nowait()  # LoadAccounts
        self.choice = self.tab.findChild(QComboBox, 'accountGroupChoice')
        self.search = self.tab.findChild(QLineEdit, 'accountSearch')

    def tearDown(self):
        self.tab.close()
        self.tab.deleteLater()
        self.window.close()
        self.app.processEvents()
        self.assertEqual(self.qt_errors, [])

    def row(self, nick):
        return next(self.accounts.itemWidget(self.accounts.item(i))
                    for i in range(self.accounts.count())
                    if self.accounts.itemWidget(self.accounts.item(i)).findChild(QLabel).text() == nick)

    def check(self, nick):
        return self.row(nick).findChild(QCheckBox)

    def visible_names(self):
        return [self.accounts.itemWidget(self.accounts.item(i)).findChild(QLabel).text()
                for i in range(self.accounts.count()) if not self.accounts.item(i).isHidden()]

    def choose(self, group):
        self.assertIsNotNone(self.choice, 'Missing account group control')
        self.choice.setCurrentIndex(self.choice.findData(group))

    def launch_names(self, inspect=None):
        dialogs = []
        def confirm(dialog):
            dialogs.append(dialog)
            return inspect(dialog) if inspect else QDialog.DialogCode.Accepted
        with patch.object(QDialog, 'exec', confirm):
            self.tab.findChild(QPushButton, 'launchAccounts').click()
        if inspect:
            self.assertEqual(len(dialogs), 1)
        command = self.ctx.send_queue.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.LaunchInstance)
        return command.data[0]

    def test_legacy_defaults_and_last_group_roundtrip(self):
        self.assertEqual(self.visible_names(), ['Main'])
        self.assertTrue(self.search.isHidden())
        self.assertIsNone(self.tab.findChild(QPushButton, 'selectedAccounts'))
        self.choose('warehouse')
        self.assertEqual(self.visible_names(), ['Depot', 'Extra'])
        self.assertEqual(XuanShuSettings(self.path).get_setting('launcher_account_group'), 'warehouse')
        reopened = build_launcher_tab(self.ctx)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.findChild(QComboBox, 'accountGroupChoice').currentData(), 'warehouse')

    def test_hidden_selection_confirmation_lists_every_account_in_click_order(self):
        self.choose('warehouse')
        self.check('Depot').setChecked(True)
        self.choose('favorites')
        self.check('Main').setChecked(True)
        self.choose('other')
        self.check('Spare').setChecked(True)
        self.assertTrue(self.check('Depot').isChecked())
        def inspect(dialog):
            review = dialog.findChild(QListWidget)
            self.assertEqual([review.item(i).text() for i in range(review.count())],
                             ['Depot [仓库]', 'Main [常用]', 'Spare [其他]'])
            self.assertEqual(dialog.objectName(), 'confirmAccountLaunch')
            return QDialog.DialogCode.Accepted
        self.assertEqual(self.launch_names(inspect), ['Depot', 'Main', 'Spare'])

    def test_search_is_global_case_insensitive_and_close_restores_group(self):
        self.assertIsNotNone(self.search, 'Missing expandable account search')
        self.tab.findChild(QPushButton, 'accountSearchToggle').click()
        self.assertFalse(self.search.isHidden())
        self.search.setText(' dEpOt ')
        self.assertEqual(self.visible_names(), ['Depot'])
        self.assertEqual(self.row('Depot').findChild(QLabel, 'accountGroupLabel').text(), '仓库')
        self.check('Depot').setChecked(True)
        self.search.setText('No match')
        self.assertEqual(self.visible_names(), [])
        def inspect(dialog):
            review = dialog.findChild(QListWidget)
            self.assertEqual([review.item(i).text() for i in range(review.count())], ['Depot [仓库]'])
            return QDialog.DialogCode.Accepted
        self.assertEqual(self.launch_names(inspect), ['Depot'])
        self.tab.findChild(QPushButton, 'accountSearchToggle').click()
        self.assertEqual(self.search.text(), '')
        self.assertEqual(self.visible_names(), ['Main'])
        self.assertTrue(self.check('Depot').isChecked())

    def test_move_menu_preserves_selection_and_existing_operations(self):
        self.check('Main').setChecked(True)
        more = self.row('Main').findChild(QPushButton, 'accountMore')
        self.assertIsNotNone(more, 'Missing account overflow menu')
        def menu_inspect(menu, *_):
            actions = {action.text(): action for action in menu.actions()}
            self.assertIn(self.ctx.tl('window_config_tooltip'), actions)
            self.assertIn(self.ctx.tl('remove_account'), actions)
            self.assertTrue(all(action.menu() is None for action in menu.actions()))
            actions[self.ctx.tl('move_account_group')].trigger()
        def dialog_inspect(dialog):
            choice = dialog.findChild(QComboBox)
            self.assertEqual(choice.count(), 3)
            choice.setCurrentIndex(choice.findData('warehouse'))
            next(button for button in dialog.findChildren(QPushButton)
                 if button.text() == self.ctx.tl('save_account')).click()
            return 0
        with patch.object(QMenu, 'exec', menu_inspect), patch.object(QDialog, 'exec', dialog_inspect):
            more.click()
        self.assertEqual(self.visible_names(), [])
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups')['Main'], 'warehouse')
        self.assertEqual(self.launch_names(), ['Main'])
        self.choose('all')
        self.assertEqual(self.visible_names(), ['Main', 'Depot', 'Spare', 'Extra'])

    def test_refresh_keeps_hidden_selections_but_removes_deleted_and_managed_accounts(self):
        self.choose('all')
        self.check('Spare').setChecked(True)
        self.check('Depot').setChecked(True)
        self.check('Main').setChecked(True)
        self.choose('warehouse')
        self.launcher['populate_account_list'](self.entries)
        self.assertEqual(self.launch_names(), ['Spare', 'Depot', 'Main'])
        self.launcher['refresh_account_eligibility'](['Spare'])
        self.assertFalse(self.check('Spare').isChecked())
        self.launcher['populate_account_list']([entry for entry in self.entries if entry['nick'] != 'Depot'])
        self.assertEqual(self.launch_names(), ['Main'])

    def test_filtered_drag_reorders_full_list_without_dropping_hidden_accounts(self):
        self.choose('warehouse')
        self.assertTrue(self.accounts.model().moveRow(QModelIndex(), 3, QModelIndex(), 1))
        command = self.ctx.send_queue.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.ReorderAccounts)
        self.assertEqual(command.data, ['Main', 'Extra', 'Depot', 'Spare'])
        self.assertEqual(self.visible_names(), ['Extra', 'Depot'])

    def test_account_refresh_while_overflow_is_open_does_not_delete_active_menu(self):
        errors = []
        def refresh(menu, *_):
            self.launcher['populate_account_list'](self.entries)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        more = self.row('Main').findChild(QPushButton, 'accountMore')
        with patch.object(QMenu, 'exec', refresh), patch(
                'sys.excepthook', lambda kind, error, trace: errors.append(error)):
            more.click()
        self.assertEqual(errors, [])

    def test_confirmation_tracks_backend_refresh_and_launches_only_remaining_shown_accounts(self):
        self.check('Depot').setChecked(True)
        self.check('Main').setChecked(True)
        def inspect(dialog):
            review = dialog.findChild(QListWidget)
            self.launcher['populate_account_list']([entry for entry in self.entries if entry['nick'] != 'Depot'])
            self.assertEqual([review.item(i).text() for i in range(review.count())], ['Main [常用]'])
            return QDialog.DialogCode.Accepted
        self.assertEqual(self.launch_names(inspect), ['Main'])

    def test_bottom_has_only_original_three_icons_even_with_hidden_checks(self):
        self.check('Depot').setChecked(True)
        self.assertIsNone(self.tab.findChild(QPushButton, 'selectedAccounts'))
        layout = self.tab.layout().itemAt(self.tab.layout().count() - 1).layout()
        buttons = [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget()]
        self.assertEqual([button.toolTip() for button in buttons],
                         [self.ctx.tl(key) for key in ('add_account', 'launch_login', 'settings')])

    def test_visible_selection_launches_without_confirmation_and_empty_selection_does_nothing(self):
        with patch.object(QDialog, 'exec') as confirmation:
            self.tab.findChild(QPushButton, 'launchAccounts').click()
            self.assertTrue(self.ctx.send_queue.empty())
            self.choose('all')
            self.check('Depot').setChecked(True)
            self.tab.findChild(QPushButton, 'launchAccounts').click()
            self.assertEqual(self.ctx.send_queue.get_nowait().data[0], ['Depot'])
            confirmation.assert_not_called()

    def test_cancel_hidden_selection_confirmation_sends_no_launch_and_retains_checks(self):
        self.check('Depot').setChecked(True)
        with patch.object(QDialog, 'exec', return_value=QDialog.DialogCode.Rejected) as confirmation:
            self.tab.findChild(QPushButton, 'launchAccounts').click()
        confirmation.assert_called_once()
        self.assertTrue(self.ctx.send_queue.empty())
        self.assertTrue(self.check('Depot').isChecked())

    def test_repeated_launch_trigger_while_confirmation_open_does_not_duplicate_dialog_or_command(self):
        self.check('Depot').setChecked(True)
        dialogs = []
        def inspect(dialog):
            dialogs.append(dialog)
            if len(dialogs) == 1:
                self.tab.findChild(QPushButton, 'launchAccounts').click()
            return QDialog.DialogCode.Accepted
        self.assertEqual(self.launch_names(inspect), ['Depot'])
        self.assertEqual(len(dialogs), 1)
        self.assertTrue(self.ctx.send_queue.empty())

    def test_unavailable_or_malformed_saved_group_falls_back_without_rewriting_configuration(self):
        for saved in ('removed', 17, ['warehouse'], {'old': 'warehouse'}):
            self.settings.set_setting('launcher_account_group', saved)
            before = self.path.read_bytes()
            reopened = build_launcher_tab(self.ctx)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.findChild(QComboBox, 'accountGroupChoice').currentData(), 'favorites')
            self.assertEqual(self.path.read_bytes(), before)

    def activate_group_action(self, action):
        index = self.choice.findData(action)
        self.assertGreaterEqual(index, 0, 'Missing group dropdown action')
        self.choice.setCurrentIndex(index)
        self.choice.activated.emit(index)

    def test_dropdown_create_trims_name_selects_new_group_and_remains_compact(self):
        self.assertEqual(self.choice.itemData(0), 'all')
        self.assertEqual(self.choice.itemData(self.choice.count() - 2), '@create')
        self.assertEqual(self.choice.itemData(self.choice.count() - 1), '@manage')
        def inspect(dialog):
            dialog.findChild(QLineEdit, 'accountGroupName').setText('  我的仓库  ')
            dialog.findChild(QPushButton, 'saveAccountGroup').click()
            return dialog.result()
        with patch.object(QDialog, 'exec', inspect):
            self.activate_group_action('@create')
        group_id = self.choice.currentData()
        self.assertNotIn(group_id, ('all', 'favorites', 'warehouse', 'other', '@create', '@manage'))
        self.assertEqual(self.choice.currentText(), '我的仓库')
        self.assertEqual(XuanShuSettings(self.path).get_setting('launcher_account_group'), group_id)
        self.assertEqual(self.visible_names(), [])
        self.assertLessEqual(self.choice.maximumWidth(), 110)

    def test_real_dropdown_popup_activates_create_without_losing_previous_group_on_cancel(self):
        self.choose('warehouse')
        self.tab.resize(657, 370)
        self.tab.show()
        self.app.processEvents()
        index = self.choice.findData('@create')
        with patch.object(QDialog, 'exec', return_value=QDialog.DialogCode.Rejected) as dialog:
            self.choice.showPopup()
            view = self.choice.view()
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                             pos=view.visualRect(self.choice.model().index(index, 0)).center())
        dialog.assert_called_once()
        self.assertEqual(self.choice.currentData(), 'warehouse')
        self.assertEqual(self.visible_names(), ['Depot', 'Extra'])

    def test_group_popup_fits_actions_aligns_right_and_keeps_collapsed_layout(self):
        self.tab.resize(657, 370)
        self.tab.show()
        self.app.processEvents()
        before = self.choice.geometry()
        self.choice.showPopup()
        self.app.processEvents()
        view = self.choice.view()
        popup = view.window()
        self.addCleanup(self.choice.hidePopup)
        self.assertGreater(popup.width(), self.choice.width())
        self.assertEqual(popup.geometry().right(),
                         self.choice.mapToGlobal(self.choice.rect().topRight()).x())
        self.assertEqual(self.choice.geometry(), before)
        for action in ('@create', '@manage'):
            index = self.choice.model().index(self.choice.findData(action), 0)
            rect = view.visualRect(index)
            self.assertGreaterEqual(rect.width() - 40,
                                    self.choice.fontMetrics().horizontalAdvance(index.data()))
            self.assertLessEqual(rect.height(), 28)
        self.assertEqual(self.choice.itemText(self.choice.findData('@create')), '+新建分组')
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertFalse(popup.isVisible())

    def test_long_group_popup_stays_inside_window_with_full_name_tooltip(self):
        from src.account_groups import create_group
        name = '这是很长的自定义分组名称' * 2
        group_id = create_group(self.settings, name)
        self.launcher['populate_account_list'](self.entries)
        self.tab.resize(657, 370)
        self.tab.show()
        self.app.processEvents()
        self.choice.showPopup()
        self.app.processEvents()
        self.addCleanup(self.choice.hidePopup)
        popup = self.choice.view().window()
        from PyQt6.QtCore import QRect
        bounds = QRect(self.tab.mapToGlobal(self.tab.rect().topLeft()), self.tab.size())
        self.assertTrue(bounds.contains(popup.geometry()))
        index = self.choice.findData(group_id)
        self.assertEqual(self.choice.itemData(index, Qt.ItemDataRole.ToolTipRole), name)
        self.assertLess(popup.width() - 40, self.choice.fontMetrics().horizontalAdvance(name))

    def test_menu_paint_uses_shared_text_gutter_highlight_and_separator_edges(self):
        delegate = self.choice.view().itemDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(3, 0, 220, 24)
        option.font = self.choice.font()
        option.fontMetrics = self.choice.fontMetrics()
        option.state |= QStyle.StateFlag.State_Selected
        text_rects, backgrounds, heights = [], [], []
        for group in ('favorites', '@create', '@manage'):
            index = self.choice.model().index(self.choice.findData(group), 0)
            painter = Mock()
            painter.pen.return_value = QPen(Qt.GlobalColor.white)
            delegate.paint(painter, option, index)
            rect, alignment, text = painter.drawText.call_args.args
            text_rects.append(rect)
            backgrounds.append(painter.fillRect.call_args.args[0])
            heights.append(delegate.sizeHint(option, index).height())
            self.assertTrue(alignment & Qt.AlignmentFlag.AlignLeft)
            self.assertEqual(text, index.data())
            self.assertGreaterEqual(rect.width(), option.fontMetrics.horizontalAdvance(text))
            self.assertEqual(painter.drawLine.call_count, 0)
        self.assertEqual(text_rects, [text_rects[0]] * 3)
        self.assertEqual(backgrounds, [backgrounds[0]] * 3)
        self.assertEqual(heights, [heights[0]] * 3)
        separator = self.choice.model().index(self.choice.findData('@create') - 1, 0)
        painter = Mock()
        delegate.paint(painter, option, separator)
        left, _, right, _ = painter.drawLine.call_args.args
        self.assertEqual(left, text_rects[0].left())
        self.assertEqual(right, text_rects[0].right())
        self.assertGreater(backgrounds[0].left(), option.rect.left())
        self.assertLess(backgrounds[0].right(), option.rect.right())

    def test_invalid_create_name_displays_error_and_does_not_close_or_write(self):
        before = self.path.read_bytes()
        def inspect(dialog):
            name = dialog.findChild(QLineEdit, 'accountGroupName')
            save = dialog.findChild(QPushButton, 'saveAccountGroup')
            for invalid in ('  ', '全部', ' 仓库 '):
                name.setText(invalid)
                save.click()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
                self.assertTrue(any(label.text() for label in dialog.findChildren(QLabel)
                                    if label.styleSheet().startswith('color:')))
                self.assertEqual(self.path.read_bytes(), before)
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, 'exec', inspect):
            self.activate_group_action('@create')
        self.assertEqual(self.choice.currentData(), 'favorites')

    def test_move_dialog_lists_custom_groups_and_search_badge_uses_renamed_name(self):
        from src.account_groups import create_group, rename_group
        group_id = create_group(self.settings, '自定义仓库')
        def move(dialog):
            choice = dialog.findChild(QComboBox)
            self.assertGreaterEqual(choice.findData(group_id), 0)
            self.assertEqual(choice.findData('all'), -1)
            choice.setCurrentIndex(choice.findData(group_id))
            next(button for button in dialog.findChildren(QPushButton)
                 if button.text() == self.ctx.tl('save_account')).click()
            return dialog.result()
        def menu(menu, *_):
            next(action for action in menu.actions()
                 if action.text() == self.ctx.tl('move_account_group')).trigger()
        self.check('Main').setChecked(True)
        with patch.object(QMenu, 'exec', menu), patch.object(QDialog, 'exec', move):
            self.row('Main').findChild(QPushButton, 'accountMore').click()
        renamed = '<b>改名后的仓库</b>'
        rename_group(self.settings, group_id, renamed)
        self.launcher['populate_account_list'](self.entries)
        self.tab.findChild(QPushButton, 'accountSearchToggle').click()
        self.search.setText('main')
        badge = self.row('Main').findChild(QLabel, 'accountGroupLabel')
        badge.ensurePolished()
        self.assertEqual(badge.text(), renamed)
        self.assertGreaterEqual(badge.sizeHint().width(), badge.fontMetrics().horizontalAdvance(renamed))
        self.assertTrue(self.check('Main').isChecked())

    def test_manage_rename_delete_prompts_and_moves_members_without_clearing_selection(self):
        from src.account_groups import get_account_groups
        self.choose('warehouse')
        self.check('Depot').setChecked(True)
        def inspect(dialog):
            if dialog.objectName() == 'accountGroupNameDialog':
                dialog.findChild(QLineEdit, 'accountGroupName').setText('  大仓库  ')
                dialog.findChild(QPushButton, 'saveAccountGroup').click()
                return dialog.result()
            groups = dialog.findChild(QListWidget, 'managedAccountGroups')
            warehouse = next(groups.item(i) for i in range(groups.count())
                             if groups.item(i).data(Qt.ItemDataRole.UserRole) == 'warehouse')
            groups.setCurrentItem(warehouse)
            dialog.findChild(QPushButton, 'renameAccountGroup').click()
            self.assertEqual(self.choice.currentData(), 'warehouse')
            self.assertEqual(self.choice.currentText(), '大仓库')
            dialog.findChild(QPushButton, 'deleteAccountGroup').click()
            self.assertEqual(self.choice.currentData(), 'favorites')
            self.assertNotIn('warehouse', get_account_groups(self.settings))
            self.assertTrue(self.check('Depot').isChecked())
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, 'exec', inspect), patch.object(
                QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes) as question:
            self.activate_group_action('@manage')
        self.assertIn('2', question.call_args.args[2])
        self.assertIn('常用', question.call_args.args[2])
        self.assertEqual(self.visible_names(), ['Main', 'Depot', 'Extra'])
        self.assertEqual(self.launch_names(), ['Depot'])

    def test_manage_default_is_protected_all_absent_and_cancel_delete_changes_nothing(self):
        before = self.path.read_bytes()
        def inspect(dialog):
            groups = dialog.findChild(QListWidget, 'managedAccountGroups')
            ids = [groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(groups.count())]
            self.assertNotIn('all', ids)
            groups.setCurrentRow(ids.index('favorites'))
            self.assertFalse(dialog.findChild(QPushButton, 'deleteAccountGroup').isEnabled())
            groups.setCurrentRow(ids.index('warehouse'))
            dialog.findChild(QPushButton, 'deleteAccountGroup').click()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, 'exec', inspect), patch.object(
                QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            self.activate_group_action('@manage')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.choice.currentData(), 'favorites')

    def test_custom_group_is_available_to_move_add_and_edit_without_premature_assignment(self):
        from src.account_groups import create_group
        group_id = create_group(self.settings, '自定义仓库')
        self.launcher['populate_account_list'](self.entries)
        before = dict(self.settings.get_setting('account_groups'))
        def inspect(dialog):
            choice = dialog.findChild(QComboBox, 'accountEditGroup')
            self.assertEqual(choice.findData('all'), -1)
            self.assertGreaterEqual(choice.findData(group_id), 0)
            choice.setCurrentIndex(choice.findData(group_id))
            dialog.findChild(QLineEdit).setText('Edited')
            next(button for button in dialog.findChildren(QPushButton)
                 if button.text() == self.ctx.tl('save_account')).click()
            return dialog.result()
        with patch.object(QDialog, 'exec', inspect):
            next(button for button in self.row('Main').findChildren(QPushButton)
                 if button.toolTip() == self.ctx.tl('edit_account')).click()
        command = self.ctx.send_queue.get_nowait()
        self.assertEqual(command.data, ('Main', 'Edited', False, False, group_id))
        self.assertEqual(self.settings.get_setting('account_groups'), before)
        with patch.object(QDialog, 'exec', inspect):
            next(button for button in self.tab.findChildren(QPushButton)
                 if button.toolTip() == self.ctx.tl('add_account')).click()
        command = self.ctx.send_queue.get_nowait()
        self.assertEqual(command.data, ('Edited', False, False, group_id))


if __name__ == '__main__':
    unittest.main()
