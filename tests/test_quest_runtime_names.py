"""Exercise production code objects with the imported application's real globals."""
import asyncio
import builtins
from contextlib import ExitStack
import dis
import importlib
import inspect
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'artifacts/auto-quest-415-import-fix-20261009-202014'
APPDATA = ARTIFACT / 'probe-appdata'
APPDATA.mkdir(parents=True, exist_ok=True)
# Import the entire application, including its actual settings and dependencies,
# without starting its GUI/backend or writing the user's settings.
with patch.dict(os.environ, {'APPDATA': str(APPDATA)}):
    app = importlib.import_module('XuanShu')

from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.task_lifecycle import gather_owned
from wizwalker import XYZ

MAIN = inspect.unwrap(app.main).__code__


def nested_codes(code):
    yield code
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            yield from nested_codes(constant)


def production_code(name):
    matches = [code for code in nested_codes(MAIN) if code.co_name == name]
    assert len(matches) == 1, (name, len(matches))
    return matches[0]


def missing_globals(code, namespace):
    names = {instruction.argval for subcode in nested_codes(code)
             for instruction in dis.get_instructions(subcode)
             if instruction.opname == 'LOAD_GLOBAL'}
    return sorted(names - namespace.keys() - vars(builtins).keys())


def bind_entry(name, **closed):
    code = production_code(name)
    assert set(closed) == set(code.co_freevars)
    def cell(value):
        return (lambda: value).__closure__[0]
    function = types.FunctionType(code, app.__dict__, name,
                                  closure=tuple(cell(closed[n]) for n in code.co_freevars) or None)
    # FunctionType does not copy default arguments from a nested code object.
    if name == 'dialogue_loop':
        function.__defaults__ = (None,)
        function.__kwdefaults__ = {'questing': False}
    assert function.__globals__ is app.__dict__
    return function


class RuntimeNameAuditTests(unittest.TestCase):
    def test_main_program_bindings_and_startup_state(self):
        self.assertIs(app.automation_owner, automation_owner)
        self.assertIs(app.gather_owned, gather_owned)
        startup_stores = {i.argval for i in dis.get_instructions(MAIN)
                          if i.opname == 'STORE_GLOBAL'}
        self.assertIn('walker', startup_stores)
        report = {}
        for name in ('dialogue_loop', 'run_questing_worker', '_follow_quester_session'):
            missing = missing_globals(production_code(name), app.__dict__)
            # The game ClientHandler is intentionally initialized by main().
            self.assertFalse(set(missing) - {'walker'}, (name, missing))
            report[name] = {'missing_before_game_startup': missing,
                            'closure': list(production_code(name).co_freevars)}
        (ARTIFACT / 'main-runtime-name-audit.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')

    def test_transferred_methods_and_direct_helpers_resolve_real_module_globals(self):
        previous = json.loads((ROOT / 'artifacts/auto-quest-415-base-20261009-163300/candidate-source-audit.json').read_text())
        names = previous['baseline_methods_AST_equal'] + previous['baseline_methods_with_local_repairs']
        self.assertEqual(len(set(names)), 58)
        names += ['enter_party_dungeon', '_resume_party_dungeon_interaction',
                  '_maybe_recover_mainline', '_run_mainline_finder',
                  'teleport_party_to_quest_target']
        functions = [(f'Quester.{name}', getattr(app.Quester, name)) for name in names]
        teleport = importlib.import_module('src.teleport_math')
        utils = importlib.import_module('src.utils')
        functions += [(f'src.teleport_math.{name}', getattr(teleport, name))
                      for name in ('collision_tp', '_walk_remaining_to_target')]
        functions += [('src.utils.close_npc_quest_menu', utils.close_npc_quest_menu)]
        report = {}
        for name, function in functions:
            function = inspect.unwrap(function)
            missing = missing_globals(function.__code__, function.__globals__)
            report[name] = missing
            self.assertEqual(missing, [], name)
        (ARTIFACT / 'transferred-name-audit.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')

    def test_audit_detects_removed_production_import(self):
        with patch.dict(app.__dict__):
            del app.automation_owner
            for name in ('dialogue_loop', '_follow_quester_session'):
                self.assertIn('automation_owner', missing_globals(production_code(name), app.__dict__))


class RealMainEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = asyncio.Event()
        self.client = self.make_client('p1', XYZ(2000, 0, 0))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # walker is real main() startup state, not an injected interface import.
        self.stack.enter_context(patch.object(app, 'walker', types.SimpleNamespace(clients=[self.client]), create=True))
        self.stack.enter_context(patch.object(app, 'freecam_status', False))
        self.stack.enter_context(patch.object(app, 'use_potions', False))
        self.stack.enter_context(patch.object(app, 'close_npc_quest_menu', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(app, 'is_visible_by_path', AsyncMock(
            side_effect=lambda client, path: path == app.advance_dialog_path)))

    def make_client(self, title, position):
        return types.SimpleNamespace(title=title, questing_status=True,
            auto_dialogue_running=False, refilling_potions=False,
            quest_recovery_owner=None, entity_detect_combat_status=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/Area'), wizard_name='Wizard Name',
            root_window=types.SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[])),
            body=types.SimpleNamespace(position=AsyncMock(return_value=position)),
            send_key=AsyncMock(), teleport=AsyncMock(), mouse_handler=AsyncMock())

    async def await_action(self, coroutine, timeout=4):
        task = asyncio.create_task(coroutine)
        signal = asyncio.create_task(self.event.wait())
        try:
            done, _ = await asyncio.wait([task, signal], timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task  # Surface the original exception, including NameError.
            self.assertTrue(signal in done, 'production entry never performed the action')
        finally:
            task.cancel()
            signal.cancel()
            await asyncio.gather(task, signal, return_exceptions=True)

    async def test_independent_dialogue_uses_production_owner_import(self):
        async def send(*args, **kwargs):
            self.assertEqual(kwargs['key'], app.Keycode.SPACEBAR)
            self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'npc-dialogue')
            self.event.set()
        self.client.send_key.side_effect = send
        await self.await_action(bind_entry('dialogue_loop')([self.client]))
        self.assertFalse(self.client.auto_dialogue_running)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_task_internal_dialogue_uses_production_globals_and_gather(self):
        async def send(*args, **kwargs):
            self.assertEqual(kwargs['key'], app.Keycode.SPACEBAR)
            self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'npc-dialogue')
            self.client.questing_status = False
            self.event.set()
        async def game_quest_worker(*args):
            await self.event.wait()
        self.client.send_key.side_effect = send
        quest_reader = types.SimpleNamespace(auto_quest=game_quest_worker)
        with patch.object(app, 'Quester', Mock(return_value=quest_reader)), patch.object(
                app, 'reconcile_combat_state', AsyncMock()):
            worker = bind_entry('run_questing_worker', dialogue_loop=bind_entry('dialogue_loop'),
                                party_enabled=True, roster=[self.client])
            await asyncio.wait_for(worker(self.client), timeout=2)
        self.assertTrue(self.event.is_set())
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    def follower(self, hitter, same_area):
        quester = self.client
        app.walker.clients = [quester, hitter]
        reader = types.SimpleNamespace(
            handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
            get_truncated_quest_objectives=AsyncMock(return_value='Defeat Boss'),
            _resume_party_dungeon_interaction=AsyncMock(return_value=False))
        self.stack.enter_context(patch.object(app, 'Quester', Mock(return_value=reader)))
        for name in ('is_spiral_door_open', 'close_automation_popup', 'is_friend_teleport_error'):
            self.stack.enter_context(patch.object(app, name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(app, 'is_visible_by_path', AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(app, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(app, 'clients_share_live_area', AsyncMock(return_value=same_area)))
        self.stack.enter_context(patch.object(app, 'close_friend_windows', AsyncMock()))
        return bind_entry('_follow_quester_session', members=[quester, hitter],
            change_party_equipment=AsyncMock(), prepare_quester_name=AsyncMock(),
            update_party_status=Mock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock())

    async def test_coordinate_follow_uses_production_owner_import(self):
        hitter = self.make_client('p2', XYZ(0, 0, 0))
        async def teleport(position):
            self.assertEqual(position, await self.client.body.position())
            self.assertEqual(get_client_automation_ownership(hitter).owner_label, 'party-coordinate-follow')
            self.event.set()
        hitter.teleport.side_effect = teleport
        await self.await_action(self.follower(hitter, True)(hitter, self.client, False))
        self.assertFalse(get_client_automation_ownership(hitter).locked)

    async def test_friend_follow_uses_production_owner_import(self):
        hitter = self.make_client('p2', XYZ(0, 0, 0))
        hitter.zone_name.return_value = 'World/OldArea'
        async def teleport(client, **kwargs):
            self.assertIs(client, hitter)
            self.assertEqual(kwargs, {'name': 'Wizard Name'})
            self.assertEqual(get_client_automation_ownership(hitter).owner_label, 'party-friend-follow')
            self.event.set()
        with patch.object(app, 'teleport_to_friend_from_list', AsyncMock(side_effect=teleport)):
            await self.await_action(self.follower(hitter, False)(hitter, self.client, False))
        self.assertFalse(get_client_automation_ownership(hitter).locked)

    async def test_dialogue_fails_when_actual_import_is_removed(self):
        with patch.dict(app.__dict__):
            del app.automation_owner
            with self.assertRaisesRegex(NameError, 'automation_owner'):
                await asyncio.wait_for(bind_entry('dialogue_loop')([self.client]), timeout=1)
        self.assertFalse(self.client.auto_dialogue_running)


if __name__ == '__main__':
    unittest.main()
