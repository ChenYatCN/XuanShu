import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from src.fishing_groups import FishingGroups
from src.fish_gaming import banish_config


class FishingGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = [SimpleNamespace(title='p1', is_fishing=False), SimpleNamespace(title='p2', is_fishing=False)]
        self.started = {}
        async def fish(client, chest, **config):
            self.started[client.title] = (chest, config)
            await asyncio.Event().wait()
        self.manager = FishingGroups(fish, lambda: self.clients, Mock())
        self.runner = asyncio.create_task(self.manager.run())

    async def asyncTearDown(self):
        self.runner.cancel()
        await asyncio.gather(self.runner, return_exceptions=True)

    async def wait_started(self, count):
        async with asyncio.timeout(2):
            while len(self.started) < count:
                await asyncio.sleep(.01)

    async def test_different_configs_and_independent_stop(self):
        first = {'fish_school': 'Ice', 'fish_id': 123}
        self.manager.add(['p1'], first)
        first['fish_id'] = 999
        self.manager.add(['p2'], {'fish_chest_only': True})
        await self.wait_started(2)
        self.assertEqual(self.started['p1'][1]['fish_id'], 123)
        self.assertEqual(self.started['p1'][1]['school'], 'Ice')
        self.assertTrue(self.started['p2'][0])
        await self.manager.stop(['p1'])
        self.assertFalse(self.clients[0].is_fishing)
        self.assertTrue(self.clients[1].is_fishing)
        self.assertEqual(list(self.manager.groups), [('p2',)])

    async def test_overlap_and_missing_selection_rejected(self):
        self.manager.add(['p1'], {})
        for titles in (['p1','p2'], [], ['p3']):
            with self.assertRaises(ValueError):
                self.manager.add(titles, {})

    async def test_removed_client_stops_only_its_group(self):
        self.manager.add(['p1'], {})
        self.manager.add(['p2'], {})
        await self.wait_started(2)
        removed = self.clients.pop(0)
        async with asyncio.timeout(2):
            while ('p1',) in self.manager.groups:
                await asyncio.sleep(.01)
        self.assertFalse(removed.is_fishing)
        self.assertTrue(self.clients[0].is_fishing)

    async def test_restart_waits_for_cleanup_and_keeps_settings(self):
        self.manager.add(['p1'], {'fish_rank': 3})
        await self.wait_started(1)
        self.runner.cancel()
        await asyncio.gather(self.runner, return_exceptions=True)
        self.assertFalse(self.clients[0].is_fishing)
        self.started.clear()
        self.runner = asyncio.create_task(self.manager.run())
        await self.wait_started(1)
        self.assertEqual(self.started['p1'][1]['rank'], 3)
        await self.manager.stop()
        self.assertFalse(self.clients[0].is_fishing)

    async def test_filter_config_isolated_under_concurrent_calls(self):
        def pond(school):
            fish = AsyncMock()
            fish.is_chest.return_value = False
            fish.size.return_value = 20
            fish.template.return_value.school_name.return_value = school
            manager = AsyncMock()
            manager.fish_list.return_value = [fish]
            return manager, fish
        a, fa = pond('Ice')
        b, fb = pond('Fire')
        results = await asyncio.gather(
            banish_config(a, (False, 'Ice', 0, 0, 0, 999)),
            banish_config(b, (False, 'Fire', 0, 0, 0, 999)))
        self.assertEqual(results, [[fa], [fb]])
        fa.write_status_code.assert_not_awaited()
        fb.write_status_code.assert_not_awaited()

    async def test_title_reorder_preserves_client_ownership(self):
        self.manager.add(['p1'], {'fish_rank': 3})
        await self.wait_started(1)
        self.clients[0].title, self.clients[1].title = 'p2', 'p1'
        async with asyncio.timeout(2):
            while ('p2',) not in self.manager.groups:
                await asyncio.sleep(.01)
        await self.manager.stop(['p2'])
        self.assertFalse(self.clients[0].is_fishing)
        self.assertFalse(self.clients[1].is_fishing)
