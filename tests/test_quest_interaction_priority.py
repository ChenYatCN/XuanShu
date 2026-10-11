import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src.questing import Quester
from src.interaction_prompts import quest_interaction_matches


class QuestInteractionPriorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.quester = Quester(self.client, [self.client], None)
        self.target = XYZ(20, 30, 0)

    def test_match_chinese_and_english_target(self):
        self.assertTrue(quest_interaction_matches('使用 配置站 地点：星辰区', '配置站'))
        self.assertTrue(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Configuration Station'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'District of the Stars'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Station Vendor'))
        self.assertFalse(quest_interaction_matches('Defeat Guard in District of the Stars', 'Guard'))
        self.assertFalse(quest_interaction_matches('Use Cartography in District of the Stars', 'Cart'))

    async def test_existing_prompt_skips_teleport(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        with patch('src.questing.collision_tp', new=AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
        move.assert_not_awaited()

    async def test_prompt_during_move_stops_retries_and_drains_movement(self):
        stopped = asyncio.Event()
        async def move(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        self.quester.quest_interaction_ready = AsyncMock(side_effect=[False, True])
        with patch('src.questing.collision_tp', side_effect=move):
            await asyncio.wait_for(self.quester.move_until_quest_interaction(self.client, self.target), 1)
        self.assertTrue(stopped.is_set())

    async def test_no_prompt_preserves_normal_movement(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing.collision_tp', new=AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
        move.assert_awaited_once_with(self.client, self.target, leader_client=None)

    async def test_stop_cancels_movement_and_watcher(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def move(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing.collision_tp', side_effect=move):
            task = asyncio.create_task(self.quester.move_until_quest_interaction(self.client, self.target))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
