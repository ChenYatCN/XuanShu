"""Internal failures defer one client's work without changing the user's enable flag."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ, MemoryReadError
from wizwalker.errors import MemoryInvalidated, ExceptionalTimeout
from src.questing import Quester
from src import teleport_math as movement
from src.paths import npc_range_path
import tests.test_stale_interaction_movement as prompts
import tests.test_movement_failure_handoff as failures
from tests.test_scoped_runtime import load_function

REAL_SLEEP = asyncio.sleep


class FailureWaitTests(prompts.Fixture):
    failure = failures.FailedMovementTests.failure

    async def exhaust(self):
        self.nearby(216)
        state = await self.failure(3)
        with patch('src.questing.collision_tp', AsyncMock(return_value='exhausted')) as tp:
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'exhausted')
        return state, tp

    async def test_exhaustion_keeps_task_enabled_and_old_origin_x_blocked(self):
        await self.exhaust()
        self.assertTrue(self.client.questing_status)
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertFalse(self.keys)

    async def test_cooldown_prevents_repeated_movement_and_retains_exhausted_budget(self):
        state, _ = await self.exhaust()
        with patch('src.questing.collision_tp', AsyncMock(return_value='failed')) as tp:
            for _ in range(4):
                self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'blocked')
            tp.assert_not_awaited()
            self.clock += 10
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'blocked')
            tp.assert_not_awaited()
        self.assertIs(self.client._quest_tp_rejected, state)
        self.assertEqual(state['walks'], 3)
        self.assertFalse(state['ready'])
        self.assertTrue(self.client.questing_status)

    async def test_new_goal_bypasses_old_cooldown_without_reenabling_user_stop(self):
        await self.exhaust()
        self.goal += 1
        self.position = self.target
        self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'ready')
        self.assertTrue(self.client.questing_status)
        self.client.questing_status = False
        self.goal += 1
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'blocked')
            tp.assert_not_awaited()
        self.assertFalse(self.client.questing_status)

    async def test_unreadable_snapshot_during_wait_does_not_release_old_failure(self):
        await self.exhaust()
        self.client.quest_id.side_effect = MemoryReadError(998)
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            for _ in range(3):
                self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'blocked')
            tp.assert_not_awaited()
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.keys)

    async def test_wait_is_client_local_and_cancellable_without_input_or_reenable(self):
        await self.exhaust()
        other = SimpleNamespace(**vars(self.client))
        other._quest_internal_failure_wait = None
        other._quest_internal_block_reason = ''
        other._quest_tp_rejected = None
        self.assertEqual(await self.quester.move_until_quest_interaction(other, self.target), 'ready')
        entered = asyncio.Event()
        async def waiting(delay):
            entered.set()
            await asyncio.Event().wait()
        with patch('src.questing.asyncio.sleep', waiting):
            task = asyncio.create_task(self.quester.move_until_quest_interaction(self.client, self.target))
            await asyncio.wait_for(entered.wait(), 1)
            self.client.questing_status = False
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.client.questing_status)
        self.assertFalse(self.held)
        self.assertFalse(self.keys)

    async def test_recoverable_worker_reads_retry_in_same_worker_and_quester(self):
        self.nearby(216)
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=50))
        self.client.process_id = 1
        calls = []
        async def run(*_args, **_kwargs):
            calls.append(1)
            self.assertTrue(self.client.questing_status)
            if len(calls) < 4:
                raise (MemoryReadError(998), MemoryInvalidated('stale UI'),
                       ExceptionalTimeout('UI refresh timeout'))[len(calls)-1]
            self.client.questing_status = False  # User stop after recovery succeeds.
        self.quester.auto_quest = AsyncMock(side_effect=run)
        factory = Mock(return_value=self.quester)
        namespace = dict(Client=object, Quester=factory, asyncio=asyncio,
            walker=SimpleNamespace(clients=[self.client]), members=[self.client], roster=[self.client],
            quest_party_enabled=True, questing_status=True, questing_leader_pid=None,
            ignore_pet_level_up=False, only_play_dance_game=False,
            logger=Mock(), wizwalker=__import__('wizwalker'), cancel_mainline_recovery=Mock())
        worker = load_function('run_questing_worker', namespace)
        await worker(self.client)
        self.assertEqual(len(calls), 4)
        self.assertGreaterEqual(self.clock, 15)
        factory.assert_called_once()
        self.assertFalse(self.client.questing_status)

    async def test_initial_level_read_failure_does_not_close_or_duplicate_worker(self):
        self.nearby(216)
        self.client.process_id = 1
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(side_effect=[MemoryReadError(998), 50]))
        async def run(*_args, **_kwargs):
            self.assertTrue(self.client.questing_status)
            self.client.questing_status = False
        self.quester.auto_quest = AsyncMock(side_effect=run)
        factory = Mock(return_value=self.quester)
        namespace = dict(Client=object, Quester=factory, asyncio=asyncio,
            walker=SimpleNamespace(clients=[self.client]), members=[self.client], roster=[self.client],
            quest_party_enabled=True, questing_status=True, questing_leader_pid=None,
            ignore_pet_level_up=False, only_play_dance_game=False,
            logger=Mock(), wizwalker=__import__('wizwalker'), cancel_mainline_recovery=Mock())
        worker = load_function('run_questing_worker', namespace)
        await worker(self.client)
        self.client.stats.reference_level.assert_awaited()
        self.assertEqual(self.client.stats.reference_level.await_count, 2)
        factory.assert_called_once()
        self.quester.auto_quest.assert_awaited_once()

    async def test_shared_target_budget_uses_assigned_xyz_instead_of_hitters_own_arrow(self):
        self.nearby(216)
        self.client.quest_position.position = AsyncMock(return_value=XYZ(700, 800, 0))
        state = await self.failure(3)
        with patch('src.questing.collision_tp', AsyncMock(return_value='exhausted')):
            self.assertEqual(await self.quester.move_until_quest_interaction(
                self.client, self.target, leader_client=self.client), 'exhausted')
        self.clock += 10
        with patch('src.questing.collision_tp', AsyncMock(return_value='failed')) as tp:
            self.assertEqual(await self.quester.move_until_quest_interaction(
                self.client, self.target, leader_client=self.client), 'blocked')
            tp.assert_not_awaited()
        self.assertEqual(state['walks'], 3)
        self.assertTrue(self.client.questing_status)

    async def test_stable_departure_releases_cooldown_and_original_popup_guard(self):
        state, _ = await self.exhaust()
        self.position = XYZ(self.target.x-100, self.target.y, self.target.z)
        self.assertFalse(await self.quester._wait_internal_quest_failure(self.client))
        self.assertIs(self.client._quest_tp_rejected, state)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_dialogue_and_control_wait_do_not_discard_cooldown_or_send_input(self):
        await self.exhaust()
        state = self.client._quest_internal_failure_wait
        self.dialogue = True
        self.goal += 1  # A dialogue-transient update must not revoke the saved wait.
        self.assertFalse(await self.quester._wait_internal_quest_failure(self.client))
        self.assertIs(self.client._quest_internal_failure_wait, state)
        self.dialogue = False
        self.client.quest_recovery_owner = 'another-owner'
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'blocked')
            tp.assert_not_awaited()
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.keys)


class RoleAndDisplayTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_quester_does_not_disable_existing_client_or_other_group(self):
        first = SimpleNamespace(title='p1', questing_status=True)
        other = SimpleNamespace(title='p3', questing_status=True)
        party = SimpleNamespace(questers=[], hitters=[], hitter_assignments=[])
        apply = load_function('apply_questing_roles', dict(
            current_quest_party=Mock(return_value=party), walker=SimpleNamespace(clients=[first, other]),
            mainline_finder_enabled=False))
        self.assertIs(apply(True, [first]), party)
        self.assertTrue(first.questing_status)
        self.assertTrue(other.questing_status)

    async def test_missing_role_wait_keeps_group_enabled_until_user_cancels(self):
        client = SimpleNamespace(title='p1', questing_status=True)
        party = SimpleNamespace(questers=[], hitters=[], hitter_assignments=[])
        entered = asyncio.Event()
        async def wait(delay):
            self.assertEqual(delay, 5)
            entered.set()
            await asyncio.Event().wait()
        namespace = dict(questing_status=True, walker=SimpleNamespace(clients=[client]),
            apply_questing_roles=Mock(return_value=party), logger=Mock(),
            asyncio=SimpleNamespace(sleep=wait))
        loop = load_function('questing_loop', namespace)
        task = asyncio.create_task(loop([client]))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(client.questing_status)
        self.assertTrue(namespace['questing_status'])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    def test_status_shows_actual_blocked_group_without_marking_it_disabled(self):
        first = SimpleNamespace(title='p1', questing_status=True, quest_party_hitters=[],
            _xuanshu_mainline_progress='p1 · Empyrea 30', _quest_internal_block_reason='3次补走未成功')
        other = SimpleNamespace(title='p3', questing_status=True, quest_party_hitters=[],
            _xuanshu_mainline_progress='p3 · Mirage 20')
        status = load_function('quest_status_text', dict(current_quest_party=Mock(
            return_value=SimpleNamespace(questers=[first, other]))))
        result = status([first, other], {'toggle_questing': {'p1': True, 'p3': True}})
        self.assertIn('p1 · 等待恢复：3次补走未成功（自动任务保持启用）', result)
        self.assertIn('p3 · Mirage 20', result)
        self.assertEqual(result.count('等待恢复'), 1)
