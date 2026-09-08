import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.collecting import CollectSearch, SearchState, Candidate
from src.collect_matching import parse_collect_goal
from src.collect_matching import CollectNames


class CollectWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.count = 0
        self.position = XYZ(0, 0, 0)
        self.target_title = 'Sea Foam Crystal'
        self.text = None
        self.other = SimpleNamespace(send_key=AsyncMock())
        self.client = SimpleNamespace(
            title='p1', questing_status=True,
            quest_id=AsyncMock(return_value=42),
            zone_name=AsyncMock(return_value='Celestia/Beach'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=AsyncMock(), send_key=AsyncMock(), get_base_entity_list=AsyncMock(return_value=[]),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='Sea Foam Crystal')),
        )
        self.quester = SimpleNamespace(
            read_quest_txt=AsyncMock(side_effect=lambda _: self.text or f'收集 海洋泡沫水晶 地点：漂浮大陆 ({self.count} of 4)'),
            get_zone_chunks=AsyncMock(return_value=[]), is_position_safe=AsyncMock(return_value=True),
            clients=[self.client, self.other],
        )
        self.engine = CollectSearch(self.quester, self.client)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.collecting.is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.collecting.is_visible_by_path', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.collecting.get_popup_title', AsyncMock(side_effect=lambda _: self.target_title)))
        # Keep real scheduling/cancellation, without waiting through pickup timers.
        self.real_sleep = asyncio.sleep
        async def yield_once(_):
            await self.real_sleep(0)
        self.stack.enter_context(patch('src.collecting.asyncio.sleep', new=yield_once))

    def entity(self, template_id=255, x=10):
        return SimpleNamespace(
            object_template=AsyncMock(return_value=SimpleNamespace(
                object_name=AsyncMock(return_value='CL-SeaFoam-Crystal'),
                display_name=AsyncMock(return_value='DifferentLanguageId_123'))),
            template_id_full=AsyncMock(return_value=template_id),
            location=AsyncMock(return_value=XYZ(x, 0, 0)),
        )

    def pickup(self, *args):
        self.count += 1

    async def test_english_entity_chinese_quest_collects_only_this_client(self):
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = self.pickup
        self.assertTrue(await self.engine.run())
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.other.send_key.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()
        self.assertIn(255, self.engine.state.verified_templates)

    def leyden_jar_scene(self):
        self.count = 2
        self.position = XYZ(-3769.645, 344.938, -676.2)
        self.target_title = 'LEYDEN JAR'
        self.quester.read_quest_txt.side_effect = lambda _: f'聚集 莱顿瓶 地点：科学中心 ({self.count} of 3)'
        self.client.cache_handler.get_langcode_name.return_value = 'Leyden Jar'
        jar = self.entity(template_id=987)
        jar.object_template.return_value.object_name.return_value = 'CL-LeydenJars'
        jar.location.return_value = self.position
        self.client.get_base_entity_list.return_value = [jar]
        self.client.send_key.side_effect = self.pickup

    async def test_leyden_jar_screenshot_collects_and_confirms_last_count(self):
        self.leyden_jar_scene()
        self.assertTrue(await self.engine.run())
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertEqual(self.count, 3)
        self.assertIn(987, self.engine.state.verified_templates)
        self.other.send_key.assert_not_awaited()

    async def test_leyden_jar_internal_name_works_when_language_id_is_missing(self):
        self.leyden_jar_scene()
        self.client.cache_handler.get_langcode_name.side_effect = ValueError('unknown language ID')
        self.assertTrue(await self.engine.run())
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_leyden_jar_does_not_interact_with_crystal_popup(self):
        self.leyden_jar_scene()
        self.target_title = 'Sea Foam Crystal'
        with patch('src.collecting.collision_tp', AsyncMock()):
            self.assertFalse(await self.engine.run())
        self.client.send_key.assert_not_awaited()

    async def test_installed_catalog_is_used_for_entity_and_popup(self):
        self.text = None
        self.target_title = 'Amber Test Lantern'
        self.quester.read_quest_txt.side_effect = lambda _: f'收集 琥珀测试灯笼 地点：海滩 ({self.count}/4)'
        self.client.cache_handler.get_langcode_name.return_value = self.target_title
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = self.pickup
        catalog = SimpleNamespace(get=AsyncMock(return_value=CollectNames([
            ['NewTable_1', self.target_title, '琥珀测试灯笼']
        ])))
        with patch('src.collecting.installed_catalog', return_value=catalog):
            self.assertTrue(await self.engine.run())
        catalog.get.assert_awaited_once()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_disabling_while_catalog_loads_does_not_interact(self):
        async def disable():
            self.client.questing_status = False
            return CollectNames([])
        catalog = SimpleNamespace(get=AsyncMock(side_effect=disable))
        with patch('src.collecting.installed_catalog', return_value=catalog):
            self.assertFalse(await self.engine.run())
        self.client.get_base_entity_list.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_x_without_progress_does_not_count_as_success_or_learn_template(self):
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.assertFalse(await self.engine.run())
        self.assertEqual(self.client.send_key.await_count, 3)
        self.assertFalse(self.engine.state.verified_templates)

    async def test_unrelated_popup_does_not_press_x(self):
        self.target_title = 'Dungeon Entrance'
        self.client.get_base_entity_list.return_value = [self.entity()]
        with patch('src.collecting.collision_tp', AsyncMock()):
            self.assertFalse(await self.engine.run())
        self.client.send_key.assert_not_awaited()

    async def test_delayed_entity_load_after_region_move(self):
        entity = self.entity()
        self.client.get_base_entity_list.side_effect = [[], [], [], [entity], [entity]]
        self.quester.get_zone_chunks.return_value = [XYZ(0, 0, 600)]
        self.client.send_key.side_effect = self.pickup
        self.assertTrue(await self.engine.run())
        self.assertGreaterEqual(self.client.get_base_entity_list.await_count, 4)
        point = self.client.teleport.call_args.args[0]
        self.assertEqual(point.z, 50)

    async def test_disabling_during_region_load_stops_without_click_or_return_tp(self):
        self.quester.get_zone_chunks.return_value = [XYZ(0, 0, 0), XYZ(4000, 0, 0)]
        def disable(*_):
            self.client.questing_status = False
        self.client.teleport.side_effect = disable
        self.assertFalse(await self.engine.run())
        self.assertEqual(self.client.teleport.await_count, 1)
        self.client.send_key.assert_not_awaited()

    async def test_missing_name_falls_back_to_translated_internal_name(self):
        self.client.cache_handler.get_langcode_name.side_effect = ValueError('no translation for code')
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = self.pickup
        self.assertTrue(await self.engine.run())

    async def test_last_item_goal_transition_finishes_without_learning_from_text_only(self):
        self.count = 3
        self.client.get_base_entity_list.return_value = [self.entity()]
        def finish(*_):
            self.text = '使用 祭坛 地点：漂浮大陆'
        self.client.send_key.side_effect = finish
        self.assertTrue(await self.engine.run())
        self.assertFalse(self.engine.state.verified_templates)

    async def test_quest_switch_during_move_never_clicks(self):
        self.target_title = 'Dungeon Entrance'
        self.client.get_base_entity_list.return_value = [self.entity()]
        async def switch(*_):
            self.client.quest_id.return_value = 99
        with patch('src.collecting.collision_tp', side_effect=switch):
            self.assertFalse(await self.engine.run())
        self.client.send_key.assert_not_awaited()

    async def test_cancel_drains_collect_movement(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        self.target_title = 'Dungeon Entrance'
        self.client.get_base_entity_list.return_value = [self.entity()]
        async def moving(*_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        with patch('src.collecting.collision_tp', side_effect=moving):
            task = asyncio.create_task(self.engine.run())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.client.send_key.assert_not_awaited()

    async def test_success_retains_route_cursor_for_next_pickup(self):
        self.quester.get_zone_chunks.return_value = [XYZ(0, 0, 0), XYZ(4000, 0, 0)]
        scans = 0
        async def scan(_):
            nonlocal scans
            scans += 1
            return scans == 2
        self.engine.scan = AsyncMock(side_effect=scan)
        self.assertTrue(await self.engine.run())
        self.assertEqual(self.engine.state.cursor, 1)
        state = self.engine.state
        again = CollectSearch(self.quester, self.client)
        scans = 0
        again.scan = AsyncMock(side_effect=scan)
        self.assertTrue(await again.run())
        self.assertIs(again.state, state)
        self.assertEqual(again.state.cursor, 0)
        self.quester.get_zone_chunks.assert_awaited_once()
