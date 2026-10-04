import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester, claim_quest_recovery
from src.teleport_math import _teleport_once_verified


class DuelingTentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.DUELING_TENT_ZONE
    owner = 'dueling_tent'
    exit_xyz = (16.146, -1194.045, -4.171)
    failed_attr = '_xuanshu_dueling_tent_failed'
    recovered_attr = '_dueling_tent_recovered_at'

    def setUp(self):
        self.now = 0.
        self.zone = self.source_zone
        self.progress = (123, 1, 'Go to the exit')
        self.client = SimpleNamespace(title='p1', questing_status=True, quest_recovery_owner=None,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(9000, 0, 0))),
            teleport=AsyncMock(), quest_party_hitters=[SimpleNamespace(title='p2')])
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: self.progress)
        self.quester.move_until_quest_interaction = AsyncMock()
        self.on_tick = None
        async def tick(seconds):
            self.now += seconds
            if self.on_tick:
                self.on_tick()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)))
        self.collision = stack.enter_context(patch('src.questing.collision_tp', new=AsyncMock()))
        self.free = stack.enter_context(patch('src.questing.is_free', new=AsyncMock(return_value=True)))
        self.leader_free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.get_quest_name', new=AsyncMock(return_value='Go to the exit')))
        self.log = stack.enter_context(patch('src.questing.logger'))

    async def move(self, *times, quester=None):
        for self.now in times:
            await (quester or self.quester).teleport_to_quest_target(self.client, XYZ(9000, 0, 0))

    def transition(self):
        async def teleport(target):
            self.assertEqual((target.x, target.y, target.z), self.exit_xyz)
            self.assertEqual(self.client.quest_recovery_owner, self.owner)
            self.assertFalse(claim_quest_recovery(self.client, 'trigger_reentry'))
            self.zone = 'Novus/NV_Z01'
        self.client.teleport.side_effect = teleport

    async def test_exact_exit_after_three_failed_tps_and_ten_seconds_then_sync(self):
        self.transition()
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, self.zone)
        self.assertGreaterEqual(getattr(self.client, self.recovered_attr), 11)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        self.client.quest_position.position.assert_awaited_once()

    async def test_two_attempts_then_error_no_normal_or_special_loop_on_worker_recreate(self):
        await self.move(0, 4, 8, 11)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.log.error.assert_called_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        collision_count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        await self.move(40, 50, 60, quester=restarted)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, collision_count)
        self.progress = (123, 2, 'Go to the exit')
        await self.move(70, quester=restarted)
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertEqual(self.collision.await_count, collision_count + 1)

    async def test_other_map_cannot_trigger(self):
        self.zone = 'Novus/Interiors/NV_Z02_DuelingTent'
        await self.move(0, 4, 8, 11, 30)
        self.client.teleport.assert_not_awaited()

    async def test_goal_or_counter_progress_resets_observation(self):
        for progress in ((123, 2, 'Go to the exit'), (123, 1, 'Go to the exit (1 of 2)')):
            self.quester._krok_exit_watch.clear()
            self.progress = (123, 1, 'Go to the exit')
            await self.move(0, 4, 8)
            self.progress = progress
            await self.move(11, 15, 19)
            self.client.teleport.assert_not_awaited()

    async def test_normal_approach_and_arrival_are_not_stall(self):
        for before, after in ((XYZ(0, 0, 0), XYZ(1000, 0, 0)),
                              (XYZ(9000, 0, 0), XYZ(9000, 0, 0))):
            self.client.body.position.side_effect = lambda: before
            async def move(*args, **kwargs):
                self.client.body.position.side_effect = lambda: after
            self.collision.side_effect = move
            for step in (0, 4, 8, 11):
                self.client.body.position.side_effect = lambda: before
                await self.move(step)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_collision_rejection_counts_even_if_body_moves(self):
        self.transition()
        async def rejected(*args, **kwargs):
            self.client._collision_tp_rejections = getattr(self.client, '_collision_tp_rejections', 0) + 1
            self.client.body.position.return_value = XYZ(self.now * 200, 0, 0)
        self.collision.side_effect = rejected
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()

    async def test_loading_battle_dialogue_and_existing_owner_block(self):
        for attr in ('is_loading', 'in_battle'):
            getattr(self.client, attr).return_value = True
            self.free.return_value = False
            await self.move(0, 4, 8, 11)
            getattr(self.client, attr).return_value = False
        self.free.return_value = True
        self.leader_free.return_value = False
        await self.move(0, 4, 8, 11)
        self.leader_free.return_value = True
        self.client.is_in_dialog.return_value = True
        await self.move(0, 4, 8, 11)
        self.client.is_in_dialog.return_value = False
        self.client.quest_recovery_owner = 'dungeon_quest'
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()

    async def test_waiting_ui_resets_stall(self):
        await self.move(0, 4, 8)
        self.visible.return_value = True
        await self.move(11)
        self.visible.return_value = False
        await self.move(12, 16, 20)
        self.client.teleport.assert_not_awaited()

    async def test_loading_wait_has_no_nested_normal_tp_and_readable_new_zone_required(self):
        async def teleport(target):
            self.client.is_loading.return_value = True
            count = self.collision.await_count
            await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
            self.assertEqual(self.collision.await_count, count)
            ready = self.now + 1
            def on_tick():
                if self.now >= ready:
                    self.client.is_loading.return_value = False
                    self.zone = 'Novus/NV_Z01'
            self.on_tick = on_tick
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.client.quest_position.position.assert_awaited_once()
        self.assertTrue(self.client.quest_party_probe_pending)

    async def test_progress_after_first_special_tp_stops_retry(self):
        async def teleport(target):
            self.progress = (123, 2, 'Go to the exit')
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_battle_after_first_special_tp_stops_retry(self):
        async def teleport(target):
            self.client.in_battle.return_value = True
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancelled_special_tp_releases_owner_without_repeating(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 4, 8, 11)
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.move(20, 30)
        self.client.teleport.assert_awaited_once()

    async def test_rejection_counter_uses_existing_arrival_detector(self):
        with patch('src.teleport_math.is_free', new=AsyncMock(return_value=True)), \
             patch('src.teleport_math._arrived', new=AsyncMock(return_value=False)):
            self.assertFalse(await _teleport_once_verified(self.client, XYZ(1, 2, 3), XYZ(0, 0, 0), self.zone))
            self.assertEqual(self.client._collision_tp_rejections, 1)

    async def test_path_timeout_is_blocked_evidence(self):
        self.transition()
        self.collision.side_effect = TimeoutError('navigation timed out')
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()

    async def test_progress_during_normal_tp_prevents_recovery(self):
        await self.move(0, 4, 8)
        async def progress(*args, **kwargs):
            self.progress = (123, 2, 'Go to the exit')
        self.collision.side_effect = progress
        await self.move(11)
        self.client.teleport.assert_not_awaited()

    async def test_same_map_loading_alone_is_not_success(self):
        async def teleport(target):
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        # Bound the post-loading check without a real 35-second delay.
        async def tick(seconds):
            self.now += seconds
            if self.now > 20:
                raise TimeoutError('loading returned without confirmed new zone')
        with patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)):
            await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.client.quest_position.position.assert_not_awaited()
        self.assertIsNotNone(getattr(self.client, self.failed_attr))
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_special_recovery_blocks_hub_and_friend_tp(self):
        self.client.quest_recovery_owner = self.owner
        self.quester.followers_in_correct_zone = AsyncMock()
        await self.quester.zone_recorrect_hub()
        self.quester.followers_in_correct_zone.assert_not_awaited()
        self.assertEqual(await self.quester.friend_teleport(False), ([], None))

