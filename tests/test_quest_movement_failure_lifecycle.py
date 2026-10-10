"""Failed ordinary navmap attempts must yield, recover and rearm locally."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import questing as q
from src.automation_ownership import automation_owner, get_client_automation_ownership

REAL_SLEEP = asyncio.sleep


class MovementFailureLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.target = XYZ(2000, 0, 0)
        self.position = XYZ(0, 0, 0)
        self.zone, self.quest, self.goal, self.progress = 'World/Room', 42, 7, 'Go to Door'
        self.free, self.interaction = True, False
        self.client = SimpleNamespace(title='p1', process_id=1, questing_status=True,
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.quest),
            goal_id=AsyncMock(side_effect=lambda: self.goal),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.runner = q.Quester(self.client, [self.client], None)
        self.runner._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: (self.quest, self.goal, self.progress))
        self.runner.quest_interaction_ready = AsyncMock(side_effect=lambda *args: self.interaction)
        self.runner._note_quest_x_blocked = AsyncMock()
        self.runner._note_quest_x_lock_wait = AsyncMock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(q, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(side_effect=lambda _: self.free)))
        async def tick(_):
            await REAL_SLEEP(0)
        stack.enter_context(patch.object(q.asyncio, 'sleep', tick))
        stack.enter_context(patch.object(q, 'logger'))
        self.nav = stack.enter_context(patch.object(q, 'navmap_tp', AsyncMock()))

    async def move(self):
        result = await self.runner.move_until_quest_interaction(self.client, self.target)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertTrue(self.client.questing_status)
        return result

    async def test_all_reported_partial_landings_have_finite_retry_then_real_recovery(self):
        for gap in (606.1, 1826.0, 418.5, 1386.2, 855.4):
            with self.subTest(remaining=gap):
                self.runner._quest_movement_waiting.clear()
                self.nav.reset_mock()
                self.client.teleport.reset_mock()
                self.position = XYZ(self.target.x - gap - 5, 0, 0)
                async def partial(*args):
                    self.position = XYZ(self.target.x - gap, 0, 0)
                self.nav.side_effect = partial
                for when, expected_calls in ((0, 1), (1, 1), (4, 1), (5, 2),
                                              (6, 2), (10, 3), (20, 3), (39, 3)):
                    self.now = when
                    self.assertFalse(await self.move())
                    self.assertEqual(self.nav.await_count, expected_calls)
                self.now = 40
                self.assertFalse(await self.move())
                self.client.teleport.assert_awaited_once()
                point = self.client.teleport.await_args.args[0]
                self.assertEqual((point.x, point.y, point.z), (self.position.x + 500, 0, -1500))
                self.assertEqual(self.nav.await_count, 4)
                record = self.runner._quest_movement_waiting[id(self.client)]
                self.assertEqual(record['attempts'], 1)
                self.assertAlmostEqual(record['remaining'], gap)

    async def test_position_and_remaining_progress_refresh_old_record_during_cooldown(self):
        self.position = XYZ(self.target.x - 1826, 0, 0)
        await self.move()
        old = self.runner._quest_movement_waiting[id(self.client)]
        self.position = XYZ(self.target.x - 418, 0, 0)
        self.now = 1
        await self.move()
        new = self.runner._quest_movement_waiting[id(self.client)]
        self.assertIsNot(old, new)
        self.assertEqual(new['attempts'], 1)
        self.assertEqual(new['remaining'], 418)
        self.assertEqual(self.nav.await_count, 2)

    async def test_significant_navmap_progress_refreshes_retry_budget(self):
        await self.move()
        self.now = 5
        async def improve(*args):
            self.position = XYZ(500, 0, 0)
        self.nav.side_effect = improve
        await self.move()
        self.assertEqual(self.runner._quest_movement_waiting[id(self.client)]['attempts'], 1)

    async def test_context_changes_release_suppression_without_touching_other_state(self):
        for event in ('quest', 'goal', 'zone', 'target', 'progress', 'group', 'quester'):
            with self.subTest(event=event):
                self.setUp()
                await self.move()
                if event == 'quest': self.quest += 1
                elif event == 'goal': self.goal += 1
                elif event == 'zone': self.zone = 'World/Next'
                elif event == 'target': self.target = XYZ(3000, 0, 0)
                elif event == 'progress': self.progress += ' 1/2'
                elif event == 'group': self.client.quest_party_hitters = [object()]
                else: self.client.quest_party_quester = object()
                await self.move()
                self.assertEqual(self.nav.await_count, 2)

    async def test_valid_x_preempts_retry_and_clears_failure(self):
        await self.move()
        self.interaction = True
        self.assertTrue(await self.move())
        self.assertEqual(self.nav.await_count, 1)
        self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_potion_or_probe_pause_preserves_movement_budget(self):
        for event in ('potion', 'probe'):
            self.runner._quest_movement_waiting.clear()
            self.client.refilling_potions = False
            self.client.quest_party_hitters = [object()]
            self.client.quest_party_probe_pending = False
            await self.move()
            old = self.runner._quest_movement_waiting[id(self.client)]
            if event == 'potion': self.client.refilling_potions = True
            else: self.client.quest_party_probe_pending = True
            self.assertFalse(await self.move())
            self.assertIs(self.runner._quest_movement_waiting[id(self.client)], old)

    async def test_recovery_exception_retains_thirty_second_cooldown(self):
        for self.now in (0, 5, 10):
            await self.move()
        self.now = 40
        self.client.teleport.side_effect = RuntimeError('memory read unavailable')
        await self.move()
        self.now = 41
        await self.move()
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.nav.await_count, 3)

    async def test_cancellation_during_recovery_drains_only_its_input_owner(self):
        for self.now in (0, 5, 10):
            await self.move()
        self.now = 40
        started, drained = asyncio.Event(), asyncio.Event()
        async def recover(*args):
            started.set()
            try: await asyncio.Future()
            finally: drained.set()
        self.client.teleport.side_effect = recover
        task = asyncio.create_task(self.move())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(drained.is_set())
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_other_group_owner_and_failure_record_are_untouched(self):
        peer = SimpleNamespace(title='p3')
        sentinel = {'attempts': 2}
        self.runner._quest_movement_waiting[id(peer)] = sentinel
        async with automation_owner(peer, 'peer-movement'):
            await self.move()
            self.assertTrue(get_client_automation_ownership(peer).locked)
            self.assertIs(self.runner._quest_movement_waiting[id(peer)], sentinel)

    async def test_stop_during_cooldown_never_sends_another_input(self):
        await self.move()
        self.client.questing_status = False
        self.assertFalse(await self.runner.move_until_quest_interaction(self.client, self.target))
        self.assertEqual(self.runner._quest_movement_waiting, {})
        self.nav.assert_awaited_once()
        self.client.teleport.assert_not_awaited()
