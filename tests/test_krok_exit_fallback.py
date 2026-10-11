import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ, Keycode
from src.questing import Quester


class KrokExitFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.title = 'p1'
        self.client.zone_name.return_value = 'Krokotopia/KT_WorldTeleporter'
        self.client.is_loading.return_value = False
        self.client.body.position.return_value = XYZ(0, 0, 0)
        self.target = XYZ(2000, 0, 0)
        self.quester = Quester(self.client, [self.client], None)
        self.now = 0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.move = self.stack.enter_context(patch('src.questing.collision_tp', new=AsyncMock()))
        self.gate = self.stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = self.stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.is_free', new=AsyncMock(return_value=True)))
        self.quest = self.stack.enter_context(patch('src.questing.get_quest_name', new=AsyncMock(return_value='Go outside')))
        self.stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        async def home(*args):
            self.client.zone_name.return_value = 'Krokotopia/KT_Hub'
        self.client.send_key.side_effect = home

    async def attempt(self, now):
        self.now = now
        await self.quester.teleport_to_quest_target(self.client, self.target)

    async def test_entering_room_uses_normal_tp(self):
        await self.attempt(0)
        self.move.assert_awaited_once_with(self.client, self.target, leader_client=None)
        self.client.send_key.assert_not_awaited()

    async def test_three_stalled_attempts_over_ten_seconds_trigger_end(self):
        await self.attempt(0)
        await self.attempt(5)
        self.client.send_key.assert_not_awaited()
        await self.attempt(11)
        self.client.send_key.assert_awaited_once_with(Keycode.END, .1)

    async def test_long_single_attempt_is_not_enough(self):
        async def slow(*args, **kwargs): self.now = 30
        self.move.side_effect = slow
        await self.attempt(0)
        self.client.send_key.assert_not_awaited()

    async def test_attempt_count_alone_is_not_enough(self):
        for now in (0, 1, 2, 3): await self.attempt(now)
        self.client.send_key.assert_not_awaited()

    async def test_visible_world_gate_resets_stall_and_never_uses_end(self):
        await self.attempt(0)
        await self.attempt(5)
        self.gate.return_value = True
        await self.attempt(11)
        self.client.send_key.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        self.assertEqual(self.move.await_count, 2)

    async def test_nearby_interaction_is_not_replaced_with_end(self):
        self.target = XYZ(100, 0, 0)
        self.visible.return_value = True
        for now in (0, 5, 11): await self.attempt(now)
        self.client.send_key.assert_not_awaited()

    async def test_loading_observed_inside_tp_resets_stall(self):
        await self.attempt(0)
        await self.attempt(5)
        async def transition(*args, **kwargs):
            self.client.is_loading.return_value = True
            await asyncio.sleep(.01)
            self.client.is_loading.return_value = False
        self.move.side_effect = transition
        await self.attempt(11)
        self.client.send_key.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_movement_and_quest_change_reset_stall(self):
        await self.attempt(0)
        await self.attempt(5)
        self.client.body.position.return_value = XYZ(200, 0, 0)
        await self.attempt(11)
        self.assertEqual(self.quester._krok_exit_watch[id(self.client)]['attempts'], 0)
        self.quest.return_value = 'Use world gate'
        await self.attempt(25)
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.quester._krok_exit_watch[id(self.client)]['attempts'], 1)

    async def test_other_zone_uses_normal_tp(self):
        self.client.zone_name.return_value = 'MooShu/MS_Hub'
        await self.attempt(0)
        self.move.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_end_timeout_does_not_repeat_for_same_stall(self):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def expired(_seconds):
            raise TimeoutError()
            yield
        self.client.send_key.side_effect = None
        await self.attempt(0)
        await self.attempt(5)
        with patch('src.questing.asyncio.timeout', expired):
            await self.attempt(11)
        await self.attempt(30)
        self.client.send_key.assert_awaited_once_with(Keycode.END, .1)

    async def test_cancellation_clears_observation_state(self):
        self.move.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.attempt(0)
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        self.client.send_key.assert_not_awaited()
