import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.branding import appdata_dir, runtime_data_dir, runtime_dir
from src.collect_catalog import cache_directory
from src.updater import update_dir


class RuntimeDataPathsTests(unittest.TestCase):
    def test_generated_data_moves_but_settings_stay_in_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roaming = root / 'roaming'
            settings = roaming / 'XuanShu' / 'settings.json'
            settings.parent.mkdir(parents=True)
            settings.write_text('{"keep": true}', encoding='utf-8')
            run = root / 'run'
            with patch('src.branding.runtime_dir', return_value=run), patch.dict(
                os.environ, {'APPDATA': str(roaming)}
            ):
                self.assertEqual(appdata_dir(), settings.parent)
                self.assertEqual(settings.read_text(encoding='utf-8'), '{"keep": true}')
                data = run / 'XuanShuData'
                self.assertEqual(runtime_data_dir(), data)
                self.assertEqual(cache_directory(), data / 'collect_cache')
                self.assertEqual(update_dir(), data / 'update')

    def test_frozen_build_uses_exe_folder(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            sys, 'frozen', True, create=True
        ), patch.object(sys, 'executable', str(Path(tmp) / 'XuanShu.exe')):
            self.assertEqual(runtime_dir(), Path(tmp))


if __name__ == '__main__':
    unittest.main()
