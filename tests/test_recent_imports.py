import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.gui import helpers
from src.settings_manager import XuanShuSettings


class RecentImportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings_path = self.root / 'settings.json'
        self.settings = XuanShuSettings(str(self.settings_path))
        self.old_recent = {key: list(value) for key, value in helpers._recent_imports.items()}
        self.old_settings = helpers._settings_ref
        self.addCleanup(self.restore_globals)
        helpers.init_recent_imports(self.settings)
        self.editor = Mock()

    def restore_globals(self):
        helpers._recent_imports.update(self.old_recent)
        helpers._settings_ref = self.old_settings

    def test_missing_file_removed_and_stays_removed_after_reload(self):
        missing = str(self.root / 'missing.txt')
        other = str(self.root / 'other.txt')
        helpers.add_recent('bot', other)
        helpers.add_recent('bot', missing)
        helpers.add_recent('combat', missing)
        helpers._load_recent(missing, self.editor, 'bot')
        self.editor.setPlainText.assert_not_called()
        self.assertEqual(helpers._recent_imports['bot'], [other])
        reloaded = XuanShuSettings(str(self.settings_path))
        self.assertEqual(reloaded.get_recent_imports('bot'), [other])
        self.assertEqual(reloaded.get_recent_imports('combat'), [missing])

    def test_existing_file_loaded_and_promoted(self):
        script = self.root / 'script.txt'
        script.write_text('sleep 1')
        helpers.add_recent('bot', str(script))
        helpers.add_recent('bot', 'other.txt')
        helpers._load_recent(str(script), self.editor, 'bot')
        self.editor.setPlainText.assert_called_once_with('sleep 1')
        self.assertEqual(self.settings.get_recent_imports('bot')[0], str(script))

    def test_other_read_errors_do_not_remove_record(self):
        path = str(self.root / 'script.txt')
        helpers.add_recent('bot', path)
        for error in (PermissionError(), UnicodeDecodeError('utf8', b'\xff', 0, 1, 'invalid')):
            with self.subTest(error=type(error)), patch('builtins.open', side_effect=error):
                helpers._load_recent(path, self.editor, 'bot')
            self.assertEqual(self.settings.get_recent_imports('bot'), [path])
            self.assertEqual(helpers._recent_imports['bot'], [path])
        self.editor.setPlainText.assert_not_called()
