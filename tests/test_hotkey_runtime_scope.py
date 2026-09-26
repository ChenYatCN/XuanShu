import asyncio
import ast
from collections import defaultdict
import statistics
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock
from src.gui.commands import GUICommand, GUICommandType
from src.hotkey_groups import HotkeyGroups, run_client_worker
from src.task_lifecycle import gather_owned


class HotkeyRuntimeScopeTests(unittest.IsolatedAsyncioTestCase):
    def teleport_runtime(self, teleport):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == 'navmap_teleport')
        namespace = dict(asyncio=asyncio, statistics=statistics, logger=Mock(), navmap_tp=teleport)
        exec(compile('from __future__ import annotations\n' + ast.unparse(function),
                     'XuanShu.py', 'exec'), namespace)
        return namespace['navmap_teleport']

    async def test_scoped_mass_teleport_preserves_common_quest_target(self):
        clients = [SimpleNamespace(title=f'p{i}', quest_position=SimpleNamespace(
                    position=AsyncMock(return_value=(1, 2, 3) if i < 3 else (4, 5, 6))),
                    zone_name=AsyncMock(return_value='same')) for i in range(1, 4)]
        teleport = AsyncMock()
        run = self.teleport_runtime(teleport)
        await run(clients[0], clients[1:], mass_teleport=True, isolate_errors=True)
        self.assertEqual([call.args for call in teleport.await_args_list],
                         [(client, (1, 2, 3)) for client in clients])

    async def test_scoped_mass_teleport_skips_only_failed_client(self):
        clients = [SimpleNamespace(title=f'p{i}', quest_position=SimpleNamespace(
                    position=AsyncMock(return_value=(1, 2, 3))),
                    zone_name=AsyncMock(return_value='same')) for i in range(1, 4)]
        clients[1].quest_position.position.side_effect = RuntimeError('client closed')
        teleport = AsyncMock()
        run = self.teleport_runtime(teleport)
        await run(clients[0], clients[1:], mass_teleport=True, isolate_errors=True)
        self.assertEqual([call.args[0].title for call in teleport.await_args_list], ['p1', 'p3'])

    def runtime(self, clients, loop):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == 'run_hotkey_group')
        namespace = dict(asyncio=asyncio, walker=SimpleNamespace(clients=clients),
            gui_send_queue=Mock(), logger=Mock(), combat_loop=loop,
            gather_owned=gather_owned, run_client_worker=run_client_worker,
            bool_to_string=lambda value: 'Enabled' if value else 'Disabled',
            xuanshu_gui=SimpleNamespace(GUICommand=GUICommand, GUICommandType=GUICommandType),
            bot_tasks={('p1',): object()})
        namespace['scoped_worker_active'] = defaultdict(set)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        manager = HotkeyGroups(lambda: clients, namespace['run_hotkey_group'])
        namespace['hotkey_groups'] = manager
        return manager, namespace

    async def test_scripts_fishing_production_do_not_block_combat(self):
        clients = [SimpleNamespace(title='p1', is_ibao=True, is_fishing=False),
                   SimpleNamespace(title='p2', is_ibao=False, is_fishing=True),
                   SimpleNamespace(title='p3', is_ibao=False, is_fishing=False)]
        started = set()
        ready = asyncio.Event()
        async def combat(members):
            started.update(c.title for c in members)
            if len(started) == 3:
                ready.set()
            await asyncio.Event().wait()
        manager, namespace = self.runtime(clients, combat)
        try:
            await manager.toggle('toggle_combat', ['p1', 'p2', 'p3'])
            await asyncio.wait_for(ready.wait(), 1)
            self.assertEqual(started, {'p1', 'p2', 'p3'})
            self.assertEqual(len(manager.groups), 3)
            namespace['logger'].warning.assert_not_called()
        finally:
            await manager.stop()
        self.assertTrue(clients[0].is_ibao)
        self.assertTrue(clients[1].is_fishing)
        self.assertEqual(set(namespace['bot_tasks']), {('p1',)})

    async def test_one_failure_does_not_cancel_healthy_client(self):
        clients = [SimpleNamespace(title='p1'), SimpleNamespace(title='p2'), SimpleNamespace(title='p3')]
        healthy = asyncio.Event()
        async def combat(members):
            if members[0].title == 'p1':
                raise RuntimeError('expected client read failure')
            healthy.set()
            await asyncio.Event().wait()
        manager, _ = self.runtime(clients, combat)
        try:
            await manager.toggle('toggle_combat', ['p1', 'p2'])
            await asyncio.wait_for(healthy.wait(), 1)
            await asyncio.sleep(0.05)
            running = [c.title for members, _ in manager.groups.values() for c in members]
            self.assertEqual(running, ['p2'])
            self.assertFalse(hasattr(clients[2], 'combat_status'))
        finally:
            await manager.stop()
