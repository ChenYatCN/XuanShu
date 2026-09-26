import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QComboBox, QCheckBox, QLabel, QPushButton, QTableWidget, QWidget
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QIcon
from src.gui.actions import ActionRegistry
from src.gui.commands import GUICommandType
from src.gui.tab_hotkeys import build_hotkeys_tab


class HotkeyGroupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        settings = Mock()
        settings.get_hotkeys.return_value = {}
        self.ctx = SimpleNamespace(settings=settings, tl=lambda key: key, send_queue=Mock(),
            stroke_color='#80d8e8', text_color='#eeeeee', bg_color='#171822',
            alt_bg='#222233', icon_btn_style='',
            svgs=defaultdict(lambda: '<svg xmlns="http://www.w3.org/2000/svg"/>'),
            titlebar_svg_icon=lambda *_: QIcon(), tracked_svg_labels=[], widget_tags={}, exports={},
            repo_base='', wiki_base='', tool_name='XuanShu', tool_version='test')
        self.ctx.registry = ActionRegistry(settings, self.ctx.tl, self.ctx.send_queue, '', '', self.ctx.titlebar_svg_icon)
        with patch('src.gui.tab_hotkeys.set_label_icon'):
            self.tab = build_hotkeys_tab(self.ctx)
        self.api = self.ctx.exports['hotkeys']

    def tearDown(self):
        self.tab.close()
        self.tab.deleteLater()
        self.app.processEvents()

    def checks(self):
        return {c.text(): c for c in self.tab.findChildren(QCheckBox)}

    def test_default_all_and_no_per_row_dropdowns(self):
        self.api['set_available_clients'](['p2', 'p1'])
        self.assertEqual(self.api['selected_clients'](), ['p1', 'p2'])
        self.assertTrue(self.checks()['bot_target_all'].isChecked())
        self.assertEqual(self.tab.findChildren(QComboBox), [])
        self.assertIn('客户端', [l.text() for l in self.tab.findChildren(QLabel)])
        self.ctx.registry.callbacks['toggle_combat']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.com_type, GUICommandType.ToggleHotkeyGroup)
        self.assertEqual(command.data, {'action': 'toggle_combat', 'clients': ['p1', 'p2']})

    def test_reset_button_is_narrower_without_changing_hotkey_rows(self):
        self.tab.resize(680, 500)
        self.tab.show()
        self.app.processEvents()
        reset = next(b for b in self.tab.findChildren(QPushButton)
                     if b.text() == 'reset_defaults')
        row = self.ctx.registry.row_widgets['toggle_combat']
        edit = next(b for b in row.findChildren(QPushButton)
                    if b.toolTip() == 'bind_hotkey')
        self.assertEqual(reset.width(), 300)
        self.assertEqual(row.width(), 430)
        self.assertLessEqual(edit.geometry().right(), reset.geometry().right())
        self.api['set_available_clients']([f'p{i}' for i in range(1, 6)])
        self.app.processEvents()
        self.assertEqual(reset.width(), 300)
        self.assertEqual(row.width(), 430)

    def test_two_way_sync_and_new_clients_default_selected(self):
        self.api['set_available_clients'](['p1', 'p2'])
        self.checks()['p1'].setChecked(False)
        self.assertFalse(self.checks()['bot_target_all'].isChecked())
        self.api['set_available_clients'](['p1', 'p2', 'p3'])
        self.assertEqual(self.api['selected_clients'](), ['p2', 'p3'])
        self.assertFalse(self.checks()['bot_target_all'].isChecked())
        self.checks()['p1'].setChecked(True)
        self.assertTrue(self.checks()['bot_target_all'].isChecked())
        self.checks()['bot_target_all'].setChecked(False)
        self.assertEqual(self.api['selected_clients'](), [])
        self.ctx.registry.callbacks['toggle_questing']()
        self.assertEqual(self.ctx.send_queue.put.call_args.args[0].data['clients'], [])
        self.api['set_available_clients'](['p1', 'p2', 'p3', 'p4'])
        self.assertEqual(self.api['selected_clients'](), ['p4'])
        self.checks()['bot_target_all'].setChecked(True)
        self.assertEqual(self.api['selected_clients'](), ['p1', 'p2', 'p3', 'p4'])

    def test_disconnect_refresh_and_selection_do_not_reset(self):
        self.api['set_available_clients'](['p1', 'p2'])
        self.checks()['p2'].setChecked(False)
        self.api['set_available_clients'](['p2', 'p1'])
        self.assertEqual(self.api['selected_clients'](), ['p1'])
        self.api['set_available_clients'](['p1'])
        self.assertNotIn('p2', self.checks())
        self.assertTrue(self.checks()['bot_target_all'].isChecked())
        self.api['set_available_clients']([])
        self.assertEqual(self.api['selected_clients'](), [])
        self.assertFalse(self.checks()['bot_target_all'].isEnabled())
        self.api['set_available_clients'](['p2'])
        self.assertEqual(self.api['selected_clients'](), ['p2'])

    def test_all_supported_actions_share_only_selected_targets(self):
        self.api['set_available_clients'](['p1', 'p2', 'p3'])
        self.checks()['p3'].setChecked(False)
        for action in ('toggle_speed', 'toggle_combat', 'toggle_dialogue',
                       'toggle_dialogue_side_quests', 'toggle_sigil', 'toggle_questing',
                       'toggle_auto_pet', 'toggle_auto_potion', 'toggle_freecam',
                       'quest_tp', 'mass_tp', 'freecam_tp', 'friend_tp', 'xyz_sync', 'x_press'):
            self.ctx.registry.callbacks[action]()
            self.assertEqual(self.ctx.send_queue.put.call_args.args[0].data,
                             {'action': action, 'clients': ['p1', 'p2']})

    def test_client_row_does_not_move_logo_or_progress(self):
        self.tab.resize(1000, 500)
        self.tab.show()
        self.api['set_available_clients'](['p1'])
        self.app.processEvents()
        status = self.ctx.widget_tags['QuestPartyRuntimeStatus']
        info = status.parentWidget()
        before = info.geometry()
        self.api['set_available_clients']([f'p{i}' for i in range(1, 13)])
        status.setText('p1 → p2｜正在好友传送，等待区域切换后继续自动任务')
        status.show()
        self.app.processEvents()
        self.assertEqual(info.geometry(), before)
        host = self.tab.findChild(QWidget, 'HotkeyClientTargets')
        selector = host.parentWidget().parentWidget()
        overview = next(b for b in self.tab.findChildren(QPushButton)
                        if b.toolTip() == '状态总览')
        self.assertEqual(overview.y(), selector.y())
        self.assertEqual(selector.height(), 24)
        self.assertGreater(overview.x(), info.geometry().left())
        self.assertGreaterEqual(overview.geometry().right(), self.tab.width() - 8)
        category = next(label for label in self.tab.findChildren(QLabel)
                        if label.text() == '<b>cat_toggles</b>')
        gap = category.mapTo(self.tab, QPoint()).y() - (selector.y() + selector.height())
        self.assertEqual(gap, 0)
        self.assertEqual(status.width(), 230)
        self.assertLessEqual(status.geometry().right(), info.width())
        self.assertGreaterEqual(status.height(), status.heightForWidth(status.width()))
        self.assertLessEqual(
            selector.mapTo(self.tab, QPoint(0, selector.height())).y(),
            info.mapTo(self.tab, QPoint(0, 0)).y(),
        )
        self.assertEqual(len({self.checks()[f'p{i}'].geometry().y() for i in range(1, 13)}), 1)
        self.tab.resize(680, 500)
        self.app.processEvents()
        self.assertEqual(selector.height(), 42)
        self.assertEqual(overview.y(), selector.y())
        status.setText(
            'p12345678901234567890 → p2345678901234567890｜'
            '等待区域切换后重新检查任务客户端状态并继续自动任务'
        )
        self.app.processEvents()
        self.assertGreater(status.height(), 58)
        self.assertGreaterEqual(status.height(), status.heightForWidth(status.width()))
        self.assertLessEqual(status.geometry().right(), info.width())
        self.assertLessEqual(status.geometry().bottom(), info.height())

    def test_status_badges_fold_and_overview_follow_live_clients(self):
        self.tab.resize(1000, 500)
        self.tab.show()
        button = next(b for b in self.tab.findChildren(QPushButton)
                      if b.toolTip() == '状态总览')
        self.app.processEvents()
        row = self.ctx.registry.row_widgets['toggle_combat']
        key = self.ctx.registry.key_labels['toggle_combat']
        edit = next(b for b in row.findChildren(QPushButton)
                    if b.toolTip() == 'bind_hotkey')
        icon = row.findChildren(QLabel)[0]
        self.assertGreaterEqual(key.x() - icon.x(), 150)
        self.assertGreaterEqual(edit.x() - key.x(), 60)
        self.assertLessEqual(edit.x() - key.geometry().right() - 1, 4)
        for count, expected in ((1, None), (4, None), (5, '+1'),
                                (6, '+2'), (8, '+4'), (10, '+6'), (12, '+8')):
            self.api['set_available_clients']([f'p{i}' for i in range(1, count + 1)])
            self.api['update_client_states']({
                'toggle_combat': {f'p{i}': i % 2 == 1 for i in range(1, count + 1)}
            })
            self.app.processEvents()
            row = self.ctx.registry.row_widgets['toggle_combat']
            icon = row.findChildren(QLabel)[0]
            self.assertEqual(icon.width(), 20)
            self.assertLessEqual(icon.x(), 4)
            labels = [label.text() for label in row.findChildren(QLabel)]
            badges_host = next(label.parentWidget() for label in row.findChildren(QLabel)
                               if label.text() == '● p1')
            self.assertEqual(
                badges_host.width(), 28 * min(4, count) + (30 if count > 4 else 0)
            )
            edit = next(b for b in row.findChildren(QPushButton)
                        if b.toolTip() == 'bind_hotkey')
            self.assertEqual(row.width(), 430)
            name = next(label for label in row.findChildren(QLabel)
                        if label.text() == 'combat_toggle')
            self.assertEqual(name.width(), min(165, 312 - badges_host.width()))
            self.assertLessEqual(
                edit.x() - key.geometry().right() - 1,
                4,
            )
            self.assertGreater(badges_host.x(), edit.geometry().right())
            self.assertLessEqual(badges_host.geometry().right(), row.width())
            self.assertIn('● p1', labels)
            if count >= 2:
                self.assertIn('○ p2', labels)
            if count >= 5:
                self.assertNotIn('● p5', labels)
            more = next((b for b in row.findChildren(QPushButton)
                         if b.text().startswith('+')), None)
            if expected is None:
                self.assertTrue(more is None or not more.isVisible())
            else:
                self.assertIsNotNone(more)
                self.assertEqual(more.text(), expected)
                self.assertIn(f'p{count}', more.toolTip())
                if count >= 6:
                    self.assertIn('○ p6', more.toolTip())
        more.click()
        table = self.tab.findChild(QTableWidget)
        self.assertEqual(table.rowHeight(0), 32)
        self.assertIn('alternate-background-color: #31314a', table.styleSheet())
        self.assertIn('border-bottom: 3px solid #171822', table.styleSheet())
        self.assertEqual(table.columnCount(), 13)
        self.assertEqual(table.horizontalHeaderItem(12).text(), 'p12')
        combat_row = next(row for row in range(table.rowCount())
                          if table.item(row, 0).text() == 'combat_toggle')
        self.assertEqual(table.item(combat_row, 1).text(), '●')
        self.assertEqual(table.item(combat_row, 2).text(), '○')
        self.api['set_available_clients'](['p1', 'p2'])
        self.api['update_client_states']({'toggle_combat': {'p1': True, 'p2': False}})
        self.assertEqual(table.columnCount(), 3)
        self.assertFalse(more.isVisible())
        button.click()
        self.assertEqual(table.rowCount(), 9)
        self.api['set_available_clients']([])
        self.app.processEvents()
        self.assertFalse(badges_host.isVisible())
        self.assertLessEqual(edit.x() - key.geometry().right() - 1, 4)
