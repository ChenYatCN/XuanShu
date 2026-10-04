import asyncio
import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.game_language import GameLanguageError, apply_game_language, installed_language_patches
from src.gui.commands import GUICommand, GUICommandType
from tests.test_collect_catalog import lang, wad


class GameLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.install = Path(self.temp.name)
        self.data = self.install / 'Data' / 'GameData'
        self.data.mkdir(parents=True)
        self.root = self.data / 'Root.wad'
        self.target = self.data / 'Locale_en-US-root.wad'
        self.source = self.data / 'Locale_en-US-root.wad.d'
        wad(self.root, [('Locale/en-US/Test.lang', lang('Test', [('1', '', 'English')]), True)])
        wad(self.target, [('Fonts/Test.ttf', b'font', False)])
        wad(self.source, [('Locale/en-US/Test.lang', lang('Test', [('1', 'English', '中文')]), True)])
        self.root_bytes = self.root.read_bytes()
        self.original = self.target.read_bytes()
        self.patch_bytes = self.source.read_bytes()

    def apply(self, language, running=lambda: []):
        return apply_game_language(str(self.install), language, self.source.name, running)

    def test_chinese_preserves_original_and_source(self):
        backup = self.apply('zh')
        self.assertEqual(Path(backup).read_bytes(), self.original)
        self.assertEqual(self.target.read_bytes(), self.patch_bytes)
        self.assertEqual(self.source.read_bytes(), self.patch_bytes)
        self.assertEqual(self.root.read_bytes(), self.root_bytes)
        self.assertEqual(list(self.data.glob('*.tmp')), [])

    def test_english_disables_overlay_recoverably(self):
        backup = self.apply('en')
        self.assertFalse(self.target.exists())
        self.assertEqual(Path(backup).read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.patch_bytes)
        self.assertEqual(self.root.read_bytes(), self.root_bytes)
        self.assertIsNone(self.apply('en'))
        self.apply('zh')
        self.assertEqual(self.target.read_bytes(), self.patch_bytes)

    def test_open_unhooked_client_blocks_changes(self):
        for language in ('en', 'zh'):
            with self.assertRaisesRegex(GameLanguageError, 'close_clients'):
                self.apply(language, lambda: [123])
            self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(list(self.data.glob('*.bak')), [])

    def test_unchanged_saved_language_allows_second_account(self):
        self.apply('zh')
        self.assertIsNone(self.apply('zh', lambda: [123]))
        self.assertEqual(len(list(self.data.glob('*.bak'))), 1)

    def test_no_chinese_patch_does_not_destroy_current_overlay(self):
        wad(self.source, [('Fonts/Test.ttf', b'font', False)])
        with self.assertRaisesRegex(GameLanguageError, 'not_chinese'):
            self.apply('zh')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_missing_and_invalid_resources_do_not_mutate(self):
        self.source.unlink()
        with self.assertRaisesRegex(GameLanguageError, 'missing_patch'):
            self.apply('zh')
        with self.assertRaisesRegex(GameLanguageError, 'invalid_resource'):
            apply_game_language(str(self.install), 'zh', '../Root.wad', lambda: [])
        with self.assertRaisesRegex(GameLanguageError, 'invalid_path'):
            apply_game_language(str(self.install / 'missing'), 'en', self.source.name, lambda: [])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_copy_or_replace_failure_keeps_active_file(self):
        for operation in ('shutil.copy2', 'os.replace'):
            with patch('src.game_language.' + operation, side_effect=OSError('simulated write failure')):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    self.apply('zh')
            self.assertEqual(self.target.read_bytes(), self.original)
            self.assertEqual(list(self.data.glob('*.tmp')), [])

    def test_client_opening_during_copy_blocks_commit(self):
        calls = iter([[], [123]])
        with self.assertRaisesRegex(GameLanguageError, 'close_clients'):
            self.apply('zh', lambda: next(calls))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(list(self.data.glob('*.tmp')), [])

    def test_story_and_legacy_names_are_supported(self):
        source = self.data / 'Locale_English-Root.wad.r'
        source.write_bytes(self.patch_bytes)
        apply_game_language(str(self.install), 'zh', source.name, lambda: [])
        self.assertEqual((self.data / 'Locale_English-Root.wad').read_bytes(), self.patch_bytes)
        self.assertEqual(installed_language_patches(str(self.install)), [self.source.name, source.name])


