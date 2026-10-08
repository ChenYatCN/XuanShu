import asyncio
import ast
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.automation_ownership import get_client_automation_ownership
from src.paths import advance_dialog_path, npc_range_path
from src.questing import Quester
from tests.test_npc_mainline_menu import window


class OvergrownEstateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.zone = Quester.OVERGROWN_ESTATE_ZONE
        self.snapshot = (Quester.OVERGROWN_ESTATE_QUEST_ID, 640093639, '拜访 鲁娜 地点：Howling Lands')
        self.code = 'QuestTitle_00002170'
        self.position = XYZ(-190.645, -788.554, -1694.536)
        self.target = XYZ(-190.645, -788.554, -1696.281)
        self.dialogue = False
        self.tp_dialogue = True
        self.prompt = True
        self.region = 0
        self.current_clue = None
        self.events = []
        self.client = SimpleNamespace(title='p1', questing_status=True,
            quest_recovery_owner=None, refilling_potions=False, quest_party_status_session=None,
            quest_party_hitters=[], entity_detect_combat_status=False, quest_dungeon_recovery=None,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.snapshot[0]),
            goal_id=AsyncMock(side_effect=lambda: self.snapshot[1]),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(side_effect=lambda: self.dialogue),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)),
            root_window=window('root'), teleport=AsyncMock(), send_key=AsyncMock(),
            get_base_entity_list=AsyncMock(),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(side_effect=lambda code: code)))
        self.hitter = SimpleNamespace(title='p2', questing_status=True,
            zone_name=AsyncMock(return_value=self.zone), is_loading=AsyncMock(return_value=False),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.client.quest_party_hitters = [self.hitter]
        self.quester = Quester(self.client, [self.client], None)
        self.configure(self.quester)
        self.route = [XYZ(1000, 2000, -1200), XYZ(7000, 9000, -1600)]
        self.a = self.entity('DM_Estate_Clue_Silver', XYZ(1300, 2300, -1200))
        self.b = self.entity('DM_Estate_Clue_Hair', XYZ(1400, 2400, -1200))
        self.c = self.entity('DM_Estate_Clue_Cloth', XYZ(7300, 9300, -1600))
        self.d = self.entity('DM_Estate_Clue_Silver', XYZ(7400, 9400, -1600))
        self.other = self.entity('Other_DM_Estate_Clue_Trap', XYZ(1401, 2401, -1200))
        self.scenes = {0: [], 1: [self.a, self.b, self.other], 2: [self.c, self.d]}
        self.client.get_base_entity_list.side_effect = lambda: self.scenes[self.region]
        self.quester.get_zone_chunks = AsyncMock(side_effect=lambda client: self.route.copy())
        self.quester.read_popup = AsyncMock(side_effect=lambda c: '点击 X 收集' if self.current_clue else '按下 &Icons_XKey& 交谈')

        async def teleport(point):
            self.assert_owned()
            self.position = point
            self.events.append(('tp', point))
            self.current_clue = None
            if self.coords(point) == self.coords(Quester.OVERGROWN_ESTATE_DIALOGUE):
                self.dialogue = self.tp_dialogue
            for number, route in enumerate(self.route, 1):
                if self.coords(point) == (route.x, route.y, route.z - 550):
                    self.region = number
            for number, entities in self.scenes.items():
                for entity in entities:
                    if self.coords(point) == self.coords(entity.location.return_value):
                        self.region = number
                        self.current_clue = entity

        async def key(keycode, duration=.1):
            self.events.append(('key', keycode))
            if keycode == Keycode.X:
                self.assert_owned()
                if self.current_clue:
                    self.events.append(('clue_x', self.current_clue))
                else:
                    self.dialogue = True
            else:
                self.dialogue = False

        self.client.teleport.side_effect = teleport
        self.client.send_key.side_effect = key
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        real_sleep = asyncio.sleep
        async def sleep(seconds):
            self.now += seconds
            await real_sleep(0)
        self.stack.enter_context(patch('src.questing.asyncio.sleep', side_effect=sleep))
        self.visible = self.stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: self.dialogue if p == advance_dialog_path else
            self.prompt if p == npc_range_path and hasattr(self.client, '_xuanshu_overgrown_estate') else False)))
        self.free = self.stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(
            side_effect=lambda c: not self.dialogue and not c.is_loading.return_value
                          and not c.in_battle.return_value and not c.entity_detect_combat_status)))
        self.stack.enter_context(patch('src.questing.is_free', self.free))
        self.stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.read_dialogue_text', new=AsyncMock(
            side_effect=lambda c: 'Actual dialogue' if self.dialogue else '')))
        self.stack.enter_context(patch('src.questing.get_quest_name', new=AsyncMock(side_effect=lambda c: self.snapshot[2])))
        self.popup_title = self.stack.enter_context(patch('src.questing.get_popup_title', new=AsyncMock(
            side_effect=lambda c: self.current_clue.object_template.return_value.display_name.return_value
            if self.current_clue else '鲁娜')))
        self.collision = self.stack.enter_context(patch('src.questing.collision_tp', new=AsyncMock()))
        self.log = self.stack.enter_context(patch('src.questing.logger'))

    def configure(self, quester):
        quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot)
        quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            self.snapshot[0], self.code, 'Were-Dunnit', {'world': 'darkmoor', 'number': 53}, False))
        quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.snapshot[2])
        quester.move_until_quest_interaction = AsyncMock()
        async def dialogue_step(client):
            if self.dialogue:
                self.dialogue = False
                return True
            return False
        quester._quest_dialogue_blocks_movement = AsyncMock(side_effect=dialogue_step)

    @staticmethod
    def coords(point):
        return point.x, point.y, point.z

    def entity(self, name, xyz):
        return SimpleNamespace(object_name=AsyncMock(return_value=name),
            object_template=AsyncMock(return_value=SimpleNamespace(
                object_name=AsyncMock(return_value=name), display_name=AsyncMock(return_value=name + '_display'))),
            location=AsyncMock(return_value=xyz))

    def state(self):
        return self.client._xuanshu_overgrown_estate

    def assert_owned(self):
        self.assertEqual(self.client.quest_recovery_owner, 'overgrown_estate')
        self.assertTrue(get_client_automation_ownership(self.client).locked)

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def tick(self, seconds=1):
        self.now += seconds
        result = await self.quester._maybe_handle_overgrown_estate(self.client)
        self.assert_released()
        return result

    async def move(self, *times):
        for self.now in times:
            await self.quester.teleport_to_quest_target(self.client, self.target)
            self.assert_released()

    async def start(self):
        await self.move(0, 4, 8, 11)
        self.assertEqual(self.state()['phase'], 'dialogue_wait')

    async def until(self, phase):
        for _ in range(120):
            if self.state()['phase'] == phase:
                return
            await self.tick()
        self.fail(f'Expected {phase}, actual {self.state()["phase"]}')

    async def test_three_stalled_tps_ten_seconds_then_exact_dialogue_point(self):
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once_with(Quester.OVERGROWN_ESTATE_DIALOGUE)
        self.assertEqual(self.coords(self.position), (777.050, 1078.755, -1699.321))
        self.assertEqual(self.collision.await_count, 4)
        self.client.send_key.assert_not_awaited()

    async def test_two_tps_do_not_trigger_even_after_ten_seconds(self):
        await self.move(0, 12)
        self.client.teleport.assert_not_awaited()

    async def test_goal_progress_resets_existing_watch(self):
        await self.move(0, 4, 8)
        self.snapshot = (self.snapshot[0], 640093640, self.snapshot[2])
        await self.move(11, 15, 19)
        self.client.teleport.assert_not_awaited()

    async def test_body_and_hud_coordinate_jitter_is_not_goal_progress(self):
        await self.move(0, 4, 8)
        self.position = XYZ(50, 2000, 0)
        self.target = XYZ(99, 3333, -1696)
        await self.move(11)
        self.client.teleport.assert_awaited_once_with(Quester.OVERGROWN_ESTATE_DIALOGUE)

    async def test_wrong_zone_id_key_and_unreadable_goal_never_fallback(self):
        original = self.snapshot
        for zone, code, snapshot in (
            ('Darkmoor/DM_Z03_HowlingLands', self.code, original),
            (Quester.OVERGROWN_ESTATE_ZONE, 'QuestTitle_18F18A', original),
            (Quester.OVERGROWN_ESTATE_ZONE, self.code, (1, original[1], original[2])),
            (Quester.OVERGROWN_ESTATE_ZONE, self.code, (original[0], None, original[2]))):
            self.zone, self.code, self.snapshot = zone, code, snapshot
            await self.move(0, 4, 8, 11)
            self.quester._krok_exit_watch.clear()
        self.client.teleport.assert_not_awaited()

    async def test_assigned_hitter_and_status_session_are_not_recovery_owners(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertIsNone(await self.quester._overgrown_estate_stage(self.client))
        self.quester.clients.pop()
        self.client.quest_party_status_session = object()
        self.assertIsNone(await self.quester._overgrown_estate_stage(self.client))

    async def test_full_region_search_prefix_only_all_distinct_entities_then_wait(self):
        await self.start()
        await self.until('wait_player')
        picked = [entity for kind, entity in self.events if kind == 'clue_x']
        self.assertEqual(picked, [self.a, self.b, self.c, self.d])
        self.assertNotIn(self.other, picked)
        points = [self.coords(p) for kind, p in self.events if kind == 'tp']
        self.assertIn((1000, 2000, -1750), points)
        self.assertIn((7000, 9000, -2150), points)
        self.assertEqual(points[-1], (3108.774, 1960.014, -1714.557))
        self.assertEqual(len(self.state()['done']), 4)
        self.quester.get_zone_chunks.assert_awaited_once_with(self.client)
        self.hitter.send_key.assert_not_awaited()
        self.hitter.teleport.assert_not_awaited()

    async def test_duplicate_streamed_instances_same_location_not_interacted_twice(self):
        self.scenes[2].append(self.entity('DM_Estate_Clue_Silver', self.a.location.return_value))
        await self.start()
        await self.until('wait_player')
        self.assertEqual(len([e for k, e in self.events if k == 'clue_x']), 4,
                         [(k, self.coords(e) if k == 'tp' else e.object_name.return_value if k == 'clue_x' else e)
                          for k, e in self.events])

    async def test_existing_loaded_region_is_scanned_before_roaming(self):
        self.scenes[0] = [self.a]
        await self.start()
        await self.until('wait_player')
        points = [self.coords(p) for k, p in self.events if k == 'tp']
        self.assertLess(points.index(self.coords(self.a.location.return_value)), points.index((1000, 2000, -1750)))

    async def test_actual_dialogue_must_end_before_search(self):
        await self.start()
        self.quester._quest_dialogue_blocks_movement.side_effect = None
        self.quester._quest_dialogue_blocks_movement.return_value = True
        for _ in range(4):
            await self.tick()
        self.quester.get_zone_chunks.assert_not_awaited()
        self.assertEqual(self.state()['phase'], 'dialogue_wait')

    async def test_unseen_dialogue_never_skips_to_search(self):
        self.tp_dialogue = False
        await self.start()
        self.prompt = False
        await self.tick(16)
        self.assertEqual(self.state()['phase'], 'failed')
        self.quester.get_zone_chunks.assert_not_awaited()
        await self.tick()
        self.client.teleport.assert_awaited_once()

    async def test_talk_popup_press_x_then_shared_dialogue_handoff(self):
        self.tp_dialogue = False
        await self.start()
        await self.tick()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        await self.tick()
        await self.until('search_init')
        self.assertTrue(self.state()['dialogue_seen'])

    async def test_wrong_clue_popup_never_presses_x(self):
        await self.start()
        await self.until('clue_interact')
        self.popup_title.side_effect = None
        self.popup_title.return_value = 'Other NPC'
        await self.tick()
        self.assertEqual(self.state()['phase'], 'failed')
        self.client.send_key.assert_not_awaited()

    async def test_unconfirmed_clue_landing_never_presses_x(self):
        await self.start()
        await self.until('clue_move')
        self.client.teleport.side_effect = None
        await self.tick()
        await self.tick(6)
        self.assertEqual(self.state()['phase'], 'failed')
        self.client.send_key.assert_not_awaited()

    async def test_missing_navigation_never_claims_full_search(self):
        self.quester.get_zone_chunks.side_effect = None
        self.quester.get_zone_chunks.return_value = []
        await self.start()
        await self.until('failed')
        self.assertFalse(Quester.overgrown_estate_paused(self.client, manual_only=True))
        self.client.send_key.assert_not_awaited()

    async def test_empty_full_scan_is_not_clue_completion(self):
        self.scenes = {0: [], 1: [], 2: []}
        await self.start()
        await self.until('failed')
        self.assertFalse(Quester.overgrown_estate_paused(self.client, manual_only=True))

    async def test_player_wait_logs_exact_bilingual_mappings_once(self):
        await self.start()
        await self.until('wait_player')
        for _ in range(5):
            await self.tick()
        messages = [call.args[0] for call in self.log.info.call_args_list if 'Tranett = ' in call.args[0]]
        self.assertEqual(len(messages), 1)
        for text in ('Tranett = Silver Necklace', '特拉内特 = 银项链',
                     'Tawni = Lock of Hair', 'Tawni = 一绺头发', 'Ignacio = Red Cloth',
                     'Ignacio = 红色布料', 'Dimiti = Strange Footprints', 'Dimiti = 奇怪的脚印'):
            self.assertIn(text, messages[0])

    async def test_only_both_nonloading_clients_outside_complete(self):
        await self.start()
        await self.until('wait_player')
        before = len(self.events)
        self.zone = 'Darkmoor/DM_Z00_OutsidersCamp'
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')
        self.hitter.zone_name.return_value = 'Different/Outside'
        self.hitter.is_loading.return_value = True
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')
        self.hitter.is_loading.return_value = False
        await self.tick()
        self.assertEqual(self.state()['phase'], 'completed')
        self.assertFalse(Quester.overgrown_estate_paused(self.hitter))
        self.assertEqual(len(self.events), before)
        self.assertFalse(await self.tick())

    async def test_first_exit_does_not_pull_hitter_back_or_resume_tp(self):
        await self.start()
        await self.until('wait_player')
        self.hitter.zone_name.return_value = 'Darkmoor/DM_Z03_HowlingLands'
        count = self.client.teleport.await_count
        await self.quester.teleport_to_quest_target(self.client, XYZ(0, 0, 0))
        self.assertEqual(self.state()['phase'], 'wait_player')
        self.assertEqual(self.client.teleport.await_count, count)
        self.hitter.teleport.assert_not_awaited()

    async def test_empty_or_unreadable_peer_zone_is_not_completion(self):
        await self.start()
        await self.until('wait_player')
        self.zone = 'Outside'
        self.hitter.zone_name.return_value = ''
        await self.tick()
        self.hitter.zone_name.side_effect = RuntimeError('read failed')
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')

    async def test_one_client_does_not_satisfy_two_client_completion(self):
        await self.start()
        await self.until('wait_player')
        self.state()['participants'] = [self.client]
        self.zone = 'Outside'
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')

    async def test_cancelled_pickup_not_replayed_after_worker_recreation(self):
        await self.start()
        await self.until('clue_interact')
        self.client.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tick()
        self.assert_released()
        self.assertEqual(self.state()['phase'], 'clue_settle')
        self.quester = Quester(self.client, [self.client], None)
        self.configure(self.quester)
        self.quester.read_popup = AsyncMock(return_value='点击 X 收集')
        await self.tick(2)
        await self.tick()
        self.client.send_key.assert_awaited_once()
        self.assertNotEqual(self.state()['candidate'].key, next(iter(self.state()['done'])))

    async def test_cancelled_dialogue_tp_not_replayed(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 4, 8, 11)
        self.assert_released()
        self.assertEqual(self.state()['phase'], 'dialogue_wait')
        self.prompt = False
        await self.tick(16)
        await self.tick()
        self.client.teleport.assert_awaited_once()

    async def test_battle_loading_refill_probe_and_other_priority_defer_inputs(self):
        await self.start()
        await self.until('clue_move')
        count = self.client.teleport.await_count
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.tick()
            setattr(self.client, attr, False)
        for method in (self.client.is_loading, self.client.in_battle):
            method.return_value = True
            await self.tick()
            method.return_value = False
        self.assertEqual(self.client.teleport.await_count, count)
        self.client.send_key.assert_not_awaited()

    async def test_peer_cannot_start_duplicate_search(self):
        await self.start()
        self.assertTrue(await self.quester._maybe_handle_overgrown_estate(self.hitter))
        self.hitter.teleport.assert_not_awaited()
        self.hitter.send_key.assert_not_awaited()

    async def test_template_prefix_fallback_uses_existing_entity_metadata(self):
        self.a.object_name.return_value = 'Basic Positional'
        await self.start()
        await self.until('wait_player')
        self.assertIn(self.a, [e for k, e in self.events if k == 'clue_x'])

    async def test_late_streamed_clues_are_loaded_before_a_region_is_exhausted(self):
        original = self.client.get_base_entity_list.side_effect
        samples = 0
        def delayed_entities():
            nonlocal samples
            if self.region == 1:
                samples += 1
                if samples <= 2:
                    return []
            return original()
        self.client.get_base_entity_list.side_effect = delayed_entities
        await self.start()
        await self.until('wait_player')
        self.assertGreaterEqual(samples, 3)
        self.assertEqual(len([e for k, e in self.events if k == 'clue_x']), 4)

    async def test_expired_pointer_is_reacquired_at_its_verified_position(self):
        await self.start()
        await self.until('clue_move')
        stale = self.state()['candidate'].entity
        fresh = self.entity(stale.object_name.return_value, stale.location.return_value)
        self.scenes[1] = [fresh if e is stale else e for e in self.scenes[1]]
        stale.location.side_effect = ValueError('unloaded pointer')
        await self.until('wait_player')
        picked = [e for k, e in self.events if k == 'clue_x']
        self.assertIn(fresh, picked)
        self.assertNotIn(stale, picked)

    async def test_matching_clue_investigation_prompt_is_not_tied_to_collect_goal(self):
        await self.start()
        await self.until('clue_interact')
        self.quester.read_popup.side_effect = None
        self.quester.read_popup.return_value = '按下 X 调查线索'
        await self.tick()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertEqual(self.state()['phase'], 'clue_settle')

    async def test_sigil_near_clue_never_receives_x(self):
        await self.start()
        await self.until('clue_interact')
        self.quester.read_popup.side_effect = None
        self.quester.read_popup.return_value = '点击 X 进入'
        await self.tick(6)
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.state()['phase'], 'failed')

    async def test_cancelled_region_tp_does_not_skip_unvisited_region(self):
        await self.start()
        await self.until('region_move')
        count = self.client.teleport.await_count
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tick()
        self.assert_released()
        self.assertEqual(self.state()['cursor'], 0)
        await self.tick(6)
        self.assertEqual(self.state()['phase'], 'failed')
        self.assertEqual(self.client.teleport.await_count, count + 1)
        self.client.send_key.assert_not_awaited()

    async def test_failed_return_landing_never_starts_manual_wait_or_logs_clues(self):
        await self.start()
        await self.until('return_move')
        self.client.teleport.side_effect = None
        await self.tick()
        await self.tick(6)
        self.assertEqual(self.state()['phase'], 'failed')
        self.assertFalse(any('Tranett = ' in call.args[0] for call in self.log.info.call_args_list))

    async def test_late_priority_during_popup_read_prevents_x(self):
        await self.start()
        await self.until('clue_interact')
        original = self.popup_title.side_effect
        async def interrupted(client):
            client.refilling_potions = True
            return original(client)
        self.popup_title.side_effect = interrupted
        await self.tick()
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.state()['phase'], 'clue_interact')

    async def test_client_reentering_during_peer_zone_read_is_not_completion(self):
        await self.start()
        await self.until('wait_player')
        self.zone = 'Outside'
        async def changed():
            self.zone = Quester.OVERGROWN_ESTATE_ZONE
            return 'Other/Outside'
        self.hitter.zone_name.side_effect = changed
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')

    async def test_original_solo_iteration_stops_generic_flow_after_watch_starts(self):
        await self.move(0, 4, 8)
        self.now = 11
        self.client.use_potions = False
        self.client.auto_pet_status = False
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        self.quester._mainline_sync_blocks_movement = AsyncMock(return_value=False)
        self.quester._maybe_recover_mainline = AsyncMock(return_value=False)
        self.quester._maybe_refresh_stalled_dungeon_quest = AsyncMock(return_value=False)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester.handle_questing_zone_change = AsyncMock()
        self.quester.auto_collect_rewrite = AsyncMock()
        with patch('src.questing.close_npc_quest_menu', new=AsyncMock(return_value=False)), \
             patch('src.questing.close_automation_popup', new=AsyncMock(return_value=False)), \
             patch('src.questing.is_potion_needed', new=AsyncMock(return_value=False)), \
             patch('src.mainline_progress.log_mainline_progress', new=AsyncMock()):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.state()['phase'], 'dialogue_wait')
        self.quester.handle_questing_zone_change.assert_not_awaited()
        self.quester.auto_collect_rewrite.assert_not_awaited()

    async def test_optional_chunk_client_uses_actual_leader_zone_not_original_client(self):
        other = SimpleNamespace(zone_name=AsyncMock(return_value='Other/Original'))
        self.quester.client = other
        wad = SimpleNamespace(get_file=AsyncMock(return_value=b'verified-nav'))
        self.quester.load_wad = AsyncMock(return_value=wad)
        with patch('src.questing.parse_nav_data', return_value=([1], [])), \
             patch('src.questing.calc_chunks', return_value=self.route):
            self.assertEqual(await Quester.get_zone_chunks(self.quester, self.client), self.route)
            self.quester.load_wad.assert_awaited_with(Quester.OVERGROWN_ESTATE_ZONE)
            await Quester.get_zone_chunks(self.quester)
            self.quester.load_wad.assert_awaited_with('Other/Original')

    async def test_actual_dialogue_loop_does_not_advance_player_controlled_dialogue(self):
        from src.task_lifecycle import gather_owned
        await self.start()
        await self.until('wait_player')
        self.dialogue = True  # The player opened a dialogue during manual completion.
        count = self.client.send_key.await_count
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dialogue_loop')
        namespace = dict(asyncio=asyncio, walker=SimpleNamespace(clients=[self.client]),
            freecam_status=False, Quester=Quester, gather_owned=gather_owned,
            is_visible_by_path=self.visible, advance_dialog_path=advance_dialog_path,
            side_quest_status=False, Keycode=Keycode)
        exec(compile('from __future__ import annotations\n' + ast.unparse(function), 'XuanShu.py', 'exec'), namespace)
        task = asyncio.create_task(namespace['dialogue_loop']())
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertEqual(self.client.send_key.await_count, count)
            self.assertTrue(self.client.auto_dialogue_running)
            self.assertTrue(self.dialogue)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_priority_takeover_while_clue_streams_defers_without_failed_pickup(self):
        await self.start()
        await self.until('clue_interact')
        original = self.client.get_base_entity_list.side_effect
        def busy_entities():
            self.client.refilling_potions = True
            return original()
        self.client.get_base_entity_list.side_effect = busy_entities
        await self.tick(6)
        self.assertEqual(self.state()['phase'], 'clue_interact')
        self.client.send_key.assert_not_awaited()

    async def test_unreadable_initial_stage_defers_instead_of_aborting_visit(self):
        await self.start()
        self.dialogue = False
        self.state()['phase'] = 'dialogue_move'
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        count = self.client.teleport.await_count
        await self.tick()
        self.assertEqual(self.state()['phase'], 'dialogue_move')
        self.assertEqual(self.client.teleport.await_count, count)

    async def test_same_client_listed_twice_is_not_two_players_leaving(self):
        await self.start()
        await self.until('wait_player')
        self.state()['participants'] = [self.client, self.client]
        self.zone = 'Outside'
        await self.tick()
        self.assertEqual(self.state()['phase'], 'wait_player')

    async def test_stopped_owner_and_unrelated_client_not_paused(self):
        await self.start()
        self.client.questing_status = False
        self.assertFalse(Quester.overgrown_estate_paused(self.client))
        self.assertFalse(Quester.overgrown_estate_paused(self.hitter))
        self.assertFalse(Quester.overgrown_estate_paused(SimpleNamespace(questing_status=True)))

    async def test_generic_reentry_yields_exact_dungeon_stage(self):
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.teleport.assert_not_awaited()

    async def test_dialogue_worker_records_short_page_before_it_closes(self):
        await self.start()
        self.quester._window_text = AsyncMock(return_value='完成')
        button = window('btnRight', '完成')
        async def complete(client, target):
            self.assertIs(target, button)
            self.dialogue = False
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=button)), \
                patch.object(self.quester, '_click_ui_window', new=AsyncMock(side_effect=complete)):
            self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.assertTrue(self.state()['dialogue_seen'])
        self.assertFalse(self.dialogue)

    def test_active_handler_precedes_worker_dialogue_and_probe(self):
        tree = ast.parse(Path('src/questing.py').read_text(encoding='utf-8'))
        for name in ('auto_quest_solo', 'auto_quest_leader'):
            method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            calls = [n for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            estate = min(n.lineno for n in calls if n.func.attr == '_maybe_handle_overgrown_estate')
            dialogue = min(n.lineno for n in calls if n.func.attr == '_quest_dialogue_blocks_movement')
            self.assertLess(estate, dialogue)

    def test_follower_dialogue_and_both_watchdogs_honor_shared_state(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == 'overgrown_estate_paused']
        self.assertGreaterEqual(len(calls), 5)
        owner_lists = [n for n in ast.walk(tree) if isinstance(n, (ast.Tuple, ast.List))
                       and any(isinstance(v, ast.Constant) and v.value == 'overgrown_estate' for v in n.elts)]
        self.assertGreaterEqual(len(owner_lists), 3)


if __name__ == '__main__':
    unittest.main()
