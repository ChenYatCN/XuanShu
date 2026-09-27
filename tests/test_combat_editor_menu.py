import os
import inspect
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from src.gui.tab_actions import build_bot_tab, build_combat_tab


class CombatEditorMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = QPlainTextEdit()
        self.editor.setPlainText('abc')

    def tearDown(self):
        self.editor.close()

    def test_combat_and_script_use_the_same_native_editor(self):
        self.assertIn('editor = QPlainTextEdit()', inspect.getsource(build_combat_tab))
        self.assertIn('editor = QPlainTextEdit()', inspect.getsource(build_bot_tab))

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
