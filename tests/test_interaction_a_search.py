"""Measured A search through the current ordinary X and NPC retry callers."""
import asyncio
from contextlib import ExitStack
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Client, Keycode, XYZ
from src.paths import advance_dialog_path, npc_range_path
from src.questing import Quester


REAL_SLEEP = asyncio.sleep


class InteractionASearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = 0.0
        self.heading = 6.1  # Cross the 2pi boundary during the first segment.
        self.swept = 0.0
        self.turn_direction = 1
        self.keys = []
        self.held = set()
        self.prompt = 'Press X for something unrelated to the objective'
        self.title = 'Lever'
        self.objective = 'Talk to Sunless Shrine'
        self.goal = 7
        self.target = XYZ(1000, 2000, 3)
        self.dialogue = False
        self.on_a = lambda: None
        self.on_x = lambda: None
        self.after_sleep = lambda delay: None
        self.client = SimpleNamespace(title='p3', questing_status=True,
            entity_detect_combat_status=False, use_potions=False, auto_pet_status=False,
            quest_party_hitters=[], refilling_potions=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='SunlessShrineEnt'), quest_id=AsyncMock(return_value=42),
            goal_id=AsyncMock(side_effect=lambda: self.goal),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target),
                                 yaw=AsyncMock(side_effect=lambda: self.heading)))
        self.client.send_key = self.send_key
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.objective)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.questing.asyncio.sleep', self.sleep))
        self.stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.questing.is_potion_needed', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.close_automation_popup', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_free_leader_questing',
            AsyncMock(side_effect=lambda _: not self.dialogue)))
        self.stack.enter_context(patch('src.questing.read_dialogue_text',
            AsyncMock(side_effect=lambda _: 'Real dialogue' if self.dialogue else '')))
        self.stack.enter_context(patch('src.questing.is_visible_by_path',
            AsyncMock(side_effect=lambda _, path: path == npc_range_path or
                      (path == advance_dialog_path and self.dialogue))))
        self.stack.enter_context(patch('src.questing.exit_menus', AsyncMock()))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Use Lever')))
        self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(side_effect=lambda _: self.title)))
        self.stack.enter_context(patch('src.questing.collision_tp', AsyncMock()))

    async def sleep(self, delay):
        self.clock += delay
        self.after_sleep(delay)
        await REAL_SLEEP(0)

    async def send_key(self, key, seconds=0):
        self.keys.append(key)
        self.held.add(key)
        try:
            if key == Keycode.A:
                change = math.pi * seconds
                self.heading = (self.heading + self.turn_direction * change) % math.tau
                self.swept += change
                self.on_a()
            elif key == Keycode.X:
                self.on_x()
            await self.sleep(seconds)
        finally:
            self.held.discard(key)

    async def test_npc_three_failed_retries_reach_a_search_and_complete_real_turn(self):
        self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertGreaterEqual(self.swept, math.tau)
        self.assertLess(self.swept, math.tau + .4)
        self.assertFalse(self.held)

    async def test_ordinary_x_no_progress_reaches_same_measured_search(self):
        self.objective = 'Use Lever'
        self.prompt = 'Press X to Use'
        await self.quester.auto_quest_solo()
        self.assertGreaterEqual(self.swept, math.tau)
        self.assertEqual(self.keys.count(Keycode.X), 1)  # Original X only; unchanged object is skipped.
        self.assertFalse(self.held)

    async def search(self):
        return await self.quester._recover_quest_x_direction(self.client,
            await self.quester._dungeon_quest_snapshot(self.client), self.target,
            await self.client.zone_name(), [self.client])

    async def test_goal_id_progress_stops_after_first_changed_segment(self):
        def advance():
            if self.keys.count(Keycode.A) == 2:
                self.goal += 1  # HUD and target can lag behind the goal ID.
        self.on_a = advance
        self.assertTrue(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 2)
        self.assertAlmostEqual(self.swept, math.pi * .2)
        self.assertFalse(self.held)

    async def test_progress_after_x_does_not_send_another_a(self):
        self.on_a = lambda: setattr(self, 'title', 'Different object') if self.keys.count(Keycode.A) == 3 else None
        def advance():
            if self.keys.count(Keycode.A) == 3:
                self.objective = 'Go to next objective'
        self.on_x = advance
        self.assertTrue(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 3)

    async def test_npc_goal_id_advance_during_retry_wait_exits_before_any_search(self):
        def advance(delay):
            if self.clock > .2:
                self.goal = 8
        self.after_sleep = advance
        self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertEqual(self.keys, [])
        self.assertLess(self.clock, 1.0)

    async def test_initial_npc_x_advance_is_not_mistaken_for_a_new_stall(self):
        self.prompt = 'Press X to Talk'
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_ordinary_initial_x_progress_exits_without_turning(self):
        self.objective = 'Use Lever'
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_dialogue_no_progress_resumes_remaining_turn_excluding_npc_camera(self):
        self.on_a = lambda: setattr(self, 'title', 'Different NPC') if self.keys.count(Keycode.A) == 2 else None
        opened = []
        closed = []
        def open_dialogue():
            if not opened and self.keys.count(Keycode.A) == 2:
                opened.append((self.clock, self.swept))
                self.dialogue = True
        def finish_dialogue(delay):
            if self.dialogue:
                self.assertAlmostEqual(self.swept, opened[0][1])
                self.assertFalse(self.held & {Keycode.A})
                if self.clock - opened[0][0] >= 1.0:
                    self.dialogue = False
                    self.heading = (self.heading + 1.5) % math.tau
                    closed.append(self.clock)
        self.on_x = open_dialogue
        self.after_sleep = finish_dialogue
        self.assertFalse(await self.search())
        self.assertTrue(closed)
        self.assertEqual(self.keys.count(Keycode.A), 20)
        self.assertAlmostEqual(self.swept, math.tau)

    async def test_dialogue_progress_ends_search_without_completing_circle(self):
        self.on_a = lambda: setattr(self, 'title', 'Different NPC')
        def open_dialogue():
            self.dialogue = True
        def advance(delay):
            if self.dialogue and self.clock > .5:
                self.goal = 8
                self.dialogue = False
        self.on_x = open_dialogue
        self.after_sleep = advance
        self.assertTrue(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), 1)
        self.assertFalse(self.held)

    async def test_long_dialogue_retains_remaining_angle_after_existing_wait_timeout(self):
        self.on_a = lambda: setattr(self, 'title', 'Different NPC') if self.keys.count(Keycode.A) == 2 else None
        opened = []
        def open_dialogue():
            if not opened and self.keys.count(Keycode.A) == 2:
                opened.append(self.clock)
                self.dialogue = True
        def finish_dialogue(delay):
            if opened and self.clock - opened[0] >= 70:
                self.dialogue = False
        self.on_x = open_dialogue
        self.after_sleep = finish_dialogue
        self.assertFalse(await self.search())
        self.assertTrue(opened)
        self.assertFalse(self.dialogue)
        self.assertEqual(self.keys.count(Keycode.A), 20)
        self.assertAlmostEqual(self.swept, math.tau)

    async def test_target_change_during_dialogue_waits_for_end_before_stopping(self):
        self.on_a = lambda: setattr(self, 'title', 'Different NPC')
        self.on_x = lambda: setattr(self, 'dialogue', True)
        def change(delay):
            if self.dialogue and self.clock > .5:
                self.target = XYZ(1100, 2000, 3)
                if self.clock >= 1:
                    self.dialogue = False
        self.after_sleep = change
        await self.search()
        self.assertEqual(self.keys.count(Keycode.A), 1)
        self.assertGreaterEqual(self.clock, 4.0)
        self.assertFalse(self.dialogue)
        self.assertFalse(self.held)

    async def test_existing_world_selector_prevents_first_a(self):
        with patch('src.questing.is_spiral_door_open', AsyncMock(return_value=True)):
            self.assertFalse(await self.search())
        self.assertEqual(self.keys, [])

    async def test_dialogue_opening_during_prompt_read_prevents_x(self):
        opened = []
        def read_prompt(client):
            if not opened:
                opened.append(self.clock)
                self.dialogue = True
            return self.prompt
        def finish_dialogue(delay):
            if opened and self.clock - opened[0] > 1:
                self.dialogue = False
        def try_x():
            self.assertFalse(self.dialogue)
        self.quester.read_popup.side_effect = read_prompt
        self.after_sleep = finish_dialogue
        self.on_x = try_x
        self.assertFalse(await self.search())
        self.assertGreaterEqual(self.swept, math.tau)

    async def test_dialogue_camera_change_during_a_does_not_count_as_search_angle(self):
        opened = []
        def open_dialogue():
            if not opened and self.keys.count(Keycode.A) == 2:
                opened.append(self.clock)
                self.dialogue = True
                self.heading = (self.heading + 1.5) % math.tau
        def finish_dialogue(delay):
            if opened and self.clock - opened[0] > 1:
                self.dialogue = False
        self.on_a = open_dialogue
        self.after_sleep = finish_dialogue
        self.assertFalse(await self.search())
        self.assertGreaterEqual(self.swept, math.tau)
        self.assertLess(self.swept, math.tau + .4)

    async def test_target_change_stops_even_when_goal_and_hud_are_unchanged(self):
        def change():
            self.target = XYZ(1100, 2000, 3)
        self.on_a = change
        self.assertFalse(await self.search())
        self.assertEqual(self.keys, [Keycode.A])
        self.assertFalse(self.held)

    async def test_loading_battle_disabled_and_zone_change_each_stop_before_next_x(self):
        for cause in ('loading', 'battle', 'disabled', 'zone'):
            with self.subTest(cause=cause):
                self.setUp()
                def interrupt():
                    if cause == 'loading':
                        self.client.is_loading.return_value = True
                    elif cause == 'battle':
                        self.client.in_battle.return_value = True
                    elif cause == 'disabled':
                        self.client.questing_status = False
                    else:
                        self.client.zone_name.return_value = 'AnotherRoom'
                self.on_a = interrupt
                self.assertFalse(await self.search())
                self.assertEqual(self.keys, [Keycode.A])
                self.assertFalse(self.held)
                self.stack.close()

    async def test_full_turn_cooldown_blocks_rotation_then_expires(self):
        self.assertFalse(await self.search())
        pulses = self.keys.count(Keycode.A)
        # Persist across recreation of the quest worker.
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value=self.prompt)
        self.quester.read_quest_txt = AsyncMock(return_value=self.objective)
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), pulses)
        self.clock += 5.1
        self.assertFalse(await self.search())
        self.assertGreater(self.keys.count(Keycode.A), pulses)
        self.assertLess(self.keys.count(Keycode.A), pulses + 23)

    async def test_negative_yaw_direction_also_covers_full_circle_across_wraparound(self):
        self.turn_direction = -1
        self.assertFalse(await self.search())
        self.assertAlmostEqual(self.swept, math.tau)

    async def test_no_actual_yaw_change_stops_and_does_not_claim_full_circle(self):
        self.turn_direction = 0
        self.assertFalse(await self.search())
        self.assertLess(self.keys.count(Keycode.A), 22)  # Skipped X no longer adds wait time.
        self.assertEqual(self.heading, 6.1)
        self.assertGreater(self.client._quest_x_turn_next_at, self.clock)
        pulses = self.keys.count(Keycode.A)
        self.assertFalse(await self.search())
        self.assertEqual(self.keys.count(Keycode.A), pulses)

    async def test_task_cancellation_during_a_releases_key_and_propagates(self):
        self.on_a = lambda: asyncio.current_task().cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.search()
        self.assertEqual(self.keys, [Keycode.A])
        self.assertFalse(self.held)


class TimedAReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_client_timed_key_releases_a_when_cancelled_during_press(self):
        # Characterize the exact Client API used by the search; replace only
        # Win32 delivery so the test cannot send input to a real game window.
        utils = Client.send_key.__globals__['utils']
        down = asyncio.Event()
        held = set()
        events = []
        def keydown(handle, key):
            held.add(key)
            down.set()
        def keyup(handle, key):
            held.discard(key)
            events.append((handle, key))
        with patch.object(utils, '_send_keydown', keydown), patch.object(utils, '_send_keyup', keyup):
            task = asyncio.create_task(Client.send_key(SimpleNamespace(window_handle=12345), Keycode.A, 60))
            await asyncio.wait_for(down.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(events, [(12345, Keycode.A)])
        self.assertFalse(held)


if __name__ == '__main__':
    unittest.main()
