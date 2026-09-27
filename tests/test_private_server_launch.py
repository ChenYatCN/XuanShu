import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class SettingsStub:
    def __init__(self):
        self.values = {"private_server_accounts": {}}

    def get_setting(self, key):
        return self.values[key]

    def set_setting(self, key, value):
        self.values[key] = value


class PrivateServerLaunchTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path("XuanShu.py").read_text(encoding="utf-8"))
        names = {
            "account_uses_private_server",
            "set_account_private_server",
            "launch_account_instance",
        }
        functions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        self.settings = SettingsStub()
        self.launch = Mock(return_value=123)
        self.native = SimpleNamespace(launch_instance=self.launch)
        namespace = {
            "settings": self.settings,
            "wizlaunch": self.native,
            "logger": SimpleNamespace(info=Mock()),
            "PRIVATE_LOGIN_SERVER": "102.134.49.191:12000",
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), "XuanShu.py", "exec"), namespace)
        self.get_private = namespace["account_uses_private_server"]
        self.set_private = namespace["set_account_private_server"]
        self.start = namespace["launch_account_instance"]

    def test_private_account_uses_existing_server_override(self):
        self.set_private("Friend", True)
        self.assertTrue(self.get_private("Friend"))
        self.assertEqual(self.start("Friend", "D:/Game"), 123)
        self.launch.assert_called_once_with(
            "Friend", "D:/Game", login_server="102.134.49.191:12000"
        )

    def test_official_account_keeps_default_launch(self):
        self.start("Official", "D:/Game")
        self.launch.assert_called_once_with("Official", "D:/Game")

    def test_rename_moves_private_setting(self):
        self.set_private("Old", True)
        self.set_private("New", True, "Old")
        self.assertFalse(self.get_private("Old"))
        self.assertTrue(self.get_private("New"))

    def test_native_mode_is_reused_when_available(self):
        self.native.get_account_private = Mock(return_value=True)
        self.native.set_account_private = Mock()
        self.assertTrue(self.get_private("Native"))
        self.set_private("Native", False)
        self.native.set_account_private.assert_called_once_with("Native", False)
        self.assertFalse(self.get_private("Native"))


if __name__ == "__main__":
    unittest.main()
