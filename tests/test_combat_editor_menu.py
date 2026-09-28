import os
import inspect
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtCore import Qt

from src.gui.tab_actions import build_bot_tab, build_combat_tab
from src.gui.widgets import ThemedPlainTextEdit


class CombatEditorMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ctx = SimpleNamespace(stroke_color='#78dce8')
        self.editor = ThemedPlainTextEdit(self.ctx)
        self.editor.setPlainText('abc')

    def tearDown(self):
        self.editor.close()

    def test_combat_and_script_use_the_same_themed_native_editor(self):
        self.assertIn('editor = ThemedPlainTextEdit(ctx)', inspect.getsource(build_combat_tab))
        self.assertIn('editor = ThemedPlainTextEdit(ctx)', inspect.getsource(build_bot_tab))

    def test_all_edit_icons_follow_enabled_state_and_current_theme(self):
        class EditorWithNativeIcons(ThemedPlainTextEdit):
            def createStandardContextMenu(self):
                menu = super().createStandardContextMenu()
                source = QPixmap(16, 16)
                source.fill(Qt.GlobalColor.transparent)
                painter = QPainter(source)
                painter.fillRect(4, 4, 8, 8, QColor('#000000'))
                painter.end()
                for action in menu.actions():
                    if not action.isSeparator():
                        action.setIcon(QIcon(source))
                return menu

        editor = EditorWithNativeIcons(self.ctx)
        self.addCleanup(editor.close)
        editor.setPlainText('abc')
        names = {'edit-undo', 'edit-redo', 'edit-cut', 'edit-copy',
                 'edit-paste', 'edit-delete', 'select-all'}

        def check_menu():
            menu = editor.create_themed_context_menu()
            actions = {action.objectName(): action for action in menu.actions()
                       if action.objectName() in names}
            self.assertEqual(set(actions), names)
            disabled = menu.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            ).name()
            for action in actions.values():
                mode = QIcon.Mode.Normal if action.isEnabled() else QIcon.Mode.Disabled
                pixel = action.icon().pixmap(16, 16, mode).toImage().pixelColor(8, 8).name()
                self.assertEqual(pixel, self.ctx.stroke_color if action.isEnabled() else disabled)
            return actions

        initial = check_menu()
        self.assertFalse(initial['edit-undo'].isEnabled())
        editor.insertPlainText('d')
        editor.selectAll()
        self.app.clipboard().setText('paste')
        active = check_menu()
        for name in ('edit-undo', 'edit-cut', 'edit-copy', 'edit-paste',
                     'edit-delete', 'select-all'):
            self.assertTrue(active[name].isEnabled(), name)
        editor.undo()
        self.assertTrue(check_menu()['edit-redo'].isEnabled())
        self.ctx.stroke_color = '#32c9df'
        check_menu()

    def test_native_edit_actions_still_work(self):
        def action(name):
            menu = self.editor.createStandardContextMenu()
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
