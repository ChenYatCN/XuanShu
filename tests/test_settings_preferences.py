"""Saved user preferences control Finder and post-battle collection at their callers."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton, QWidget

from src.gui.commands import GUICommandType
from src.gui.settings_dialog import show_settings_dialog
from src.lang import load_lang
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME, XuanShuSettings
from src.sigil import Sigil
from tests import test_mainline_recovery as recovery_fixtures


def settings_context(settings):
    return SimpleNamespace(
        tl=load_lang('zh'), settings=settings, window=QWidget(),
        bg_color=DEFAULT_THEME['bg_color'], text_color=DEFAULT_THEME['text_color'],
        alt_bg=DEFAULT_THEME['alt_bg'], btn_color_hex=DEFAULT_THEME['button_color'],
        btn_style='', icon_btn_style='', svgs={'reset': '', 'import': ''},
        titlebar_svg_icon=lambda *_: QIcon(), exports={}, send_queue=queue.Queue(),
        gui_font='Segoe UI', gui_font_size=9)


def checkbox(dialog, ctx, key):
    return next(box for box in dialog.findChildren(QCheckBox)
                if box.text() == ctx.tl('setting_' + key))


def click_button(dialog, ctx, key):
    next(button for button in dialog.findChildren(QPushButton)
         if button.text() == ctx.tl(key)).click()


class PreferenceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_configuration_uses_compatible_defaults_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            old = {'hotkeys': {}, 'settings': {'mainline_finder_enabled': True, 'use_potions': True}}
            path.write_text(json.dumps(old), encoding='utf8')
            before = path.read_bytes()
            settings = XuanShuSettings(path)
            self.assertIs(settings.get_setting('quest_finder_enabled'), True)
            self.assertIs(settings.get_setting('post_combat_wisps_enabled'), False)
            self.assertIs(settings.get_settings()['quest_finder_enabled'], True)
            self.assertIs(settings.get_settings()['post_combat_wisps_enabled'], False)
            self.assertEqual(path.read_bytes(), before)

    def test_save_applies_both_preferences_but_cancel_does_not_write_or_emit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = XuanShuSettings(path)
            settings.set_setting('mainline_finder_enabled', True)
            ctx = settings_context(settings)
            self.addCleanup(ctx.window.close)
            def save(dialog):
                finder = checkbox(dialog, ctx, 'quest_finder_enabled')
                wisps = checkbox(dialog, ctx, 'post_combat_wisps_enabled')
                self.assertTrue(finder.isChecked())
                self.assertTrue(finder.isEnabled())
                self.assertFalse(wisps.isChecked())
                before = path.read_bytes()
                finder.setChecked(False)
                wisps.setChecked(True)
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(ctx.send_queue.empty())
                click_button(dialog, ctx, 'settings_save')
                return 0
            with patch.object(settings, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', save):
                show_settings_dialog(ctx)
            stored = XuanShuSettings(path)
            self.assertFalse(stored.get_setting('quest_finder_enabled'))
            self.assertTrue(stored.get_setting('post_combat_wisps_enabled'))
            command = ctx.send_queue.get_nowait()
            self.assertEqual(command.com_type, GUICommandType.UpdateSettings)
            self.assertEqual(command.data, {'quest_finder_enabled': False, 'post_combat_wisps_enabled': True})
            before = path.read_bytes()
            ctx.settings = stored
            def cancel(dialog):
                self.assertFalse(checkbox(dialog, ctx, 'quest_finder_enabled').isChecked())
                self.assertTrue(checkbox(dialog, ctx, 'post_combat_wisps_enabled').isChecked())
                checkbox(dialog, ctx, 'quest_finder_enabled').setChecked(True)
                checkbox(dialog, ctx, 'post_combat_wisps_enabled').setChecked(False)
                checkbox(dialog, ctx, 'mainline_finder_enabled').setChecked(False)
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(ctx.send_queue.empty())
                click_button(dialog, ctx, 'settings_cancel')
                return 0
            with patch.object(stored, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', cancel):
                show_settings_dialog(ctx)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(ctx.send_queue.empty())

    def test_parent_disabled_preserves_saved_child_preference_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = XuanShuSettings(Path(directory) / 'settings.json')
            settings.set_settings({'mainline_finder_enabled': True, 'quest_finder_enabled': True})
            ctx = settings_context(settings)
            self.addCleanup(ctx.window.close)
            def disable_parent(dialog):
                parent = checkbox(dialog, ctx, 'mainline_finder_enabled')
                child = checkbox(dialog, ctx, 'quest_finder_enabled')
                parent.setChecked(False)
                self.assertFalse(child.isEnabled())
                self.assertTrue(child.isChecked())
                click_button(dialog, ctx, 'settings_save')
                return 0
            with patch.object(settings, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', disable_parent):
                show_settings_dialog(ctx)
            self.assertFalse(settings.get_setting('mainline_finder_enabled'))
            self.assertTrue(settings.get_setting('quest_finder_enabled'))
            self.assertEqual(ctx.send_queue.get_nowait().data, {'mainline_finder_enabled': False})
            def reopen(dialog):
                parent = checkbox(dialog, ctx, 'mainline_finder_enabled')
                child = checkbox(dialog, ctx, 'quest_finder_enabled')
                self.assertFalse(child.isEnabled())
                self.assertTrue(child.isChecked())
                parent.setChecked(True)
                self.assertTrue(child.isEnabled())
                self.assertTrue(child.isChecked())
                click_button(dialog, ctx, 'settings_cancel')
                return 0
            with patch.object(settings, 'get_theme', return_value=dict(DEFAULT_THEME)), \
                    patch.object(QDialog, 'exec', reopen):
                show_settings_dialog(ctx)
            self.assertTrue(ctx.send_queue.empty())


class FinderPreferenceTests(unittest.IsolatedAsyncioTestCase):
    setUp = recovery_fixtures.MainlineRecoveryTests.setUp
    initialize = recovery_fixtures.MainlineRecoveryTests.initialize
    runtime = recovery_fixtures.MainlineRecoveryTests.runtime
    tick = recovery_fixtures.MainlineRecoveryTests.tick
    recover = recovery_fixtures.MainlineRecoveryTests.recover

    async def test_disabled_finder_does_not_click_system_card_and_closes_book(self):
        await self.initialize()
        self.client.quest_finder_enabled = False
        self.client.tracked = 77
        self.client.pages = [['任务搜寻']]
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertFalse(self.client.book_open)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertTrue(self.client.mainline_finder_enabled)

    async def test_disabled_finder_still_restores_an_owned_mainline_card(self):
        await self.initialize()
        self.client.quest_finder_enabled = False
        self.client.tracked = 77
        self.client.pages = [['原主线']]
        await self.recover()
        self.assertEqual(self.client.tracked, 42)
        self.assertFalse(self.client.book_open)

    async def test_disabled_finder_does_not_exhaust_recovery_before_reenable(self):
        await self.initialize()
        self.client.quest_finder_enabled = False
        self.client.tracked = 77
        self.client.pages = [['任务搜寻']]
        for _ in range(4):
            await self.recover(times=2)
            state = self.client._xuanshu_mainline_recovery
            self.assertEqual(state['attempts'], 0)
            self.assertEqual(self.client.tracked, 77)
            self.assertFalse(self.client.book_open)
            self.clock += 61  # Advance the existing recovery cooldown.
        self.client.quest_finder_enabled = True
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 999)

    async def test_missing_preference_keeps_existing_finder_behavior(self):
        await self.initialize()
        self.client.tracked = 77
        self.client.pages = [['任务搜寻']]
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 999)

    async def test_turning_off_finder_during_last_read_blocks_the_click(self):
        await self.initialize()
        self.client.quest_finder_enabled = True
        self.client.tracked = 77
        self.client.pages = [['任务搜寻']]
        recovery = self.runtime()
        original = recovery.finder_task_state
        async def disabled_during_read(client):
            result = await original(client)
            client.quest_finder_enabled = False
            return result
        with patch.object(recovery, 'finder_task_state', disabled_during_read):
            await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertFalse(self.client.book_open)


def app_tree():
    return ast.parse(Path('XuanShu.py').read_text(encoding='utf8'))


def async_driver(body, namespace, argument):
    driver = ast.parse(f'async def run({argument}):\n    pass\n')
    driver.body[0].body = copy.deepcopy(body)
    exec(compile(ast.fix_missing_locations(driver), 'XuanShu.py', 'exec'), namespace)
    return namespace['run']


class PreferenceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_clients_receive_the_current_saved_preferences(self):
        from tests.test_ibao_simple_restart import install_real_initializer
        from tests.test_ibao_restart_lifecycle import launcher_namespace
        for finder, wisps in ((True, False), (False, True)):
            with self.subTest(finder=finder, wisps=wisps):
                namespace = launcher_namespace()
                install_real_initializer(namespace)
                namespace.update(quest_finder_enabled=finder, post_combat_wisps_enabled=wisps)
                client = namespace['walker'].client_cls(22)
                namespace['launched_account_map'][22] = 'account'
                namespace['wizlaunch'].update_player_gid = Mock()
                client.client_object = SimpleNamespace(speed_multiplier=AsyncMock(return_value=27))
                client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=170))
                client.game_client = SimpleNamespace(user_id=AsyncMock(return_value=202))
                await namespace['_init_client_attrs'](client)
                self.assertIs(client.quest_finder_enabled, finder)
                self.assertIs(client.post_combat_wisps_enabled, wisps)

    async def test_saved_update_command_changes_existing_clients_in_both_directions(self):
        tree = app_tree()
        case = next(case for node in ast.walk(tree) if isinstance(node, ast.Match)
                    for case in node.cases if isinstance(case.pattern, ast.MatchValue)
                    and isinstance(case.pattern.value, ast.Attribute)
                    and case.pattern.value.attr == 'UpdateSettings')
        clients = [SimpleNamespace(quest_finder_enabled=True, post_combat_wisps_enabled=False),
                   SimpleNamespace(quest_finder_enabled=True, post_combat_wisps_enabled=False)]
        namespace = {'walker': SimpleNamespace(clients=clients), 'logger': Mock(),
                     'quest_finder_enabled': True, 'post_combat_wisps_enabled': False,
                     'cancel_mainline_recovery': Mock()}
        apply = async_driver(case.body, namespace, 'com')
        for finder, wisps in ((False, True), (True, False)):
            await apply(SimpleNamespace(data={'quest_finder_enabled': finder,
                                             'post_combat_wisps_enabled': wisps}))
            self.assertIs(namespace['quest_finder_enabled'], finder)
            self.assertIs(namespace['post_combat_wisps_enabled'], wisps)
            for client in clients:
                self.assertIs(client.quest_finder_enabled, finder)
                self.assertIs(client.post_combat_wisps_enabled, wisps)
        await apply(SimpleNamespace(data={'mainline_finder_enabled': False}))
        for client in clients:
            self.assertFalse(client.mainline_finder_enabled)
            self.assertTrue(client.quest_finder_enabled)

    async def test_post_battle_switch_controls_collection_but_preserves_safe_return(self):
        tree = app_tree()
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                     and ast.unparse(node.test) == 'p.just_left_combat and await is_free(p)')
        for enabled in (None, False, True):
            with self.subTest(enabled=enabled):
                collect, clear = AsyncMock(), AsyncMock()
                client = SimpleNamespace(title='p1', just_left_combat=True, process_id=1,
                                         teleport=AsyncMock(), original_location_before_combat='saved')
                if enabled is not None:
                    client.post_combat_wisps_enabled = enabled
                namespace = dict(asyncio=SimpleNamespace(sleep=AsyncMock()), logger=Mock(),
                    is_free=AsyncMock(return_value=True), clear_post_combat_phase=clear,
                    collect_wisps_with_limit=collect, dungeon_return_is_current=AsyncMock(return_value=True),
                    original_client_locations={1: 'saved'})
                await async_driver([block], namespace, 'p')(client)
                if enabled:
                    collect.assert_awaited_once_with(client, limit=2)
                else:
                    collect.assert_not_awaited()
                clear.assert_awaited_once_with(client)
                client.teleport.assert_awaited_once_with('saved')
                self.assertFalse(client.quest_party_safe_return_active)

    async def test_sigil_post_battle_collection_uses_preference_and_explicit_opt_out(self):
        for enabled, requested, expected in ((None, True, 0), (False, True, 0),
                                             (True, True, 1), (True, False, 0)):
            with self.subTest(enabled=enabled, requested=requested):
                client = SimpleNamespace(in_battle=AsyncMock(return_value=False))
                if enabled is not None:
                    client.post_combat_wisps_enabled = enabled
                with patch('src.sigil.collect_wisps', AsyncMock()) as collect:
                    await Sigil(client, [client], None).wait_for_combat_finish(
                        await_combat=False, should_collect_wisps=requested)
                self.assertEqual(collect.await_count, expected)

    async def test_short_sigil_post_battle_collection_cannot_bypass_the_preference(self):
        tree = ast.parse(Path('src/sigil.py').read_text(encoding='utf8'))
        for name in ('solo_farming_logic', 'leader_farming_logic'):
            method = next(node for node in ast.walk(tree)
                          if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            branch = next(node for node in ast.walk(method) if isinstance(node, ast.If)
                          and ast.unparse(node.test) == 'await self.read_quest_name() == self.original_quest')
            start = next(i for i, node in enumerate(branch.body)
                         if ast.unparse(node) == 'await self.wait_for_combat_finish()') + 1
            end = next(i for i, node in enumerate(branch.body)
                       if 'teleport(after_xyz)' in ast.unparse(node))
            for enabled in (False, True):
                with self.subTest(method=name, enabled=enabled):
                    clients = [SimpleNamespace(post_combat_wisps_enabled=enabled, teleport=AsyncMock()),
                               SimpleNamespace(post_combat_wisps_enabled=False, teleport=AsyncMock())]
                    sigil = Sigil(clients[0], clients, None)
                    collect = AsyncMock()
                    namespace = dict(asyncio=SimpleNamespace(sleep=AsyncMock(), gather=asyncio.gather),
                                     collect_wisps=collect, calc_FrontalVector=AsyncMock(return_value='after'))
                    await async_driver(branch.body[start:end + 1], namespace, 'self')(sigil)
                    if enabled:
                        collect.assert_awaited_once_with(clients[0])
                    else:
                        collect.assert_not_awaited()
                    clients[0].teleport.assert_awaited_once_with('after')


if __name__ == '__main__':
    unittest.main()
