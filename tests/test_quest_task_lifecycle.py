# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from src.task_lifecycle import gather_owned


class QuestTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def questing_function(self, name):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        return next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )

    async def test_stop_clears_run_state_and_restart_reassigns_current_clients(self):
        quester = SimpleNamespace()
        hitter = SimpleNamespace()
        clients = [quester, hitter]
        assignments = [(hitter, quester)]
        def current_quest_party(members=None):
            return SimpleNamespace(
                questers=[quester], hitters=[hitter],
                hitter_assignments=assignments,
            )
        namespace = {
            'walker': SimpleNamespace(clients=clients),
            'current_quest_party': current_quest_party,
        }
        function = self.questing_function('apply_questing_roles')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        apply_roles = namespace['apply_questing_roles']

        apply_roles(True)
        self.assertEqual(quester.quest_party_hitters, [hitter])
        for client in clients:
            client.quest_party_observed_zone = 'old zone'
            client.quest_party_status_session = object()
            client.quest_party_quest_worker_restart_requested = True
            client.quest_party_battle_started_at = 10.0
            client.quest_party_battle_rescue_active = True
            client.quest_party_battle_rescue_at = 20.0
            client.quest_party_solo_gear_active = True
            client.in_solo_zone = True
            client.quest_party_probe_pending = True
            client.quest_party_quest_worker_zone = 'old zone'
            client.quest_party_group_dungeon_zone = 'old dungeon'
            client.quest_party_confirmed_dungeon_transition = ('before', 'after')
        apply_roles(False)
        for client in clients:
            self.assertFalse(client.questing_status)
            self.assertEqual(client.quest_party_hitters, [])
            self.assertIsNone(client.quest_party_observed_zone)
            self.assertIsNone(client.quest_party_status_session)
            self.assertIsNone(client.quest_party_quest_worker_zone)
            self.assertIsNone(client.quest_party_group_dungeon_zone)
            self.assertIsNone(client.quest_party_confirmed_dungeon_transition)
            self.assertFalse(client.quest_party_quest_worker_restart_requested)
            self.assertFalse(client.quest_party_probe_pending)
            self.assertFalse(client.quest_party_battle_rescue_active)
            self.assertFalse(client.in_solo_zone)

        assignments.clear()
        apply_roles(True)
        self.assertTrue(quester.questing_status)
        self.assertTrue(hitter.questing_status)
        self.assertEqual(quester.quest_party_hitters, [])
        self.assertFalse(quester.quest_party_probe_pending)

    async def test_cancelled_supervisor_drains_quest_worker(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def run_questing_worker(client):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        async def reference_level():
            return 1
        client = SimpleNamespace(stats=SimpleNamespace(reference_level=reference_level))
        namespace = {
            'asyncio': asyncio, 'Client': object, 'members': None,
            'questing_status': True, 'walker': SimpleNamespace(clients=[client]),
            'run_questing_worker': run_questing_worker,
        }
        function = self.questing_function('async_questing')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        task = asyncio.create_task(namespace['async_questing'](client))
        await asyncio.wait_for(started.wait(), 4.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())
        self.assertIsNone(client.quest_party_quest_worker_task)

    async def test_error_drains_sibling_before_retry(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def mover():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                stopped.set()
        async def fail():
            await started.wait()
            raise RuntimeError('memory read failed')
        with self.assertRaises(RuntimeError):
            await gather_owned(mover(), fail())
        self.assertTrue(stopped.is_set())

    async def test_stop_drains_nested_workers(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def mover():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        task = asyncio.create_task(gather_owned(gather_owned(mover())))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_queued_quest_loop_cannot_reenable_after_stop(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'questing_loop')
        namespace = {'questing_status': False}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        await namespace['questing_loop']()
