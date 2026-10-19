"""Climb prompts use current-target transition proof, never object proximity."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.interaction_prompts import (
    interaction_kind, is_dungeon_entry_prompt, task_object_prompt, task_transition_prompt,
)
from src.paths import npc_range_path
from tests.test_stale_interaction_movement import Fixture
from tests.test_task_target_transition import TargetTransitionTests as TransitionFixture


class ClimbPromptTests(unittest.TestCase):
    def test_verified_climb_instructions_are_transitions_and_never_objects_or_enter(self):
        for prompt in ('按下 攀爬', '按下 X 攀爬', 'Press X to Climb',
                       '<center>按下 <icon;X> 攀爬</center>',
                       'Press &InputBindings_NPCInteract& to Climb',
                       'Press X or <icon;LeftMouseClick> to Climb',
                       '按下 或 攀登', 'Press X to Climb Rope', '按下 攀爬绳子',
                       '<string;GUI_00006133>'):
            with self.subTest(prompt=prompt):
                self.assertEqual(interaction_kind(prompt), 'climb')
                self.assertTrue(task_transition_prompt('Unlisted Passage', prompt))
                self.assertFalse(task_object_prompt('Unlisted Passage', prompt))
                self.assertFalse(is_dungeon_entry_prompt(prompt))

    def test_climb_words_in_titles_or_free_text_do_not_make_an_action(self):
        for title, prompt in (('Climb Vines', '按 使用'), ('攀爬藤蔓', 'Press X to Talk'),
                              ('Vines', 'Climb'), ('Vines', '攀爬'),
                              ('Vines', 'Press X to Climbdown'), ('Vines', 'Press X to Climb Anything')):
            with self.subTest(prompt=prompt):
                self.assertFalse(task_transition_prompt(title, prompt))


class ClimbTransitionTests(Fixture):
    approach = TransitionFixture.approach

    def setUp(self):
        super().setUp()
        # Reproduce the reported task/popup mismatch with synthetic stable IDs/XYZ.
        self.objective = 'Tosh.Oh!'
        self.title, self.prompt = 'Vines', '按下 攀爬'
        self.nearby(14820)
        self.range_visible = False
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))

    async def climb_ready(self, gap=112):
        self.assertEqual(await self.approach(gap), 'ready')
        self.assertIsNotNone(getattr(self.client, '_quest_target_approach', None))
        self.assertEqual(self.keys, [])
        return await self.quester._interaction_eligibility(self.client, self.target)

    async def test_tosh_oh_p1_p2_real_approach_accepts_climb_and_sends_one_x(self):
        for client, gap, prompt in (('p1', 66, '按下 攀爬'),
                                     ('p2', 112, '按下 攀爬'),
                                     ('p1', 112, 'Press X to Climb')):
            with self.subTest(client=client, gap=gap, prompt=prompt):
                self.setUp()
                self.client.title, self.prompt = client, prompt
                proof = await self.climb_ready(gap)
                self.assertTrue(proof.accepted, proof.reason)
                self.assertEqual(proof.reason, 'accepted-task-target-transition')
                self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_climb_gateway_cannot_override_known_collect_action_conflict(self):
        for objective, title, accepted in (('Defeat Guardians in Unknown Region', 'Unlisted Cliff', True),
                                  ('收集 齿轮 地点：远方 (0/3)', '未知通道', False)):
            with self.subTest(objective=objective):
                self.setUp()
                self.objective, self.title = objective, title
                proof = await self.climb_ready()
                self.assertEqual(proof.reason, 'accepted-current-target-gateway' if accepted
                                 else 'task-target-interaction-incompatible-action')
                self.assertEqual(await self.quester._send_quest_interaction_x(
                    self.client, self.target, proof), accepted)
                self.assertEqual(self.keys, [Keycode.X] if accepted else [])
                self.stack.close()

    async def test_climb_keeps_transition_250u_limit_and_remote_9500u_guard(self):
        proof = await self.climb_ready(250)
        self.assertTrue(proof.accepted, proof.reason)
        for gap in (251, 9500):
            self.position = XYZ(self.target.x-gap, self.target.y, self.target.z)
            self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_nearby_or_noop_climb_has_no_approach_authorization(self):
        self.range_visible = True
        self.position = XYZ(self.target.x-66, self.target.y, self.target.z)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_final_climb_x_revalidates_task_prompt_body_safety_and_provenance(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'title', 'action', 'body',
                      'hidden', 'loading', 'battle', 'expired'):
            with self.subTest(cause=cause):
                self.setUp()
                proof = await self.climb_ready()
                self.assertTrue(proof.accepted, proof.reason)
                if cause == 'quest': self.client.quest_id.return_value = 999
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'target': self.target = XYZ(self.target.x+30, self.target.y, self.target.z)
                if cause == 'title': self.title = 'Different Passage'
                if cause == 'action': self.prompt = 'Press X to Talk'
                if cause == 'hidden': self.range_visible = False
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                if cause == 'expired': self.client._quest_target_approach['confirmed_at'] -= 6
                if cause == 'body':
                    parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                             global_id_full=AsyncMock(return_value=210))
                    pet = SimpleNamespace(position=self.client.body.position,
                                          parent_client_object=AsyncMock(return_value=parent))
                    self.stack.enter_context(patch.object(movement, 'get_controlled_body',
                        AsyncMock(return_value=(pet, 200))))
                self.assertFalse(await self.quester._send_quest_interaction_x(
                    self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()


del TransitionFixture
