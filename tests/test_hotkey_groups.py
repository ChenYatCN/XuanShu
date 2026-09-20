import asyncio
import unittest
from types import SimpleNamespace
from src.hotkey_groups import HotkeyGroups, run_client_worker


class HotkeyGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_clients_toggle_and_disconnect(self):
        clients = [SimpleNamespace(title=f'p{i}') for i in range(1, 4)]
        seen, stopped = [], []
        async def run(action, members):
            name = members[0].title
            seen.append(name)
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(name)
        manager = HotkeyGroups(lambda: clients, run)
        try:
            await manager.toggle('toggle_combat', None)
            await asyncio.sleep(0)
            self.assertEqual(seen, ['p1', 'p2', 'p3'])
            await manager.toggle('toggle_combat', ['p2'])
            self.assertEqual(stopped, ['p2'])
            self.assertEqual(len(manager.groups), 2)
            clients.pop()
            await manager.remove_missing()
            self.assertIn('p3', stopped)
            self.assertEqual(len(manager.groups), 1)
        finally:
            await manager.stop()

    async def test_missing_client_does_not_block_live_member(self):
        started = []
        async def run(action, members):
            started.extend(c.title for c in members)
        manager = HotkeyGroups(lambda: [SimpleNamespace(title='p1')], run)
        await manager.toggle('toggle_speed', ['p1', 'p2'])
        await asyncio.sleep(0)
        self.assertEqual(started, ['p1'])
        await manager.stop()
        for titles in ([], ['p2']):
            with self.assertRaises(ValueError):
                await manager.toggle('toggle_speed', titles)

    async def test_worker_failure_and_disconnect_are_isolated(self):
        clients = [SimpleNamespace(title='p1'), SimpleNamespace(title='p2')]
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def failed():
            raise RuntimeError('expected read failure')
        async def running():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        first = asyncio.create_task(run_client_worker(clients[0], lambda: clients, failed))
        second = asyncio.create_task(run_client_worker(clients[1], lambda: clients, running))
        await started.wait()
        await first
        self.assertFalse(second.done())
        clients.pop()
        await asyncio.wait_for(second, 1)
        self.assertTrue(stopped.is_set())

    async def test_team_disconnect_keeps_remaining_workers(self):
        clients = [SimpleNamespace(title='p1'), SimpleNamespace(title='p2')]
        stopped = []
        async def worker(c):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(c.title)
        async def run(action, members):
            await asyncio.gather(*[run_client_worker(c, lambda: clients, lambda c=c: worker(c))
                                   for c in members])
        manager = HotkeyGroups(lambda: clients, run)
        await manager.toggle('toggle_questing', ['p1', 'p2'])
        await asyncio.sleep(0.01)
        clients.pop()
        await manager.remove_missing()
        await asyncio.sleep(0.3)
        self.assertEqual(stopped, ['p2'])
        self.assertTrue(manager.active('toggle_questing'))
        await manager.stop()
