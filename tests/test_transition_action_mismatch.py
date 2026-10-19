"""Find/Visit-style objectives may end on a strongly evidenced entrance prompt."""
import unittest

from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from tests import test_current_target_object as objects


class TransitionActionMismatchTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, objective='Find Hidden Cave', title='Saboteur Cave',
                prompt='Press X to Enter'):
        fixture = objects.CurrentTargetObjectTests()
        fixture.setUp()
        self.addCleanup(fixture.stack.close)
        fixture.objective, fixture.title, fixture.prompt = objective, title, prompt
        return fixture

    async def test_exact_current_task_transition_allows_objective_action_mismatch(self):
        fixture = self.fixture()
        proof = await fixture.object_ready(gap=0)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertIn(proof.reason, (
            'accepted-task-target-transition', 'accepted-current-target-gateway'))
        self.assertTrue(await fixture.quester._send_quest_interaction_x(
            fixture.client, fixture.target, proof))
        self.assertFalse(await fixture.quester._send_quest_interaction_x(
            fixture.client, fixture.target, proof))
        self.assertEqual(fixture.keys, [Keycode.X])

    async def test_weak_transition_objective_action_mismatch_is_rejected(self):
        fixture = self.fixture()
        proof = await fixture.object_ready(gap=140)
        self.assertFalse(proof.accepted)
        self.assertFalse(await fixture.quester._send_quest_interaction_x(
            fixture.client, fixture.target, proof))
        self.assertEqual(fixture.keys, [])

    async def test_stale_transition_prompt_is_rejected(self):
        fixture = self.fixture()
        fixture.position = XYZ(fixture.target.x-100, fixture.target.y, fixture.target.z)
        fixture.range_visible = True

        async def arrive(*_args, **_kwargs):
            fixture.position = XYZ(*fixture.target)
            return 'ready'

        with patch('src.questing.collision_tp', AsyncMock(side_effect=arrive)):
            await fixture.quester.move_until_quest_interaction(
                fixture.client, fixture.target)
        proof = await fixture.quester._interaction_eligibility(
            fixture.client, fixture.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await fixture.quester._send_quest_interaction_x(
            fixture.client, fixture.target, proof))
        self.assertEqual(fixture.keys, [])

    async def test_no_approach_transition_objective_action_mismatch_is_rejected(self):
        fixture = self.fixture()
        fixture.position = XYZ(*fixture.target)
        fixture.range_visible = True
        proof = await fixture.quester._interaction_eligibility(
            fixture.client, fixture.target)
        self.assertFalse(proof.accepted)
        self.assertEqual(fixture.keys, [])

    async def test_strong_spatial_incompatible_action_is_rejected(self):
        """Keep incompatible non-transition actions rejected at exact arrival."""
        fixture = self.fixture(
            objective='Talk to Task Device', title='Task Device',
            prompt='Press X to Use')
        proof = await fixture.object_ready(gap=0)
        self.assertFalse(proof.accepted)
        self.assertFalse(await fixture.quester._send_quest_interaction_x(
            fixture.client, fixture.target, proof))
        self.assertEqual(fixture.keys, [])


if __name__ == '__main__':
    unittest.main()
