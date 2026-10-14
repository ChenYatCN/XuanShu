"""Restore the reference ordinary prompt range independently of TP failure state."""
import asyncio
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import teleport_math as movement
from src.paths import npc_range_path
import tests.test_stale_interaction_movement as prompts


class OrdinaryRangeTests(prompts.Fixture):
    async def test_182_unit_prompt_reaches_real_ordinary_x_without_tp_or_name_matching(self):
        self.nearby(182)
        self.title = 'Unrelated display name'
        self.prompt = 'Press X to Use'
        self.objective = 'A different task name'
        self.on_x = lambda: setattr(self, 'goal', self.goal + 1)
        with patch('src.questing.collision_tp', AsyncMock()) as tp:
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 1)
        tp.assert_not_awaited()

    async def test_failed_or_stale_x_identity_is_not_an_ordinary_target_proof_gate(self):
        self.nearby(182)
        retry = await self.quester._quest_interaction_retry_state(self.client,self.target)
        signature = await self.quester._quest_interaction_signature(self.client)
        retry['failed'].add(signature)
        retry['stale'].add(signature)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))

    async def test_reference_distance_is_strictly_less_than_750(self):
        self.nearby(182)
        for distance, expected in ((749,True),(750,False),(751,False)):
            with self.subTest(distance=distance):
                self.position = XYZ(self.target.x-distance,self.target.y,self.target.z)
                self.assertEqual(await self.quester.quest_interaction_ready(self.client,self.target),expected)

    async def test_absent_range_prompt_still_cannot_handoff(self):
        self.nearby(182)
        with patch.object(movement,'is_visible_by_path',AsyncMock(return_value=False)):
            self.assertFalse(await movement._movement_interaction_handoff(
                self.client,self.target,await movement._movement_task_snapshot(self.client)))

    async def test_failed_tp_at_visible_world_gate_stays_blocked_inside_750(self):
        self.nearby(182)
        self.title = '世界之门'
        self.prompt = 'Press X to interact'
        task = await movement._movement_task_snapshot(self.client)
        key=(task[3],(task[0],task[1],tuple(task[2]),task[3]),tuple(self.target))
        self.client._quest_tp_rejected=dict(key=key,walks=1,origin=self.position,anchor=self.position,
                                           ready=False,announced=False,
                                           origin_signature=(self.title,self.prompt))
        for _ in range(3):
            await self.quester.auto_quest_solo()
        self.assertNotIn(Keycode.X,self.keys)

    async def test_stable_departure_uses_ordinary_range_without_150_gate(self):
        self.nearby(354)
        task = await movement._movement_task_snapshot(self.client)
        key=(task[3],(task[0],task[1],tuple(task[2]),task[3]),tuple(self.target))
        self.client._quest_tp_rejected=dict(key=key,walks=1,origin=self.position,anchor=self.position,
                                           ready=False,announced=False,
                                           origin_signature=(self.title,self.prompt))
        self.position=XYZ(self.target.x-182,self.target.y,self.target.z)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))
        self.position=XYZ(self.target.x-140,self.target.y,self.target.z)
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))

    async def test_actual_world_gate_without_failed_tp_is_allowed_at_182(self):
        self.nearby(182)
        self.title='世界之门'
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))

    async def test_loading_and_battle_still_block_x_inside_restored_range(self):
        self.nearby(182)
        for method in (self.client.is_loading,self.client.in_battle):
            with self.subTest(method=method):
                method.return_value=True
                self.assertFalse(await self.quester.quest_interaction_ready(self.client,self.target))
                await self.quester.auto_quest_solo()
                self.assertNotIn(Keycode.X,self.keys)
                method.return_value=False

    async def test_real_dialogue_yields_movement_but_never_grants_new_x(self):
        self.nearby(182)
        task=await movement._movement_task_snapshot(self.client)
        self.dialogue=True
        self.assertTrue(await self.quester.quest_interaction_ready(self.client,self.target))
        self.assertFalse(await self.quester.quest_interaction_ready(self.client,self.target,task_before=task))

    async def test_919_unit_height_difference_is_not_changed_to_horizontal_distance(self):
        self.nearby(0)
        self.position=XYZ(self.target.x,self.target.y,self.target.z+919)
        self.assertFalse(await self.quester.quest_interaction_ready(self.client,self.target))
