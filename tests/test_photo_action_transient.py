"""Localized photo actions and stable arrowless stages use the existing photo path."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.interaction_prompts import quest_has_action
from src import teleport_math as movement
from src.automation_ownership import automation_owner
from tests.test_stale_interaction_movement import Fixture


class PhotoActionTextTests(unittest.TestCase):
    def test_localized_and_english_photo_instructions(self):
        for text in (
            '<center>按下Z来拍照 下一个房间 地点：夜之森林</center>',
            '按下 <icon;Z> 来拍照 下一个房间 地点：夜之森林',
            '按下 &lt;image;Z&gt; 来拍照 下一个房间',
            '按下 &Icons_ZKey& 来拍照 下一个房间',
            '按下 Z 来拍照 Tree 地点：Night Forest',
            'Press <icon;Z> to Photomance the Next Room in Night Forest',
            'Press Z to Take a Picture of the Next Room',
            'Take a Picture of the Next Room in Night Forest',
            'Take Photo of an Unlisted Scene', 'Take a Photo of a Tree',
        ):
            with self.subTest(text=text):
                self.assertTrue(quest_has_action(text, 'photomance'))

    def test_ordinary_actions_and_photo_words_in_targets_are_not_photo_tasks(self):
        for text in ('Enter the Next Room', 'Talk to the Photographer',
                     'Collect a photo', 'Use the Camera', 'Defeat Camera Golem',
                     'Go To Photomance Gallery', 'Collect Take a Picture in the Gallery',
                     'Press X to Take a Picture', '按下X来拍照 下一个房间', ''):
            with self.subTest(text=text):
                self.assertFalse(quest_has_action(text, 'photomance'))


class ArrowlessPhotoTests(Fixture):
    def setUp(self):
        super().setUp()
        self.goal = 673648071
        self.client.quest_id.return_value = 148900263012727231
        self.client.zone_name.return_value = 'Lemuria/Interiors/LM_Z03_Cellar'
        self.objective = '按下 Z 来拍照 下一个房间 地点：夜之森林'
        self.title, self.prompt = '下一个房间', '按下 Z 来拍照'
        self.target = XYZ(0, 0, 0)
        self.nearby(0)
        self.quester._finish_photomancy_photo = AsyncMock(return_value=False)
        self.quester.auto_collect_rewrite = AsyncMock()
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)))

    async def test_zero_target_stable_photo_hud_dispatches_existing_photo(self):
        self.quester.take_photomancy_photo = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_awaited_once()
        self.quester.auto_collect_rewrite.assert_not_awaited()

    async def test_zero_target_without_photo_semantics_stays_with_collector(self):
        self.objective = 'Enter the Next Room in Night Forest'
        self.quester.take_photomancy_photo = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_not_awaited()
        self.quester.auto_collect_rewrite.assert_awaited_once_with(self.client)

    async def test_same_stage_keeps_one_complete_s_zz_round_until_progress(self):
        for _ in range(3):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z])
        self.assertEqual(self.quester._finish_photomancy_photo.await_count, 1)
        self.goal += 1
        self.objective = 'Collect a photo in Night Forest'
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z])
        self.objective = 'Take a Picture of Another Scene in Night Forest'
        self.goal += 1
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z] * 2)

    async def test_goal_change_or_safety_loss_during_backstep_cancels_old_shutter(self):
        for cause in ('goal', 'quest', 'zone', 'objective', 'stop', 'loading', 'battle'):
            with self.subTest(cause=cause):
                self.setUp()
                original = self.client.send_key
                async def changed(key, seconds=0):
                    await original(key, seconds)
                    if key == Keycode.S:
                        if cause == 'goal': self.goal += 1
                        if cause == 'quest': self.client.quest_id.return_value += 1
                        if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                        if cause == 'objective': self.objective = 'Enter the Next Room'
                        if cause == 'stop': self.client.questing_status = False
                        if cause == 'loading': self.client.is_loading.return_value = True
                        if cause == 'battle': self.client.in_battle.return_value = True
                self.client.send_key = changed
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.S])
                self.stack.close()

    async def test_cancel_after_first_z_never_replays_same_photo_round(self):
        original = self.client.send_key
        async def cancel(key, seconds=0):
            await original(key, seconds)
            if key == Keycode.Z: raise asyncio.CancelledError()
        self.client.send_key = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.auto_quest_solo()
        self.client.send_key = original
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z])

    async def test_foreign_input_owner_never_sends_photo_keys(self):
        task = await movement._movement_task_snapshot(self.client)
        async with automation_owner(self.client, 'potion'):
            self.assertFalse(await asyncio.create_task(self.quester._take_ready_photomancy_photo(
                self.client, self.target, task)))
        self.assertEqual(self.keys, [])

    async def test_zero_argument_cannot_bypass_a_nonzero_current_task_target(self):
        self.target = XYZ(1000, 2000, 3)
        task = await movement._movement_task_snapshot(self.client)
        self.assertIsNone(await self.quester._photomancy_interaction_ready(
            self.client, XYZ(0, 0, 0), task))
        self.assertEqual(self.keys, [])

    async def test_nonzero_target_still_needs_correlated_visible_z_prompt(self):
        self.target = XYZ(1000, 2000, 3)
        task = await movement._movement_task_snapshot(self.client)
        self.assertIsNone(await self.quester._photomancy_interaction_ready(self.client, self.target, task))
        visible = AsyncMock(side_effect=lambda _c, p: p == movement.npc_range_path)
        with patch('src.questing.is_visible_by_path', visible):
            for prompt in ('按下<icon;Z>来拍照', 'Press Z to Take a Picture'):
                self.prompt = prompt
                self.assertEqual(await self.quester._photomancy_interaction_ready(
                    self.client, self.target, task), self.objective, self.client._quest_photo_ready_log)

    async def test_direct_generic_caller_also_waits_for_objective_progress(self):
        await self.quester.take_photomancy_photo(self.client, self.objective)
        await self.quester.take_photomancy_photo(self.client, self.objective)
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z])
        self.objective += ' (1/2)'
        await self.quester.take_photomancy_photo(self.client, self.objective)
        self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z] * 2)

    async def test_concurrent_direct_call_cannot_start_a_second_round(self):
        stepped, release = asyncio.Event(), asyncio.Event()
        original = self.client.send_key
        async def send(key, seconds=0):
            await original(key, seconds)
            if key == Keycode.S:
                stepped.set()
                await release.wait()
        self.client.send_key = send
        first = asyncio.create_task(self.quester.take_photomancy_photo(self.client, self.objective))
        try:
            await asyncio.wait_for(stepped.wait(), .5)
            second = asyncio.create_task(self.quester.take_photomancy_photo(self.client, self.objective))
            await asyncio.sleep(0)
            self.assertEqual(self.keys, [Keycode.S])
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(self.keys, [Keycode.S, Keycode.Z, Keycode.Z])
        finally:
            release.set()
            await first
