import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox
from PyQt6.QtGui import QIcon
from src.gui.tab_fishing import build_fishing_tab
from src.gui.commands import GUICommandType


class FishingGroupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ctx = SimpleNamespace(settings=Mock(), tl=lambda k: 'Running: {groups}' if k == 'bot_running_groups' else k,
            stroke_color='#80d8e8', text_color='#eeeeee', bg_color='#181824', alt_bg='#222233',
            titlebar_svg_icon=lambda *a: QIcon(), tracked_svg_labels=[],
            icon_btn_style="", tracked_toggle_btns=[], svgs={"play":"play", "kill":"kill"},
            widget_tags={}, exports={}, send_queue=Mock())
        self.ctx.settings.get_settings.return_value = {}
        self.tab = build_fishing_tab(self.ctx)
        self.api = self.ctx.exports['fishing']
        self.api['set_available_clients'](['p1', 'p2'])
        self.checks = {c.text(): c for c in self.tab.findChildren(QCheckBox)}
        self.checks['bot_target_all'].setChecked(False)
        self.school = next(c for c in self.tab.findChildren(QComboBox) if c.findData('Ice') >= 0)

    def tearDown(self):
        self.tab.deleteLater()
        self.app.processEvents()

    def test_independent_start_and_stop_payloads(self):
        self.checks['p1'].setChecked(True)
        self.school.setCurrentIndex(self.school.findData('Ice'))
        self.api['toggle_selected']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.com_type, GUICommandType.StartFishingGroup)
        self.assertEqual(command.data['clients'], ['p1'])
        first = {'clients': ['p1'], 'settings': command.data['settings']}
        self.api['set_running_groups']([first])
        self.checks['p1'].setChecked(False)
        self.checks['p2'].setChecked(True)
        self.assertTrue(self.school.isEnabled())
        self.school.setCurrentIndex(self.school.findData('Fire'))
        self.api['toggle_selected']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.data['clients'], ['p2'])
        self.assertEqual(command.data['settings']['fish_school'], 'Fire')
        self.assertEqual(first['settings']['fish_school'], 'Ice')
        self.api['set_running_groups']([first, {'clients':['p2'], 'settings':command.data['settings']}])
        self.checks['p2'].setChecked(False)
        self.checks['p1'].setChecked(True)
        self.api['toggle_selected']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.com_type, GUICommandType.StopFishingGroup)
        self.assertEqual(command.data, {'clients':['p1']})

    def test_client_list_refresh_and_no_implicit_all_clients(self):
        self.api['toggle_selected']()
        self.ctx.send_queue.put.assert_not_called()
        self.checks['p1'].setChecked(True)
        self.api['set_available_clients'](['p1','p3'])
        checks = {c.text(): c for c in self.tab.findChildren(QCheckBox)}
        self.assertEqual(set(checks), {'bot_target_all', 'p1','p3'})
        self.assertTrue(checks['p1'].isChecked())
        self.assertFalse(checks['p3'].isChecked())

    def test_script_style_all_checkbox_and_no_group_dropdown(self):
        self.checks['bot_target_all'].setChecked(True)
        self.assertTrue(self.checks['p1'].isChecked())
        self.assertTrue(self.checks['p2'].isChecked())
        self.api['set_available_clients'](['p1', 'p2', 'p3'])
        checks = {c.text(): c for c in self.tab.findChildren(QCheckBox)}
        self.assertTrue(checks['p3'].isChecked())
        self.assertEqual(len(self.tab.findChildren(QComboBox)), 1)

    def test_filter_controls_keep_readable_height_in_small_window(self):
        from PyQt6.QtWidgets import QSpinBox, QDoubleSpinBox, QScrollArea
        self.assertEqual(self.tab.findChildren(QScrollArea), [])
        self.tab.resize(1000, 280)
        self.tab.show()
        self.app.processEvents()
        controls = (self.tab.findChildren(QSpinBox)
                    + self.tab.findChildren(QDoubleSpinBox) + [self.school])
        for control in controls:
            self.assertGreaterEqual(control.height(), 32)

    def test_selector_below_chest_hint_and_status_in_bottom_action_row(self):
        from PyQt6.QtWidgets import QWidget, QLabel
        from PyQt6.QtCore import QPoint
        self.api['set_running_groups']([{'clients': ['p1'], 'settings': {'fish_school': 'Ice'}}])
        self.tab.resize(1000, 360)
        self.tab.show()
        self.app.processEvents()
        selector = self.tab.findChild(QWidget, 'FishingClientSelector')
        status = self.tab.findChild(QLabel, 'FishingRunningGroups')
        selector_pos = selector.mapTo(self.tab, QPoint(0, 0))
        status_pos = status.mapTo(self.tab, QPoint(0, 0))
        hint = self.tab.findChild(QLabel, 'FishingChestHint')
        hint_bottom = hint.mapTo(self.tab, QPoint(0, hint.height()))
        panel = self.tab.findChild(QWidget, 'FishingChestPanel')
        self.assertIs(selector.parentWidget(), panel)
        self.assertGreaterEqual(selector_pos.y(), hint_bottom.y())
        self.assertGreater(status_pos.x(), selector_pos.x() + selector.width())
        self.assertIn('p1', status.toolTip())
        from PyQt6.QtWidgets import QPushButton
        button = self.tab.findChild(QPushButton, 'ToggleFishingGroup')
        button_pos = button.mapTo(self.tab, QPoint(0, 0))
        self.assertEqual(status_pos.x() + status.width(), self.tab.width() - 8)
        self.assertEqual(button_pos.y() + button.height(), self.tab.height() - 8)
        self.assertLess(selector_pos.y() + selector.height(), button_pos.y())
        self.assertLessEqual(abs(status_pos.y() + status.height()/2 - button_pos.y() - button.height()/2), 1)

    def test_many_clients_wrap_inside_left_panel(self):
        from PyQt6.QtWidgets import QWidget
        self.api['set_available_clients']([f'p{i}' for i in range(1, 25)])
        self.tab.resize(1000, 420)
        self.tab.show()
        self.app.processEvents()
        selector = self.tab.findChild(QWidget, 'FishingClientSelector')
        checks = selector.findChildren(QCheckBox)
        self.assertGreater(len({check.y() for check in checks}), 1)
        for check in checks:
            self.assertLessEqual(check.geometry().right(), check.parentWidget().width())
            self.assertLessEqual(check.geometry().bottom(), check.parentWidget().height())

    def test_chest_hint_is_small_single_line_without_group_title(self):
        from PyQt6.QtWidgets import QLabel, QGroupBox
        hint = self.tab.findChild(QLabel, 'FishingChestHint')
        hint.ensurePolished()
        self.assertFalse(hint.wordWrap())
        self.assertEqual(hint.font().pointSizeF(),
                         max(8.0, self.tab.font().pointSizeF() - 2.0) + 1.0)
        self.assertFalse(hint.font().italic())
        from PyQt6.QtCore import Qt
        self.assertEqual(hint.alignment(), Qt.AlignmentFlag.AlignCenter)
        self.assertNotIn('fish_chest_filter', [g.title() for g in self.tab.findChildren(QGroupBox)])

    def test_fishing_actions_are_icon_only_with_state_tooltips(self):
        from PyQt6.QtWidgets import QPushButton
        button = self.tab.findChild(QPushButton, 'ToggleFishingGroup')
        stop_selected = self.tab.findChild(QPushButton, 'StopFishingGroup')
        self.assertEqual(button.text(), '')
        self.assertEqual(stop_selected.text(), '')
        self.assertEqual(button.toolTip(), 'fish_start_selected')
        self.checks['p1'].setChecked(True)
        self.api['set_running_groups']([{'clients':['p1'], 'settings':{}}])
        self.assertEqual(button.toolTip(), 'fish_start_selected')
        self.assertFalse(button.isEnabled())
        self.ctx.send_queue.put.reset_mock()
        button.click()
        self.ctx.send_queue.put.assert_not_called()
        self.assertEqual(stop_selected.toolTip(), 'fish_stop_selected')
        self.assertEqual(button.width(), 40)
        self.assertEqual(button.iconSize().width(), 16)
        self.assertEqual(stop_selected.size(), button.size())
        self.assertEqual(stop_selected.iconSize(), button.iconSize())
        stop_selected.click()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.com_type, GUICommandType.StopFishingGroup)
        self.assertEqual(command.data, {'clients': ['p1']})

    def test_fish_start_icon_stays_fixed_and_side_regions_align_outward(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel, QWidget
        from src.gui.widgets import FlowLayout
        render = Mock(return_value=QIcon())
        self.ctx.titlebar_svg_icon = render
        self.api['retheme']()
        from src.gui.tab_fishing import _fish_svg
        self.assertEqual(_fish_svg(self.ctx.stroke_color), render.call_args.args[0])
        self.checks['p1'].setChecked(True)
        self.api['set_running_groups']([{'clients':['p1'], 'settings':{}}])
        self.assertEqual(_fish_svg(self.ctx.stroke_color), render.call_args.args[0])
        status = self.tab.findChild(QLabel, 'FishingRunningGroups')
        self.assertEqual(status.alignment(), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        selector = self.tab.findChild(QWidget, 'FishingClientSelector')
        flow = selector.findChild(FlowLayout)
        self.assertTrue(flow.alignment() & Qt.AlignmentFlag.AlignLeft)
