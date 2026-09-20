import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QComboBox, QCheckBox, QLabel, QWidget
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
            stroke_color='#80d8e8', text_color='#eeeeee', alt_bg='#222233', icon_btn_style='',
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

    def test_client_wrap_does_not_move_logo_or_progress(self):
        self.tab.resize(1000, 500)
        self.tab.show()
        self.api['set_available_clients'](['p1'])
        self.app.processEvents()
        status = self.ctx.widget_tags['QuestPartyRuntimeStatus']
        info = status.parentWidget()
        before = info.geometry()
        self.api['set_available_clients']([f'p{i}' for i in range(1, 13)])
        status.setText('p1 → p2｜任务进行中')
        status.show()
        self.app.processEvents()
        self.assertEqual(info.geometry(), before)
        host = self.tab.findChild(QWidget, 'HotkeyClientTargets')
        self.assertLessEqual(host.geometry().right(), info.geometry().left())
