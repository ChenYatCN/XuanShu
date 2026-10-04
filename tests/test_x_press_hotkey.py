import ast
import asyncio
import configparser
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from wizwalker import Keycode, ModifierKeys
from src.gui.actions import ActionRegistry
from src.gui.commands import GUICommandType
from src.settings_manager import XuanShuSettings


class XPressHotkeyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.settings = XuanShuSettings(str(self.path))

    def test_defaults_and_reset_leave_plain_x_available(self):
        expected = {'key': 'X', 'modifiers': []}
        self.assertEqual(self.settings.get_hotkeys()['x_press'], expected)
        self.settings.set_hotkey('x_press', 'F10', [])
        self.settings.reset_hotkeys()
        self.assertEqual(self.settings.get_hotkeys()['x_press'], expected)

    def test_existing_bare_x_and_other_settings_survive(self):
        self.settings.set_hotkey('x_press', 'X', [])
        self.settings.set_hotkey('toggle_speed', 'F10', ['SHIFT'])
        self.settings.set_setting('fusion_result_side', 'left')
        loaded = XuanShuSettings(str(self.path))
        self.assertEqual(loaded.get_hotkeys()['x_press'], {'key': 'X', 'modifiers': []})
        self.assertEqual(loaded.get_hotkeys()['toggle_speed'], {'key': 'F10', 'modifiers': ['SHIFT']})
        self.assertEqual(loaded.get_setting('fusion_result_side'), 'left')
        self.assertEqual(XuanShuSettings(str(self.path)).get_hotkeys(), loaded.get_hotkeys())

    def test_custom_x_binding_and_unbound_are_preserved(self):
        for key, mods in [('X', ['CTRL']), ('F10', []), (None, None)]:
            if key is None:
                self.settings.clear_hotkey('x_press')
            else:
                self.settings.set_hotkey('x_press', key, mods)
            expected = None if key is None else {'key': key, 'modifiers': mods}
            self.assertEqual(XuanShuSettings(str(self.path)).get_hotkeys()['x_press'], expected)

    def test_legacy_ini_x_import_keeps_bare_x(self):
        parser = configparser.ConfigParser()
        parser.read_string('[hotkeys]\nx_press=X\n')
        self.settings.migrate_from_ini(parser)
        self.assertEqual(self.settings.get_hotkeys()['x_press'], {'key': 'X', 'modifiers': []})

    def test_rebind_persists_and_dispatches_matching_safe_binding(self):
        messages = queue.Queue()
        registry = ActionRegistry(self.settings, lambda key: key, messages, '', '', Mock())
        registry.do_rebind('x_press', 'X', [])
        self.assertEqual(registry.get_binding_display('x_press'), 'X')
        command = messages.get_nowait()
        self.assertEqual(command.com_type, GUICommandType.RebindHotkey)
        self.assertEqual(command.data, ('x_press', 'X', []))
        registry.do_rebind('x_press', 'X', ['CTRL'])
        self.assertEqual(messages.get_nowait().data, ('x_press', 'X', ['CTRL']))
        self.assertEqual(registry.get_binding_display('x_press'), 'Ctrl+X')

    def runtime(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                     and node.name in ('enable_hotkeys', 'disable_hotkeys', '_is_passthrough_x',
                                       'x_press_hotkey', 'passthrough_x_loop')]
        listener = SimpleNamespace(add_hotkey=AsyncMock(), remove_hotkey=AsyncMock())
        clients = [SimpleNamespace(is_foreground=i == 0) for i in range(3)]
        namespace = dict(settings=self.settings, listener=listener, logger=Mock(),
            hotkey_status=False, _active_bindings={}, _FREECAM_ACTIONS={'toggle_freecam', 'freecam_tp'},
            _make_hotkey_callback=lambda action: action, Keycode=Keycode, ModifierKeys=ModifierKeys,
            walker=SimpleNamespace(clients=clients), foreground_client=clients[0], mass_key_press=AsyncMock())
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        return namespace

    async def test_backend_never_registers_or_unregisters_bare_x(self):
        namespace = self.runtime()
        listener = namespace['listener']
        await namespace['enable_hotkeys']()
        calls = [call for call in listener.add_hotkey.await_args_list if call.args[0] == Keycode.X]
        self.assertEqual(calls, [])
        self.assertEqual(namespace['_active_bindings']['x_press'], {'key': 'X', 'modifiers': []})
        await namespace['disable_hotkeys']()
        self.assertFalse(any(call.args[0] == Keycode.X for call in listener.remove_hotkey.await_args_list))
        self.assertEqual(namespace['_active_bindings'], {})

    async def poll(self, namespace, states):
        key_state = Mock(side_effect=states)
        namespace['ctypes'] = SimpleNamespace(windll=SimpleNamespace(
            user32=SimpleNamespace(GetAsyncKeyState=key_state)))

        async def tick(seconds):
            self.assertEqual(seconds, 0.01)
            if key_state.call_count >= len(states):
                raise asyncio.CancelledError()

        namespace['asyncio'] = SimpleNamespace(sleep=tick)
        with self.assertRaises(asyncio.CancelledError):
            await namespace['passthrough_x_loop']()

    async def test_physical_x_mirrors_once_per_press_only_to_background(self):
        namespace = self.runtime()
        await namespace['enable_hotkeys']()
        await self.poll(namespace, [0, 0x8000, 0x8000, 0, 0x8000, 0])
        calls = namespace['mass_key_press'].await_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIsNone(call.args[0])
            self.assertEqual(call.args[1], namespace['walker'].clients[1:])
            self.assertEqual(call.args[3], Keycode.X)
        # No foreground injection and no chat/Enter inspection occurs.

    async def test_disabled_unbound_nonforeground_and_initially_held_x_do_not_mirror(self):
        for mode in ('disabled', 'unbound', 'nonforeground', 'held'):
            with self.subTest(mode=mode):
                namespace = self.runtime()
                await namespace['enable_hotkeys']()
                if mode == 'disabled':
                    await namespace['disable_hotkeys']()
                elif mode == 'unbound':
                    namespace['_active_bindings'].pop('x_press')
                elif mode == 'nonforeground':
                    namespace['walker'].clients[0].is_foreground = False
                states = [0x8000, 0x8000, 0] if mode == 'held' else [0, 0x8000, 0]
                await self.poll(namespace, states)
                namespace['mass_key_press'].assert_not_awaited()

    async def test_forward_failure_does_not_stop_passive_listener(self):
        namespace = self.runtime()
        await namespace['enable_hotkeys']()
        namespace['mass_key_press'].side_effect = [RuntimeError('client closed'), None]
        await self.poll(namespace, [0, 0x8000, 0, 0x8000, 0])
        self.assertEqual(namespace['mass_key_press'].await_count, 2)

    async def test_custom_modifier_binding_keeps_existing_listener_behavior(self):
        self.settings.set_hotkey('x_press', 'X', ['CTRL'])
        namespace = self.runtime()
        await namespace['enable_hotkeys']()
        namespace['listener'].add_hotkey.assert_any_await(
            Keycode.X, 'x_press', modifiers=ModifierKeys.NOREPEAT | ModifierKeys.CTRL)
        await self.poll(namespace, [0, 0x8000, 0])
        namespace['mass_key_press'].assert_not_awaited()
        await namespace['disable_hotkeys']()
        namespace['listener'].remove_hotkey.assert_any_await(
            Keycode.X, modifiers=ModifierKeys.NOREPEAT | ModifierKeys.CTRL)

    async def test_rebinding_between_plain_x_and_custom_key_uses_correct_listener(self):
        namespace = self.runtime()
        await namespace['enable_hotkeys']()
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        case = next(case for node in ast.walk(tree) if isinstance(node, ast.Match)
                    for case in node.cases if isinstance(case.pattern, ast.MatchValue)
                    and ast.unparse(case.pattern.value).endswith('.RebindHotkey'))
        function = ast.AsyncFunctionDef(name='rebind', args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=case.body, decorator_list=[])
        namespace['_kill_tool_callback'] = Mock()
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     'XuanShu.py', 'exec'), namespace)
        namespace['listener'].add_hotkey.reset_mock()
        namespace['listener'].remove_hotkey.reset_mock()
        namespace['com'] = SimpleNamespace(data=('x_press', 'F10', []))
        await namespace['rebind']()
        namespace['listener'].remove_hotkey.assert_not_awaited()
        namespace['listener'].add_hotkey.assert_awaited_once_with(
            Keycode.F10, 'x_press', modifiers=ModifierKeys.NOREPEAT)
        namespace['listener'].add_hotkey.reset_mock()
        namespace['com'].data = ('x_press', 'X', [])
        await namespace['rebind']()
        namespace['listener'].remove_hotkey.assert_awaited_once_with(
            Keycode.F10, modifiers=ModifierKeys.NOREPEAT)
        namespace['listener'].add_hotkey.assert_not_awaited()
        self.assertEqual(namespace['_active_bindings']['x_press'], {'key': 'X', 'modifiers': []})
