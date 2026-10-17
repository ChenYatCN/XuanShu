"""Current-target transition prompts are proven by movement, never door names."""
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.paths import npc_range_path
from src import teleport_math as movement
from tests.test_stale_interaction_movement import Fixture


class TargetTransitionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective = '前往下一个区域 地点：远方'
        self.title, self.prompt = '世界之门', '按下 或 互动'
        self.nearby(800)
        self.range_visible = False
        visible = AsyncMock(side_effect=lambda _client, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))

    async def approach(self, gap):
        async def transport(*_args, **_kwargs):
            self.position = XYZ(self.target.x-gap, self.target.y, self.target.z)
            self.range_visible = True
            return 'ready'  # Successful guarded approach, without exact-target arrival.
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            return await self.quester.move_until_quest_interaction(self.client, self.target)

    async def test_observed_transitions_allow_once_after_current_target_approach(self):
        for title, action, gap in (
                ('世界之门', '按下 或 互动', 175), ('Safehouse', '点击 进入', 166),
                ('Safehouse', '点击 进入', 153), ('去玫瑰镇(La Ville Rose)', '按 使用', 88),
                ('Unlisted Interior', 'Press X to Enter', 175),
                ('前往从未登记的地区', '按 使用', 88)):
            with self.subTest(title=title, gap=gap):
                self.setUp()
                self.title, self.prompt = title, action
                self.assertEqual(await self.approach(gap), 'ready')
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted, proof.reason)
                self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_transition_range_is_250u_not_a_distance_only_permission(self):
        await self.approach(250)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted, proof.reason)
        self.position = XYZ(self.target.x-251, self.target.y, self.target.z)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_use_objective_without_collect_progress_can_target_a_transition(self):
        self.objective = 'Use the next route switch in This Zone'
        await self.approach(175)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted, proof.reason)

    async def test_close_transition_without_provenance_or_with_old_task_rejects(self):
        self.range_visible = True
        self.position = XYZ(self.target.x-50, self.target.y, self.target.z)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.position = XYZ(self.target.x-800, self.target.y, self.target.z)
        self.range_visible = False
        await self.approach(175)
        self.goal += 1
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_old_readable_transition_can_upgrade_after_real_current_approach(self):
        self.range_visible = True
        await self.approach(175)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_unreadable_preexisting_popup_is_not_evidence_of_a_new_prompt(self):
        self.range_visible, self.title = True, ''
        async def transport(*_args, **_kwargs):
            self.position = XYZ(self.target.x-175, self.target.y, self.target.z)
            self.title = '世界之门'
            return 'failed'
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_confirmed_tp_or_walk_can_upgrade_a_readable_persistent_transition(self):
        for method in ('tp', 'walk'):
            with self.subTest(method=method):
                self.setUp()
                self.range_visible = True  # Readable before this stable task's actual approach.
                task = await movement._movement_task_snapshot(self.client)
                async def transport(*_args, **_kwargs):
                    if method == 'tp':
                        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', point))
                        record = dict(key=(task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)),
                            control=None, origin=self.position, anchor=self.position, walks=0,
                            rejected=False, confirmed=False, ready=False)
                        self.assertTrue(await movement._teleport_once_verified(self.client,
                            self.target, self.position, task[3], validation_state=record))
                    self.position = XYZ(self.target.x-175, self.target.y, self.target.z)
                    return 'ready'
                with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
                    await self.quester.move_until_quest_interaction(self.client, self.target)
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted)
                self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_remote_task_with_nearby_world_gate_or_safehouse_never_sends_x(self):
        for title, action in (('世界之门', '按下 或 互动'), ('Safehouse', '点击 进入')):
            self.title, self.prompt = title, action
            self.range_visible = True
            self.position = XYZ(self.target.x-9500, self.target.y, self.target.z)
            proof = await self.quester._interaction_eligibility(self.client, self.target)
            self.assertFalse(proof.accepted)
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_only_classified_transitions_can_gateway_for_collect_objectives(self):
        for title, action, objective, gateway in (
                ('无关机关', '按 使用', '前往下一个区域 地点：远方', False),
                ('无关NPC', '按下 或 交谈', '前往下一个区域 地点：远方', False),
                ('世界之门', '按下 或 互动', '收集 齿轮 地点：远方 (0/3)', True),
                ('世界之门', '按下 或 互动', '使用 路线开关 地点：远方 (0/3)', True),
                ('去未知地区', '按下 采集', '前往下一个区域 地点：远方', False)):
            with self.subTest(title=title, objective=objective):
                self.setUp()
                self.title, self.prompt, self.objective = title, action, objective
                await self.approach(175)
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertEqual(proof.accepted, gateway)
                if gateway:
                    self.assertEqual(proof.reason, 'accepted-current-target-gateway')
                self.assertEqual(await self.quester._send_quest_interaction_x(self.client, self.target, proof), gateway)
                self.assertEqual(self.keys, [Keycode.X] if gateway else [])
                self.stack.close()

    async def test_transition_final_x_is_cancelled_when_snapshot_or_popup_changes(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'title', 'action', 'hidden', 'loading', 'battle'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.approach(175)
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted, proof.reason)
                if cause == 'quest': self.client.quest_id.return_value = 999
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'target': self.target = XYZ(1100, 2000, 3)
                if cause == 'title': self.title = 'Different Gate'
                if cause == 'action': self.prompt = '点击 进入'
                if cause == 'hidden': self.range_visible = False
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                self.assertFalse(await self.quester._send_quest_interaction_x(
                    self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()
