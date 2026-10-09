import asyncio
import unittest
from types import SimpleNamespace
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
        self.client.quest_party_hitters = []
        self.client.quest_id.return_value = 42
        self.client.goal_id.return_value = 7
        self.client.zone_name.return_value = 'World/Zone'
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value='Press X to Talk')
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.target = XYZ(20, 30, 0)
        self.client.quest_position.position.return_value = self.target
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.free = AsyncMock(return_value=True)
        patcher = patch('src.questing.is_free_leader_questing', new=self.free)
        patcher.start()
        self.addCleanup(patcher.stop)
        portal = patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False))
        portal.start()
        self.addCleanup(portal.stop)

    async def test_unknown_prompt_gets_a_checked_attempt_and_shared_cooldown(self):
        self.quester.read_popup.return_value = '按X 未知动作'
        self.client.body.position.return_value = self.target
        with (patch('src.questing.is_visible_by_path', AsyncMock(
                side_effect=lambda client, path: path == ['WorldView', 'NPCRangeWin'])),
              patch('src.questing.asyncio.sleep', AsyncMock()),
              patch('src.questing.time.monotonic', return_value=10.0)):
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
            other = Quester(self.client, [self.client], None)
            other.read_popup = self.quester.read_popup
            self.assertTrue(await other.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_awaited_once()
        self.assertEqual(self.client.quest_interaction_attempt['attempts'], 1)
        self.client.quest_id.assert_awaited()  # Check the actual post-input outcome.

    async def test_unknown_prompt_no_progress_is_bounded_and_does_not_permanently_hold_movement(self):
        self.quester.read_popup.return_value = '按X 未知动作'
        self.client.body.position.return_value = self.target
        now = [10.0]
        with (patch('src.questing.is_visible_by_path', AsyncMock(
                side_effect=lambda client, path: path == ['WorldView', 'NPCRangeWin'])),
              patch('src.questing.asyncio.sleep', AsyncMock()),
              patch('src.questing.time.monotonic', side_effect=lambda: now[0])):
            await self.quester.handle_quest_interaction(self.client, self.target)
            now[0] = 12.0
            await self.quester.handle_quest_interaction(self.client, self.target)
            now[0] = 14.0
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            now[0] = 20.0
            await self.quester.handle_quest_interaction(self.client, self.target)
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_generic_x_never_uses_stale_coordinates_or_stopped_task(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Use'
        self.client.quest_position.position.return_value = XYZ(300, 400, 0)
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.client.questing_status = False
        self.client.quest_position.position.return_value = self.target
        await self.quester.handle_quest_interaction(self.client, self.target)
        self.client.send_key.assert_not_awaited()

    async def test_goal_change_cancels_and_drains_ordinary_movement(self):
        started = asyncio.Event()
        drained = asyncio.Event()
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        self.quester._maybe_reenter_quest_trigger = AsyncMock(return_value=False)
        async def move(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        with patch('src.questing.navmap_tp', AsyncMock(side_effect=move)):
            task = asyncio.create_task(self.quester.move_until_quest_interaction(self.client, self.target))
            await started.wait()
            self.client.goal_id.return_value = 8
            await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())

    def test_match_chinese_and_english_target(self):
        self.assertTrue(quest_interaction_matches('使用 配置站 地点：星辰区', '配置站'))
        self.assertTrue(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Configuration Station'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'District of the Stars'))
        self.assertFalse(quest_interaction_matches('Use Configuration Station in District of the Stars', 'Station Vendor'))
        self.assertFalse(quest_interaction_matches('Defeat Guard in District of the Stars', 'Guard'))
        self.assertFalse(quest_interaction_matches('Use Cartography in District of the Stars', 'Cart'))

    def test_verified_object_aliases_do_not_guess_other_levers(self):
        for goal in ('使用 旗杆操纵杆 地点：海象堡', 'Use Flag Control Lever in Walruskberg'):
            for title in ('标志控制杆', '旗杆操纵杆', 'Flag Control Lever'):
                self.assertTrue(quest_interaction_matches(goal, title))
            for title in ('旗杆杠杆', 'Flag Pole Lever', '海象堡'):
                self.assertFalse(quest_interaction_matches(goal, title))
        self.assertFalse(quest_interaction_matches('击败 旗杆操纵杆 地点：海象堡', '标志控制杆'))

    async def test_real_use_alias_sends_own_input_after_target_validation(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = '按X或<icon;mouse>使用'
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='使用 旗杆操纵杆 地点：海象堡')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='标志控制杆')), \
                patch('src.questing.asyncio.sleep', AsyncMock()), \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_awaited_once()
        move.assert_not_awaited()

    async def test_415_unknown_prompt_uses_x_without_repeated_teleport(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = '按X 未知动作'
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='使用 旗杆操纵杆 地点：海象堡')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='标志控制杆')), \
                patch('src.questing.asyncio.sleep', AsyncMock()) as pause, \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        move.assert_not_awaited()
        self.client.send_key.assert_awaited_once()
        pause.assert_awaited_once_with(.75)

    async def test_415_sand_sea_portal_does_not_require_destination_in_title(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = '<center>按下 <image;GUI/Art/Art_Keyboard_X.dds> 传送</center>'
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='把传送门带到 沙海 地点：沙海')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='门户网站')), \
                patch('src.questing.asyncio.sleep', AsyncMock()), \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        move.assert_not_awaited()
        self.client.send_key.assert_awaited_once()

    async def test_415_x_still_requires_nearby_visible_window_and_free_client(self):
        self.quester.read_popup.return_value = '按X 未知动作'
        for visible, position, loading, battle, free in (
                (False, self.target, False, False, True),
                (True, XYZ(2000, 30, 0), False, False, True),
                (True, self.target, True, False, True),
                (True, self.target, False, True, True),
                (True, self.target, False, False, False)):
            with self.subTest(state=(visible, position, loading, battle, free)), \
                    patch('src.questing.is_visible_by_path', AsyncMock(return_value=visible)):
                self.client.body.position.return_value = position
                self.client.is_loading.return_value = loading
                self.client.in_battle.return_value = battle
                self.free.return_value = free
                await self.quester.handle_quest_interaction(self.client, self.target)
        self.client.send_key.assert_not_awaited()

    async def test_generic_prompt_change_before_x_never_inputs(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.side_effect = ['Press X to Use', 'Press X to Read']
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_not_awaited()

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

    async def test_cave_goal_never_stops_at_nearby_riley_talk_popup(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = XYZ(self.target.x - 227, self.target.y, self.target.z)
        self.quester.read_popup.return_value = 'Press X to Talk'
        self.quester.handle_npc_talking_quests = AsyncMock(return_value=True)
        self.quester._maybe_reenter_quest_trigger = AsyncMock(return_value=False)
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='前往 怪客的洞穴 地点：沥青洞穴')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='莱利·长穴')), \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            self.assertFalse(await self.quester.quest_interaction_ready(
                self.client, self.target, require_objective_match=False))
            self.assertFalse(await self.quester.handle_quest_interaction(self.client, self.target))
            await self.quester.move_until_quest_interaction(self.client, self.target)
        move.assert_awaited_once_with(self.client, self.target, leader_client=None)
        self.quester.handle_npc_talking_quests.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_matching_npc_still_takes_priority_over_ordinary_teleport(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = 'Press X to Talk'
        self.quester.handle_npc_talking_quests = AsyncMock(return_value=True)
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='Talk To Merle in The Commons')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='Merle')), \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        move.assert_not_awaited()
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.client, [self.client])

    async def test_ordinary_talk_goal_keeps_existing_npc_name_tolerance(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = 'Press X to Talk'
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='拜访 莱利 地点：沥青洞穴')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='莱利·长穴')):
            self.assertFalse(quest_interaction_matches('拜访 莱利 地点：沥青洞穴', '莱利·长穴'))
            self.assertTrue(await self.quester.quest_interaction_ready(
                self.client, self.target, require_objective_match=False))

    async def test_manual_and_auto_navmap_both_finish_retreat_and_walk_past_wrong_npc(self):
        from src import teleport_math
        self.target = XYZ(200, 0, 0)
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.zone_name.return_value = 'World/Zone'
        self.quester.read_popup.return_value = 'Press X to Talk'
        self.quester._maybe_reenter_quest_trigger = AsyncMock(return_value=False)
        position = [XYZ(-27, 0, 0)]
        visible = [False]
        landed = []
        walked = []
        self.client.body.position.side_effect = lambda: position[0]
        async def teleport(point):
            landed.append((point.x, point.y, point.z))
            visible[0] = True  # Unrelated NPC popup appears during TP settling.
            if len(landed) > 1:
                position[0] = point
        async def goto(x, y):
            walked.append((x, y))
            position[0] = XYZ(x, y, 0)
        self.client.teleport.side_effect = teleport
        self.client.goto.side_effect = goto
        real_sleep = asyncio.sleep
        async def tick(_):
            await real_sleep(0)
        wad = SimpleNamespace(get_file=AsyncMock(return_value=b'nav'))
        vertices = [XYZ(200, 0, 0), XYZ(-1000, 0, 0)]
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda *_: visible[0])), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='前往 怪客的洞穴 地点：沥青洞穴')), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='莱利·长穴')), \
                patch('src.teleport_math.is_free', AsyncMock(return_value=True)), \
                patch('src.teleport_math.load_wad', AsyncMock(return_value=wad)), \
                patch('src.teleport_math.parse_nav_data', return_value=(vertices, [])), \
                patch('src.teleport_math.get_neighbors', side_effect=lambda v, *_: [p for p in vertices if p is not v]), \
                patch('src.questing.asyncio.sleep', new=tick):
            for mode in ('manual', 'auto'):
                with self.subTest(mode=mode):
                    position[0] = XYZ(-27, 0, 0)
                    visible[0] = False
                    landed.clear()
                    walked.clear()
                    if mode == 'manual':
                        await teleport_math.navmap_tp(self.client, self.target)
                    else:
                        await self.quester.move_until_quest_interaction(self.client, self.target)
                    self.assertEqual(landed, [(200, 0, 0), (-100, 0, 0)])
                    self.assertEqual(walked, [(200, 0)])

    async def test_existing_prompt_skips_teleport(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        with patch('src.questing.navmap_tp', new=AsyncMock()) as move:
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
        with patch('src.questing.navmap_tp', side_effect=move):
            await asyncio.wait_for(self.quester.move_until_quest_interaction(self.client, self.target), 1)
        self.assertTrue(stopped.is_set())

    async def test_no_prompt_preserves_normal_movement(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing.navmap_tp', new=AsyncMock()) as move:
            await self.quester.move_until_quest_interaction(self.client, self.target)
        move.assert_awaited_once_with(self.client, self.target, leader_client=None)

    async def test_existing_dialogue_skips_movement_even_without_target_prompt(self):
        self.free.return_value = False
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing.navmap_tp', new=AsyncMock()) as move:
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
            with patch('src.questing.navmap_tp', side_effect=move):
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
        with patch('src.questing.navmap_tp', side_effect=move):
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
            ('Use Chest in Forest', 'Chest', 'unrecognized prompt', True),
            ('Use Chest in Forest', 'Vendor', 'unrecognized prompt', False),
            ('Use Chest in Forest', 'Chest', '', False),
            ('Use Chest in Forest', 'Chest', '&lt;string;GUI_00000003&gt;', True))
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
                    patch('src.questing.navmap_tp', side_effect=move):
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
        self.quester.party_dungeon_entry_visible = AsyncMock(return_value=True)
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.assertEqual(self.quester.enter_party_dungeon.await_args.args, ([self.client],))
        self.client.send_key.assert_not_awaited()

    async def test_visible_team_up_allows_translated_or_inside_target_and_stops_movement(self):
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = 'Press X to Enter'
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='格罗夫勋爵的洞穴')), \
                patch('src.questing.get_quest_name', AsyncMock()) as objective, \
                patch('src.questing.navmap_tp', AsyncMock()) as move:
            for text in ('前往 Grof的洞穴 地点：冻泪河', 'Defeat Grof in Cave'):
                objective.return_value = text
                self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
                await self.quester.move_until_quest_interaction(self.client, self.target)
            move.assert_not_awaited()
            self.client.quest_id.return_value = 0
            self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_hidden_team_up_does_not_classify_ordinary_enter_as_party_dungeon(self):
        from src.paths import team_up_wait_path, team_up_button_path
        self.client.quest_party_hitters = []
        self.client.is_loading.return_value = self.client.in_battle.return_value = False
        self.client.body.position.return_value = self.target
        self.quester.read_popup.return_value = 'Press X to Enter'
        self.quester.prepare_party_dungeon_entry = AsyncMock()
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p not in (team_up_wait_path, team_up_button_path))), \
                patch('src.questing.get_popup_title', AsyncMock(return_value='House')), \
                patch('src.questing.get_quest_name', AsyncMock(return_value='Go To House in Town')), \
                patch('src.questing.asyncio.sleep', AsyncMock()):
            self.assertFalse(await self.quester.party_dungeon_entry_visible(self.client))
            self.quester.quest_interaction_ready = AsyncMock(return_value=True)
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.quester.prepare_party_dungeon_entry.assert_not_awaited()
        self.client.send_key.assert_awaited_once()

    async def test_task_change_before_x_does_not_use_old_collect_prompt(self):
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Collect'
        self.client.goal_id.side_effect = [7, 8]
        self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.client.send_key.assert_not_awaited()

    async def test_ordinary_enter_with_assigned_hitter_never_falls_back_to_own_x(self):
        self.client.quest_party_hitters = [AsyncMock()]
        self.client.in_solo_zone = False
        self.client.quest_party_group_dungeon_zone = None
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.read_popup.return_value = 'Press X to Enter'
        self.quester.party_dungeon_entry_visible = AsyncMock(return_value=False)
        self.quester.prepare_party_dungeon_entry = AsyncMock(return_value=[self.client])
        self.quester.enter_party_dungeon = AsyncMock(return_value=False)
        with patch('src.questing.get_popup_title', AsyncMock(return_value='House')):
            self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
        self.quester.prepare_party_dungeon_entry.assert_awaited_once()
        self.quester.enter_party_dungeon.assert_awaited_once()
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
