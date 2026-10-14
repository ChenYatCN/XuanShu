"""Failure identities are local to A search; dialogue gates final quest reads."""
import math
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.paths import advance_dialog_path, decline_quest_path, npc_range_path
import tests.test_interaction_a_search as fixtures


class Fixture(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.InteractionASearchTests.setUp
    sleep = fixtures.InteractionASearchTests.sleep
    send_key = fixtures.InteractionASearchTests.send_key
    search = fixtures.InteractionASearchTests.search

    def identity(self):
        self.title = 'Failed NPC'
        self.stack.enter_context(patch('src.questing.get_popup_title',
            AsyncMock(side_effect=lambda _: self.title)))


class FailedObjectTests(Fixture):
    async def test_original_failed_object_never_gets_another_x(self):
        self.identity()
        self.assertFalse(await self.search())
        self.assertAlmostEqual(self.swept, math.tau)
        self.assertEqual(self.keys.count(Keycode.X), 0)
        self.assertFalse(self.held)

    async def test_different_title_same_talk_prompt_tries_x_and_stops_on_progress(self):
        self.identity()
        self.prompt = 'Press X to Talk'
        def turn():
            if self.keys.count(Keycode.A) == 3:
                self.title = 'Different NPC'
        self.on_a = turn
        self.on_x = lambda: setattr(self, 'goal', 8)
        self.assertTrue(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 3)
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_changed_action_and_title_are_both_tracked_across_revisits(self):
        self.identity()
        def turn():
            i = self.keys.count(Keycode.A) % 4
            self.title = 'Failed NPC' if i in (0, 1) else 'Other NPC'
            self.prompt = 'Press X to Talk' if i in (0, 2) else 'Press X to Use'
        self.prompt = 'Press X to Talk'
        self.on_a = turn
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.X), 3)
        self.assertAlmostEqual(self.swept, math.tau)

    async def test_empty_or_unreadable_identity_is_not_a_new_object(self):
        self.identity()
        def turn():
            self.title = '' if self.keys.count(Keycode.A) % 2 else 'New NPC'
            self.prompt = 'Press X to Talk' if not self.title else ''
        self.on_a = turn
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.X), 0)

    async def test_same_failed_object_is_not_retried_in_a_later_unchanged_search_round(self):
        self.identity()
        def turn():
            self.title = 'Other NPC' if self.keys.count(Keycode.A) % 2 else 'Failed NPC'
        self.on_a = turn
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.X), 1)
        self.clock += 5.1
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.X), 1)

    async def test_read_failure_or_disappearing_window_keeps_turning_without_x(self):
        for cause in ('read', 'window'):
            with self.subTest(cause=cause):
                self.setUp()
                self.identity()
                if cause == 'read':
                    def title(_):
                        if self.keys.count(Keycode.A): raise RuntimeError('replaced popup')
                        return self.title
                    self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(side_effect=title)))
                else:
                    self.stack.enter_context(patch('src.questing.is_visible_by_path',
                        AsyncMock(side_effect=lambda _, path: path == npc_range_path and not self.keys)))
                self.assertFalse(await self.search())
                self.assertEqual(self.keys.count(Keycode.X), 0)
                self.assertAlmostEqual(self.swept, math.tau)
                self.stack.close()

    async def test_window_disappearing_during_signature_or_state_read_prevents_x(self):
        for cause in ('signature', 'state'):
            with self.subTest(cause=cause):
                self.setUp()
                self.identity()
                visible = [True]
                read_new = []
                self.stack.enter_context(patch('src.questing.is_visible_by_path',
                    AsyncMock(side_effect=lambda _, path: path == npc_range_path and visible[0])))
                self.on_a = lambda: setattr(self, 'title', 'New NPC')
                def title(_):
                    if self.title == 'New NPC':
                        read_new.append(True)
                        if cause == 'signature': visible[0] = False
                    return self.title
                self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(side_effect=title)))
                def position():
                    if read_new and cause == 'state': visible[0] = False
                    return self.target
                self.client.body.position.side_effect = position
                self.assertFalse(await self.search())
                self.assertEqual(self.keys.count(Keycode.X), 0)
                self.assertAlmostEqual(self.swept, math.tau)
                self.stack.close()

    async def test_original_identity_is_captured_before_x_changes_popup(self):
        self.identity()
        self.prompt = 'Press X to Use'
        def interact():
            if self.keys.count(Keycode.X) == 1:
                self.title = 'New object'
            else:
                self.goal = 8
        self.on_x = interact
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 2)
        self.assertEqual(self.keys.count(Keycode.A), 1)


