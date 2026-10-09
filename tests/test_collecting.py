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

    async def test_stolen_food_screenshot_collects_food_stores_and_confirms_progress(self):
        for missing_label in (False, True):
            with self.subTest(missing_label=missing_label):
                self.count = 0
                self.position = XYZ(-5004.500, 4956.112, 25.736)
                self.target_title = '食品店'
                self.quester.read_quest_txt.side_effect = lambda _: f'寻找 被偷走的食物 地点：盐土沼泽 ({self.count} of 5)'
                self.client.zone_name.return_value = 'Azteca/AZ_Z04_SaltmeadowSwamp'
                food = self.entity(template_id=549)
                food.location.return_value = self.position
                food.object_name = AsyncMock(return_value='AZ-FoodStores')
                food.object_template.return_value.object_name.return_value = 'Basic Food Object'
                food.object_template.return_value.display_name.return_value = (
                    'Unknown_00000549' if missing_label else 'WizardGameObjects_00000549')
                self.client.cache_handler.get_langcode_name.return_value = '食品店'
                self.client.cache_handler.get_langcode_name.side_effect = (
                    ValueError('label unavailable') if missing_label else None)
                self.client.get_base_entity_list.return_value = [food]
                self.client.send_key.reset_mock()
                self.client.send_key.side_effect = self.pickup
                self.engine = CollectSearch(self.quester, self.client)
                self.assertTrue(await self.engine.run())
                self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
                self.assertEqual(self.count, 1)
                self.other.send_key.assert_not_awaited()
                if missing_label:
                    food.object_name.assert_awaited()

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
        self.client.send_key.side_effect = None
        with patch('src.collecting.navmap_tp', AsyncMock()):
            self.assertFalse(await self.engine.run())
        self.assertEqual(self.client.send_key.await_count, self.engine.PROMPT_ALIGN_MAX_STEPS)
        self.assertTrue(all(call.args[0] == Keycode.A for call in self.client.send_key.await_args_list))

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
        with patch('src.collecting.navmap_tp', AsyncMock()):
            self.assertFalse(await self.engine.run())
        self.assertEqual(self.client.send_key.await_count, self.engine.PROMPT_ALIGN_MAX_STEPS)
        self.assertTrue(all(call.args[0] == Keycode.A for call in self.client.send_key.await_args_list))

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

    async def test_rotating_goal_changes_realm_only_after_full_fourteen_region_round(self):
        self.text = '收集 旋转旋转 地点：测试区 (0 of 1)'
        self.quester.get_zone_chunks.return_value = [XYZ(i * 100, 0, 600) for i in range(14)]
        self.quester.change_realm_for_rotating = AsyncMock(return_value=True)
        self.engine.scan = AsyncMock(return_value=False)
        self.assertFalse(await self.engine.run())
        self.assertEqual(self.engine.scan.await_count, 15)  # loaded area + 14 regions
        self.assertEqual(self.client.teleport.await_count, 15)  # 14 regions + return
        self.quester.change_realm_for_rotating.assert_awaited_once_with(self.client)
        next_round = CollectSearch(self.quester, self.client)
        next_round.scan = AsyncMock(return_value=False)
        self.assertFalse(await next_round.run())
        next_round.scan.assert_not_awaited()

    async def test_rotating_goal_does_not_change_realm_before_fourteen_regions(self):
        self.text = '收集 旋转旋转 地点：测试区 (0 of 1)'
        self.quester.get_zone_chunks.return_value = [XYZ(i * 100, 0, 600) for i in range(13)]
        self.quester.change_realm_for_rotating = AsyncMock(return_value=True)
        self.engine.scan = AsyncMock(return_value=False)
        self.assertFalse(await self.engine.run())
        self.quester.change_realm_for_rotating.assert_not_awaited()
        self.assertIsNone(getattr(self.client, 'quest_rotating_realm_attempted', None))

    async def test_ordinary_collect_keeps_refresh_retry_without_realm_change(self):
        self.quester.get_zone_chunks.return_value = [XYZ(i * 100, 0, 600) for i in range(14)]
        self.quester.change_realm_for_rotating = AsyncMock(return_value=True)
        self.engine.scan = AsyncMock(return_value=False)
        self.assertFalse(await self.engine.run())
        self.quester.change_realm_for_rotating.assert_not_awaited()
        self.assertIsNone(getattr(self.client, 'quest_rotating_realm_attempted', None))

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
        with patch('src.collecting.navmap_tp', side_effect=switch):
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
        with patch('src.collecting.navmap_tp', side_effect=moving):
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

    async def lock_point(self):
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = self.pickup
        self.assertTrue(await self.engine.run())
        self.assertEqual((self.engine.state.anchor.x, self.engine.state.anchor.y), (10, 0))
        self.engine.state.recent.clear()
        self.client.teleport.reset_mock()

    def respawn_clock(self):
        clock = SimpleNamespace(now=0.0)
        self.stack.enter_context(patch('src.collecting.time', SimpleNamespace(monotonic=lambda: clock.now)))
        async def tick(seconds):
            clock.now += seconds
            await self.real_sleep(0)
        self.stack.enter_context(patch('src.collecting.asyncio.sleep', tick))
        return clock

    async def test_anchor_wait_90_seconds_resumes_route_and_can_collect_a_far_nonrespawning_item(self):
        await self.lock_point()
        clock = self.respawn_clock()
        state = self.engine.state
        state.route = [XYZ(0, 0, 0), XYZ(5000, 0, 0)]
        state.cursor = 1
        self.client.get_base_entity_list.side_effect = lambda: (
            [self.entity(x=5000)] if self.client.teleport.await_count else [])
        async def move(point):
            self.assertGreaterEqual(clock.now, 90)
            self.position = point
        self.client.teleport.side_effect = move
        again = CollectSearch(self.quester, self.client)
        self.assertTrue(await again.run())
        self.assertEqual(self.count, 2)
        self.assertIs(again.state, state)
        self.assertEqual(state.anchor.x, 10)  # Keep the FIRST successful point.
        self.assertEqual(state.cursor, 0)  # Resume the old route, do not rebuild it.
        self.assertIsNone(state.anchor_wait_started_at)
        self.assertFalse(state.rescan_after_anchor_timeout)
        self.client.teleport.assert_awaited_once()
        self.quester.get_zone_chunks.assert_not_awaited()
        self.other.send_key.assert_not_awaited()

    async def test_respawn_before_90_seconds_collects_without_full_map_search(self):
        await self.lock_point()
        clock = self.respawn_clock()
        self.client.get_base_entity_list.side_effect = lambda: [self.entity()] if clock.now >= 88 else []
        self.assertTrue(await CollectSearch(self.quester, self.client).run())
        self.assertLess(clock.now, 90)
        self.assertEqual(self.count, 2)
        self.assertIsNone(self.engine.state.anchor_wait_started_at)
        self.assertFalse(self.engine.state.rescan_after_anchor_timeout)
        self.client.teleport.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_worker_recreation_keeps_elapsed_wait_and_timeout_allows_subsequent_search(self):
        await self.lock_point()
        clock = self.respawn_clock()
        async def interrupted():
            if clock.now >= 40:
                raise asyncio.CancelledError()
            return []
        self.client.get_base_entity_list.side_effect = interrupted
        with self.assertRaises(asyncio.CancelledError):
            await CollectSearch(self.quester, self.client).run()
        self.assertEqual(self.engine.state.anchor_wait_started_at, 0)
        self.assertEqual(clock.now, 40)
        self.client.get_base_entity_list.side_effect = None
        self.client.get_base_entity_list.return_value = []
        self.quester.get_zone_chunks.return_value = [XYZ(5000, 0, 0)]
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.assertTrue(self.engine.state.rescan_after_anchor_timeout)
        self.assertLess(clock.now, 100)  # Remaining 50 seconds, not another 90.
        before = clock.now
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.assertLess(clock.now - before, 10)  # Do not re-enter the dead anchor wait.
        self.quester.get_zone_chunks.assert_awaited_once()

    async def test_wait_timeout_rechecks_stop_zone_or_goal_before_resuming_search(self):
        await self.lock_point()
        clock = self.respawn_clock()
        for mode in ('stop', 'zone', 'quest', 'goal'):
            with self.subTest(mode=mode):
                self.client.questing_status = True
                self.client.zone_name.return_value = 'Celestia/Beach'
                self.client.quest_id.return_value = 42
                self.text = None
                self.engine.state.anchor_wait_started_at = None
                clock.now = 0
                async def changed():
                    if clock.now >= 90:
                        if mode == 'stop':
                            self.client.questing_status = False
                        elif mode == 'zone':
                            self.client.zone_name.return_value = 'Different/Area'
                        elif mode == 'quest':
                            self.client.quest_id.return_value = 99
                        else:
                            self.text = '拜访 校长 地点：广场'
                    return []
                self.client.get_base_entity_list.side_effect = changed
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                self.assertFalse(self.engine.state.rescan_after_anchor_timeout)
                self.assertIsNone(self.engine.state.anchor_wait_started_at)
        self.client.teleport.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_refill_or_other_recovery_interrupts_and_resets_respawn_wait(self):
        await self.lock_point()
        clock = self.respawn_clock()
        for attr, value in (('refilling_potions', True), ('quest_recovery_owner', 'other')):
            with self.subTest(attr=attr):
                self.engine.state.anchor_wait_started_at = 0
                clock.now = 80
                setattr(self.client, attr, value)
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                self.assertIsNone(self.engine.state.anchor_wait_started_at)
                self.assertFalse(self.engine.state.rescan_after_anchor_timeout)
                setattr(self.client, attr, False if attr == 'refilling_potions' else None)
        self.client.teleport.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_expired_wait_does_not_resume_map_search_during_refill_or_other_recovery(self):
        await self.lock_point()
        self.engine.state.rescan_after_anchor_timeout = True
        self.engine.state.route = [XYZ(5000, 0, 0)]
        self.client.get_base_entity_list.reset_mock()
        for attr, value in (('refilling_potions', True), ('quest_recovery_owner', 'other')):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                setattr(self.client, attr, False if attr == 'refilling_potions' else None)
        self.assertTrue(self.engine.state.rescan_after_anchor_timeout)
        self.client.teleport.assert_not_awaited()
        self.client.get_base_entity_list.assert_not_awaited()

    async def test_next_pickup_searches_other_points(self):
        await self.lock_point()
        self.client.get_base_entity_list.side_effect = [[self.entity(x=500)]]
        again = CollectSearch(self.quester, self.client)
        self.assertTrue(await again.run())
        self.assertEqual(self.count, 2)
        self.assertEqual(self.engine.state.anchor.x, 10)  # keep FIRST pickup, not the latest
        self.client.teleport.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_missing_respawn_returns_to_rescan_without_input(self):
        await self.lock_point()
        self.client.send_key.reset_mock()
        calls = 0
        async def missing():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.client.questing_status = False
            return []
        self.client.get_base_entity_list.side_effect = missing
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.assertEqual(calls, 3)
        self.quester.get_zone_chunks.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_far_loaded_object_is_not_permission_to_leave_anchor_area(self):
        await self.lock_point()
        self.client.get_base_entity_list.return_value = [self.entity(x=5000)]
        again = CollectSearch(self.quester, self.client)
        again.wait_at_anchor = AsyncMock(return_value=False)
        self.client.send_key.reset_mock()
        self.assertFalse(await again.run())
        self.client.send_key.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()
        again.wait_at_anchor.assert_awaited_once()

    async def test_returns_to_first_pickup_before_waiting_for_respawn(self):
        await self.lock_point()
        self.position = XYZ(500, 0, 0)
        self.client.get_base_entity_list.return_value = []
        again = CollectSearch(self.quester, self.client)
        again.loaded_entities = AsyncMock(return_value=[])
        again.wait_at_anchor = AsyncMock(return_value=False)
        async def return_to_anchor(client, xyz):
            self.assertEqual(xyz.x, 10)
            self.position = xyz
        with patch('src.collecting.navmap_tp', AsyncMock(side_effect=return_to_anchor)) as move:
            self.assertFalse(await again.run())
        move.assert_awaited_once()
        again.wait_at_anchor.assert_awaited_once()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_wait_reads_new_entity_instead_of_reusing_expired_pointer(self):
        await self.lock_point()
        self.client.get_base_entity_list.side_effect = [[], [], [self.entity()]]
        again = CollectSearch(self.quester, self.client)
        self.assertTrue(await again.run())
        self.assertEqual(self.count, 2)
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_failed_anchor_return_never_restarts_full_map_search(self):
        await self.lock_point()
        self.position = XYZ(5000, 0, 0)
        self.client.get_base_entity_list.return_value = []
        again = CollectSearch(self.quester, self.client)
        again.wait_at_anchor = AsyncMock()
        with patch('src.collecting.navmap_tp', AsyncMock()):
            self.assertFalse(await again.run())
        self.quester.get_zone_chunks.assert_not_awaited()
        again.wait_at_anchor.assert_not_awaited()
        self.assertEqual(again.state.anchor.x, 10)

    async def test_new_quest_invalidates_old_pickup_anchor(self):
        await self.lock_point()
        old = self.engine.state
        self.client.quest_id.return_value = 99
        self.client.get_base_entity_list.return_value = []
        again = CollectSearch(self.quester, self.client)
        self.assertFalse(await again.run())
        self.assertIsNot(again.state, old)
        self.assertIsNone(again.state.anchor)
        self.quester.get_zone_chunks.assert_awaited_once()

    async def test_anchor_wait_cancel_propagates_without_roaming(self):
        await self.lock_point()
        self.client.get_base_entity_list.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await CollectSearch(self.quester, self.client).run()
        self.client.teleport.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_other_recovery_or_refill_interrupts_anchor_wait(self):
        await self.lock_point()
        for name, value in [('refilling_potions', True), ('quest_recovery_owner', 'other')]:
            with self.subTest(name=name):
                setattr(self.client, name, value)
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                setattr(self.client, name, False if name == 'refilling_potions' else None)
        self.quester.get_zone_chunks.assert_not_awaited()

    async def test_completed_counter_exits_without_more_pickups(self):
        await self.lock_point()
        self.count = 4
        self.client.send_key.reset_mock()
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.client.send_key.assert_not_awaited()

    async def test_goal_change_during_wait_exits_without_moving(self):
        await self.lock_point()
        async def changed():
            self.text = '拜访 校长 地点：广场'
            return []
        self.client.get_base_entity_list.side_effect = changed
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.client.teleport.assert_not_awaited()

    async def test_wrong_popup_turns_a_until_matching_collect_then_x(self):
        self.target_title = 'LEGEND OF SUTEKH'
        self.client.get_base_entity_list.return_value = [self.entity()]
        async def keypress(key, duration):
            if key == Keycode.A:
                if self.client.send_key.await_count == 2:
                    self.target_title = 'Sea Foam Crystal'
            elif key == Keycode.X:
                self.assertEqual(self.target_title, 'Sea Foam Crystal')
                self.count += 1
        self.client.send_key.side_effect = keypress
        with patch('src.collecting.navmap_tp', AsyncMock()) as movement:
            self.assertTrue(await self.engine.run())
        movement.assert_awaited_once()
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.A, Keycode.A, Keycode.X])
        self.assertEqual(self.count, 1)
        self.other.send_key.assert_not_awaited()

    async def test_alignment_stops_on_task_or_client_state_change(self):
        self.target_title = 'Wrong NPC'
        self.engine.zone = 'Celestia/Beach'
        self.engine.quest_id = 42
        self.engine.goal = await self.engine.snapshot()
        self.engine.state = SearchState(('alignment',))
        candidate = Candidate(self.entity(), XYZ(10, 0, 0), '', 'Sea Foam Crystal', 'CL-SeaFoam-Crystal', 255, 100)
        for mode in ('stop', 'zone', 'quest', 'goal', 'loading', 'battle', 'dialogue', 'potions', 'recovery', 'entity_move'):
            with self.subTest(mode=mode):
                self.client.questing_status = True
                self.client.zone_name.return_value = 'Celestia/Beach'
                self.client.quest_id.return_value = 42
                self.client.is_loading.return_value = False
                self.client.in_battle.return_value = False
                self.client.refilling_potions = False
                self.client.quest_recovery_owner = None
                self.text = None
                candidate.entity.location.return_value = XYZ(10, 0, 0)
                free = AsyncMock(return_value=True)
                async def changed(key, duration):
                    self.assertEqual(key, Keycode.A)
                    if mode == 'stop':
                        self.client.questing_status = False
                    elif mode == 'zone':
                        self.client.zone_name.return_value = 'Another/Area'
                    elif mode == 'quest':
                        self.client.quest_id.return_value = 99
                    elif mode == 'goal':
                        self.text = '拜访 校长 地点：广场'
                    elif mode == 'loading':
                        self.client.is_loading.return_value = True
                    elif mode == 'battle':
                        self.client.in_battle.return_value = True
                    elif mode == 'dialogue':
                        free.return_value = False
                    elif mode == 'potions':
                        self.client.refilling_potions = True
                    elif mode == 'recovery':
                        self.client.quest_recovery_owner = 'other-recovery'
                    else:
                        candidate.entity.location.return_value = XYZ(1000, 0, 0)
                self.client.send_key.reset_mock()
                self.client.send_key.side_effect = changed
                with patch('src.collecting.is_free', free):
                    self.assertFalse(await self.engine.align_collect_prompt(candidate))
                self.client.send_key.assert_awaited_once_with(Keycode.A, .1)

    async def test_alignment_requires_actual_arrival_and_live_entity(self):
        self.target_title = 'Wrong NPC'
        self.engine.zone = 'Celestia/Beach'
        self.engine.quest_id = 42
        self.engine.goal = await self.engine.snapshot()
        self.engine.state = SearchState(('alignment',))
        candidate = Candidate(self.entity(), XYZ(1000, 0, 0), '', 'Sea Foam Crystal', 'CL-SeaFoam-Crystal', 255, 100)
        self.assertFalse(await self.engine.align_collect_prompt(candidate))
        self.client.send_key.assert_not_awaited()
        candidate.xyz = XYZ(10, 0, 0)
        self.position = XYZ(float('nan'), 0, 0)
        self.assertFalse(await self.engine.align_collect_prompt(candidate))
        self.client.send_key.assert_not_awaited()
        self.position = XYZ(0, 0, 0)
        candidate.entity.location.side_effect = ValueError('expired')
        self.assertFalse(await self.engine.align_collect_prompt(candidate))
        self.client.send_key.assert_not_awaited()

    async def test_alignment_cancellation_releases_ownership_without_x(self):
        from src.automation_ownership import get_client_automation_ownership
        self.target_title = 'Dungeon Entrance'
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = asyncio.CancelledError()
        with patch('src.collecting.navmap_tp', AsyncMock()):
            with self.assertRaises(asyncio.CancelledError):
                await self.engine.run()
        self.client.send_key.assert_awaited_once_with(Keycode.A, .1)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    def selenopolis_books(self):
        self.client._selenopolis_book_exit = None
        self.text = '寻找 魔法书 地点：Marketplace of Ideas (0 of 3)'
        self.client.zone_name.return_value = CollectSearch.SELENOPOLIS_BOOK_STORAGE
        self.target_title = '魔法书'
        self.position = XYZ(1000, 2000, 0)
        entity = self.entity()
        entity.location.return_value = XYZ(1010, 2000, 0)
        entity.object_template.return_value.object_name.return_value = 'KT-Books'
        self.client.cache_handler.get_langcode_name.return_value = '魔法书'
        self.client.get_base_entity_list.return_value = [entity]
        async def exit_storage(point):
            self.assertEqual((point.x, point.y, point.z), (7553.401, -9213.249, -365.059))
            self.client.zone_name.return_value = CollectSearch.SELENOPOLIS_BOOK_MARKET
        self.client.teleport.side_effect = exit_storage
        async def pickup(key, duration):
            self.assertEqual(key, Keycode.X)
            self.count += 1
            self.text = f'寻找 魔法书 地点：Marketplace of Ideas ({self.count} of 3)'
        self.client.send_key.side_effect = pickup

    async def test_book_exit_switches_to_market_before_entity_scan_and_resets_route(self):
        self.selenopolis_books()
        old = SearchState((CollectSearch.SELENOPOLIS_BOOK_STORAGE, 42,
                           parse_collect_goal(self.text).key, 3), route=[XYZ(-9999, 0, 0)], cursor=8)
        self.client._deimos_collect_search = old
        entities = self.client.get_base_entity_list.return_value
        async def market_entities():
            self.assertEqual(self.engine.zone, CollectSearch.SELENOPOLIS_BOOK_MARKET)
            self.assertEqual(await self.client.zone_name(), self.engine.zone)
            return entities
        self.client.get_base_entity_list.side_effect = market_entities
        self.assertTrue(await self.engine.run())
        self.client.teleport.assert_awaited_once_with(CollectSearch.SELENOPOLIS_BOOK_EXIT)
        self.assertIsNot(self.engine.state, old)
        self.assertEqual(self.engine.state.key[0], CollectSearch.SELENOPOLIS_BOOK_MARKET)
        self.quester.get_zone_chunks.assert_not_awaited()
        self.assertIsNone(self.client._selenopolis_book_exit)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_book_exit_then_wrong_sigil_turns_to_book_and_finishes_two_of_three(self):
        self.selenopolis_books()
        self.count = 2
        self.text = '寻找 魔法书 地点：Marketplace of Ideas (2 of 3)'
        self.target_title = 'LEGEND OF SUTEKH'
        async def interact(key, duration):
            if key == Keycode.A:
                self.target_title = '魔法书'
            else:
                self.assertEqual(key, Keycode.X)
                self.assertEqual(self.target_title, '魔法书')
                self.count += 1
                self.text = '寻找 魔法书 地点：Marketplace of Ideas (3 of 3)'
        self.client.send_key.side_effect = interact
        with patch('src.collecting.navmap_tp', AsyncMock()) as movement:
            self.assertTrue(await self.engine.run())
        self.client.teleport.assert_awaited_once_with(CollectSearch.SELENOPOLIS_BOOK_EXIT)
        movement.assert_awaited_once()
        self.assertEqual([call.args[0] for call in self.client.send_key.await_args_list],
                         [Keycode.A, Keycode.X])
        self.assertEqual(self.count, 3)
        self.other.send_key.assert_not_awaited()

    async def test_book_exit_waits_for_loading_and_stable_destination(self):
        self.selenopolis_books()
        loading_reads = [0]
        async def loading():
            if self.client.teleport.await_count:
                loading_reads[0] += 1
                self.client.get_base_entity_list.assert_not_awaited()
                return loading_reads[0] < 3
            return False
        # Stop this special loading stub after the helper confirms the market;
        # normal entity scan must not see any intermediate loading screen.
        async def entities():
            self.assertGreaterEqual(loading_reads[0], 5)
            self.client.is_loading.side_effect = None
            return self.client.get_base_entity_list.return_value
        self.client.is_loading.side_effect = loading
        self.client.get_base_entity_list.side_effect = entities
        self.engine.scan = AsyncMock(return_value=True)
        self.assertTrue(await self.engine.run())
        self.client.teleport.assert_awaited_once()

    async def test_book_exit_is_limited_to_supplied_stage(self):
        for variant in ('zone', 'target', 'location', 'total', 'completed'):
            with self.subTest(variant=variant):
                self.selenopolis_books()
                if variant == 'zone':
                    self.client.zone_name.return_value = CollectSearch.SELENOPOLIS_BOOK_MARKET
                elif variant == 'target':
                    self.text = self.text.replace('魔法书', '另一个物品')
                elif variant == 'location':
                    self.text = self.text.replace('Marketplace of Ideas', 'Another Market')
                elif variant == 'total':
                    self.text = self.text.replace('of 3', 'of 4')
                else:
                    self.text = self.text.replace('(0 of 3)', '(3 of 3)')
                engine = CollectSearch(self.quester, self.client)
                engine.scan = AsyncMock(return_value=True)
                engine.leave_selenopolis_book_storage = AsyncMock(return_value=True)
                await engine.run()
                engine.leave_selenopolis_book_storage.assert_not_awaited()

    async def test_book_exit_timeout_never_scans_storage_and_has_retry_cooldown(self):
        self.selenopolis_books()
        self.client.teleport.side_effect = None
        self.assertFalse(await self.engine.run())
        self.client.get_base_entity_list.assert_not_awaited()
        self.quester.get_zone_chunks.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.client.teleport.assert_awaited_once_with(CollectSearch.SELENOPOLIS_BOOK_EXIT)
        self.assertFalse(await CollectSearch(self.quester, self.client).run())
        self.client.teleport.assert_awaited_once()

    async def test_book_exit_state_changes_abort_without_scan_or_pickup(self):
        for mode in ('unexpected_zone', 'stop', 'quest', 'goal'):
            with self.subTest(mode=mode):
                self.selenopolis_books()
                self.client.questing_status = True
                self.client.quest_id.return_value = 42
                self.client.teleport.reset_mock()
                async def changed(*_):
                    self.client.zone_name.return_value = CollectSearch.SELENOPOLIS_BOOK_MARKET
                    if mode == 'unexpected_zone':
                        self.client.zone_name.return_value = 'Krokotopia/AnotherZone'
                    elif mode == 'stop':
                        self.client.questing_status = False
                    elif mode == 'quest':
                        self.client.quest_id.return_value = 99
                    else:
                        self.text = '拜访 校长 地点：广场'
                self.client.teleport.side_effect = changed
                self.assertFalse(await CollectSearch(self.quester, self.client).run())
                self.client.get_base_entity_list.assert_not_awaited()
                self.client.send_key.assert_not_awaited()
                self.client.teleport.assert_awaited_once()

    async def test_book_exit_respects_existing_recovery_and_cancellation(self):
        from src.automation_ownership import get_client_automation_ownership
        self.selenopolis_books()
        self.client.quest_recovery_owner = 'other-recovery'
        self.assertFalse(await self.engine.run())
        self.client.teleport.assert_not_awaited()
        self.client.get_base_entity_list.assert_not_awaited()
        self.client.quest_recovery_owner = None
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await CollectSearch(self.quester, self.client).run()
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.client.send_key.assert_not_awaited()
