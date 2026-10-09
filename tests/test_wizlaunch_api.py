import ast
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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

    def test_steam_metadata_without_credentials_is_not_launch_ready(self):
        with tempfile.TemporaryDirectory() as appdata:
            with patch.dict(os.environ, {'APPDATA': appdata}):
                nickname = 'steam-login-validation-test'
                wizlaunch.create_steam_account(nickname)
                self.assertTrue(wizlaunch.get_account_steam(nickname))
                self.assertIn('credentials are missing', wizlaunch.validate_account(nickname))

    def test_saving_steam_account_collects_credentials_before_mode_flag(self):
        tree = ast.parse((ROOT / 'XuanShu.py').read_text(encoding='utf-8'))
        case = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.match_case)
                    and isinstance(node.pattern, ast.MatchValue)
                    and isinstance(node.pattern.value, ast.Attribute)
                    and node.pattern.value.attr == 'SaveAccount')
        function = ast.AsyncFunctionDef(name='save_account',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                               kw_defaults=[], defaults=[]),
            body=case.body, decorator_list=[])
        native = SimpleNamespace(has_account=Mock(return_value=False),
            prompt_save_account=Mock(), set_account_steam=Mock(),
            create_steam_account=Mock())
        calls = Mock()
        calls.attach_mock(native.prompt_save_account, 'credentials')
        calls.attach_mock(native.set_account_steam, 'mode')
        namespace = dict(asyncio=asyncio, wizlaunch=native,
            com=SimpleNamespace(data=('Steam', True, False)),
            set_account_private_server=Mock(), logger=Mock(), gui_send_queue=Mock(),
            build_account_list_payload=Mock(return_value=[]),
            xuanshu_gui=SimpleNamespace(GUICommand=Mock(),
                GUICommandType=SimpleNamespace(UpdateAccountList=object())))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     'XuanShu.py', 'exec'), namespace)
        asyncio.run(namespace['save_account']())
        self.assertEqual([call[0] for call in calls.mock_calls], ['credentials', 'mode'])
        native.prompt_save_account.assert_called_once_with('Steam')
        native.set_account_steam.assert_called_once_with('Steam', True)
        native.create_steam_account.assert_not_called()


if __name__ == '__main__':
    unittest.main()
