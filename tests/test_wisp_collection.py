import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src.utils import collect_wisps, collect_wisps_with_limit


class WispCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.health = 100
        self.mana = 100
        self.client = SimpleNamespace(
            stats=SimpleNamespace(
                current_hitpoints=AsyncMock(side_effect=lambda: self.health),
                max_hitpoints=AsyncMock(return_value=100),
                current_mana=AsyncMock(side_effect=lambda: self.mana),
                max_mana=AsyncMock(return_value=100)),
            zone_name=AsyncMock(return_value='Mirage/Caravan'), teleport=AsyncMock())
        self.health_wisps = [SimpleNamespace(location=AsyncMock(return_value=XYZ(i, 0, 0))) for i in (1, 2)]
        self.mana_wisps = [SimpleNamespace(location=AsyncMock(return_value=XYZ(i, 0, 0))) for i in (3, 4)]
        self.entities = {'WispHealth': self.health_wisps, 'WispMana': self.mana_wisps, 'WispGold': []}
        self.sprinter = SimpleNamespace(
            get_base_entities_with_vague_name=AsyncMock(side_effect=lambda name: self.entities[name]),
            find_safe_entities_from=AsyncMock(side_effect=lambda entities: entities))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.utils.SprintyClient', Mock(return_value=self.sprinter)))
        self.free = self.stack.enter_context(patch('src.utils.is_free', AsyncMock(return_value=True)))
        self.real_sleep = asyncio.sleep
        async def tick(_):
            await self.real_sleep(0)
        self.stack.enter_context(patch('src.utils.asyncio.sleep', new=tick))

    async def test_full_health_with_low_mana_never_chases_health_wisps(self):
        self.mana = 10
        await collect_wisps(self.client)
        self.assertEqual([call.args[0] for call in self.sprinter.get_base_entities_with_vague_name.await_args_list],
                         ['WispMana', 'WispGold'])
        self.assertEqual([call.args[0].x for call in self.client.teleport.await_args_list], [3, 4])
        self.sprinter.find_safe_entities_from.assert_awaited_once_with(self.mana_wisps)

    async def test_full_resources_skip_limited_collection_entirely(self):
        await collect_wisps_with_limit(self.client)
        self.sprinter.get_base_entities_with_vague_name.assert_not_awaited()
        self.client.teleport.assert_not_awaited()

    async def test_limited_collection_uses_existing_safe_entity_filter(self):
        self.health = 10
        self.sprinter.find_safe_entities_from.side_effect = None
        self.sprinter.find_safe_entities_from.return_value = [self.health_wisps[1]]
        await collect_wisps_with_limit(self.client, limit=2)
        self.sprinter.find_safe_entities_from.assert_awaited_once_with(self.health_wisps)
        self.client.teleport.assert_awaited_once_with(self.health_wisps[1].location.return_value)

    async def test_resource_filled_by_first_pickup_skips_remaining_wisps(self):
        self.health = 10
        async def pickup(_):
            self.health = 100
        self.client.teleport.side_effect = pickup
        await collect_wisps_with_limit(self.client)
        self.client.teleport.assert_awaited_once_with(self.health_wisps[0].location.return_value)

    async def test_limit_stops_after_requested_number_of_safe_wisps(self):
        self.health = self.mana = 10
        await collect_wisps_with_limit(self.client, limit=2)
        self.assertEqual([call.args[0].x for call in self.client.teleport.await_args_list], [1, 2])

    async def test_loading_battle_or_zone_change_stops_collection(self):
        self.health = 10
        self.free.return_value = False
        await collect_wisps_with_limit(self.client)
        self.client.teleport.assert_not_awaited()
        self.free.return_value = True
        self.client.zone_name.side_effect = ['Mirage/Caravan', 'Mirage/AnotherZone']
        await collect_wisps_with_limit(self.client)
        self.client.teleport.assert_not_awaited()
