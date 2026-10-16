"""Only a current visible Z interaction may hand failed movement to the original photo action."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import Keycode
from src import teleport_math as movement
from src.paths import npc_range_path
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests.test_walk_goto_regression import TaskOperationTests as Base


class PhotoHandoffTests(Base):
    def setUp(self):
        super().setUp()
        self.objective = 'Photomance a Photo of Tree'
        self.title = 'Tree'
        self.prompt = 'Press Z to Photomance'
        self.goto_moves = False
        self.photo_visible = False
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(
            side_effect=lambda _: self.objective)))
        self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: path == npc_range_path and self.photo_visible)))
        self.quester._finish_photomancy_photo = AsyncMock()

    async def test_stalled_waypoint_with_live_z_prompt_calls_original_photo_without_x(self):
        original = self.client.goto
        async def goto(x, y):
            await original(x, y)
            self.photo_visible = True
        self.client.goto = goto
        self.quester.take_photomancy_photo = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_awaited_once()
        self.assertIn('tp', [kind for kind, _ in self.movement_events])
        self.assertIn('walk', [kind for kind, _ in self.movement_events])
        self.assertNotIn(Keycode.X, self.keys)

    async def test_hud_z_text_without_visible_photo_interaction_does_not_bypass_failure(self):
        self.objective = 'Press Z to Photomance a Photo of Tree'
        self.quester.take_photomancy_photo = AsyncMock()
        with patch('src.teleport_math.navmap_tp', AsyncMock()):
            await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_not_awaited()
        self.assertIsNotNone(self.client._quest_internal_failure_wait)

    async def test_unrelated_title_and_bare_photo_action_are_not_ready(self):
        for title, prompt in (('Other Machine', 'Press Z to Photomance'),
                              ('Tree', 'Photomance a Photo of')):
            with self.subTest(title=title,prompt=prompt):
                self.setUp()
                self.photo_visible = True
                self.title, self.prompt = title, prompt
                self.quester.take_photomancy_photo = AsyncMock()
                # The ordinary interaction gate is independent of the strict photo gate.
                with patch.object(self.quester, 'quest_interaction_ready', AsyncMock(return_value=False)), \
                     patch('src.teleport_math.navmap_tp', AsyncMock()):
                    await self.quester.auto_quest_solo()
                self.quester.take_photomancy_photo.assert_not_awaited()

    async def test_photo_ready_during_cooldown_keeps_existing_walk_budget(self):
        with patch('src.teleport_math.navmap_tp', AsyncMock()):
            await self.quester.auto_quest_solo()
            failure = self.client._quest_tp_rejected
            walks = failure['walks']
            self.photo_visible = True
            self.quester.take_photomancy_photo = AsyncMock()
            await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_awaited_once()
        self.assertEqual(failure['walks'], walks)
        self.assertIs(self.client._quest_tp_rejected, failure)  # An input handoff did not advance the quest.

    async def test_task_change_after_photo_backstep_stops_old_shutter(self):
        self.photo_visible = True
        original = self.client.send_key
        async def changed(key, seconds=0):
            await original(key, seconds)
            if key == Keycode.S: self.goal += 1
        self.client.send_key = changed
        await self.quester.auto_quest_solo()
        self.assertIn(Keycode.S, self.keys)
        self.assertNotIn(Keycode.Z, self.keys)

    async def test_busy_owner_does_not_start_photo(self):
        self.photo_visible = True
        self.quester.take_photomancy_photo = AsyncMock()
        async with automation_owner(self.client, 'potion'):
            await self.quester.auto_quest_solo()
        self.quester.take_photomancy_photo.assert_not_awaited()

    async def test_control_change_after_backstep_stops_old_shutter(self):
        self.photo_visible = True
        original = self.client.send_key
        changed = self.stack.enter_context(patch('src.questing._movement_control_changed',
            AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.teleport_math._movement_control_changed', changed))
        async def send(key, seconds=0):
            await original(key, seconds)
            if key == Keycode.S: changed.return_value = True
        self.client.send_key = send
        await self.quester.auto_quest_solo()
        self.assertIn(Keycode.S, self.keys)
        self.assertNotIn(Keycode.Z, self.keys)

    async def test_cancel_photo_releases_its_input_owner(self):
        self.photo_visible = True
        original = self.client.send_key
        async def send(key, seconds=0):
            if key == Keycode.S: raise asyncio.CancelledError
            await original(key, seconds)
        self.client.send_key = send
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.auto_quest_solo()
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertNotIn(Keycode.Z, self.keys)

    async def test_real_task_advancement_clears_only_matching_failed_move(self):
        with patch('src.teleport_math.navmap_tp', AsyncMock()):
            await self.quester.auto_quest_solo()
            self.assertIsNotNone(self.client._quest_tp_rejected)
            self.photo_visible = True
            self.quester.take_photomancy_photo = AsyncMock(side_effect=lambda *_args, **_kw:
                setattr(self, 'goal', self.goal + 1))
            await self.quester.auto_quest_solo()
        self.assertIsNone(self.client._quest_tp_rejected)
        self.assertIsNone(self.client._quest_internal_failure_wait)
        self.assertEqual(self.client._quest_internal_block_reason, '')

    async def test_new_target_still_attempts_tp_before_a_nearby_photo_prompt(self):
        task = await movement._movement_task_snapshot(self.client)
        self.client._quest_target_first_move = dict(task=task, target=self.target,
            shared=None, pending=True, active=False)
        self.photo_visible = True
        self.quester.take_photomancy_photo = AsyncMock()
        await self.quester.auto_quest_solo()
        self.assertIn('tp', [kind for kind, _ in self.movement_events])
        self.quester.take_photomancy_photo.assert_awaited_once()

    async def test_loading_and_ui_read_failure_never_grant_photo(self):
        self.photo_visible = True
        task = await movement._movement_task_snapshot(self.client)
        self.client.is_loading.return_value = True
        self.assertIsNone(await self.quester._photomancy_interaction_ready(self.client, self.target, task))
        self.client.is_loading.return_value = False
        self.quester.read_popup.side_effect = ValueError('UI unavailable')
        self.assertIsNone(await self.quester._photomancy_interaction_ready(self.client, self.target, task))


del Base


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PhotoHandoffTests(name) for name in PhotoHandoffTests.__dict__
        if name.startswith('test_'))
