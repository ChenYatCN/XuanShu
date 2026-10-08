import ast
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import wizlaunch


ROOT = Path(__file__).resolve().parents[1]


class WizlaunchAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((ROOT / 'XuanShu.spec').read_text(encoding='utf-8'))
        cls.guard = ast.Module(body=[
            node for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in {
                    '_required_wizlaunch_api', '_missing_wizlaunch_api',
                } for target in node.targets)
            ) or (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == '_missing_wizlaunch_api'
            )
        ], type_ignores=[])

    def check_module(self, module):
        namespace = {'_wizlaunch': module}
        exec(compile(self.guard, 'XuanShu.spec', 'exec'), namespace)
        return namespace['_required_wizlaunch_api']

    def test_installed_native_exports_required_account_api(self):
        required = self.check_module(wizlaunch)
        for name in required:
            self.assertTrue(callable(getattr(wizlaunch, name)), name)
        self.assertIn('create_steam_account', required)

    def test_old_same_version_module_is_rejected(self):
        old = SimpleNamespace(__version__='0.3.1', **{
            name: lambda: None for name in (
                'set_account_steam', 'get_account_steam', 'validate_account',
                'get_window_config', 'set_window_config', 'clear_window_config',
                'launch_instances',
            )
        })
        with self.assertRaisesRegex(RuntimeError, 'create_steam_account') as error:
            self.check_module(old)
        self.assertIn('maturin develop --release', str(error.exception))

    def test_missing_steam_creation_alone_blocks_packaging(self):
        required = self.check_module(wizlaunch)
        module = SimpleNamespace(**{
            name: getattr(wizlaunch, name)
            for name in required if name != 'create_steam_account'
        })
        with self.assertRaisesRegex(RuntimeError, 'create_steam_account'):
            self.check_module(module)

    def test_native_steam_creation_rejects_empty_without_writing_metadata(self):
        with tempfile.TemporaryDirectory() as appdata:
            with patch.dict(os.environ, {'APPDATA': appdata}):
                with self.assertRaisesRegex(RuntimeError, 'nickname is empty'):
                    wizlaunch.create_steam_account('')
            self.assertEqual(list(Path(appdata).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
