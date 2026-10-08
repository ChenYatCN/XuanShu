import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src.questing import Quester
from src.interaction_prompts import quest_interaction_matches


class QuestInteractionPriorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.questing_status = True
        self.client.quest_recovery_owner = None
        self.client.refilling_potions = False
        self.client.post_combat_cleanup_active = False
        self.client.quest_party_battle_rescue_active = False
        self.client.quest_id.return_value = 42
        self.client.goal_id.return_value = 7
        self.client.zone_name.return_value = 'World/Zone'
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value='Press X to Talk')
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.target = XYZ(20, 30, 0)
        self.free = AsyncMock(return_value=True)
        patcher = patch('src.questing.is_free_leader_questing', new=self.free)
        patcher.start()
        self.addCleanup(patcher.stop)
        portal = patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False))
        portal.start()
        self.addCleanup(portal.stop)

    def test_match_chinese_and_english_target(self):
        self.assertTrue(quest_interaction_matches('使用 配置站 地点：星辰区', '配置站'))
        self.assertTrue(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Configuration Station'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'District of the Stars'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Station Vendor'))
        self.assertFalse(quest_interaction_matches('Defeat Guard in District of the Stars', 'Guard'))
        self.assertFalse(quest_interaction_matches('Use Cartography in District of the Stars', 'Cart'))

    async def test_ready_requires_nearby_matching_target_not_vendor_or_zone(self):
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', new=AsyncMock(return_value='Talk To Merle in The Commons')), \
                patch('src.questing.get_popup_title', new=AsyncMock()) as title:
            for name, expected in (('Merle', True), ('Vendor', False), ('The Commons', False), ('', False)):
                title.return_value = name
                self.assertEqual(await self.quester.quest_interaction_ready(self.client, self.target), expected)
            title.return_value = 'Merle'
            self.client.body.position.return_value = XYZ(2000, 30, 0)
            self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))

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

    async def test_existing_dialogue_skips_movement_even_without_target_prompt(self):
        self.free.return_value = False
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing.collision_tp', new=AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
        move.assert_not_awaited()
        self.quester.quest_interaction_ready.assert_not_awaited()

    async def test_dialogue_opening_during_move_cancels_only_current_client(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        async def move(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        peer = asyncio.create_task(asyncio.Event().wait())
        try:
            with patch('src.questing.collision_tp', side_effect=move):
                task = asyncio.create_task(self.quester.move_until_quest_interaction(self.client, self.target))
                await started.wait()
                self.free.return_value = False
                await asyncio.wait_for(task, 1)
            self.assertTrue(stopped.is_set())
            self.assertFalse(peer.done())
        finally:
            peer.cancel()
            await asyncio.gather(peer, return_exceptions=True)

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

    async def test_collect_open_transport_and_world_gate_require_current_target(self):
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        cases = (
            ('Collect Mushrooms in Forest', 'Mushrooms', 'Press X to Collect', True),
            ('Collect Mushrooms in Forest', 'Vendor', 'Press X to Collect', False),
            ('Use Chest in Forest', 'Chest', 'Press X to Open', True),
            ('Go To Boat in Harbor', 'Boat', 'Press X to Ride', True),
            ('Go To Marleybone in The Commons', 'World Gate', 'Press X to Teleport', True),
            ('前往 马利骨 地点：公共区', '世界之门', 'Press X to Enter', True),
            ('Talk To Merle in The Commons', 'World Gate', 'Press X to Teleport', False),
            ('Defeat Guard in The Commons', 'World Gate', 'Press X to Teleport', False),
            ('Go To Marleybone in The Commons', 'Vendor', 'Press X to Teleport', False),
            ('Use Chest in Forest', 'Chest', 'unrecognized prompt', False))
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock()) as objective, \
                patch('src.questing.get_popup_title', AsyncMock()) as title:
            for text, name, prompt, expected in cases:
                with self.subTest(prompt=(text, name, prompt)):
                    objective.return_value = text
                    title.return_value = name
                    self.quester.read_popup.return_value = prompt
                    self.assertEqual(await self.quester.quest_interaction_ready(self.client, self.target), expected)

    async def test_world_selection_opening_during_move_drains_only_own_movement(self):
        stopped = asyncio.Event()
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        async def move(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        peer = asyncio.create_task(asyncio.Event().wait())
        try:
            with patch('src.questing.is_spiral_door_open', AsyncMock(side_effect=[False, True])), \
                    patch('src.questing.collision_tp', side_effect=move):
                await asyncio.wait_for(self.quester.move_until_quest_interaction(self.client, self.target), 1)
            self.assertTrue(stopped.is_set())
            self.assertFalse(peer.done())
        finally:
            peer.cancel()
            await asyncio.gather(peer, return_exceptions=True)

    async def test_collect_or_open_prompt_uses_own_x_without_task_teleport(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        for prompt in ('Press X to Collect', 'Press X to Open', 'Press X to Ride'):
            self.client.send_key.reset_mock()
            self.quester.read_popup.return_value = prompt
            with patch('src.questing.asyncio.sleep', AsyncMock()):
                self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
            self.client.send_key.assert_awaited_once()
            self.client.teleport.assert_not_awaited()

    async def test_world_gate_reuses_existing_destination_handler(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Teleport'
        self.quester.new_world_doors = AsyncMock(return_value=False)
        with patch('src.questing.asyncio.sleep', AsyncMock()), \
                patch('src.questing.is_spiral_door_open', AsyncMock(return_value=True)), \
                patch('src.questing.spiral_door_with_quest', AsyncMock()) as travel:
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.quester.new_world_doors.assert_awaited_once_with(self.client)
        travel.assert_awaited_once_with(self.client)

    async def test_enter_prompt_keeps_existing_party_entry(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Enter'
        self.quester.prepare_party_dungeon_entry = AsyncMock(return_value=[self.client])
        self.quester.enter_party_dungeon = AsyncMock(return_value=True)
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.quester.enter_party_dungeon.assert_awaited_once_with([self.client])
        self.client.send_key.assert_not_awaited()

    async def test_task_change_before_x_does_not_use_old_collect_prompt(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Collect'
        self.client.goal_id.side_effect = [7, 8]
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_not_awaited()

    async def test_boat_raft_and_ride_prompts_use_own_x_without_dungeon_wait(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.prepare_party_dungeon_entry = AsyncMock()
        self.quester.enter_party_dungeon = AsyncMock()
        for prompt in ('Press X to Enter Boat', '按X 进入船',
                       'Press X to Use Magic Raft', '按下 X 使用魔法艇',
                       'Press X to Ride', '按下 X 骑乘', '按X骑到内陆'):
            with self.subTest(prompt=prompt):
                self.client.send_key.reset_mock()
                self.quester.read_popup.return_value = prompt
                with patch('src.questing.asyncio.sleep', AsyncMock()):
                    self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
                self.client.send_key.assert_awaited_once()
        self.quester.prepare_party_dungeon_entry.assert_not_awaited()
        self.quester.enter_party_dungeon.assert_not_awaited()
        self.client.teleport.assert_not_awaited()

    async def test_unknown_task_identity_never_sends_interaction_input(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Ride'
        for quest_id, goal_id, zone in ((None, 7, 'World/Zone'),
                                       (42, None, 'World/Zone'), (42, 7, ''),
                                       (True, 7, 'World/Zone')):
            with self.subTest(identity=(quest_id, goal_id, zone)):
                self.client.quest_id.return_value = quest_id
                self.client.goal_id.return_value = goal_id
                self.client.zone_name.return_value = zone
                self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_not_awaited()

    async def test_refill_or_cleanup_keeps_input_and_does_not_fall_back_to_movement(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        for flag in ('refilling_potions', 'post_combat_cleanup_active'):
            setattr(self.client, flag, True)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
            setattr(self.client, flag, False)
        self.client.send_key.assert_not_awaited()
