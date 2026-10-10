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

    async def test_multiple_questers_do_not_enable_finder_implicitly(self):
        clients = [SimpleNamespace(questing_status=False) for _ in range(3)]
        namespace = {
            'walker': SimpleNamespace(clients=clients),
            'current_quest_party': lambda *args, **kwargs: SimpleNamespace(
                questers=clients, hitters=[], hitter_assignments=[]),
            'mainline_finder_enabled': False,
        }
        function = self.questing_function('apply_questing_roles')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        namespace['apply_questing_roles'](True)
        self.assertTrue(all(c.questing_status for c in clients))
        self.assertTrue(all(c.mainline_finder_enabled is False for c in clients))

    async def test_stop_clears_run_state_and_restart_reassigns_current_clients(self):
        quester = SimpleNamespace(questing_status=False)
        hitter = SimpleNamespace(questing_status=False)
        clients = [quester, hitter]
        assignments = [(hitter, quester)]
        def current_quest_party(members=None, **kwargs):
            return SimpleNamespace(
                questers=[quester], hitters=[hitter],
                hitter_assignments=assignments,
            )
        namespace = {
            'walker': SimpleNamespace(clients=clients),
            'current_quest_party': current_quest_party,
            'mainline_finder_enabled': False,
        }
        function = self.questing_function('apply_questing_roles')
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        apply_roles = namespace['apply_questing_roles']

        apply_roles(True)
        self.assertEqual(quester.quest_party_hitters, [hitter])
        generation = hitter.quest_party_battle_entry_generation
        self.assertIs(hitter.quest_party_quester, quester)
        for client in clients:
            client.quest_party_observed_zone = 'old zone'
            client.quest_party_status_session = object()
            client.quest_party_quest_worker_restart_requested = True
            client.quest_party_battle_started_at = 10.0
            client.quest_party_battle_rescue_active = True
            client.quest_party_battle_rescue_at = 20.0
            client.quest_party_battle_entry_recovery = {'attempts': 2}
            client.quest_party_battle_sync_state = 'failed'
            client.quest_party_solo_gear_active = True
            client.in_solo_zone = True
            client.quest_party_probe_pending = True
            client.quest_party_probe_wait = {'zone': 'old zone', 'equipping': True}
            client.quest_party_quest_worker_zone = 'old zone'
            client.quest_party_group_dungeon_zone = 'old dungeon'
            client.quest_party_confirmed_dungeon_transition = ('before', 'after')
            client.quest_party_dungeon_interaction = {'phase': 'transition'}
            client.quest_party_shared_target = {'zone': 'old dungeon'}
            client.npc_mainline_menu_selection = {'failed': True}
            client.quest_invitation_state = {'failed': True}
            client.quest_interaction_attempt = {'attempts': 1}
            client._quest_x_turn_failed = ('old quest',)
            client._npc_complete_state = {'attempts': 3}
        apply_roles(False)
        self.assertGreater(hitter.quest_party_battle_entry_generation, generation)
        for client in clients:
            self.assertFalse(client.questing_status)
            self.assertEqual(client.quest_party_hitters, [])
            self.assertIsNone(client.quest_party_quester)
            self.assertIsNone(client.quest_party_observed_zone)
            self.assertIsNone(client.quest_party_status_session)
            self.assertIsNone(client.quest_party_quest_worker_zone)
            self.assertIsNone(client.quest_party_group_dungeon_zone)
            self.assertIsNone(client.quest_party_confirmed_dungeon_transition)
            self.assertIsNone(client.quest_party_dungeon_interaction)
            self.assertIsNone(client.quest_party_shared_target)
            self.assertIsNone(client.npc_mainline_menu_selection)
            self.assertIsNone(client.quest_invitation_state)
            self.assertIsNone(client.quest_interaction_attempt)
            self.assertIsNone(client._quest_x_turn_failed)
            self.assertIsNone(client._npc_complete_state)
            self.assertFalse(client.quest_party_quest_worker_restart_requested)
            self.assertFalse(client.quest_party_probe_pending)
            self.assertIsNone(client.quest_party_probe_wait)
            self.assertFalse(client.quest_party_battle_rescue_active)
            self.assertIsNone(client.quest_party_battle_entry_recovery)
            self.assertIsNone(client.quest_party_battle_sync_state)
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
