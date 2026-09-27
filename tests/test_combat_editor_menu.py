import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QWidget

from src.gui.helpers import build_shared_svgs, titlebar_svg_icon
from src.gui.widgets import ThemedContextMenuEditor


class CombatEditorMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.ctx = SimpleNamespace(
            stroke_color='#80d8e8', alt_bg='#171823', text_color='#f4f6fb',
            svgs=build_shared_svgs('#80d8e8'),
            titlebar_svg_icon=lambda svg, size=24: titlebar_svg_icon(self.window, svg, size),
        )
        self.editor = ThemedContextMenuEditor(self.ctx)
        self.editor.setPlainText('abc')

    def tearDown(self):
        self.editor.close()
        self.window.close()

    def test_standard_actions_and_shortcuts_are_preserved(self):
        standard = QPlainTextEdit()
        standard.setPlainText('abc')
        expected = standard.createStandardContextMenu()
        menu = self.editor.create_themed_context_menu()
        signature = lambda actions: [
            (action.objectName(), action.text(), action.isSeparator(),
             action.isEnabled(), action.shortcut().toString())
            for action in actions
        ]
        self.assertEqual(signature(menu.actions()), signature(expected.actions()))
        for action in menu.actions():
            if action.objectName() in self.editor._EDIT_ICONS:
                self.assertFalse(action.icon().isNull(), action.objectName())
                for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Disabled):
                    image = action.icon().pixmap(16, 16, mode).toImage()
                    self.assertTrue(any(
                        image.pixelColor(x, y).alpha() > 0
                        for x in range(image.width()) for y in range(image.height())
                    ), (action.objectName(), mode))

    def test_native_edit_actions_still_work(self):
        def action(name):
            menu = self.editor.create_themed_context_menu()
            return next(item for item in menu.actions() if item.objectName() == name)

        action('select-all').trigger()
        self.assertEqual(self.editor.textCursor().selectedText(), 'abc')
        action('edit-copy').trigger()
        self.assertEqual(self.app.clipboard().text(), 'abc')
        action('edit-cut').trigger()
        self.assertEqual(self.editor.toPlainText(), '')
        action('edit-paste').trigger()
        self.assertEqual(self.editor.toPlainText(), 'abc')
        action('edit-undo').trigger()
        self.assertEqual(self.editor.toPlainText(), '')
        action('edit-redo').trigger()
        self.assertEqual(self.editor.toPlainText(), 'abc')
        action('select-all').trigger()
        action('edit-delete').trigger()
        self.assertEqual(self.editor.toPlainText(), '')

    def test_new_menu_uses_current_theme_colors(self):
        first = self.editor.create_themed_context_menu()
        self.assertIn('#171823', first.styleSheet())
        self.ctx.stroke_color = '#ffc66d'
        self.ctx.alt_bg = '#f5f5f5'
        self.ctx.text_color = '#202020'
        self.ctx.svgs = build_shared_svgs(self.ctx.stroke_color)
        second = self.editor.create_themed_context_menu()
        self.assertIn('#f5f5f5', second.styleSheet())
        self.assertIn('#ffc66d', second.styleSheet())
