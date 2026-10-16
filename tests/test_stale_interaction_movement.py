"""Reference ordinary prompt range, with state changes still ending old operations."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import teleport_math as movement
from src.paths import npc_range_path, dialog_text_path, team_up_button_path
import tests.test_interaction_a_search as fixtures


class Fixture(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.InteractionASearchTests.setUp
    sleep = fixtures.InteractionASearchTests.sleep
    send_key = fixtures.InteractionASearchTests.send_key

    def nearby(self, gap=476):
        self.position = XYZ(self.target.x - gap, self.target.y, self.target.z)
        self.client.body.position.side_effect = lambda: self.position
        self.stack.enter_context(patch.object(movement, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _c, path: path == npc_range_path or path == dialog_text_path and self.dialogue)))


class CurrentPromptTests(Fixture):
    async def test_476_unit_prompt_hands_off_in_reference_ordinary_range(self):
        self.nearby()
        snapshot = await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(await movement._movement_stop_reason(
            self.client, self.target, 'SunlessShrineEnt', snapshot))

    async def test_task_watcher_also_allows_nearby_prompt_without_new_name_gate(self):
        self.nearby()
        with patch('src.questing.get_quest_name', AsyncMock(return_value='Use Lever')):
            self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
            with patch('src.questing.collision_tp', AsyncMock()) as tp:
                await self.quester.move_until_quest_interaction(self.client, self.target)
            tp.assert_not_awaited()

    async def test_real_dialogue_still_stops_with_far_target_and_range_prompt(self):
        self.nearby()
        self.dialogue = True
        snapshot = await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(await movement._movement_stop_reason(
            self.client, self.target, 'SunlessShrineEnt', snapshot))
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_failed_x_alone_does_not_block_next_ordinary_npc_iteration(self):
        self.prompt = 'Press X to Talk'
        self.objective = 'Talk to Lever'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        self.assertGreater(first_x, 0)
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            for _ in range(3):
                self.clock += 6
                await self.quester.auto_quest_solo()
        self.assertGreater(self.keys.count(Keycode.X), first_x)
        tp.assert_not_awaited()

    async def test_same_failed_x_identity_keeps_reference_ordinary_interaction(self):
        self.prompt = 'Press X to Use'
        self.objective = 'Use Lever'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            self.clock += 6
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), first_x + 1)
        tp.assert_not_awaited()

    async def test_new_goal_with_same_prompt_is_not_blocked_by_old_identity(self):
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        self.goal += 1
        self.position = self.target
        self.target = XYZ(self.target.x + 60, self.target.y, self.target.z)
        self.client.body.position.side_effect = lambda: self.position
        self.nearby(60)
        async def arrive(*_args, **_kwargs):
            self.position = self.target
            return 'ready'
        with patch('src.questing.collision_tp', AsyncMock(side_effect=arrive)) as tp:
            self.clock += 6
            await self.quester.auto_quest_solo()
        tp.assert_awaited_once()
        self.assertEqual(self.keys.count(Keycode.X), first_x + 1)

    async def test_unrelated_new_identity_is_rejected_after_old_failure(self):
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        self.title = 'Other object'
        self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
        self.clock += 6
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), first_x)

    async def test_task_change_during_movement_never_interacts_with_old_target(self):
        self.prompt = 'Press X to Talk'
        async def change(*_args, **_kwargs):
            self.goal += 1
            self.target = XYZ(self.target.x + 60, self.target.y, self.target.z)
        self.quester.teleport_to_quest_target = AsyncMock(side_effect=change)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_unreadable_identity_never_authorizes_ordinary_x(self):
        self.title = ''
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        self.clock += 6
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_confirmed_dialogue_progress_releases_failure_for_same_goal_coordinates(self):
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        first_x = self.keys.count(Keycode.X)
        before = await self.quester._dungeon_quest_snapshot(self.client)
        self.dialogue = True
        def settle(_delay):
            self.dialogue = False
            self.objective = 'Use Lever (updated objective)'
        self.after_sleep = settle
        self.assertTrue(await self.quester.handle_npc_talking_quests(
            self.client, [self.client], direction_search=False, before_progress=before))
        self.after_sleep = lambda _delay: None
        self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
        self.clock += 6
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), first_x + 1)

    async def test_existing_team_up_control_yields_for_entry_even_before_close_approach(self):
        self.nearby()
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _c, path: path in (npc_range_path, team_up_button_path))))
        snapshot = await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(await movement._movement_stop_reason(
            self.client, self.target, 'SunlessShrineEnt', snapshot))

    async def test_prompt_appearance_and_x_without_progress_never_report_quest_success(self):
        self.prompt = 'Press X to Talk'
        before = await self.quester._dungeon_quest_snapshot(self.client)
        self.assertFalse(await self.quester.handle_npc_talking_quests(
            self.client, [self.client], before_progress=before,
            failed_interaction=await self.quester._quest_interaction_signature(self.client)))
        self.assertEqual(await self.quester._dungeon_quest_snapshot(self.client), before)

    async def test_current_task_stop_check_does_not_require_a_different_popup_identity(self):
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        self.position = self.target
        self.target = XYZ(self.target.x + 60, self.target.y, self.target.z)
        self.goal += 1
        self.nearby(60)
        self.stack.enter_context(patch.object(movement, 'get_popup_title', AsyncMock(return_value=self.title)))
        self.stack.enter_context(patch.object(movement, 'get_window_from_path', AsyncMock(
            return_value=SimpleNamespace(maybe_text=AsyncMock(return_value=self.prompt)))))
        self.client.root_window = object()
        snapshot = await movement._movement_task_snapshot(self.client)
        self.assertIsNotNone(await movement._movement_stop_reason(
            self.client, self.target, 'SunlessShrineEnt', snapshot))

    async def test_goal_change_during_final_interaction_identity_read_prevents_x(self):
        self.prompt = 'Press X to Talk'
        self.quester.teleport_to_quest_target = AsyncMock()
        reads = []
        async def signature(*_args, **_kwargs):
            reads.append(1)
            if len(reads) == 2:  # First gate read, then original pre-X identity capture.
                self.goal += 1
            return self.title, self.prompt
        self.quester._quest_interaction_signature = signature
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_failed_tp_log_includes_actual_task_and_target(self):
        self.nearby(182)
        task=await movement._movement_task_snapshot(self.client)
        key=(task[3],(task[0],task[1],tuple(task[2]),task[3]),tuple(self.target))
        self.client._quest_tp_rejected=dict(key=key,walks=1,origin=self.position,anchor=self.position,
                                           ready=False,announced=False)
        with patch.object(movement.logger, 'debug') as log:
            self.clock += 6
            await self.quester.auto_quest_solo()
        messages = [c.args[0] for c in log.call_args_list if 'failed-task-movement' in c.args[0]]
        self.assertTrue(messages)
        for field in ('task=', 'target=', 'identity='):
            self.assertIn(field, messages[0])


if __name__ == '__main__':
    unittest.main()
