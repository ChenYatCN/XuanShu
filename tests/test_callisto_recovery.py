import asyncio
import ast
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.paths import npc_range_path
from src.questing import Quester
from src.automation_ownership import get_client_automation_ownership


class CallistoRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.text = '击败 噬魂者 地点：Chamber of Callisto'
        self.position = XYZ(0, 0, 0)
        self.popup = '罐子'
        self.tick = None
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            quest_dungeon_recovery=None, quest_party_hitters=[], refilling_potions=False,
            zone_name=AsyncMock(return_value=Quester.CALLISTO_ZONE),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.text)
        self.refresh = AsyncMock(return_value=True)
        self.quester._refresh_dungeon_quest = self.refresh

        async def sleep(seconds):
            self.now += seconds
            if self.tick:
                self.tick()

        async def teleport(point):
            self.position = point
            if point == Quester.CALLISTO_BATTLE:
                self.client.in_battle.return_value = True

        self.client.teleport.side_effect = teleport
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(
            side_effect=lambda c: not c.is_loading.return_value and not c.in_battle.return_value
            and not c.is_in_dialog.return_value)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, path: path == npc_range_path)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.title = stack.enter_context(patch('src.questing.get_popup_title', new=AsyncMock(
            side_effect=lambda c: self.popup)))
        stack.enter_context(patch('src.questing.logger'))

    async def handle(self):
        return await self.quester._maybe_handle_callisto(self.client)

    def jar_stage(self):
        self.text = '使用 罐子 地点：Chamber of Callisto'
        self.client.goal_id.return_value = 8
        self.client.in_battle.return_value = False

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.quest_dungeon_recovery['active'])
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_exact_three_minute_boundary_then_battle_then_jar(self):
        # A nearby interaction is irrelevant to the defeat stage.
        self.visible.side_effect = lambda c, p: False
        self.assertFalse(await self.handle())
        self.now = 179.9
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.now = 180.0
        self.assertTrue(await self.handle())
        self.client.teleport.assert_awaited_once_with(Quester.CALLISTO_BATTLE)
        self.client.send_key.assert_not_awaited()
        self.refresh.assert_not_awaited()
        self.assert_released()
        self.jar_stage()
        self.visible.side_effect = lambda c, p: p == npc_range_path
        self.assertTrue(await self.handle())
        self.assertEqual([c.args[0] for c in self.client.teleport.await_args_list],
                         [Quester.CALLISTO_BATTLE, Quester.CALLISTO_JAR])
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assert_released()
        self.assertTrue(await self.handle())
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.client.send_key.await_count, 1)

    async def test_full_hud_bilingual_defeat_stage_matches(self):
        self.assertEqual(self.quester._callisto_stage(
            '<center>击败 噬魂者<br> Soul Eater 地点：Chamber of Callisto</center>'), 'battle')

    async def test_english_stages_match(self):
        self.assertEqual(self.quester._callisto_stage('Defeat Soul Eater in Chamber of Callisto'), 'battle')
        self.assertEqual(self.quester._callisto_stage('Use Jar in Chamber of Callisto'), 'jar')

    async def test_other_zone_target_action_or_location_does_not_trigger(self):
        self.client.zone_name.return_value = 'Krokotopia/OtherRoom'
        self.assertFalse(await self.handle())
        self.client.zone_name.return_value = Quester.CALLISTO_ZONE
        for text in ('击败 托特 地点：Chamber of Callisto',
                     '击败 噬魂者 地点：Marketplace of Ideas',
                     '收集 罐子 地点：Chamber of Callisto',
                     '使用 罐子和书 地点：Chamber of Callisto'):
            self.text = text
            self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_progress_and_unreadable_snapshot_reset_stall_timer(self):
        self.visible.side_effect = lambda c, p: False
        await self.handle()
        self.now = 179
        self.client.goal_id.return_value = 9
        await self.handle()
        self.now = 180
        await self.handle()
        self.client.teleport.assert_not_awaited()
        self.quester.read_quest_txt.side_effect = None
        self.quester.read_quest_txt.return_value = ''
        await self.quester._maybe_refresh_stalled_dungeon_quest(self.client)
        self.assertIsNone(self.client.quest_dungeon_recovery['since'])

    async def test_battle_not_started_is_bounded_and_not_replayed_immediately(self):
        self.visible.side_effect = lambda c, p: False
        self.client.teleport.side_effect = lambda point: None
        await self.handle()
        self.now = 180
        self.assertTrue(await self.handle())
        self.assertLess(self.now, 191)
        self.client.teleport.assert_awaited_once_with(Quester.CALLISTO_BATTLE)
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 1)
        self.assert_released()

    async def test_different_jar_popup_title_still_presses_x(self):
        self.jar_stage()
        self.popup = '卡利斯托'
        self.assertTrue(await self.handle())
        self.client.teleport.assert_awaited_once_with(Quester.CALLISTO_JAR)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertLess(self.now, 6)
        self.assert_released()

    async def test_far_or_nonfinite_jar_position_does_not_interact(self):
        self.jar_stage()
        self.client.teleport.side_effect = lambda point: None
        for position in (XYZ(10000, 10000, 0), XYZ(float('nan'), 0, 0)):
            self.position = position
            self.client._xuanshu_callisto_jar_retry = None
            await self.handle()
            self.client.send_key.assert_not_awaited()

    async def test_busy_or_assigned_hitter_never_moves(self):
        self.jar_stage()
        for attr, value in (('refilling_potions', True), ('quest_party_probe_pending', True),
                            ('quest_party_battle_rescue_active', True), ('post_combat_movement_active', True),
                            ('is_loading', True), ('in_battle', True), ('is_in_dialog', True),
                            ('quest_party_status_session', object()), ('questing_status', False)):
            with self.subTest(attr=attr):
                if attr in ('is_loading', 'in_battle', 'is_in_dialog'):
                    getattr(self.client, attr).return_value = value
                else:
                    setattr(self.client, attr, value)
                await self.handle()
                self.client.teleport.assert_not_awaited()
                self.client.send_key.assert_not_awaited()
                if attr in ('is_loading', 'in_battle', 'is_in_dialog'):
                    getattr(self.client, attr).return_value = False
                else:
                    setattr(self.client, attr, None if attr == 'quest_party_status_session'
                            else True if attr == 'questing_status' else False)
        self.client.quest_party_hitters = [self.client]
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_other_recovery_owner_is_not_overwritten(self):
        self.jar_stage()
        self.client.quest_recovery_owner = 'potion_refill'
        self.assertTrue(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.assertEqual(self.client.quest_recovery_owner, 'potion_refill')

    async def test_transition_after_jar_teleport_aborts_x(self):
        self.jar_stage()
        async def teleport(point):
            self.position = point
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_goal_change_after_jar_teleport_aborts_x(self):
        self.jar_stage()
        async def teleport(point):
            self.position = point
            self.client.goal_id.return_value = 9
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_popup_wording_change_does_not_block_x(self):
        self.jar_stage()
        self.title.side_effect = ['罐子', '门', '门', '门']
        await self.handle()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assert_released()

    async def test_cancellation_releases_both_locks(self):
        self.jar_stage()
        self.client.teleport.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()

    async def test_loading_started_during_ui_checks_prevents_teleport(self):
        self.jar_stage()
        reads = 0
        async def visible(c, path):
            nonlocal reads
            reads += 1
            if reads == 5:
                self.client.is_loading.return_value = True
            return False
        self.visible.side_effect = visible
        await self.handle()
        self.client.teleport.assert_not_awaited()
        self.assert_released()

    async def test_refill_started_during_x_window_read_prevents_x(self):
        self.jar_stage()
        async def visible(c, path):
            if path == npc_range_path:
                self.client.refilling_potions = True
                return True
            return False
        self.visible.side_effect = visible
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_worker_recreation_retains_timer_and_jar_retry(self):
        self.visible.side_effect = lambda c, p: False
        await self.handle()
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.text)
        self.now = 180
        self.assertTrue(await self.handle())
        self.jar_stage()
        self.visible.side_effect = lambda c, p: p == npc_range_path
        await self.handle()
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.text)
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.send_key.assert_awaited_once()

    async def test_ordinary_dungeon_recovery_unchanged(self):
        self.client.zone_name.return_value = 'OtherDungeon/Room'
        self.text = '击败 噬魂者 地点：Chamber of Callisto'
        await self.quester._confirm_dungeon_entry(self.client, 'Outside')
        await self.quester._maybe_refresh_stalled_dungeon_quest(self.client)
        self.now = 180
        self.visible.side_effect = lambda c, p: False
        await self.quester._maybe_refresh_stalled_dungeon_quest(self.client)
        self.refresh.assert_awaited_once()
        self.client.teleport.assert_not_awaited()

    def test_solo_leader_and_target_movement_use_handler(self):
        tree = ast.parse(Path('src/questing.py').read_text(encoding='utf-8'))
        for name in ('auto_quest_solo', 'auto_quest_leader', 'teleport_to_quest_target'):
            function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            self.assertTrue(any(isinstance(n, ast.Attribute) and n.attr == '_maybe_handle_callisto'
                                for n in ast.walk(function)), name)

    async def test_solo_worker_reaches_jar_step_before_generic_movement(self):
        self.jar_stage()
        for name in ('_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
                     '_maybe_handle_bumbles_pet', 'handle_pending_dungeon_confirmation',
                     '_maybe_recover_lemuria_navigation', '_quest_party_probe_blocks_movement',
                     '_maybe_handle_bumbles_mind', '_maybe_handle_tamarin_house',
                     '_mainline_sync_blocks_movement'):
            setattr(self.quester, name, AsyncMock(return_value=False))
        self.quester._maybe_recover_mainline = AsyncMock(return_value=False)
        with (patch('src.questing.close_npc_quest_menu', new=AsyncMock(return_value=False)),
              patch('src.questing.close_automation_popup', new=AsyncMock(return_value=False))):
            await self.quester.auto_quest_solo()
        self.client.teleport.assert_awaited_once_with(Quester.CALLISTO_JAR)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.quester._maybe_recover_mainline.assert_not_awaited()
        self.assert_released()


if __name__ == '__main__':
    unittest.main()
