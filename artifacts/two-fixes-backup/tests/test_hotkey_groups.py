import asyncio
import unittest
from types import SimpleNamespace
from src.hotkey_groups import HotkeyGroups, parse_groups


class HotkeyGroupTests(unittest.IsolatedAsyncioTestCase):
    def test_group_config(self):
        self.assertEqual(parse_groups('组1=p1,p2\n组2=p3，p4'), {'组1': ['p1', 'p2'], '组2': ['p3', 'p4']})
        for text in ('组1=', '组1=p1\n组1=p2', '组1=p0', 'p1,p2'):
            with self.assertRaises(ValueError):
                parse_groups(text)

    async def test_independent_groups_and_disconnect(self):
        clients = [SimpleNamespace(title=f'p{i}') for i in range(1, 5)]
        seen, stopped = [], []
        async def run(action, members):
            seen.append((action, tuple(c.title for c in members)))
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(tuple(c.title for c in members))
        manager = HotkeyGroups(lambda: clients, run)
        try:
            await manager.toggle('toggle_combat', ['p1', 'p2'])
            await manager.toggle('toggle_combat', ['p3', 'p4'])
            await asyncio.sleep(0)
            self.assertEqual(len(seen), 2)
            with self.assertRaises(ValueError):
                await manager.toggle('toggle_combat', ['p2', 'p3'])
            await manager.toggle('toggle_combat', ['p1', 'p2'])
            self.assertEqual(stopped, [('p1', 'p2')])
            self.assertTrue(manager.active('toggle_combat'))
            clients.pop()
            await manager.remove_missing()
            self.assertFalse(manager.groups)
            self.assertEqual(stopped[-1], ('p3', 'p4'))
        finally:
            await manager.stop()

    async def test_missing_target_never_falls_back_to_all(self):
        async def run(*args):
            self.fail('must not run')
        manager = HotkeyGroups(lambda: [SimpleNamespace(title='p1')], run)
        for titles in ([], ['p2']):
            with self.assertRaises(ValueError):
                await manager.toggle('toggle_speed', titles)
        self.assertFalse(manager.groups)
