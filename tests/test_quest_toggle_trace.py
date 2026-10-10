"""Trace the existing command path without adding toggle policy or cancelling users."""
import ast
import asyncio
import os
import queue
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import wizwalker.hotkey as native
from wizwalker import Keycode, ModifierKeys
from src.gui.commands import GUICommand, GUICommandType, GUIKeys
from src.hotkey_groups import HotkeyGroups
from tests import test_hotkey_group_ui as ui_fixture


def case_function(path, command, name, asynchronous=False):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    case = next(case for node in ast.walk(tree) if isinstance(node, ast.Match)
                for case in node.cases if isinstance(case.pattern, ast.MatchValue)
                and ast.unparse(case.pattern.value).endswith('.' + command))
    args = ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[])
    body = [ast.For(target=ast.Name(id='com', ctx=ast.Store()),
                    iter=ast.Name(id='commands', ctx=ast.Load()), body=case.body, orelse=[])]
    cls = ast.AsyncFunctionDef if asynchronous else ast.FunctionDef
    function = cls(name=name, args=args, body=body, decorator_list=[])
    return compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), path, 'exec')


class QuestToggleTraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.native_patch = patch.object(native, 'user32', SimpleNamespace(
            RegisterHotKey=Mock(return_value=1), UnregisterHotKey=Mock(return_value=1)))
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)
        self.clients = [SimpleNamespace(title='p3', questing_status=False),
                        SimpleNamespace(title='p4', questing_status=False)]

        async def run(action, members):
            for client in members:
                client.questing_status = True
            try:
                await asyncio.Event().wait()
            finally:
                for client in members:
                    client.questing_status = False

        self.groups = HotkeyGroups(lambda: self.clients, run)
        self.addAsyncCleanup(self.groups.stop)
        self.ns = dict(asyncio=asyncio, time=time, sys=sys, logger=Mock(),
            settings=SimpleNamespace(get_hotkeys=lambda: {'toggle_questing': {'key': 'F3', 'modifiers': []}}),
            listener=native.HotkeyListener(), hotkey_status=False, questing_status=False,
            _active_bindings={}, _FREECAM_ACTIONS={'toggle_freecam', 'freecam_tp'},
            walker=SimpleNamespace(clients=self.clients), hotkey_groups=self.groups,
            gui_send_queue=queue.Queue(), Keycode=Keycode, ModifierKeys=ModifierKeys,
            xuanshu_gui=SimpleNamespace(GUICommand=GUICommand, GUICommandType=GUICommandType),
            grouped_hotkey_actions={'toggle_questing'}, questing_task=None,
            speed_task=None, combat_task=None, dialogue_task=None, sigil_task=None,
            auto_pet_task=None, auto_potion_status=False, side_quest_status=False)
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        names = {'_log_quest_toggle', '_make_hotkey_callback', '_is_passthrough_x',
                 'enable_hotkeys', 'disable_hotkeys'}
        definitions = [node for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), 'XuanShu.py', 'exec'), self.ns)
        exec(case_function('XuanShu.py', 'ToggleHotkeyGroup', 'dispatch', True), self.ns)

    async def event(self):
        await self.ns['listener']._handle_hotkey(Keycode.F3.value, 0)
        await asyncio.sleep(0)
        return self.ns['gui_send_queue'].get_nowait()

    async def dispatch(self, command):
        self.ns['commands'] = [command]
        await self.ns['dispatch']()
        await asyncio.sleep(0)

    def group_command(self, trigger):
        return GUICommand(GUICommandType.ToggleHotkeyGroup,
            {'action': 'toggle_questing', 'clients': ['p3', 'p4']}, trigger=trigger)

    async def test_single_event_traces_one_toggle_and_normal_second_event_stops(self):
        await self.ns['enable_hotkeys']()
        first = await self.event()
        self.assertEqual(first.trigger['event'], 'WM_HOTKEY')
        self.assertEqual(first.trigger['source'], 'hotkey')
        self.assertEqual(first.trigger['listener_id'], id(self.ns['listener']))
        self.assertIsNotNone(first.trigger['registration_id'])
        await self.dispatch(self.group_command(first.trigger))
        self.assertTrue(self.groups.active('toggle_questing'))
        second = await self.event()
        await self.dispatch(self.group_command(second.trigger))
        self.assertFalse(self.groups.active('toggle_questing'))
        self.assertNotEqual(first.trigger['callback_ns'], second.trigger['callback_ns'])
        stages = [call.args[1] for call in self.ns['logger'].info.call_args_list]
        self.assertEqual(stages.count('toggle_begin'), 2)
        self.assertEqual(stages.count('toggle_end'), 2)

    async def test_hold_has_norepeat_registration_without_inventing_keyup_or_repeat(self):
        await self.ns['enable_hotkeys']()
        call = native.user32.RegisterHotKey.call_args
        self.assertTrue(call.args[2] & int(ModifierKeys.NOREPEAT))
        event = await self.event()
        self.assertEqual(event.trigger['repeat'], 'not_provided_by_WM_HOTKEY')
        self.assertTrue(self.ns['gui_send_queue'].empty())
        self.assertFalse(self.groups.active('toggle_questing'))

    async def test_pending_callback_after_unregister_is_identifiable_and_not_cancelled(self):
        await self.ns['enable_hotkeys']()
        listener = self.ns['listener']
        await listener._handle_hotkey(Keycode.F3.value, 0)
        pending = listener._callback_tasks[-1]
        await self.ns['disable_hotkeys']()
        await pending
        command = self.ns['gui_send_queue'].get_nowait()
        self.assertFalse(command.trigger['enabled_at_callback'])
        self.assertFalse(command.trigger['binding_active_at_callback'])
        self.assertFalse(pending.cancelled())
        self.assertFalse(self.groups.active('toggle_questing'))
        self.assertIn((Keycode.F3.value, 0), listener._callbacks)
        # Evidence of the dependency's retained mapping, not a proposed policy.

    async def test_queued_gui_command_keeps_original_source_through_re_registration(self):
        await self.ns['enable_hotkeys']()
        old = await self.event()
        await self.ns['disable_hotkeys']()
        await self.ns['enable_hotkeys']()
        new = await self.event()
        self.assertNotEqual(old.trigger['callback_id'], new.trigger['callback_id'])
        self.assertTrue(old.trigger['enabled_at_callback'])
        await self.dispatch(self.group_command(old.trigger))
        self.assertTrue(self.groups.active('toggle_questing'))
        await self.dispatch(self.group_command(new.trigger))
        self.assertFalse(self.groups.active('toggle_questing'))

    async def test_listener_enable_disable_does_not_change_running_quest_group(self):
        await self.dispatch(self.group_command({'source': 'ui'}))
        original_task = next(iter(self.groups.groups.values()))[1]
        await self.ns['enable_hotkeys']()
        await self.ns['disable_hotkeys']()
        await self.ns['enable_hotkeys']()
        self.assertIs(next(iter(self.groups.groups.values()))[1], original_task)
        self.assertTrue(all(c.questing_status for c in self.clients))

    async def test_listener_disable_during_valid_stop_keeps_cleanup_running(self):
        cleaning = asyncio.Event()
        finish_cleanup = asyncio.Event()

        async def worker(action, members):
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish_cleanup.wait()

        self.groups.run = worker
        await self.ns['enable_hotkeys']()
        await self.dispatch(self.group_command({'source': 'ui'}))
        self.ns['commands'] = [self.group_command({'source': 'hotkey'})]
        stopping = asyncio.create_task(self.ns['dispatch']())
        await asyncio.wait_for(cleaning.wait(), 1)
        await self.ns['disable_hotkeys']()
        self.assertFalse(stopping.done())
        finish_cleanup.set()
        await asyncio.wait_for(stopping, 1)
        self.assertFalse(self.groups.active('toggle_questing'))

    async def test_untraced_internal_command_is_labelled_without_assuming_ui(self):
        await self.dispatch(self.group_command(None))
        calls = [c for c in self.ns['logger'].info.call_args_list if c.args[1] == 'toggle_begin']
        self.assertEqual(calls[0].args[2], {'source': 'command_without_source'})

    async def test_listener_replacement_does_not_relabel_an_old_pending_callback(self):
        await self.ns['enable_hotkeys']()
        old_listener = self.ns['listener']
        old_callback = old_listener._callbacks[(Keycode.F3.value, 0)]
        await self.ns['disable_hotkeys']()
        self.ns['listener'] = native.HotkeyListener()
        await self.ns['enable_hotkeys']()
        await old_callback()
        old = self.ns['gui_send_queue'].get_nowait()
        self.assertEqual(old.trigger['listener_id'], id(old_listener))
        self.assertNotEqual(old.trigger['listener_id'], id(self.ns['listener']))
        self.assertFalse(self.groups.active('toggle_questing'))

    async def test_rebind_preserves_norepeat_and_records_the_new_binding(self):
        await self.ns['enable_hotkeys']()
        self.ns['_kill_tool_callback'] = Mock()
        self.ns['settings'].get_hotkeys = lambda: {'toggle_questing': {'key': 'F4', 'modifiers': ['CTRL']}}
        self.ns['commands'] = [GUICommand(GUICommandType.RebindHotkey, ('toggle_questing', 'F4', ['CTRL']))]
        exec(case_function('XuanShu.py', 'RebindHotkey', 'rebind', True), self.ns)
        await self.ns['rebind']()
        listener = self.ns['listener']
        await listener._handle_hotkey(Keycode.F4.value, int(ModifierKeys.CTRL))
        await asyncio.sleep(0)
        command = self.ns['gui_send_queue'].get_nowait()
        self.assertEqual(command.trigger['binding'], {'key': 'F4', 'modifiers': ['CTRL']})
        self.assertIsNotNone(command.trigger['registration_id'])
        self.assertTrue(native.user32.RegisterHotKey.call_args.args[2] & int(ModifierKeys.NOREPEAT))

    async def test_legacy_toggle_command_records_before_and_after_state(self):
        async def legacy_toggle():
            self.ns['questing_status'] = not self.ns['questing_status']
        self.ns['GUIKeys'] = GUIKeys
        self.ns['toggle_questing_hotkey'] = legacy_toggle
        self.ns['commands'] = [GUICommand(GUICommandType.ToggleOption, GUIKeys.toggle_questing)]
        exec(case_function('XuanShu.py', 'ToggleOption', 'legacy', True), self.ns)
        await self.ns['legacy']()
        self.assertTrue(self.ns['questing_status'])
        stages = [c.args[1] for c in self.ns['logger'].info.call_args_list]
        self.assertEqual(stages, ['legacy_toggle_begin', 'legacy_toggle_end'])


class QuestToggleUITraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixture.HotkeyGroupUITests.setUpClass()
        cls.app = ui_fixture.HotkeyGroupUITests.app

    setUp = ui_fixture.HotkeyGroupUITests.setUp
    tearDown = ui_fixture.HotkeyGroupUITests.tearDown
    checks = ui_fixture.HotkeyGroupUITests.checks

    def test_hotkey_gui_dispatch_preserves_source_and_records_current_selection(self):
        self.api['set_available_clients'](['p1', 'p2', 'p3', 'p4'])
        trigger = {'source': 'hotkey', 'event': 'WM_HOTKEY', 'listener_id': 123, 'callback_id': 456}
        pending = GUICommand(GUICommandType.InvokeAction, 'toggle_questing', trigger=trigger)
        # The existing GUI route reads selection when the queued action is delivered.
        for title in ('p1', 'p2'):
            self.checks()[title].setChecked(False)
        ns = dict(registry=self.ctx.registry, commands=[pending])
        exec(case_function('src/gui/main.py', 'InvokeAction', 'dispatch'), ns)
        ns['dispatch']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.data['clients'], ['p3', 'p4'])
        self.assertEqual(command.trigger['selected_clients'], ['p3', 'p4'])
        self.assertEqual(command.trigger['listener_id'], 123)
        self.assertEqual(command.trigger['callback_id'], 456)
        self.assertNotIn('selected_clients', trigger)

    def test_ui_mouse_press_is_distinct_and_does_not_create_second_command(self):
        from PyQt6.QtCore import Qt, QEvent
        from src.gui.widgets import ToggleNameLabel
        self.api['set_available_clients'](['p3', 'p4'])
        row = self.ctx.registry.row_widgets['toggle_questing']
        label = row.findChild(ToggleNameLabel)
        event = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
                                type=lambda: QEvent.Type.MouseButtonPress)
        before = self.ctx.send_queue.put.call_count
        label.mousePressEvent(event)
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(self.ctx.send_queue.put.call_count - before, 1)
        self.assertEqual(command.trigger['source'], 'ui')
        self.assertEqual(command.trigger['event'], 'mouse_press')
        self.assertEqual(command.trigger['button'], 'LeftButton')

    def test_other_actions_keep_existing_no_argument_invoke_path(self):
        self.api['set_available_clients'](['p3', 'p4'])
        ns = dict(registry=self.ctx.registry, commands=[GUICommand(
            GUICommandType.InvokeAction, 'toggle_combat')])
        exec(case_function('src/gui/main.py', 'InvokeAction', 'dispatch'), ns)
        ns['dispatch']()
        command = self.ctx.send_queue.put.call_args.args[0]
        self.assertEqual(command.data, {'action': 'toggle_combat', 'clients': ['p3', 'p4']})
        self.assertIsNone(command.trigger)
