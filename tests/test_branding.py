import os,sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from src.branding import appdata_dir
class BrandingTests(unittest.TestCase):
 def test_migration_preserves_original_and_new_settings(self):
  with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'APPDATA':tmp}):
   base=Path(tmp);old=base/'Deimos';old.mkdir();(old/'settings.json').write_text('{"x":1}');(old/'default_theme.json').write_text('{}')
   new=base/'XuanShu';new.mkdir();(new/'settings.json').write_text('{"x":2}')
   self.assertEqual(appdata_dir(),new);self.assertEqual((new/'settings.json').read_text(),'{"x":2}');self.assertEqual((old/'settings.json').read_text(),'{"x":1}')
   self.assertTrue((new/'default_theme.json').exists())
   (new/'default_theme.json').unlink();appdata_dir();self.assertFalse((new/'default_theme.json').exists())
 def test_legacy_class_alias(self):
  from src.settings_manager import XuanShuSettings,DeimosSettings
  self.assertIs(XuanShuSettings,DeimosSettings)
 def test_resources_from_other_directory(self):
  from src.gui.helpers import resource_path
  from src.lang import load_lang
  self.assertTrue(Path(resource_path('XuanShu-logo.ico')).is_file())
  self.assertIn('XuanShu',load_lang('en')('license_text'))
 def test_update_asset_names(self):
  from src.updater import get_latest_release
  from types import SimpleNamespace
  payload={'tag_name':'v2.1.4','assets':[{'name':'XuanShu.exe','browser_download_url':'https://example.invalid/XuanShu.exe'},{'name':'XuanShu.exe.sha256','browser_download_url':'https://example.invalid/checksum'}]}
  replies=[SimpleNamespace(raise_for_status=lambda:None,json=lambda:payload),SimpleNamespace(raise_for_status=lambda:None,text='a'*64+'  XuanShu.exe')]
  with patch('src.updater.requests.get',side_effect=replies):
   result=get_latest_release('owner','repo');self.assertTrue(result.exe_url.endswith('/XuanShu.exe'));self.assertEqual(result.sha256,'a'*64)
if __name__=='__main__':unittest.main()
