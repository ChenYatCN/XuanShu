import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton
from PyQt6.QtCore import QSize
from src.gui.helpers import (configure_titlebar_button, titlebar_svg_icon,
                             build_shared_svgs)
from src.gui.actions import ActionRegistry
from src.gui.ibao_dialog import build_ibao_dialog


class ActionSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registry_baseline_and_independent_titlebar(self):
        parent = QWidget()
        factory = lambda svg, size=24: titlebar_svg_icon(parent, svg, size)
        ctx = SimpleNamespace(window=parent, stroke_color='#80d8e8',
            text_color='#eeeeee', bg_color='#181824', alt_bg='#222233',
            titlebar_svg_icon=factory, icon_btn_style='',
            svgs=build_shared_svgs('#80d8e8'), exports={}, send_queue=Mock())
        registry = ActionRegistry(None, lambda k: k, ctx.send_queue, '', '', factory)
        baseline = registry.action_icon_btn(ctx.svgs['import'], 'import', lambda: None)
        self.assertEqual(baseline.size(), QSize(40, 40))
        self.assertEqual(baseline.iconSize(), QSize(16, 16))
        dialog = build_ibao_dialog(ctx)
        for button in dialog.findChildren(QPushButton):
            if button.accessibleName() in ('启动所选', '停止所选'):
                self.assertEqual(button.size(), baseline.size())
                self.assertEqual(button.iconSize(), baseline.iconSize())
        minimize = next(b for b in dialog.findChildren(QPushButton)
                        if '最小化生产线' in b.toolTip())
        main_minimize = QPushButton()
        configure_titlebar_button(main_minimize, factory, ctx.stroke_color)
        self.assertEqual(minimize.size(), main_minimize.size())
        self.assertEqual(minimize.iconSize(), main_minimize.iconSize())
        self.assertEqual(minimize.styleSheet(), main_minimize.styleSheet())
        self.assertEqual(minimize.icon().pixmap(16, 16).toImage(),
                         main_minimize.icon().pixmap(16, 16).toImage())
        parent.show()
        dialog.show()
        minimize.click()
        self.app.processEvents()
        self.assertTrue(dialog.isMinimized())
        self.assertFalse(parent.isMinimized())
        self.assertIsNone(dialog.parentWidget())
        ctx.stroke_color = '#ffbb44'
        ctx.exports['ibao']['retheme']()
        self.assertEqual(minimize.iconSize(), QSize(16, 16))
        dialog.close()
        parent.close()
