"""Real target movement may complete before its Enter popup becomes visible."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.paths import npc_range_path
from tests.test_stale_interaction_movement import Fixture


class TargetProvenanceLifecycleTests(Fixture):
    def setUp(self):
        super().setUp()
        self.client.quest_id.return_value = 140456013717888036
        self.goal = 1691515200
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/KT_Z06_Forgotten'
        self.target = XYZ(-26124.23046875, 17997.279296875, 0.99951171875)
        self.objective = '前往任务指定区域 地点：被遗忘之地'
        self.title, self.prompt = 'Chamber of Mimas', '点击 进入'
        self.nearby(490)
        self.range_visible = True
        visible = AsyncMock(side_effect=lambda _client, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))
        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', point))

    async def confirmed_tp(self, *, gap=.00048, change=None, outcome='ready'):
        snapshot = await movement._movement_task_snapshot(self.client)
        async def transport(client, target, **_kwargs):
            self.assertEqual(tuple(target), tuple(snapshot[2]))
            state = dict(key=(snapshot[3], (snapshot[0], snapshot[1], tuple(snapshot[2]), snapshot[3]), tuple(target)),
                control=None, origin=self.position, anchor=self.position, walks=0,
                rejected=False, confirmed=False, ready=False)
            self.assertTrue(await movement._teleport_once_verified(client, target, self.position,
                snapshot[3], validation_state=state))
            self.position = XYZ(target.x-gap, target.y, target.z)
            self.range_visible = False  # UI catches up after the confirmed movement.
            if change:
                change()
            return outcome
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            return await self.quester.move_until_quest_interaction(self.client, self.target)

    async def test_mimas_real_tp_then_delayed_same_enter_sends_one_x(self):
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'task-target-transition-too-far')
        self.assertEqual(await self.confirmed_tp(), 'ready')
        self.assertIsNotNone(getattr(self.client, '_quest_target_approach', None))
        self.assertFalse(self.range_visible)
        self.clock += .8
        self.range_visible = True
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-task-target-enter')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_successful_provenance_survives_same_target_noop_before_popup(self):
        await self.confirmed_tp()
        record = self.client._quest_target_approach
        self.assertIsNotNone(record)
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        self.assertIs(self.client._quest_target_approach, record)
        self.range_visible = True
        self.assertTrue((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_noop_at_target_cannot_mint_provenance(self):
        self.position = self.target
        for _ in range(3):
            with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
                await self.quester.move_until_quest_interaction(self.client, self.target)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.assertEqual(self.keys, [])

    async def test_changed_snapshot_or_loading_invalidates_delayed_provenance(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.confirmed_tp()
                self.assertIsNotNone(self.client._quest_target_approach)
                if cause == 'quest': self.client.quest_id.return_value = 99
                if cause == 'goal': self.goal += 1
                if cause == 'target': self.target = XYZ(self.target.x+30, self.target.y, self.target.z)
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                self.range_visible = True
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertFalse(proof.accepted)
                self.assertIsNone(self.client._quest_target_approach)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_failed_movement_does_not_leave_provenance_even_with_actual_displacement(self):
        self.range_visible = False
        await self.confirmed_tp(outcome='failed')
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.range_visible = True
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_task_change_during_actual_tp_cannot_publish_provenance(self):
        await self.confirmed_tp(change=lambda: setattr(self, 'goal', self.goal+1))
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.assertEqual(self.keys, [])

    async def test_known_action_conflicts_reject_but_compatible_enter_keeps_provenance(self):
        await self.confirmed_tp()
        self.range_visible = True
        for title, action, object_action in (('Other NPC', '按下 或 交谈', False),
                ('Other Flower', '按下 采集', False), ('Other Lever', '按 使用', False),
                ('Other Entrance', '点击 进入', True)):
            self.title, self.prompt = title, action
            proof = await self.quester._interaction_eligibility(self.client, self.target)
            self.assertEqual(proof.accepted, object_action)
            if object_action:
                self.assertEqual(proof.reason, 'accepted-task-target-enter')
            self.assertEqual(await self.quester._send_quest_interaction_x(self.client, self.target, proof), object_action)
        self.assertEqual(self.keys, [Keycode.X])

    async def test_9500u_unrelated_enter_still_has_no_x(self):
        self.nearby(9500)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_callisto_real_tp_preserves_existing_enter_dispatch(self):
        self.title = 'Chamber of Callisto'
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        await self.confirmed_tp()
        self.range_visible = True
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-task-target-enter')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_expired_provenance_cannot_be_renewed_by_noop(self):
        await self.confirmed_tp()
        self.client._quest_target_approach['confirmed_at'] -= 6
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        self.range_visible = True
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertIsNone(self.client._quest_target_approach)

    async def test_readiness_unsafe_roundtrip_cannot_revive_delayed_provenance(self):
        for state in ('is_loading', 'in_battle'):
            with self.subTest(state=state):
                self.setUp()
                await self.confirmed_tp()
                getattr(self.client, state).return_value = True
                self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
                getattr(self.client, state).return_value = False
                self.range_visible = True
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertFalse(proof.accepted)
                self.assertIsNone(self.client._quest_target_approach)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_control_change_revokes_delayed_provenance(self):
        await self.confirmed_tp()
        self.range_visible = True
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        pet = SimpleNamespace(position=self.client.body.position,
                              parent_client_object=AsyncMock(return_value=parent))
        with patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))):
            self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertIsNone(self.client._quest_target_approach)

    async def test_cancel_during_completion_clears_only_its_provisional_proof(self):
        entered = asyncio.Event()
        original = self.quester._interaction_eligibility
        async def eligibility(*args, **kwargs):
            if getattr(self.client, '_quest_target_approach', None) is not None:
                entered.set()
                await asyncio.Event().wait()
            return await original(*args, **kwargs)
        self.quester._interaction_eligibility = eligibility
        worker = asyncio.create_task(self.confirmed_tp())
        try:
            await asyncio.wait_for(entered.wait(), .5)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
            self.assertIsNone(self.client._quest_target_approach)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_successful_safe_landing_and_walk_can_preserve_delayed_prompt_proof(self):
        for kind in ('safe-landing', 'walk'):
            with self.subTest(kind=kind):
                self.setUp()
                self.range_visible = False
                self.nearby(5000)
                if kind == 'safe-landing':
                    await self.confirmed_tp(gap=175)
                else:
                    async def walk(*_args, **_kwargs):
                        self.position = XYZ(self.target.x-175, self.target.y, self.target.z)
                        return 'ready'
                    with patch('src.questing.collision_tp', AsyncMock(side_effect=walk)):
                        await self.quester.move_until_quest_interaction(self.client, self.target)
                self.range_visible = True
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertEqual(proof.reason, 'accepted-task-target-enter')
                self.stack.close()
