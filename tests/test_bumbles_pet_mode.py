import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester, claim_quest_recovery
from src.paths import pet_system_button_path, play_as_pet_button_path


class BumblesPetModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.zone = Quester.BUMBLES_PET_ZONE
        self.progress = (500123, 7, '使用宠物模式寻找大黄蜂 地点：废料堆')
        self.quest_code = 'QuestTitle_17442D'
        self.goal_code = 'WizQst17442D_00000005'
        self.tip = '<string;GUI2_00001181>'
        self.expanded = False
        self.mode_at = None
        self.goal = SimpleNamespace(name_lang_key=AsyncMock(side_effect=lambda: self.goal_code))
        self.quest = SimpleNamespace(goal_data=AsyncMock(side_effect=lambda: {self.progress[1]: self.goal}))
        self.button = SimpleNamespace(tip=AsyncMock(side_effect=lambda: self.tip),
            is_visible=AsyncMock(side_effect=lambda: self.expanded),
            is_control_grayed=AsyncMock(return_value=False))
        self.menu = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            is_control_grayed=AsyncMock(return_value=False))
        self.client = SimpleNamespace(title='p2', questing_status=True, quest_recovery_owner=None,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.progress[0]),
            goal_id=AsyncMock(side_effect=lambda: self.progress[1]),
            quest_manager=AsyncMock(return_value=SimpleNamespace(
                quest_data=AsyncMock(side_effect=lambda: {self.progress[0]: self.quest}))),
            root_window=object(), cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='')),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=Quester.BUMBLES_PET_POSITION)),
            teleport=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.quester._mainline_identity = AsyncMock(side_effect=lambda _: (
            self.progress[0], self.quest_code, 'Nose for Clues',
            {'world': 'LEMURIA', 'number': 83}, True))
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: self.progress)
        self.barrier = self.quester._mainline_sync_blocks_movement = AsyncMock(return_value=False)
        async def tick(seconds):
            self.now += seconds
            if self.mode_at is not None and self.now >= self.mode_at:
                self.tip = '<string;GUI2_00001155>'
        async def node(root, path):
            if path == pet_system_button_path:
                return self.menu
            if path == play_as_pet_button_path:
                return self.button
            return None
        async def click(client, path):
            self.assertIs(client, self.client)
            self.assertEqual(client.quest_recovery_owner, 'bumbles_pet')
            self.assertFalse(claim_quest_recovery(client, 'dungeon_quest'))
            if path == pet_system_button_path:
                self.expanded = True
            else:
                self.mode_at = self.now + .6
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)))
        stack.enter_context(patch('src.questing.get_window_from_path', new=AsyncMock(side_effect=node)))
        self.click = stack.enter_context(patch('src.questing.click_window_by_path', new=AsyncMock(side_effect=click)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)))
        self.log = stack.enter_context(patch('src.questing.logger'))

    async def check(self):
        return await self.quester._maybe_handle_bumbles_pet(self.client)

    async def test_exact_quest_stage_tp_menu_click_and_stable_confirmation(self):
        self.assertTrue(await self.check())
        point = self.client.teleport.await_args.args[0]
        self.assertEqual((point.x, point.y, point.z), (14017.3837890625, -563.506591796875, -2051.80712890625))
        self.client.teleport.assert_awaited_once()
        self.assertEqual([call.args[1] for call in self.click.await_args_list],
                         [pet_system_button_path, play_as_pet_button_path])
        self.assertTrue(self.client.quest_bumbles_pet['confirmed'])
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertGreaterEqual(self.now, self.mode_at + 1)
        await self.check()
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.click.await_count, 2)

    async def test_already_pet_no_tp_no_click_and_reads_progress(self):
        self.tip = 'Cancel Play as Pet'
        await self.check()
        self.client.teleport.assert_not_awaited()
        self.click.assert_not_awaited()
        self.assertTrue(self.client.quest_bumbles_pet['confirmed'])

    async def test_existing_menu_not_toggled(self):
        self.expanded = True
        await self.check()
        self.click.assert_awaited_once_with(self.client, play_as_pet_button_path)

    async def test_wrong_area_quest_goal_and_substring_not_triggered(self):
        original = self.progress
        for field, value in [('zone', 'Lemuria/LM_Z06_SkyCity'),
                             ('quest_code', 'QuestTitle_174430'),
                             ('goal_code', 'WizQst17442D_00000006'),
                             ('progress', (500123, 7, '使用宠物模式寻找大黄蜂和金狮 地点：废料堆'))]:
            previous = getattr(self, field)
            setattr(self, field, value)
            self.assertFalse(await self.check())
            setattr(self, field, previous)
        self.client.teleport.assert_not_awaited()
        self.click.assert_not_awaited()

    async def test_english_stage_and_no_numeric_id_inference(self):
        self.progress = (9123456, 4, 'Pet Mode to Find Bumbles in Heap')
        self.assertIsNotNone(await self.quester._bumbles_pet_stage(self.client))
        self.assertEqual((await self.quester._bumbles_pet_stage(self.client))[0][0], 9123456)

    async def test_busy_guards_and_sync_barrier_block_operations(self):
        for attr in ('is_loading', 'in_battle', 'is_in_dialog'):
            getattr(self.client, attr).return_value = True
            await self.check()
            getattr(self.client, attr).return_value = False
        self.client.quest_recovery_owner = 'tamarin_house'
        await self.check()
        self.client.quest_recovery_owner = None
        self.barrier.return_value = True
        await self.check()
        self.client.teleport.assert_not_awaited()
        self.click.assert_not_awaited()

    async def test_party_probe_does_not_deadlock_hitter(self):
        self.client.quest_party_probe_pending = True
        await self.check()
        self.assertFalse(self.client.bumbles_pet_pending)
        self.client.teleport.assert_not_awaited()

    async def test_pure_hitter_excluded(self):
        self.client.quest_party_status_session = object()
        self.assertFalse(await self.check())
        self.client.teleport.assert_not_awaited()

    async def test_two_button_attempts_failure_warned_once_no_whole_workflow_retry(self):
        async def click(client, path):
            self.expanded = True
        self.click.side_effect = click
        await self.check()
        self.assertEqual([call.args[1] for call in self.click.await_args_list].count(play_as_pet_button_path), 2)
        self.client.teleport.assert_awaited_once()
        self.log.warning.assert_called_once()
        await self.check()
        self.assertEqual(self.click.await_count, 3)
        self.client.teleport.assert_awaited_once()

    async def test_unknown_mode_never_tp_or_click(self):
        self.tip = 'unknown'
        await self.check()
        self.client.teleport.assert_not_awaited()
        self.click.assert_not_awaited()

    async def test_position_not_arrived_never_click(self):
        self.client.body.position.return_value = XYZ(0, 0, 0)
        await self.check()
        self.client.teleport.assert_awaited_once()
        self.click.assert_not_awaited()
        self.log.warning.assert_called_once()

    async def test_next_stage_cancels_pet_once_and_resumes_only_after_stable_wizard(self):
        await self.check()
        self.progress = (500123, 8, 'Collect Key in Heap')
        self.goal_code = 'WizQst17442D_00000006'
        self.mode_at = None
        start = self.now
        async def cancel(client, path):
            self.assertEqual(path, play_as_pet_button_path)
            self.assertIs(client, self.client)
            self.assertTrue(client.bumbles_pet_pending)
            self.assertEqual(client.quest_recovery_owner, 'bumbles_pet')
            self.tip = 'Play as Pet (Free)'
        self.click.reset_mock()
        self.click.side_effect = cancel
        self.assertFalse(await self.check())
        self.click.assert_awaited_once_with(self.client, play_as_pet_button_path)
        self.assertGreaterEqual(self.now - start, 1)
        self.assertFalse(self.client.bumbles_pet_pending)
        self.assertIsNone(self.client.quest_bumbles_pet)
        self.client.teleport.assert_awaited_once()
        self.assertFalse(await self.check())
        self.click.assert_awaited_once()

    async def test_transient_unreadable_stage_does_not_reset_failure_latch(self):
        self.tip = 'unknown'
        await self.check()
        self.quester._bumbles_pet_stage = AsyncMock(return_value=None)
        self.tip = 'Play as Pet (Free)'
        self.quester._dungeon_quest_snapshot = AsyncMock(return_value=None)
        self.assertTrue(await self.check())
        self.assertTrue(self.client.quest_bumbles_pet['attempted'])

    async def test_cancelled_tp_releases_ownership(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.check()
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.check()
        self.client.teleport.assert_awaited_once()

    async def test_localized_cancel_tip_uses_game_cache(self):
        self.tip = '取消扮演宠物'
        self.client.cache_handler.get_langcode_name.side_effect = lambda key: self.tip if key == 'GUI2_00001155' else ''
        self.assertTrue(await self.quester._play_as_pet_state(self.client))

    async def test_pet_pending_blocks_task_tp_and_other_recovery(self):
        self.tip = 'Cancel Play as Pet'
        await self.check()
        with patch('src.questing.collision_tp', new=AsyncMock()) as tp:
            await self.quester.teleport_to_quest_target(self.client, XYZ(1, 2, 3))
            tp.assert_not_awaited()
        self.assertTrue(await self.quester._dungeon_recovery_blocked(self.client))
        self.assertTrue(await self.quester._trigger_reentry_blocked(self.client))
        self.assertTrue(await self.quester._mainline_finder_blocked(self.client))
        self.assertTrue(await self.quester._lemuria_navigation_blocked(self.client, self.zone))

    async def test_missing_or_grayed_button_never_clicked(self):
        self.expanded = True
        self.button.is_control_grayed.return_value = True
        await self.check()
        self.click.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancel_failure_never_clicks_twice_or_releases_task_tp(self):
        await self.check()
        self.progress = (500123, 8, 'Collect Key in Heap')
        self.goal_code = 'WizQst17442D_00000006'
        self.click.reset_mock()
        self.click.side_effect = None
        self.assertTrue(await self.check())
        self.assertTrue(await self.check())
        self.click.assert_awaited_once_with(self.client, play_as_pet_button_path)
        self.assertTrue(self.client.bumbles_pet_pending)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancel_waits_for_task_progress_and_busy_end(self):
        await self.check()
        self.click.reset_mock()
        await self.check()
        self.click.assert_not_awaited()
        self.progress = (500123, 8, 'Collect Key in Heap')
        self.goal_code = 'WizQst17442D_00000006'
        self.client.in_battle.return_value = True
        self.assertTrue(await self.check())
        self.click.assert_not_awaited()

    async def test_tp_immediately_completes_stage_no_unnecessary_pet_toggle(self):
        async def teleport(point):
            self.progress = (500123, 8, 'Collect Key in Heap')
            self.goal_code = 'WizQst17442D_00000006'
        self.client.teleport.side_effect = teleport
        await self.check()
        self.click.assert_not_awaited()
        self.log.warning.assert_not_called()
        self.assertTrue(await self.check())
        self.now += 1.1
        self.assertFalse(await self.check())
        self.client.teleport.assert_awaited_once()



