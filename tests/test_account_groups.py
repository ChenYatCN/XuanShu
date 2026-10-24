import ast
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.settings_manager import XuanShuSettings


class AccountGroupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.original = {'hotkeys': {'custom': None}, 'settings': {
            'private_server_accounts': {'Old': True}, 'unknown': {'keep': [1, 2]},
        }, 'other': 'preserved'}
        self.path.write_text(json.dumps(self.original), encoding='utf8')
        self.settings = XuanShuSettings(self.path)

    def test_legacy_missing_and_invalid_group_use_favorites_without_rewriting(self):
        from src.account_groups import get_account_group
        before = self.path.read_bytes()
        self.assertEqual(get_account_group(self.settings, 'Old'), 'favorites')
        self.assertEqual(self.path.read_bytes(), before)
        self.settings.set_setting('account_groups', {'Old': 'unknown'})
        self.assertEqual(get_account_group(self.settings, 'Old'), 'favorites')

    def test_move_roundtrips_without_changing_other_configuration(self):
        from src.account_groups import get_account_group, set_account_group
        set_account_group(self.settings, 'Old', 'warehouse')
        set_account_group(self.settings, 'Another', 'other')
        reopened = XuanShuSettings(self.path)
        self.assertEqual(get_account_group(reopened, 'Old'), 'warehouse')
        self.assertEqual(get_account_group(reopened, 'Another'), 'other')
        saved = json.loads(self.path.read_text(encoding='utf8'))
        saved['settings'].pop('account_groups')
        self.assertEqual(saved, self.original)
        with self.assertRaises(ValueError):
            set_account_group(self.settings, 'Old', 'all')
        self.assertEqual(get_account_group(reopened, 'Old'), 'warehouse')

    def run_account_command(self, command, data, fail=False):
        from src.account_groups import rename_account_group, remove_account_group, get_account_groups, set_account_group
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf8'))
        case = next(node for node in ast.walk(tree) if isinstance(node, ast.match_case)
                    and isinstance(node.pattern, ast.MatchValue)
                    and isinstance(node.pattern.value, ast.Attribute)
                    and node.pattern.value.attr == command)
        function = ast.AsyncFunctionDef(name='dispatch',
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=case.body, decorator_list=[])
        native = Mock()
        native.has_account.return_value = False
        native.has_account_credential.return_value = True
        if fail:
            native.update_account.side_effect = RuntimeError('rejected')
            native.prompt_save_account.side_effect = RuntimeError('cancelled')
        namespace = dict(asyncio=asyncio, settings=self.settings, wizlaunch=native,
            rename_account_group=rename_account_group, remove_account_group=remove_account_group,
            get_account_groups=get_account_groups, set_account_group=set_account_group,
            com=SimpleNamespace(data=data), set_account_private_server=Mock(),
            logger=Mock(), gui_send_queue=Mock(), launched_account_map={},
            walker=SimpleNamespace(clients=[]), build_account_list_payload=lambda: [],
            xuanshu_gui=SimpleNamespace(GUICommand=Mock(), GUICommandType=SimpleNamespace(
                UpdateAccountList=object(), AccountActionError=object())))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     'XuanShu.py', 'exec'), namespace)
        asyncio.run(namespace['dispatch']())

    def test_successful_backend_rename_and_delete_follow_group_without_touching_others(self):
        self.settings.set_setting('account_groups', {'Old': 'warehouse', 'Keep': 'other'})
        self.run_account_command('UpdateAccount', ('Old', 'New', False, False))
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'),
                         {'New': 'warehouse', 'Keep': 'other'})
        self.run_account_command('DeleteAccount', 'New')
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Keep': 'other'})

    def test_failed_backend_rename_preserves_group(self):
        self.settings.set_setting('account_groups', {'Old': 'warehouse'})
        self.run_account_command('UpdateAccount', ('Old', 'New', False, False), fail=True)
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Old': 'warehouse'})

    def test_malformed_group_map_does_not_break_move_rename_or_delete(self):
        from src.account_groups import get_account_group, set_account_group
        for malformed in ('bad', 17, ['warehouse']):
            with self.subTest(value=malformed):
                self.settings.set_setting('account_groups', malformed)
                self.assertEqual(get_account_group(self.settings, 'Old'), 'favorites')
                self.run_account_command('UpdateAccount', ('Old', 'New', False, False))
                self.run_account_command('DeleteAccount', 'New')
                set_account_group(self.settings, 'Old', 'other')
                self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Old': 'other'})

    def test_custom_group_stable_id_survives_rename_and_reload(self):
        from src.account_groups import create_group, rename_group, get_account_groups, set_account_group, get_account_group
        group = create_group(self.settings, '  仓库二  ')
        set_account_group(self.settings, 'Old', group)
        rename_group(self.settings, group, '  备用仓库  ')
        reopened = XuanShuSettings(self.path)
        self.assertEqual(get_account_groups(reopened)[group], '备用仓库')
        self.assertEqual(get_account_group(reopened, 'Old'), group)
        self.assertEqual(reopened.get_setting('account_groups'), {'Old': group})
        self.assertEqual(reopened.get_setting('unknown'), {'keep': [1, 2]})

    def test_group_names_reject_empty_duplicate_and_reserved_without_writing(self):
        from src.account_groups import create_group, rename_group
        group = create_group(self.settings, 'Vault')
        before = self.path.read_bytes()
        for invalid in ('', '  ', ' 全部 ', 'ALL', ' vault ', '常用'):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                create_group(self.settings, invalid)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            rename_group(self.settings, group, ' 仓库 ')
        self.assertEqual(self.path.read_bytes(), before)

    def test_delete_group_moves_every_assignment_and_resets_current_view_atomically(self):
        from src.account_groups import delete_group, get_account_groups, get_account_group
        self.settings.set_settings({'account_groups': {'Old': 'warehouse', 'Offline': 'warehouse', 'Keep': 'other'},
                                    'launcher_account_group': 'warehouse'})
        delete_group(self.settings, 'warehouse')
        reopened = XuanShuSettings(self.path)
        self.assertNotIn('warehouse', get_account_groups(reopened))
        self.assertEqual(reopened.get_setting('account_groups'),
                         {'Old': 'favorites', 'Offline': 'favorites', 'Keep': 'other'})
        self.assertEqual(reopened.get_setting('launcher_account_group'), 'favorites')
        self.assertEqual(get_account_group(reopened, 'Legacy'), 'favorites')
        self.assertEqual(reopened.get_setting('private_server_accounts'), {'Old': True})

    def test_all_is_not_a_group_and_default_cannot_be_deleted_even_after_rename(self):
        from src.account_groups import delete_group, rename_group, get_account_groups
        rename_group(self.settings, 'favorites', '主账号')
        self.assertEqual(get_account_groups(self.settings)['favorites'], '主账号')
        for group in ('favorites', 'all'):
            with self.assertRaises(ValueError):
                delete_group(self.settings, group)
        with self.assertRaises(ValueError):
            rename_group(self.settings, 'all', '汇总')

    def test_legacy_fixed_groups_are_available_without_rewriting_or_losing_membership(self):
        from src.account_groups import get_account_groups, get_account_group, create_group
        self.settings.set_setting('account_groups', {'Old': 'warehouse', 'Keep': 'other'})
        before = self.path.read_bytes()
        self.assertEqual(get_account_groups(self.settings),
                         {'favorites': '常用', 'warehouse': '仓库', 'other': '其他'})
        self.assertEqual(get_account_group(self.settings, 'Old'), 'warehouse')
        self.assertEqual(self.path.read_bytes(), before)
        create_group(self.settings, '新组')
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Old': 'warehouse', 'Keep': 'other'})

    def test_successful_save_and_edit_record_selected_group_only_after_native_success(self):
        from src.account_groups import create_group, get_account_group
        group = create_group(self.settings, '仓库二')
        self.run_account_command('SaveAccount', ('Saved', False, False, group))
        self.assertEqual(get_account_group(XuanShuSettings(self.path), 'Saved'), group)
        self.settings.set_setting('account_groups', {'Old': 'warehouse'})
        self.run_account_command('UpdateAccount', ('Old', 'New', False, False, group))
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'New': group})

    def test_cancelled_save_and_failed_edit_leave_group_assignment_untouched(self):
        from src.account_groups import create_group
        group = create_group(self.settings, '仓库二')
        self.settings.set_setting('account_groups', {'Old': 'warehouse'})
        self.run_account_command('SaveAccount', ('Saved', False, False, group), fail=True)
        self.run_account_command('UpdateAccount', ('Old', 'New', False, False, group), fail=True)
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Old': 'warehouse'})

    def test_group_deleted_during_account_save_falls_back_to_default(self):
        from src.account_groups import create_group, delete_group
        group = create_group(self.settings, '临时分组')
        delete_group(self.settings, group)
        self.run_account_command('SaveAccount', ('Saved', False, False, group))
        self.assertEqual(XuanShuSettings(self.path).get_setting('account_groups'), {'Saved': 'favorites'})


if __name__ == '__main__':
    unittest.main()
