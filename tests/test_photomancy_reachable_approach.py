"""A model centre is not a photo standing point; keep real action evidence."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ, Keycode
from src import teleport_math as movement
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.questing import Quester
from tests.test_photomancy_movement_handoff import PhotoHandoffTests as PhotoBase
from tests.test_party_dungeon_entry_sync import EntrySyncTests as DungeonBase


class ReachablePhotoTests(PhotoBase):
    async def failed_approach(self):
        # Exercise the real movement/failure path, then expose the current action.
        with patch('src.teleport_math.navmap_tp', AsyncMock()):
            await self.quester.auto_quest_solo()
        self.assertIsNotNone(self.client._quest_tp_rejected)
        self.photo_visible = True
        return await movement._movement_task_snapshot(self.client)

    async def test_attempted_tp_does_not_require_confirmed_model_centre_landing(self):
        task = await self.failed_approach()
        self.client._quest_target_first_move.update(pending=True, active=True)
        if isinstance(getattr(self.client, '_quest_tp_landing', None), dict):
            self.client._quest_tp_landing['confirmed'] = False
        self.assertEqual(await self.quester._photomancy_interaction_ready(
            self.client, self.target, task), self.objective)

    async def test_exhausted_pending_move_still_checks_current_photo_action(self):
        task = await self.failed_approach()
        self.client._quest_target_first_move['pending'] = True
        await self.quester._defer_internal_quest_failure(self.client, 'fixture exhausted',
            kind='exhausted', delay=10, task=task, target=self.target)
        walks = self.client._quest_tp_rejected['walks']
        before = list(self.movement_events)
        await self.quester.auto_quest_solo()
        self.assertIn(Keycode.Z, self.keys)
        self.assertNotIn(Keycode.X, self.keys)
        self.assertEqual(self.movement_events, before)
        self.assertEqual(self.client._quest_tp_rejected['walks'], walks)
        self.assertIsNotNone(self.client._quest_internal_failure_wait)  # Input is not completion.

    async def test_shared_target_owner_can_hand_off_to_photo_in_its_own_task(self):
        task = await self.failed_approach()
        async with automation_owner(self.client, 'dungeon-shared-target'):
            self.assertTrue(await self.quester._take_ready_photomancy_photo(self.client, self.target, task))
            self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'dungeon-shared-target')
        self.assertIn(Keycode.Z, self.keys)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_foreign_shared_target_owner_does_not_grant_photo_input(self):
        task = await self.failed_approach()
        async with automation_owner(self.client, 'dungeon-shared-target'):
            self.assertFalse(await asyncio.create_task(self.quester._take_ready_photomancy_photo(
                self.client, self.target, task)))
        self.assertNotIn(Keycode.Z, self.keys)

    async def test_repeated_photo_rejection_reports_specific_reason_once(self):
        from loguru import logger
        task = await self.failed_approach()
        self.photo_visible = False
        self.client._quest_photo_ready_log = None  # Start observing this rejection series.
        messages = []
        sink = logger.add(lambda m: messages.append(str(m)), level='DEBUG')
        try:
            for _ in range(3):
                self.assertIsNone(await self.quester._photomancy_interaction_ready(self.client, self.target, task))
        finally:
            logger.remove(sink)
        rejected = [m for m in messages if '[photo_ready]' in m and 'prompt-not-ready' in m]
        self.assertEqual(len(rejected), 1)
        self.assertNotIn(Keycode.Z, self.keys)

    async def test_model_height_does_not_qualify_current_z_action(self):
        self.target = XYZ(1057.792, -203.634, 162.266)
        self.safe = XYZ(911.890, -175.498, 1.435)
        with patch('src.collision_math.find_walkable_teleport_point', return_value=(self.safe, 'fixture')):
            task = await self.failed_approach()
        self.position = self.safe
        self.assertGreater(movement.calc_Distance(self.position, self.target), 200)
        self.assertTrue(await self.quester._take_ready_photomancy_photo(self.client, self.target, task))
        self.assertIn(Keycode.Z, self.keys)


class DungeonPhotoTests(DungeonBase):
    async def test_hitters_photo_ready_is_dispatched_in_existing_shared_worker(self):
        await self.enter()
        reader = Quester(self.h, [self.h], None)
        reader.read_quest_txt = AsyncMock(return_value='Photomance a Photo of Tree')
        reader.read_popup = AsyncMock(return_value='Press Z to Photomance')
        self.q.title_text = self.h.title_text = 'Tree'
        reader.teleport_to_quest_target = AsyncMock(return_value='photo-ready')
        actions = []
        async def photo(client, objective, *, guard):
            self.assertIs(client, self.h)
            self.assertTrue(await guard())
            actions.append((client.title, objective))
        reader.take_photomancy_photo = photo
        await reader.move_dungeon_member()
        self.assertEqual(actions, [('p2', 'Photomance a Photo of Tree')])
        self.assertFalse(get_client_automation_ownership(self.h).locked)
        self.h.send_key.assert_not_awaited()


del PhotoBase, DungeonBase


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(name) for cls in (ReachablePhotoTests, DungeonPhotoTests)
        for name in cls.__dict__ if name.startswith('test_'))
