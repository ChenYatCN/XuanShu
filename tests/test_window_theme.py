import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QColorDialog, QFontDialog, QMenu, QMessageBox, QPushButton

from src.gui.theme import compute_styles
from src.settings_manager import DEFAULT_THEME


class WindowThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_numeric_fields_share_fishing_bottom_border(self):
        styles = compute_styles(DEFAULT_THEME)['app_style']
        self.assertIn('QSpinBox, QDoubleSpinBox { background-color:', styles)
        self.assertIn('border: none; border-bottom: 1px solid', styles)
        self.assertIn('QSpinBox:focus, QDoubleSpinBox:focus {', styles)

    def test_main_controls_retain_legacy_styles(self):
        styles = compute_styles(DEFAULT_THEME)
        for selector in ('QPushButton {', 'QLineEdit:focus', 'QComboBox:focus',
                         'QCheckBox:focus', 'QCheckBox::indicator:unchecked'):
            self.assertNotIn(selector, styles['app_style'])
        for key in ('btn_style', 'icon_btn_style'):
            self.assertNotIn('QPushButton:focus', styles[key])
        for selector in ('QMenu::item:selected', 'QMenu::item:disabled',
                         'QHeaderView::section', 'QTableView'):
            self.assertIn(selector, styles['app_style'])

    def test_native_caption_theme_preserves_dialog_semantics_and_skips_os_pickers(self):
        from src.gui.window_theme import install_window_theme
        controller = install_window_theme(self.app, DEFAULT_THEME)
        dialog = QDialog()
        dialog.setWindowTitle('Theme probe')
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        flags = dialog.windowFlags()
        minimum, maximum = dialog.minimumSize(), dialog.maximumSize()
        close = QPushButton('Close', dialog)
        close.clicked.connect(dialog.reject)
        with patch.object(controller, '_apply_caption') as apply:
            dialog.show()
            self.app.processEvents()
            self.assertTrue(any(call.args[0] is dialog for call in apply.call_args_list))
            self.assertEqual(dialog.windowFlags(), flags)
            self.assertEqual(dialog.minimumSize(), minimum)
            self.assertEqual(dialog.maximumSize(), maximum)
            self.assertEqual(dialog.windowModality(), Qt.WindowModality.ApplicationModal)
            close.click()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            picker = QFileDialog()
            self.addCleanup(picker.deleteLater)
            self.assertFalse(controller.owns_window(picker))
            for picker_type in (QColorDialog, QFontDialog):
                other_picker = picker_type()
                self.addCleanup(other_picker.deleteLater)
                self.assertFalse(controller.owns_window(other_picker))
            self.assertTrue(controller.owns_window(QMessageBox(dialog)))
            self.assertTrue(controller.owns_window(QMenu(dialog)))
        dialog.close()
        dialog.deleteLater()

    def test_custom_titlebar_buttons_do_not_draw_focus_border(self):
        from src.gui.helpers import configure_titlebar_button
        from PyQt6.QtGui import QIcon
        button = QPushButton()
        configure_titlebar_button(button, lambda *args: QIcon(), DEFAULT_THEME['stroke_color'], close=True)
        self.assertIn('QPushButton:focus', button.styleSheet())
        self.assertIn('border-color: transparent', button.styleSheet())

    def test_theme_update_rethemes_existing_dialog_without_changing_flags(self):
        from src.gui.window_theme import install_window_theme
        controller = install_window_theme(self.app, DEFAULT_THEME)
        dialog = QDialog()
        flags = dialog.windowFlags()
        dialog.show()
        self.app.processEvents()
        with patch.object(controller, '_apply_caption') as apply:
            controller.set_theme(dict(DEFAULT_THEME, titlebar_bg='#eeeeee', text_color='#111111'))
            self.assertTrue(any(call.args[0] is dialog for call in apply.call_args_list))
        self.assertEqual(dialog.windowFlags(), flags)
        dialog.close()
        dialog.deleteLater()


    def test_spinboxes_keep_classic_vertical_buttons(self):
        from src.gui.window_theme import install_window_theme
        from PyQt6.QtWidgets import QSpinBox, QDoubleSpinBox, QStyleOptionSpinBox, QStyle
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import QPoint
        self.app.setStyle('windows11')
        controller = install_window_theme(self.app, DEFAULT_THEME)
        for cls in (QSpinBox, QDoubleSpinBox):
            spin = cls()
            spin.resize(200, 36)
            spin.setRange(0, 10)
            spin.setValue(5)
            spin.show()
            self.app.processEvents()
            opt = QStyleOptionSpinBox()
            spin.initStyleOption(opt)
            up = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt,
                QStyle.SubControl.SC_SpinBoxUp, spin)
            down = spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt,
                QStyle.SubControl.SC_SpinBoxDown, spin)
            self.assertEqual(up.left(), down.left())
            self.assertLess(up.center().y(), down.center().y())
            for rect, expected in ((up, 6), (down, 4)):
                for point in (rect.center(), rect.topLeft() + QPoint(2, 2),
                              rect.bottomRight() - QPoint(2, 2)):
                    spin.setValue(5)
                    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=point)
                    self.assertEqual(spin.value(), expected)
            spin.setButtonSymbols(cls.ButtonSymbols.NoButtons)
            spin.initStyleOption(opt)
            self.assertTrue(spin.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,
                opt, QStyle.SubControl.SC_SpinBoxUp, spin).isEmpty())
            spin.close()
            spin.deleteLater()


if __name__ == '__main__':
    unittest.main()
