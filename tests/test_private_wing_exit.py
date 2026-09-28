import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ

from src.questing import Quester, claim_quest_recovery


class PrivateWingExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.zone = Quester.PRIVATE_WING_ZONE
        self.now = 0.0
        self.progress = (123, 1, 'Find the exit')
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(9000, 0, 0))),
            teleport=AsyncMock(),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester.PRIVATE_WING_STABLE_SECONDS = 0
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: self.progress)
        self.quester.move_until_quest_interaction = AsyncMock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        self.collision = stack.enter_context(patch('src.questing.collision_tp', new=AsyncMock()))
        self.free = stack.enter_context(patch('src.questing.is_free', new=AsyncMock(return_value=True)))
        self.zone_stable = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.get_quest_name', new=AsyncMock(return_value='Find the exit')))
        self.zone_wait = stack.enter_context(patch('src.questing.wait_for_zone_change', new=AsyncMock()))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock()))

    async def move(self, *times):
        for self.now in times:
            await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))

    async def test_stall_uses_exact_exit_and_waits_for_stable_new_zone(self):
        async def teleport(target):
            self.assertEqual(self.client.quest_recovery_owner, 'private_wing')
            self.assertFalse(claim_quest_recovery(self.client, 'dungeon_quest'))
            self.assertEqual((target.x, target.y, target.z), (20.521, 11529.802, 2.052))
            self.zone = 'Empyrea/Interiors/NextZone'
        self.client.teleport.side_effect = teleport
        self.zone_stable.side_effect = [False, True, True]
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.zone_wait.assert_awaited_once_with(self.client, current_zone=Quester.PRIVATE_WING_ZONE)
        self.client.quest_position.position.assert_awaited()
        self.assertEqual(self.zone_stable.await_count, 3)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(self.client._xuanshu_private_wing_failed)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_normal_zone_change_during_task_move_never_uses_exit(self):
        async def normal_transition(*_args, **_kwargs):
            self.zone = 'Empyrea/Interiors/NextZone'
        self.collision.side_effect = normal_transition
        await self.move(0)
        self.client.teleport.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_quest_progress_resets_existing_stall_observation(self):
        await self.move(0, 4)
        self.progress = (123, 2, 'Find the exit')
        await self.move(8, 11, 15)
        self.client.teleport.assert_not_awaited()

    async def test_failure_releases_lock_and_never_repeats_exit_tp(self):
        self.zone_wait.side_effect = TimeoutError('no zone change')
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.move(15, 20, 25)
        self.client.teleport.assert_awaited_once()
        self.assertTrue(self.quester._krok_exit_watch[id(self.client)]['end_sent'])
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: self.progress)
        for self.now in (30, 35, 40, 45):
            await restarted.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.client.teleport.assert_awaited_once()
        self.progress = (123, 2, 'Find the exit')
        self.now = 50
        await restarted.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.assertIsNone(self.client._xuanshu_private_wing_failed)

    async def test_not_running_busy_or_other_zone_never_uses_exit(self):
        self.client.questing_status = False
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.client.questing_status = True
        self.client.quest_recovery_owner = 'dungeon_quest'
        await self.move(15)
        self.client.teleport.assert_not_awaited()
        self.client.quest_recovery_owner = None
        self.client.is_loading.return_value = True
        await self.move(20)
        self.client.teleport.assert_not_awaited()
        self.client.is_loading.return_value = False
        self.zone = 'Empyrea/Interiors/OtherZone'
        await self.move(25)
        self.client.teleport.assert_not_awaited()
