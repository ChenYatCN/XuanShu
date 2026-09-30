import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester, claim_quest_recovery


class TamarinHouseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.zone = Quester.TAMARIN_HOUSE_ZONE
        self.text = '<center>寻找 金狮 地点：废料堆</center>'
        self.identity = (42, 7)
        self.events = []
        self.client = SimpleNamespace(title='p1', questing_status=True,
            quest_recovery_owner=None, zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(800, 1, 0))),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.text)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: (
            (*self.identity, self.text) if self.identity else None))
        self.quester.move_until_quest_interaction = AsyncMock()
        async def teleport(point):
            self.assertEqual(self.client.quest_recovery_owner, 'tamarin_house')
            self.assertFalse(claim_quest_recovery(self.client, 'mainline_finder'))
            self.events.append(((point.x, point.y, point.z), self.now))
            if point == self.quester.TAMARIN_HOUSE_EXIT:
                self.zone = 'Lemuria/Interiors/NewRoom'
            else:
                self.client.body.position.return_value = point
        self.client.teleport.side_effect = teleport
        async def tick(seconds):
            self.now += seconds
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock(side_effect=tick)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        self.wait = stack.enter_context(patch('src.questing.wait_for_zone_change', AsyncMock()))
        self.collision = stack.enter_context(patch('src.questing.collision_tp', AsyncMock()))

    async def run_flow(self):
        return await self.quester._maybe_handle_tamarin_house(self.client)

    def test_exact_stage_match_not_substrings_or_next_stage(self):
        for text in (self.text, 'Find the Golden Lion in Heap'):
            self.assertTrue(self.quester._tamarin_house_task_matches(text))
        for text in ('与 金狮 对话 地点：废料堆', '寻找 金狮 地点：天空城',
                     '金狮', 'Defeat the Golden Lion in Heap', ''):
            self.assertFalse(self.quester._tamarin_house_task_matches(text))

    async def test_order_coordinates_and_stable_intervals(self):
        self.assertTrue(await self.run_flow())
        self.assertEqual([event[0] for event in self.events], [
            (-1371.641, 1631.124, 1.0), (9.903, -424.032, 1.0)])
        self.assertGreaterEqual(self.events[1][1] - self.events[0][1], 1.5)
        self.assertGreaterEqual(self.now - self.events[1][1], 1.5)
        self.wait.assert_awaited_once_with(self.client, current_zone=Quester.TAMARIN_HOUSE_ZONE)
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'completed')
        self.assertFalse(self.client._xuanshu_tamarin_house_stage['active'])
        self.assertIsNone(self.client.quest_recovery_owner)
        self.client.quest_position.position.assert_awaited_once()

    async def test_loading_and_nested_normal_tp_cannot_run_second_stage_early(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            if self.client.teleport.await_count == 1:
                self.client.is_loading.return_value = True
        async def wait(*args, **kwargs):
            self.assertEqual(self.client.teleport.await_count, 1)
            await self.quester.teleport_to_quest_target(self.client, XYZ(800, 1, 0))
            self.collision.assert_not_awaited()
            self.client.is_loading.return_value = False
        self.client.teleport.side_effect = teleport
        self.wait.side_effect = wait
        await self.run_flow()
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_wrong_zone_or_task_or_stopped_never_triggers(self):
        self.zone = 'Lemuria/LM_Z07_Heap'
        self.assertFalse(await self.run_flow())
        self.zone = Quester.TAMARIN_HOUSE_ZONE
        self.text = '与 金狮 对话 地点：废料堆'
        self.assertFalse(await self.run_flow())
        self.text = '寻找 金狮 地点：废料堆'
        self.client.questing_status = False
        self.assertFalse(await self.run_flow())
        self.client.teleport.assert_not_awaited()

    async def test_other_recovery_and_busy_states_defer_without_claim(self):
        self.client.quest_recovery_owner = 'dungeon_quest'
        self.assertTrue(await self.run_flow())
        self.assertEqual(self.client.quest_recovery_owner, 'dungeon_quest')
        self.client.quest_recovery_owner = None
        self.free.return_value = False
        self.assertTrue(await self.run_flow())
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(hasattr(self.client, '_xuanshu_tamarin_house_stage'))

    async def test_hitter_does_not_run_two_stage_tp(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.run_flow())
        self.client.teleport.assert_not_awaited()

    async def test_completed_stage_is_not_repeated_even_with_recreated_quester(self):
        await self.run_flow()
        self.zone = Quester.TAMARIN_HOUSE_ZONE
        restarted = Quester(self.client, [self.client], None)
        restarted.read_quest_txt = self.quester.read_quest_txt
        self.assertFalse(await restarted._maybe_handle_tamarin_house(self.client))
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_no_first_transition_is_bounded_and_warning_not_repeated(self):
        self.client.teleport.side_effect = None
        with patch('src.questing.logger') as log:
            await self.run_flow()
            await self.run_flow()
        self.assertEqual(self.client.teleport.await_count, 2)
        log.warning.assert_called_once()
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'failed')
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_transition_timeout_never_runs_second_tp(self):
        self.wait.side_effect = TimeoutError('still loading')
        await self.run_flow()
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_task_identity_change_cancels_second_tp(self):
        async def wait(*args, **kwargs):
            self.identity = (43, 7)
        self.wait.side_effect = wait
        await self.run_flow()
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'progressed')

    async def test_task_text_progress_cancels_second_tp(self):
        async def wait(*args, **kwargs):
            self.text = '与 金狮 对话 地点：废料堆'
        self.wait.side_effect = wait
        await self.run_flow()
        self.client.teleport.assert_awaited_once()

    async def test_text_fallback_works_without_readable_ids(self):
        self.identity = None
        await self.run_flow()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'completed')

    async def test_second_tp_exception_cleans_lock_and_state(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            if self.client.teleport.await_count == 2:
                raise RuntimeError('second teleport failed')
            await original(point)
        self.client.teleport.side_effect = teleport
        await self.run_flow()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client._xuanshu_tamarin_house_stage['active'])
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'failed')

    async def test_cancelled_flow_cleans_lock_and_does_not_replay(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client._xuanshu_tamarin_house_stage['active'])
        self.assertFalse(await self.run_flow())

    async def test_battle_after_first_tp_prevents_second(self):
        async def wait(*args, **kwargs):
            self.client.in_battle.return_value = True
        self.wait.side_effect = wait
        await self.run_flow()
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_second_tp_not_near_target_retries_at_most_twice(self):
        async def teleport(point):
            if self.client.teleport.await_count == 1:
                self.zone = 'Lemuria/Interiors/NewRoom'
        self.client.teleport.side_effect = teleport
        self.client.body.position.return_value = XYZ(1000, 2000, 0)
        await self.run_flow()
        self.assertEqual(self.client.teleport.await_count, 3)  # first + two second-stage attempts
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'failed')

    async def test_hub_end_recovery_is_preempted_without_moving_other_client(self):
        other = SimpleNamespace(title='p2', send_key=AsyncMock(), in_battle=AsyncMock(return_value=True))
        self.quester.clients.append(other)
        self.quester.followers_in_correct_zone = AsyncMock(return_value=False)
        await self.quester.zone_recorrect_hub()
        self.client.send_key.assert_not_awaited()
        other.send_key.assert_not_awaited()
        self.assertEqual(self.client._xuanshu_tamarin_house_stage['result'], 'completed')

    async def test_ordinary_task_tp_runs_only_after_special_flow_finished(self):
        await self.quester.teleport_to_quest_target(self.client, XYZ(800, 1, 0))
        self.quester.move_until_quest_interaction.assert_not_awaited()
        await self.quester.teleport_to_quest_target(self.client, XYZ(800, 1, 0))
        self.quester.move_until_quest_interaction.assert_awaited_once()

    def test_special_flow_precedes_mainline_recovery_in_solo_worker(self):
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(Quester.auto_quest_solo)))
        calls = {node.func.attr: node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in ('_maybe_handle_tamarin_house', '_maybe_recover_mainline')}
        self.assertLess(calls['_maybe_handle_tamarin_house'], calls['_maybe_recover_mainline'])

    async def test_loading_started_during_task_recheck_blocks_second_tp(self):
        arrival_snapshots = 0
        def snapshot(_):
            nonlocal arrival_snapshots
            if self.zone != Quester.TAMARIN_HOUSE_ZONE:
                arrival_snapshots += 1
                if arrival_snapshots == 2:
                    self.client.is_loading.return_value = True
            return (*self.identity, self.text)
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.run_flow()
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)


if __name__ == '__main__':
    unittest.main()
