import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QIcon
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QWidget, QDoubleSpinBox,
    QStyle, QStyleFactory, QStyleOptionSpinBox)
from src.gui.ibao_dialog import build_ibao_dialog
from src.gui.theme import compute_styles


class IbaoSpinBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_arrow_layout_and_mouse_targets_match_across_native_styles(self):
        app = self.app
        old_style, old_sheet = app.style().objectName(), app.styleSheet()
        theme = dict(stroke_color='#80d8e8', text_color='#eeeeee',
            bg_color='#181824', alt_bg='#222233', button_color='#777788',
            titlebar_bg='#181824')
        try:
            for style in QStyleFactory.keys():
                with self.subTest(style=style):
                    app.setStyle(style)
                    app.setStyleSheet(compute_styles(theme)['app_style'])
                    parent = QWidget()
                    ctx = SimpleNamespace(window=parent, **theme,
                        titlebar_svg_icon=lambda *args: QIcon(), icon_btn_style='',
                        svgs={'play': '', 'kill': ''}, exports={}, send_queue=Mock())
                    dialog = build_ibao_dialog(ctx)
                    dialog.show()
                    app.processEvents()
                    spin = dialog.findChild(QDoubleSpinBox)
                    for height in (32, 42):
                        spin.setFixedSize(206, height)
                        app.processEvents()
                        option = QStyleOptionSpinBox()
                        spin.initStyleOption(option)
                        up = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,
                            option, QStyle.SubControl.SC_SpinBoxUp, spin)
                        down = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,
                            option, QStyle.SubControl.SC_SpinBoxDown, spin)
                        self.assertEqual(up.left(), down.left())
                        self.assertLess(up.center().y(), down.center().y())
                        self.assertFalse(up.intersects(down))
                        for rect, expected in ((up, .4), (down, .2)):
                            for point in (rect.center(), rect.topLeft() + QPoint(2, 2),
                                          rect.bottomRight() - QPoint(2, 2)):
                                spin.setValue(.3)
                                QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=point)
                                self.assertAlmostEqual(spin.value(), expected)
                    spin.setValue(.3)
                    ctx.exports['ibao']['retheme']()
                    self.assertAlmostEqual(spin.value(), .3)
                    spin.setEnabled(False)
                    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=up.center())
                    self.assertAlmostEqual(spin.value(), .3)
                    dialog.close()
                    dialog.deleteLater()
                    parent.close()
                    app.processEvents()
        finally:
            app.setStyle(old_style)
            app.setStyleSheet(old_sheet)