class DialogueStabilityTests(Fixture):
    def dialogue_update(self):
        self.dialogue = True
        self.client.auto_dialogue_running = True
        def update(delay):
            if self.clock < .3:
                self.objective = ''
                self.goal = 0
            elif self.clock < 1:
                self.objective = 'Show Must Go On'
                self.goal = 99
                self.target = XYZ(9999, 9999, 0)
            else:
                self.dialogue = False
                self.objective = 'Mirage next objective'
                self.goal = 8
                self.target = XYZ(1100, 2100, 3)
        self.after_sleep = update

    async def test_handler_waits_for_dialogue_end_and_stable_final_state(self):
        progress = await self.quester._dungeon_quest_snapshot(self.client)
        self.dialogue_update()
        self.assertTrue(await self.quester.handle_npc_talking_quests(
            self.client, [self.client], before_progress=progress))
        self.assertFalse(self.dialogue)
        self.assertGreaterEqual(self.clock, 4)
        self.assertEqual(self.goal, 8)
        self.assertEqual(self.keys, [])
        self.assertTrue(self.client.questing_status)

    async def test_solo_does_not_log_or_move_during_dialogue_then_continues(self):
        self.dialogue_update()
        log = self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.quester.teleport_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        log.assert_not_awaited()
        self.quester.teleport_to_quest_target.assert_not_awaited()
        self.assertFalse(self.dialogue)
        self.assertEqual(self.goal, 8)
        self.after_sleep = lambda delay: None
        self.prompt = 'Press X to Talk'
        self.on_x = lambda: setattr(self, 'goal', 9)
        await self.quester.auto_quest_solo()
        log.assert_awaited_once_with(self.client)
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.client, self.target)
        self.assertEqual((self.target.x, self.target.y, self.target.z), (1100, 2100, 3))

    async def test_dialogue_reopening_during_target_read_prevents_move(self):
        opened = []
        def target():
            if not opened:
                opened.append(self.clock)
                self.dialogue = True
            return self.target
        def close(delay):
            if opened and self.clock - opened[0] >= 1:
                self.dialogue = False
        self.client.quest_position.position.side_effect = target
        self.after_sleep = close
        self.quester.teleport_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.teleport_to_quest_target.assert_not_awaited()
        self.assertFalse(self.dialogue)

    async def test_quest_invitation_without_advance_button_blocks_decisions(self):
        self.dialogue_update()
        self.stack.enter_context(patch('src.questing.is_visible_by_path',
            AsyncMock(side_effect=lambda _, path: path == npc_range_path or
                      path == decline_quest_path and self.dialogue)))
        log = self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.quester.teleport_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        log.assert_not_awaited()
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_auto_dialogue_flag_alone_does_not_block_normal_x(self):
        self.client.auto_dialogue_running = True
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertLess(self.clock, 2)

    async def test_loading_battle_or_user_stop_exits_wait_without_toggling_task(self):
        for cause in ('loading', 'battle', 'stop'):
            with self.subTest(cause=cause):
                self.setUp()
                self.dialogue = True
                def interrupt(delay):
                    if cause == 'stop': self.client.questing_status = False
                    elif cause == 'loading': self.client.is_loading.return_value = True
                    else: self.client.in_battle.return_value = True
                self.after_sleep = interrupt
                self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
                self.assertEqual(self.client.questing_status, cause != 'stop')
                self.assertEqual(self.keys, [])
                self.assertLess(self.clock, 1)
                self.stack.close()

    async def test_dialogue_reopening_during_final_read_keeps_update_pending(self):
        progress = await self.quester._dungeon_quest_snapshot(self.client)
        self.dialogue_update()
        def text(_):
            if self.clock >= 5:
                self.dialogue = True
            return self.objective
        self.quester.read_quest_txt.side_effect = text
        self.assertFalse(await self.quester.handle_npc_talking_quests(
            self.client, [self.client], direction_search=False, before_progress=progress))
        self.assertTrue(self.dialogue)
        self.assertTrue(self.client._quest_dialogue_update_pending)

    async def test_nonfinite_previous_target_is_not_a_stable_pair(self):
        progress = await self.quester._dungeon_quest_snapshot(self.client)
        self.dialogue_update()
        calls = []
        def target():
            calls.append(self.clock)
            return XYZ(float('nan'), 1, 2) if len(calls) == 2 else self.target
        self.client.quest_position.position.side_effect = target
        self.assertTrue(await self.quester.handle_npc_talking_quests(
            self.client, [self.client], direction_search=False, before_progress=progress))
        self.assertGreaterEqual(len(calls), 4)
        self.assertGreaterEqual(self.clock, 6.4)

    async def test_a_search_does_not_accept_temporary_progress_during_snapshot_read(self):
        progress = await self.quester._dungeon_quest_snapshot(self.client)
        opened = []
        def text(_):
            if not opened:
                opened.append(self.clock)
                self.dialogue = True
                self.objective = 'Show Must Go On'
            return self.objective
        def finish(delay):
            if opened and self.clock - opened[0] >= 1:
                self.dialogue = False
                self.goal = 8
                self.objective = 'Mirage next objective'
        self.quester.read_quest_txt.side_effect = text
        self.after_sleep = finish
        self.assertTrue(await self.quester._recover_quest_x_direction(
            self.client, progress, self.target, await self.client.zone_name(), [self.client]))
        self.assertFalse(self.dialogue)
        self.assertGreaterEqual(self.clock, 4)
        self.assertEqual(self.keys, [])

    async def test_fleeting_dialogue_is_remembered_until_quest_reads_settle(self):
        for caller in ('search', 'handler'):
            with self.subTest(caller=caller):
                self.setUp()
                progress = await self.quester._dungeon_quest_snapshot(self.client)
                polls = []
                def visible(_, path):
                    if path == npc_range_path: return True
                    if path == advance_dialog_path:
                        polls.append(True)
                        if len(polls) == 1:
                            self.goal = 8
                            self.objective = 'Mirage next objective'
                            return True
                    return False
                self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=visible)))
                if caller == 'search':
                    result = await self.quester._recover_quest_x_direction(
                        self.client, progress, self.target, await self.client.zone_name(), [self.client])
                else:
                    result = await self.quester.handle_npc_talking_quests(
                        self.client, [self.client], before_progress=progress, direction_search=False)
                self.assertTrue(result)
                self.assertGreaterEqual(self.clock, 4)
                self.assertEqual(self.keys, [])
                self.stack.close()


if __name__ == '__main__':
    unittest.main()