def backend_case(name):
    """Execute the actual command branch without starting the game/backend."""
    source = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
    case = next(case for node in ast.walk(source) if isinstance(node, ast.Match)
                for case in node.cases if isinstance(case.pattern, ast.MatchValue)
                and ast.unparse(case.pattern.value).endswith('.' + name))
    class EndCommand(ast.NodeTransformer):
        def visit_Continue(self, node):
            # In production this ends the current queued command; the fixture
            # executes just one command, not the surrounding infinite queue loop.
            return ast.copy_location(ast.Return(value=None), node)

    case.body = [EndCommand().visit(statement) for statement in case.body]
    function = ast.AsyncFunctionDef(name='branch', args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[ast.While(test=ast.Constant(True), body=case.body + [ast.Break()], orelse=[])],
        decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    return compile(module, 'XuanShu.py', 'exec')


class GameLanguageBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_setting_persists_only_after_success(self):
        for language, failure in (('zh', False), ('zh', True), (None, False)):
            settings, queue = Mock(), Mock()
            apply = Mock(side_effect=GameLanguageError('game_language_missing_patch') if failure else None)
            namespace = dict(asyncio=asyncio, os=os, utils=SimpleNamespace(get_all_wizard_handles=lambda: []),
                com=SimpleNamespace(data={'game_path': 'test', 'language': language, 'patch_name': 'Locale_en-US-root.wad.d'}),
                GameLanguageError=GameLanguageError, apply_game_language=apply, settings=settings,
                logger=Mock(), gui_send_queue=queue,
                xuanshu_gui=SimpleNamespace(GUICommand=GUICommand, GUICommandType=GUICommandType))
            exec(backend_case('SetGameLanguage'), namespace)
            await namespace['branch']()
            self.assertEqual(settings.set_settings.called, not failure)
            self.assertEqual(queue.put.call_args.args[0].data['ok'], not failure)
            self.assertEqual(apply.called, language is not None)

    async def test_launcher_applies_after_verification_and_failure_stops_launch(self):
        calls = []
        settings = Mock()
        settings.get_setting.side_effect = {'game_language': 'zh', 'verify_patch_files': True,
            'game_language_patch': 'Locale_en-US-root.wad.d',
            'game_language_path': os.path.normcase(os.path.abspath('test'))}.get
        def verify(path):
            calls.append('verify')
            return True
        def apply(*args):
            calls.append('language')
            raise GameLanguageError('game_language_missing_patch')
        launch = Mock()
        namespace = dict(asyncio=asyncio, os=os, settings=settings, com=SimpleNamespace(data=(['one'], 'test')),
            utils=SimpleNamespace(override_wiz_install_location=Mock(), get_all_wizard_handles=lambda: []),
            walker=SimpleNamespace(clients=[]), launched_account_map={}, wizlaunch=Mock(), logger=Mock(),
            wizpatch_runner=SimpleNamespace(patch_game_files=verify), apply_game_language=apply,
            GameLanguageError=GameLanguageError, gui_send_queue=Mock(), released_handles=set(),
            launch_account_instance=launch,
            xuanshu_gui=SimpleNamespace(GUICommand=GUICommand, GUICommandType=GUICommandType))
        exec(backend_case('LaunchInstance'), namespace)
        await namespace['branch']()
        self.assertEqual(calls, ['verify', 'language'])
        launch.assert_not_called()
