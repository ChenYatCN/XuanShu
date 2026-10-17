"""Permanent offline reproduction of the observed Wallaru campfire interaction."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.interaction_prompts import quest_interaction_matches
from src.paths import npc_range_path
from src.automation_ownership import automation_owner
from tests.test_stale_interaction_movement import Fixture


class TaskTargetInteractionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.client.quest_id.return_value = 127508164802731876
        self.goal = 2559431
        self.client.zone_name.return_value = 'Wallaru/WL_Z07_EucalyptusForest'
        self.objective = '休息 篝火 地点：桉树林'
        self.title, self.prompt = '在营地休息', '按下 或 交谈'
        self.nearby(800)

    async def approach(self, gap=250, *, outcome='ready', move=True):
        async def transport(client, target, **_kwargs):
            # Only game transport is simulated. The real wrapper must observe
            # movement towards this task and grant a fresh HUD proof itself.
            self.assertIs(client, self.client)
            self.assertEqual(tuple(target), tuple(self.target))
            if move:
                self.position = XYZ(target.x-gap, target.y, target.z)
            return outcome
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            return await self.quester.move_until_quest_interaction(self.client, self.target)

    async def test_observed_campfire_alias_at_210_to_250u_sends_exactly_one_x(self):
        for gap in (210, 250):
            with self.subTest(gap=gap):
                self.setUp()
                self.assertFalse(quest_interaction_matches(self.objective, self.title))
                await self.approach(gap)
                self.assertEqual(self.keys, [], 'the rejected/accepted movement gate does not press X')
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertEqual(proof.reason, 'accepted-task-target-interaction')
                self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_300u_boundary_is_not_the_ordinary_750u_range(self):
        await self.approach(300)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-task-target-interaction')
        self.position = XYZ(self.target.x-301, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'task-target-interaction-too-far')
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_close_semantic_popup_without_approach_is_rejected(self):
        self.position = XYZ(self.target.x-200, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'task-target-interaction-no-provenance')
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_called_noop_navigation_cannot_create_approach_proof(self):
        self.position = XYZ(self.target.x-250, self.target.y, self.target.z)
        await self.approach(move=False, outcome='ready')
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_movement_away_from_target_is_not_target_approach(self):
        self.position = XYZ(self.target.x-200, self.target.y, self.target.z)
        await self.approach(250)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_unrelated_use_after_real_target_approach_does_not_send_x(self):
        self.title, self.prompt = '使用宝箱', 'Press X to Use'
        await self.approach()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'object-too-far')
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_generic_ui_words_alone_are_not_task_semantics(self):
        for generic in ('使用', '点击', '交谈', '进入'):
            with self.subTest(generic=generic):
                self.setUp()
                self.objective = f'{generic} 控制器 地点：桉树林'
                self.title = f'{generic}宝箱'
                await self.approach()
                self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_strict_npc_and_use_keep_their_original_reason(self):
        for objective, title, prompt in (
                ('交谈 毛茸茸狂豹 地点：桉树林', '毛茸茸狂豹', '按下 或 交谈'),
                ('Use Task Lever', 'Task Lever', 'Press X to Use')):
            with self.subTest(title=title):
                self.objective, self.title, self.prompt = objective, title, prompt
                self.position = XYZ(self.target.x-400, self.target.y, self.target.z)
                self.assertEqual((await self.quester._interaction_eligibility(
                    self.client, self.target)).reason, 'accepted-current-task')

    async def test_ordinary_specific_use_action_is_not_misclassified_as_dedicated_collect(self):
        self.objective, self.title, self.prompt = '使用 控制器 地点：桉树林', '古老控制器', '按 使用'
        await self.approach(250)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-task-target-interaction')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_font_of_germination_uses_strong_entity_match_without_approach(self):
        self.objective = '复原 Font of Germination 地点：Vivarium'
        self.title, self.prompt = 'FONT OF GERMINATION', '按下 或 互动'
        self.position = XYZ(self.target.x-175, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-current-task')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_font_task_does_not_authorize_nearby_ancient_lever(self):
        self.objective = '复原 Font of Germination 地点：Vivarium'
        self.title, self.prompt = 'Ancient Lever', '按下 或 互动'
        self.position = XYZ(self.target.x-50, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_collect_semantic_overlap_cannot_enter_ordinary_fallback(self):
        self.objective = '收集 休息 道具 地点：桉树林 (0/3)'
        for prompt in ('按下 或 交谈', '按下 采集'):
            with self.subTest(prompt=prompt):
                self.prompt = prompt
                await self.approach()
                self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
                self.assertEqual(self.keys, [])

    async def test_far_task_with_nearby_popup_cannot_use_old_approach(self):
        await self.approach()
        self.position = XYZ(self.target.x-9500, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_final_dispatch_rechecks_every_snapshot_and_safety_field(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'objective', 'title', 'action',
                      'hidden', 'loading', 'battle', 'distance', 'provenance'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.approach()
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted)
                if cause == 'quest': self.client.quest_id.return_value = 99
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'target': self.target = XYZ(1200, 2000, 3)
                if cause == 'objective': self.objective = '休息 下一个目标 地点：桉树林'
                if cause == 'title': self.title = '休息在另一处'
                if cause == 'action': self.prompt = 'Press X to Open'
                if cause == 'hidden':
                    self.stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)))
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                if cause == 'distance': self.position = XYZ(self.target.x-301, self.target.y, self.target.z)
                if cause == 'provenance': self.client._quest_target_approach = None
                self.assertFalse(await self.quester._send_quest_interaction_x(
                    self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_final_dispatch_rechecks_controlled_body_and_input_owner(self):
        await self.approach()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        pet = SimpleNamespace(position=self.client.body.position,
                              parent_client_object=AsyncMock(return_value=parent))
        with patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))):
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        # Re-establish a real approach after switching control back.
        self.position = XYZ(self.target.x-800, self.target.y, self.target.z)
        await self.approach()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted)
        entered, release = asyncio.Event(), asyncio.Event()
        async def busy():
            async with automation_owner(self.client, 'combat'):
                entered.set()
                await release.wait()
        task = asyncio.create_task(busy())
        await entered.wait()
        try:
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        finally:
            release.set()
            await task
        self.assertEqual(self.keys, [])

    async def test_icon_markup_and_location_words_do_not_authorize_unrelated_target(self):
        self.objective = '<center>休息 <icon;Use_MU_Fire.dds> 篝火 地点：桉树林</center>'
        await self.approach()
        self.assertEqual((await self.quester._interaction_eligibility(
            self.client, self.target)).reason, 'accepted-task-target-interaction')
        self.title = '桉树林入口'
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_visible_correlated_prompt_stops_ongoing_approach_without_exact_arrival(self):
        cancelled = asyncio.Event()
        async def transport(*_args, **_kwargs):
            self.position = XYZ(self.target.x-250, self.target.y, self.target.z)
            try:
                await asyncio.Event().wait()  # Exact QuestData XYZ is unreachable.
            finally:
                cancelled.set()
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            result = await asyncio.wait_for(self.quester.move_until_quest_interaction(
                self.client, self.target), .5)
        self.assertEqual(result, 'ready')
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.keys, [], 'movement hands off; it never sends X itself')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_pending_or_bounced_landing_cannot_authorize_alias_x(self):
        await self.approach()
        task = await movement._movement_task_snapshot(self.client)
        record = dict(key=(task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)),
                      control=None, origin=self.position, anchor=self.position, walks=0,
                      attempted=True, rejected=True, confirmed=False, ready=False,
                      origin_signature=(self.title, self.prompt))
        self.client._quest_tp_rejected = record
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        record['pending'] = self.target, self.position, task[3]
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_last_position_read_cannot_bypass_snapshot_or_provenance_revalidation(self):
        for change in ('goal', 'provenance', 'nonfinite'):
            with self.subTest(change=change):
                self.setUp()
                await self.approach()
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted)
                reads = 0
                async def read_position(_client):
                    nonlocal reads
                    reads += 1
                    if reads == 2:
                        if change == 'goal': self.goal += 1
                        if change == 'provenance': self.client._quest_target_approach = None
                        if change == 'nonfinite': return XYZ(float('nan'), self.target.y, self.target.z)
                    return self.position
                with patch.object(movement, '_movement_position', read_position):
                    self.assertFalse(await self.quester._send_quest_interaction_x(
                        self.client, self.target, proof))
                self.assertEqual(self.keys, [])
                self.stack.close()
