import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QComboBox
from PyQt6.QtGui import QIcon
from src.gui.actions import ActionRegistry
from src.gui.commands import GUICommandType
from src.gui.tab_hotkeys import build_hotkeys_tab


class HotkeyGroupUITests(unittest.TestCase):
    def test_named_target_persistence_and_legacy_default(self):
        app = QApplication.instance() or QApplication([])
        data = {'hotkey_client_groups': {'组1': ['p1', 'p2']}}
        settings = Mock()
        settings.get_setting.side_effect = data.get
        settings.get_hotkeys.return_value = {}
        settings.set_setting.side_effect = lambda key, value: data.update({key: value})
        ctx = SimpleNamespace(settings=settings, tl=lambda key: key, send_queue=Mock(),
            stroke_color='#80d8e8', text_color='#eeeeee', icon_btn_style='',
            svgs=defaultdict(lambda: '<svg xmlns="http://www.w3.org/2000/svg"/>'),
            titlebar_svg_icon=lambda *_: QIcon(), tracked_svg_labels=[], widget_tags={}, exports={},
            repo_base='', wiki_base='', tool_name='XuanShu', tool_version='test')
        ctx.registry = ActionRegistry(settings, ctx.tl, ctx.send_queue, '', '', ctx.titlebar_svg_icon)
        with patch('src.gui.tab_hotkeys.set_label_icon'):
            tab = build_hotkeys_tab(ctx)
        try:
            row = ctx.registry.row_widgets['toggle_combat']
            choice = row.findChild(QComboBox)
            self.assertEqual(choice.currentData(), '__legacy__')
            ctx.registry.callbacks['toggle_combat']()
            self.assertEqual(ctx.send_queue.put.call_args.args[0].com_type, GUICommandType.ToggleOption)
            choice.setCurrentIndex(choice.findData('组1'))
            ctx.registry.callbacks['toggle_combat']()
            command = ctx.send_queue.put.call_args.args[0]
            self.assertEqual(command.com_type, GUICommandType.ToggleHotkeyGroup)
            self.assertEqual(command.data, {'action': 'toggle_combat', 'clients': ['p1', 'p2']})
            self.assertEqual(data['hotkey_group_targets']['toggle_combat'], '组1')
            self.assertEqual(len(tab.findChildren(QComboBox)), 9)
        finally:
            tab.close()
            tab.deleteLater()
            app.processEvents()
