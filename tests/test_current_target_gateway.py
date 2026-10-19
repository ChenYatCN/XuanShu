"""A task can point to a gateway before its Talk/Collect/Defeat objective."""
import asyncio
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.automation_ownership import automation_owner
from src.paths import npc_range_path
from tests.test_stale_interaction_movement import Fixture
from tests import test_target_provenance_lifecycle as lifecycle


class CurrentTargetGatewayTests(Fixture):
    confirmed_tp = lifecycle.TargetProvenanceLifecycleTests.confirmed_tp

    def setUp(self):
        super().setUp()
        self.client.quest_id.return_value = 134826514187425562
        self.goal = 572984775
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/KT_Z06_Forgotten'
        self.target = XYZ(-12007.58984375, 8032.98779296875, 0.99969482421875)
        self.objective = '击败 先知 地点：Tomb of the Forgotten'
        self.title, self.prompt = 'Chamber of Phoebe', '点击 进入'
        self.nearby(14820)
        self.range_visible = True
        visible = AsyncMock(side_effect=lambda _client, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))
        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', point))

    async def gateway(self, gap=.0003):
        await self.confirmed_tp(gap=gap)
        self.range_visible = True
        return await self.quester._interaction_eligibility(self.client, self.target)

    async def test_phoebe_defeat_objective_real_tp_authorizes_one_existing_x(self):
        proof = await self.gateway()
        self.assertTrue(proof.accepted, proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-target-gateway')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_talk_objective_can_enter_building_with_unrelated_title(self):
        self.objective = '和 学者 交谈 地点：远方'
        self.title = 'Unlisted Building'
        proof = await self.gateway(175)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_collect_gateway_unknown_action_cannot_override_known_conflict(self):
        for title, action, accepted in (('Unlisted Region', '点击 进入', False),
                              ('去未知地区', '按 使用', True), ('世界之门', '按下 或 互动', True)):
            with self.subTest(action=action):
                self.setUp()
                self.objective = '收集 齿轮 地点：远方 (0/3)'
                self.title, self.prompt = title, action
                proof = await self.gateway(175)
                self.assertEqual(proof.reason, 'accepted-current-target-gateway' if accepted
                                 else 'task-target-interaction-incompatible-action')
                self.assertEqual(await self.quester._send_quest_interaction_x(
                    self.client, self.target, proof), accepted)
                self.assertEqual(self.keys, [Keycode.X] if accepted else [])
                self.stack.close()

    async def test_gateway_never_recruits_npc_collect_or_plain_use_prompts(self):
        for title, action in (('Other NPC', '按下 或 交谈'), ('Other Item', '按下 采集'),
                              ('Other Lever', '按 使用')):
            with self.subTest(action=action):
                self.setUp()
                self.title, self.prompt = title, action
                proof = await self.gateway(175)
                self.assertFalse(proof.accepted)
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_gateway_cannot_take_dedicated_collect_prompt(self):
        self.objective = '收集 齿轮 地点：远方 (0/3)'
        self.title, self.prompt = 'Other Item', '按下 采集'
        proof = await self.gateway(175)
        self.assertFalse(proof.accepted)
        self.assertEqual(self.keys, [])

    async def test_nearby_door_without_current_target_movement_has_no_authorization(self):
        self.position = self.target
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_noop_tp_cannot_create_gateway_authorization(self):
        self.position = self.target
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            for _ in range(3):
                await self.quester.move_until_quest_interaction(self.client, self.target)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.assertEqual(self.keys, [])

    async def test_remote_9500u_enter_never_sends_x(self):
        self.nearby(9500)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_gateway_keeps_250u_limit_even_with_provenance(self):
        proof = await self.gateway(250)
        self.assertTrue(proof.accepted, proof.reason)
        self.position = XYZ(self.target.x-251, self.target.y, self.target.z)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_final_x_revokes_gateway_on_changed_snapshot_prompt_or_safety(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'title', 'action', 'hidden', 'loading', 'battle', 'expired'):
            with self.subTest(cause=cause):
                self.setUp()
                proof = await self.gateway()
                self.assertTrue(proof.accepted, proof.reason)
                if cause == 'quest': self.client.quest_id.return_value = 999
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'target': self.target = XYZ(self.target.x+30, self.target.y, self.target.z)
                if cause == 'title': self.title = 'Different Door'
                if cause == 'action': self.prompt = 'Press X to Enter'
                if cause == 'hidden': self.range_visible = False
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                if cause == 'expired': self.client._quest_target_approach['confirmed_at'] -= 6
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_final_x_respects_existing_owner(self):
        proof = await self.gateway()
        self.assertTrue(proof.accepted, proof.reason)
        owned, release = asyncio.Event(), asyncio.Event()
        async def another_owner():
            async with automation_owner(self.client, 'test-other-owner'):
                owned.set()
                await release.wait()
        worker = asyncio.create_task(another_owner())
        try:
            await owned.wait()
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
            self.assertEqual(self.keys, [])
        finally:
            release.set()
            await worker

    async def test_semantic_use_stays_on_existing_semantic_path(self):
        self.objective = '使用 营地装置 地点：远方'
        self.title, self.prompt = '营地装置开关', '按 使用'
        proof = await self.gateway(175)
        self.assertEqual(proof.reason, 'accepted-task-target-interaction')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])
