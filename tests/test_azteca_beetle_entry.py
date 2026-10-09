import ast
import asyncio
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from src.collect_matching import parse_collect_goal
from src.collecting import CollectSearch, SearchState
from src.questing import Quester


class AztecaBeetleEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.zone = CollectSearch.AZTECA_BEETLE_SOURCE
        self.text = '收集 胭脂甲虫 地点：暴雨森林 (0 of 4)'
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            quest_id=AsyncMock(return_value=42),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            teleport=AsyncMock(), send_key=AsyncMock(),
            get_base_entity_list=AsyncMock(return_value=[]))
        self.other = SimpleNamespace(teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = SimpleNamespace(
            clients=[self.client, self.other],
            read_quest_txt=AsyncMock(side_effect=lambda _: self.text),
            get_zone_chunks=AsyncMock())
        self.engine = CollectSearch(self.quester, self.client)
        async def teleport(point):
            self.assertEqual((point.x, point.y, point.z), (-4447.606, 5160.961, 493.799))
            self.assertEqual(self.client.quest_recovery_owner, 'azteca_beetle_entry')
            self.zone = CollectSearch.AZTECA_BEETLE_DESTINATION
        self.client.teleport.side_effect = teleport
        async def scan(_):
            self.assertEqual(self.zone, CollectSearch.AZTECA_BEETLE_DESTINATION)
            return True
        self.engine.scan = AsyncMock(side_effect=scan)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.free = self.stack.enter_context(patch('src.collecting.is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.collecting.installed_catalog', return_value=None))
        self.stack.enter_context(patch('src.collecting.time', SimpleNamespace(monotonic=lambda: self.now)))
        async def tick(seconds):
            self.now += seconds
        self.stack.enter_context(patch('src.collecting.asyncio.sleep', AsyncMock(side_effect=tick)))

    async def test_entry_then_destination_search_replaces_source_route(self):
        old = SearchState((self.zone, 42, parse_collect_goal(self.text).key, 4), route=[XYZ(-99, 0, 0)])
        self.client._deimos_collect_search = old
        self.assertTrue(await self.engine.run())
        self.client.teleport.assert_awaited_once_with(CollectSearch.AZTECA_BEETLE_ENTRY)
        self.assertIsNot(self.engine.state, old)
        self.assertEqual(self.engine.state.key[0], CollectSearch.AZTECA_BEETLE_DESTINATION)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.other.teleport.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_full_map_chunks_are_read_only_after_destination_confirmed(self):
        self.engine.scan.side_effect = [False, True]
        async def chunks():
            self.assertEqual(self.zone, CollectSearch.AZTECA_BEETLE_DESTINATION)
            return [XYZ(10, 20, 30)]
        self.quester.get_zone_chunks.side_effect = chunks
        self.engine.loaded_entities = AsyncMock(return_value=[])
        async def move(point):
            if self.client.teleport.await_count == 1:
                self.assertEqual(point, CollectSearch.AZTECA_BEETLE_ENTRY)
                self.zone = CollectSearch.AZTECA_BEETLE_DESTINATION
        self.client.teleport.side_effect = move
        self.assertTrue(await self.engine.run())
        self.quester.get_zone_chunks.assert_awaited_once()
        point = self.client.teleport.await_args_list[1].args[0]
        self.assertEqual((point.x, point.y, point.z), (10, 20, -520))

    async def test_waits_for_loading_and_three_stable_destination_reads(self):
        reads = [0]
        async def loading():
            if self.client.teleport.await_count:
                reads[0] += 1
                self.client.get_base_entity_list.assert_not_awaited()
                self.zone = '' if reads[0] < 3 else CollectSearch.AZTECA_BEETLE_DESTINATION
                return reads[0] < 3
            return False
        self.client.is_loading.side_effect = loading
        self.assertTrue(await self.engine.run())
        self.assertGreaterEqual(reads[0], 5)

    async def test_only_supplied_stage_requires_entry(self):
        self.engine.zone = self.zone
        for text in (self.text, '收集 胭脂甲虫 地点：暴雨森林 (3 of 4)',
                     '收集 Cochineal Beetles 地点：暴雨森林 (1 of 4)'):
            self.engine.goal = parse_collect_goal(text)
            self.assertTrue(self.engine.azteca_beetle_route_required())
        for text in ('收集 胭脂甲虫 地点：暴雨森林 (4 of 4)',
                     '收集 胭脂甲虫 地点：暴雨森林 (0 of 6)',
                     '收集 水晶 地点：暴雨森林 (0 of 4)',
                     '收集 胭脂甲虫 地点：其他区域 (0 of 4)', '拜访 NPC 地点：暴雨森林'):
            self.engine.goal = parse_collect_goal(text)
            self.assertFalse(self.engine.azteca_beetle_route_required(), text)
        self.zone = CollectSearch.AZTECA_BEETLE_DESTINATION
        self.assertTrue(await self.engine.run())
        self.client.teleport.assert_not_awaited()

    async def test_failed_transition_never_searches_source_and_has_cooldown(self):
        self.client.teleport.side_effect = None
        self.assertFalse(await self.engine.run())
        self.assertLessEqual(self.now, 31)
        self.client.get_base_entity_list.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_state_change_during_transition_aborts_without_search(self):
        for mode in ('zone', 'quest', 'goal', 'stopped', 'battle'):
            with self.subTest(mode=mode):
                self.zone = CollectSearch.AZTECA_BEETLE_SOURCE
                self.text = '收集 胭脂甲虫 地点：暴雨森林 (0 of 4)'
                self.client.questing_status = True
                self.client.quest_id.return_value = 42
                self.client._xuanshu_azteca_beetle_entry = None
                self.free.return_value = True
                async def changed(_):
                    self.zone = CollectSearch.AZTECA_BEETLE_DESTINATION
                    if mode == 'zone':
                        self.zone = 'Azteca/AnotherZone'
                    elif mode == 'quest':
                        self.client.quest_id.return_value = 99
                    elif mode == 'goal':
                        self.text = '拜访 NPC 地点：暴雨森林'
                    elif mode == 'stopped':
                        self.client.questing_status = False
                    else:
                        self.free.return_value = False
                self.client.teleport.side_effect = changed
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                self.client.get_base_entity_list.assert_not_awaited()
                self.assertIsNone(self.client.quest_recovery_owner)

    async def test_recovery_refill_probe_and_hitter_do_not_tp(self):
        for attr, value in (('quest_recovery_owner', 'other'), ('refilling_potions', True),
                            ('quest_party_probe_pending', True), ('quest_party_status_session', object())):
            setattr(self.client, attr, value)
            self.assertFalse(await self.engine.run())
            setattr(self.client, attr, None)
        self.other.quest_party_hitters = [self.client]
        self.assertFalse(await self.engine.run())
        self.client.teleport.assert_not_awaited()
        self.client.get_base_entity_list.assert_not_awaited()

    async def test_cancellation_releases_both_owners(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.engine.run()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_party_arrival_requests_probe_before_search(self):
        self.client.quest_party_hitters = [self.other]
        self.assertFalse(await self.engine.run())
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, CollectSearch.AZTECA_BEETLE_DESTINATION)
        self.client.get_base_entity_list.assert_not_awaited()
        self.other.teleport.assert_not_awaited()

    async def test_worker_preflight_blocks_normal_movement_on_failed_transition(self):
        quester = Quester(self.client, self.quester.clients, None)
        quester.read_quest_txt = self.quester.read_quest_txt
        self.client.teleport.side_effect = None
        self.assertTrue(await quester._maybe_enter_azteca_beetle_map(self.client))
        self.client.get_base_entity_list.assert_not_awaited()
        self.text = '收集 水晶 地点：暴雨森林 (0 of 4)'
        self.assertFalse(await quester._maybe_enter_azteca_beetle_map(self.client))

    def test_solo_and_leader_preflight_precedes_normal_quest_tp(self):
        tree = ast.parse((Path(__file__).parents[1] / 'src' / 'questing.py').read_text(encoding='utf-8'))
        for name in ('auto_quest_solo', 'auto_quest_leader'):
            worker = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            calls = [node for node in ast.walk(worker) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
            entry = next(node.lineno for node in calls if node.func.attr == '_maybe_enter_azteca_beetle_map')
            movement = [node.lineno for node in calls if node.func.attr in (
                'teleport_party_to_quest_target', 'teleport_to_quest_target', 'bring_clients_to_same_location')]
            self.assertTrue(movement)
            self.assertLess(entry, min(movement))
