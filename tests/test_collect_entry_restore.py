"""Regression: a zero quest arrow must reach the existing food collector."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import questing as q, collecting as c
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests import test_unguided_collect as fixture
from tests import test_quest_415_baseline as ordinary_fixture


class FoodEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture.UnguidedCollectTests.setUp(self)
        self.count, self.kind = 3, 3
        self.runner.read_quest_txt.side_effect = lambda _: f'收集 食品 地点：康达沙漠 ({self.count}/5)'
        self.runner._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.client.cache_handler.get_langcode_name.return_value = '食品'
        self.food = fixture.UnguidedCollectTests.entity(self)
        self.food.object_template.return_value.object_name.return_value = 'Food'
        self.client.get_base_entity_list.return_value = [self.food]
        self.client.send_key.side_effect = lambda *_: setattr(self, 'count', self.count + 1)
        popup = patch.object(c, 'get_popup_title', AsyncMock(return_value='食品'))
        popup.start()
        self.addCleanup(popup.stop)

    async def enter(self):
        await self.runner.auto_quest_solo()
        if self.client.send_key.await_count == 0:
            self.now += 1
            await self.runner.auto_quest_solo()

    async def test_food_three_of_five_zero_arrow_scans_and_confirms_four(self):
        with patch.object(self.runner, 'auto_collect_rewrite', wraps=self.runner.auto_collect_rewrite) as entry:
            await self.enter()
        entry.assert_awaited_once_with(self.client)
        self.client.get_base_entity_list.assert_awaited()
        self.runner.move_until_quest_interaction.assert_awaited_once()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertEqual(self.count, 4)
        self.assertIn(91, self.client._deimos_collect_search.verified_templates)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_missing_quest_registry_still_collects(self):
        self.client.quest_manager.return_value.quest_data.return_value = {}
        await self.enter()
        self.assertEqual(self.count, 4)
        self.client.get_base_entity_list.assert_awaited()

    async def test_food_continues_from_three_to_five_on_next_search(self):
        await self.enter()
        self.assertEqual(self.count, 4)
        self.client.get_base_entity_list.return_value = [fixture.UnguidedCollectTests.entity(self, 1002)]
        self.now += 6
        await self.runner.auto_quest_solo()
        self.assertEqual(self.count, 5)
        self.assertEqual(self.client.send_key.await_count, 2)
        self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_missing_goal_registry_still_collects(self):
        self.quest.goal_data.return_value = {}
        self.assertIsNotNone(await self.runner._unguided_collect_context(self.client))
        await self.enter()
        self.assertEqual(self.count, 4)

    async def test_unknown_type_still_collects(self):
        self.kind = 99
        await self.enter()
        self.assertEqual(self.count, 4)

    async def test_unreadable_type_still_collects(self):
        self.data.goal_type.side_effect = OSError('transient Goal data')
        await self.enter()
        self.assertEqual(self.count, 4)

    async def test_unparsed_chinese_counter_scans_without_assuming_progress(self):
        self.runner.read_quest_txt.side_effect = lambda _: '收集 食品 地点：康达沙漠 （三/五）'
        self.client.send_key.side_effect = None
        await self.enter()
        self.client.get_base_entity_list.assert_awaited()
        self.assertEqual(self.client.send_key.await_count, 3)
        self.assertIn(('entity', 1001, 91), self.client._deimos_collect_search.blacklist)
        self.assertEqual(self.count, 3)

    async def test_default_guided_collect_uses_existing_scanner_and_confirms_count(self):
        self.target = XYZ(500, 0, 0)
        self.assertIsNone(await self.runner._unguided_collect_context(self.client))
        self.assertTrue(await self.runner.auto_collect_rewrite(self.client))
        self.client.get_base_entity_list.assert_awaited()
        self.assertEqual(self.count, 4)
        self.runner.move_until_quest_interaction.assert_not_awaited()

    async def test_explicit_combat_type_blocks_collect_even_with_collect_text(self):
        self.kind = 1
        self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
        self.assertEqual(self.client._quest_collect_block_reason[0], 'explicit_non_collect_type:1')
        self.client.get_base_entity_list.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_explicit_npc_text_blocks_collect_without_goal_registry(self):
        self.quest.goal_data.return_value = {}
        self.runner.read_quest_txt.side_effect = lambda _: '拜访 食品管理员 地点：康达沙漠'
        self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
        self.assertEqual(self.client._quest_collect_block_reason[0], 'explicit_combat_npc_travel_or_photo')
        self.client.get_base_entity_list.assert_not_awaited()

    async def test_preflight_loading_battle_refill_dialogue_and_stop_block_scanning(self):
        for event in ('loading', 'battle', 'refill', 'dialogue', 'stop'):
            with self.subTest(event=event):
                self.client.questing_status = True
                self.client.refilling_potions = False
                self.client.is_loading.return_value = self.client.in_battle.return_value = False
                with patch.object(q, 'is_free_leader_questing', AsyncMock(return_value=event != 'dialogue')):
                    if event == 'loading': self.client.is_loading.return_value = True
                    elif event == 'battle': self.client.in_battle.return_value = True
                    elif event == 'refill': self.client.refilling_potions = True
                    elif event == 'stop': self.client.questing_status = False
                    self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
        self.client.get_base_entity_list.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_other_input_owner_is_preserved(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def dialogue():
            async with automation_owner(self.client, 'npc-dialogue'):
                started.set()
                await release.wait()
        task = asyncio.create_task(dialogue())
        try:
            await started.wait()
            self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.client.get_base_entity_list.assert_not_awaited()
        finally:
            release.set()
            await task

    async def test_zero_entry_does_not_bypass_group_recovery_or_modal_guards(self):
        for attr, value in (('quest_party_quester', object()), ('quest_party_probe_pending', True),
                            ('quest_party_target_sync_active', True), ('quest_party_dungeon_entry_active', True),
                            ('post_combat_cleanup_active', True), ('quest_recovery_owner', 'potion-return')):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                self.assertFalse(await self.runner.auto_collect_rewrite(self.client))
                setattr(self.client, attr, None)
        self.client.get_base_entity_list.assert_not_awaited()
        self.client.send_key.assert_not_awaited()


class ZeroArrowLegacyEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ordinary_fixture.OrdinaryQuest415Tests.setUp(self)
        self.target = XYZ(0, 0, 0)
        self.objective = '收集 食品 地点：康达沙漠 (3/5)'
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.objective)
        self.quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.quester._unguided_collect_context = AsyncMock(return_value=None)
        self.quester.auto_collect_rewrite = AsyncMock(return_value=False)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.range_visible = False

    async def test_zero_arrow_calls_public_entry_even_when_auxiliary_context_is_none(self):
        await self.quester.auto_quest_solo()
        self.quester.auto_collect_rewrite.assert_awaited_once_with(self.client)

    async def test_zero_arrow_unparsed_counter_calls_public_entry(self):
        self.objective = '收集 食品 地点：康达沙漠 （三/五）'
        await self.quester.auto_quest_solo()
        self.quester.auto_collect_rewrite.assert_awaited_once_with(self.client)

    async def test_guided_collect_keeps_normal_target_movement(self):
        self.target = XYZ(1000, 2000, 0)
        self.quester._quest_local_interaction_ready = AsyncMock(return_value=False)
        self.quester.teleport_to_quest_target = AsyncMock(return_value=False)
        await self.quester.auto_quest_solo()
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.client, self.target)
        self.quester.auto_collect_rewrite.assert_not_awaited()

    async def test_guided_combat_keeps_normal_target_movement(self):
        self.objective = 'Defeat Sand Spider in Desert (3/5)'
        await self.test_guided_collect_keeps_normal_target_movement()
