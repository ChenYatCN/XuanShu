"""No-arrow object goals reuse the collector, with client-local progress and cancellation."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import questing as q, collecting as c
from src.collect_matching import parse_collect_goal
from src.automation_ownership import automation_owner, get_client_automation_ownership

REAL_SLEEP = asyncio.sleep


class UnguidedCollectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.count, self.kind, self.helper = 0., 0, 8, False
        self.text = None
        self.target = XYZ(0, 0, 0)
        self.data = SimpleNamespace(goal_type=AsyncMock(side_effect=lambda: SimpleNamespace(value=self.kind)),
                                    no_quest_helper=AsyncMock(side_effect=lambda: self.helper))
        self.quest = SimpleNamespace(goal_data=AsyncMock(return_value={7: self.data}),
                                     permit_quest_helper=AsyncMock(return_value=True))
        self.client = SimpleNamespace(title='p1', questing_status=True, quest_recovery_owner=None,
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            zone_name=AsyncMock(return_value='World/Objects'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 0, 0))),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={42: self.quest}))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='Test Obelisk')),
            send_key=AsyncMock(), teleport=AsyncMock(), get_base_entity_list=AsyncMock(return_value=[]))
        self.runner = q.Quester(self.client, [self.client], None)
        self.runner.read_quest_txt = AsyncMock(side_effect=lambda _: self.text or f'Activate Test Obelisk ({self.count}/2)')
        self.runner.get_zone_chunks = AsyncMock(return_value=[])
        self.runner.is_position_safe = AsyncMock(return_value=True)
        self.runner.move_until_quest_interaction = AsyncMock(return_value=True)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (q, c):
            stack.enter_context(patch.object(module, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(return_value=False)))
        stack.enter_context(patch.object(q, 'is_spiral_door_open', AsyncMock(return_value=False)))
        stack.enter_context(patch.object(c, 'is_free', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(c, 'is_visible_by_path', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(c, 'get_popup_title', AsyncMock(return_value='Test Obelisk')))
        stack.enter_context(patch.object(c, 'installed_catalog', return_value=None))
        async def tick(_):
            await REAL_SLEEP(0)
        stack.enter_context(patch.object(c.asyncio, 'sleep', tick))

    def entity(self, address=1001, x=100):
        return SimpleNamespace(
            object_template=AsyncMock(return_value=SimpleNamespace(
                object_name=AsyncMock(return_value='Test Obelisk'), display_name=AsyncMock(return_value='test'))),
            template_id_full=AsyncMock(return_value=91), read_base_address=AsyncMock(return_value=address),
            location=AsyncMock(return_value=XYZ(x, 0, 0)))

    async def search(self):
        context = await self.runner._unguided_collect_context(self.client)
        self.assertIsNotNone(context)
        return c.CollectSearch(self.runner, self.client, progress_context=context)

    async def test_zero_unreadable_or_explicit_no_helper_all_authorize_object_goal(self):
        for mode in ('zero', 'unreadable', 'no_helper'):
            with self.subTest(mode=mode):
                self.target, self.helper = XYZ(0, 0, 0), False
                self.client.quest_position.position.side_effect = lambda: self.target
                if mode == 'unreadable':
                    self.client.quest_position.position.side_effect = OSError('no arrow')
                elif mode == 'no_helper':
                    self.target, self.helper = XYZ(1000, 0, 0), True
                self.assertIsNotNone(await self.runner._unguided_collect_context(self.client))

    async def test_count_does_not_authorize_explicit_combat_npc_or_waypoint_types(self):
        for self.kind in (1, 2, 4, 5, 15, 16):
            with self.subTest(kind=self.kind):
                self.assertIsNone(await self.runner._unguided_collect_context(self.client))
        self.kind = 8
        for self.text in ('Defeat Test Obelisk (0/2)', 'Talk to Test Obelisk (0/2)',
                          'Go to Test Obelisk (0/2)', 'Open Dungeon Door (0/2)',
                          'Activate Portal (0/2)', 'Activate Test Obelisk (2/2)'):
            with self.subTest(text=self.text):
                self.assertIsNone(await self.runner._unguided_collect_context(self.client))

    async def test_valid_guidance_uses_normal_flow_and_missing_registry_allows_search(self):
        self.target = XYZ(500, 0, 0)
        self.assertIsNone(await self.runner._unguided_collect_context(self.client))
        self.target = XYZ(0, 0, 0)
        self.quest.goal_data.return_value = {}
        context = await self.runner._unguided_collect_context(self.client)
        self.assertIsNotNone(context)
        self.assertEqual(context['metadata'], 'goal_data_missing')

    async def test_hitter_loading_battle_refill_and_modal_guards(self):
        for attr, value in (('quest_party_quester', object()), ('refilling_potions', True),
                            ('quest_party_probe_pending', True), ('quest_party_dungeon_entry_active', True),
                            ('quest_recovery_owner', 'other-recovery')):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                self.assertIsNone(await self.runner._unguided_collect_context(self.client))
                setattr(self.client, attr, None)
        for method in (self.client.is_loading, self.client.in_battle):
            method.return_value = True
            self.assertIsNone(await self.runner._unguided_collect_context(self.client))
            method.return_value = False

    async def test_unknown_type_needs_second_stable_observation(self):
        self.kind = 0
        context = await self.runner._unguided_collect_context(self.client)
        with patch.object(c.CollectSearch, 'run', AsyncMock(return_value=False)) as run:
            await self.runner._maybe_unguided_collect(self.client, context)
            run.assert_not_awaited()
            self.now = .4
            await self.runner._maybe_unguided_collect(self.client, context)
            run.assert_awaited_once()

    async def test_real_engine_updates_count_and_uses_entity_landing_wrapper(self):
        engine = await self.search()
        entity = self.entity()
        self.client.get_base_entity_list.return_value = [entity]
        def pickup(*_):
            self.count += 1
        self.client.send_key.side_effect = pickup
        self.assertTrue(await engine.run())
        self.assertEqual(self.count, 1)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        args = self.runner.move_until_quest_interaction.await_args
        self.assertIs(args.args[0], self.client)
        self.assertEqual(args.args[1], entity.location.return_value)
        self.assertIs(args.kwargs['collect_search'], engine)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_three_no_progress_x_blacklists_exact_entity_not_same_name_peer(self):
        first, second = self.entity(1001), self.entity(1002, 200)
        self.client.get_base_entity_list.return_value = [first]
        engine = await self.search()
        self.assertFalse(await engine.run())
        self.assertEqual(self.client.send_key.await_count, 3)
        self.assertIn(('entity', 1001, 91), engine.state.blacklist)
        engine2 = await self.search()
        self.client.get_base_entity_list.return_value = [first, second]
        engine2.zone, engine2.quest_id, engine2.goal, engine2.state = (
            engine.zone, engine.quest_id, engine.goal, engine.state)
        candidates = await engine2.candidates([first, second])
        self.assertEqual([item.entity for item in candidates], [second])
        self.client.goal_id.return_value = 8
        self.quest.goal_data.return_value[8] = self.data
        engine3 = await self.search()
        await engine3.run()
        self.assertIsNot(engine3.state, engine.state)

    async def test_goal_change_after_x_exits_without_second_old_goal_x(self):
        engine = await self.search()
        self.client.get_base_entity_list.return_value = [self.entity()]
        self.client.send_key.side_effect = lambda *_: setattr(self.client.goal_id, 'return_value', 8)
        self.assertTrue(await engine.run())
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_cancel_running_scan_releases_own_input_and_state(self):
        context = await self.runner._unguided_collect_context(self.client)
        started = asyncio.Event()
        async def run(search):
            async with automation_owner(self.client, 'collect-region-load'):
                started.set()
                await asyncio.Future()
        with patch.object(c.CollectSearch, 'run', run):
            task = asyncio.create_task(self.runner._maybe_unguided_collect(self.client, context))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertIsNone(self.client._quest_progress_collect)

    async def test_other_group_owner_state_and_keys_are_untouched(self):
        other = SimpleNamespace(_deimos_collect_search=object())
        sentinel = other._deimos_collect_search
        context = await self.runner._unguided_collect_context(self.client)
        async with automation_owner(other, 'npc-dialogue'):
            with patch.object(c.CollectSearch, 'run', AsyncMock(return_value=False)):
                await self.runner._maybe_unguided_collect(self.client, context)
            self.assertIs(other._deimos_collect_search, sentinel)
            self.assertTrue(get_client_automation_ownership(other).locked)

    async def test_unreadable_arrow_after_accepted_landing_still_reaches_object_fallback(self):
        self.runner._quest_movement_landings[id(self.client)] = {
            'signature': ((42, 7, 'World/Objects'), (1000, 0, 0)), 'collect': False}
        self.client.quest_position.position.side_effect = OSError('arrow temporarily absent')
        self.runner._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.runner._maybe_unguided_collect = AsyncMock(return_value=True)
        await self.runner.auto_quest_solo()
        self.runner._maybe_unguided_collect.assert_awaited_once()
        self.assertEqual(self.runner._maybe_unguided_collect.await_args.args[1]['identity'],
                         (42, 7, 'World/Objects'))
        self.assertNotIn(id(self.client), self.runner._quest_movement_landings)
        self.client.send_key.assert_not_awaited()

    async def test_guidance_loading_battle_or_stop_interrupts_owned_scan(self):
        for event in ('guidance', 'loading', 'battle', 'stop'):
            with self.subTest(event=event):
                self.target = XYZ(0, 0, 0)
                self.client.questing_status = True
                self.client.is_loading.return_value = self.client.in_battle.return_value = False
                context = await self.runner._unguided_collect_context(self.client)
                started = asyncio.Event()
                async def run(search):
                    async with automation_owner(self.client, 'collect-region-load'):
                        started.set()
                        await asyncio.Future()
                with patch.object(c.CollectSearch, 'run', run):
                    task = asyncio.create_task(self.runner._maybe_unguided_collect(self.client, context))
                    await started.wait()
                    if event == 'guidance': self.target = XYZ(1000, 0, 0)
                    elif event == 'loading': self.client.is_loading.return_value = True
                    elif event == 'battle': self.client.in_battle.return_value = True
                    else: self.client.questing_status = False
                    await asyncio.wait_for(task, 2)
                self.assertFalse(get_client_automation_ownership(self.client).locked)
                self.assertIsNone(self.client._quest_progress_collect)
                self.client.send_key.assert_not_awaited()
                self.client.teleport.assert_not_awaited()

    async def test_two_party_collectors_progress_independently_without_hitter_input(self):
        hitter1, hitter2 = SimpleNamespace(send_key=AsyncMock()), SimpleNamespace(send_key=AsyncMock())
        self.client.quest_party_hitters = [hitter1]
        self.client.quest_party_status_session = object()
        other = SimpleNamespace(**vars(self.client))
        other.title, other.quest_party_hitters, other.quest_party_status_session = 'p3', [hitter2], object()
        other.send_key = AsyncMock()
        other.get_base_entity_list = AsyncMock(return_value=[self.entity(2001)])
        self.client.get_base_entity_list.return_value = [self.entity(1001)]
        count = 0
        def first(*_): self.count += 1
        def second(*_):
            nonlocal count
            count += 1
        self.client.send_key.side_effect, other.send_key.side_effect = first, second
        runner = q.Quester(other, [other], None)
        runner.read_quest_txt = AsyncMock(side_effect=lambda _: f'Activate Test Obelisk ({count}/2)')
        runner.get_zone_chunks = AsyncMock(return_value=[])
        runner.is_position_safe = AsyncMock(return_value=True)
        runner.move_until_quest_interaction = AsyncMock(return_value=True)
        context1 = await self.runner._unguided_collect_context(self.client)
        context2 = await runner._unguided_collect_context(other)
        engines = (c.CollectSearch(self.runner, self.client, progress_context=context1),
                   c.CollectSearch(runner, other, progress_context=context2))
        self.assertEqual(await asyncio.gather(*(engine.run() for engine in engines)), [True, True])
        self.assertEqual((self.count, count), (1, 1))
        self.assertIsNot(engines[0].state, engines[1].state)
        self.assertNotEqual(engines[0].state.key, engines[1].state.key)
        hitter1.send_key.assert_not_awaited()
        hitter2.send_key.assert_not_awaited()


class UnguidedParserTests(unittest.TestCase):
    def test_interaction_count_before_or_after_location_and_markup(self):
        for text in ('粉碎 方尖碑 地点：山谷 (0/1)', 'Inspect Test Obelisk (0/1) in Valley',
                     '<center>激活 方尖碑 0/1 地点：山谷</center>'):
            with self.subTest(text=text):
                goal = parse_collect_goal(text, allow_interaction=True)
                self.assertIsNotNone(goal)
                self.assertEqual((goal.current, goal.total), (0, 1))

    def test_optional_parser_keeps_default_collect_parser_contract(self):
        self.assertIsNone(parse_collect_goal('Inspect Test Obelisk (0/1)'))
        goal = parse_collect_goal('Collect Test Obelisk (0 of 2)')
        self.assertEqual((goal.target, goal.current, goal.total), ('Test Obelisk', 0, 2))
