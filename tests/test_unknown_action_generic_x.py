"""Unrecognized words cannot veto a fresh exact-task approach or invent one."""
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from tests import test_current_target_object as objects


SAMPLES = (
    ('校准 神秘装置', 'UnknownDevice', '按下 共鸣'),
    ('抚平 时空裂隙', 'TemporalThing', '按下 编织'),
)


class UnknownActionGenericXTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, sample=SAMPLES[0]):
        f = objects.CurrentTargetObjectTests()
        f.setUp()
        self.addCleanup(f.stack.close)
        f.objective, f.title, f.prompt = sample
        return f

    async def assert_one_generic_x(self, sample):
        f = self.fixture(sample)
        proof = await f.object_ready(gap=0)
        self.assertIsNotNone(f.client._quest_target_approach)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertEqual(f.keys, [Keycode.X])
        self.assertEqual(f.goal, 606539207, 'X is not fabricated quest completion')

    async def test_unseen_device_action_sends_exactly_one_x(self):
        await self.assert_one_generic_x(SAMPLES[0])

    async def test_unseen_temporal_action_sends_exactly_one_x(self):
        await self.assert_one_generic_x(SAMPLES[1])

    async def test_unknown_action_auto_quest_dispatch_keeps_one_x_across_rounds(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                f = self.fixture(sample)
                try:
                    await f.object_ready(gap=0)
                    with patch.object(f.quester, 'teleport_to_quest_target', AsyncMock(return_value='ready')):
                        await f.quester.auto_quest_solo()
                        await f.quester.auto_quest_solo()
                    self.assertEqual(f.keys.count(Keycode.X), 2)
                    self.assertEqual(f.goal, 606539207)
                finally:
                    f.stack.close()

    async def test_unknown_prompt_at_140_units_never_sends_x(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                f = self.fixture(sample)
                try:
                    proof = await f.object_ready(gap=140)
                    self.assertFalse(proof.accepted)
                    self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
                    self.assertEqual(f.keys, [])
                finally:
                    f.stack.close()

    async def test_unknown_fallback_accepts_five_units_but_not_beyond(self):
        for sample in SAMPLES:
            for gap, accepted in ((5, True), (5.001, False)):
                with self.subTest(sample=sample, gap=gap):
                    f = self.fixture(sample)
                    try:
                        proof = await f.object_ready(gap=gap)
                        self.assertEqual(proof.accepted, accepted, proof.reason)
                        self.assertEqual(await f.quester._send_quest_interaction_x(
                            f.client, f.target, proof), accepted)
                        self.assertEqual(f.keys, [Keycode.X] if accepted else [])
                    finally:
                        f.stack.close()

    async def test_final_position_outside_strong_range_revokes_unknown_x(self):
        f = self.fixture()
        proof = await f.object_ready(gap=0)
        self.assertTrue(proof.accepted, proof.reason)
        f.position = XYZ(f.target.x-140, f.target.y, f.target.z)
        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertEqual(f.keys, [])

    async def test_unknown_prompt_without_approach_never_sends_x(self):
        f = self.fixture()
        f.position = XYZ(*f.target)
        proof = await f.quester._interaction_eligibility(f.client, f.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertEqual(f.keys, [])

    async def test_unchanged_preexisting_unknown_prompt_never_sends_x(self):
        f = self.fixture()
        async def arrive(*_args, **_kwargs):
            f.position = XYZ(*f.target)
            return 'ready'  # Keep the original visible identity throughout the real displacement.
        with patch('src.questing.collision_tp', AsyncMock(side_effect=arrive)):
            await f.quester.move_until_quest_interaction(f.client, f.target)
        self.assertIsNotNone(f.client._quest_target_approach)
        proof = await f.quester._interaction_eligibility(f.client, f.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertEqual(f.keys, [])

    async def test_expired_unknown_prompt_approach_never_sends_x(self):
        f = self.fixture()
        await f.object_ready(gap=0)
        f.client._quest_target_approach['confirmed_at'] -= 6
        proof = await f.quester._interaction_eligibility(f.client, f.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
        self.assertEqual(f.keys, [])

    async def test_known_incompatible_actions_cannot_be_overridden_by_arrival_or_name(self):
        for objective, prompt in (
                ('Talk to Task Device', 'Press X to Use'),
                ('Use Task Device', 'Press X to Talk'),
                ('Enter Task Device', 'Press X to Use'),
                ('Collect Task Device', 'Press X to Talk')):
            for title in ('Other Device', 'Task Device'):
                with self.subTest(objective=objective, prompt=prompt, title=title):
                    f = self.fixture((objective, title, prompt))
                    try:
                        proof = await f.object_ready(gap=0)
                        self.assertFalse(proof.accepted, proof.reason)
                        self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
                        self.assertEqual(f.keys, [])
                    finally:
                        f.stack.close()

    async def test_final_snapshot_change_revokes_unknown_generic_x(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'objective', 'title', 'action', 'missing'):
            with self.subTest(cause=cause):
                f = self.fixture()
                try:
                    proof = await f.object_ready(gap=0)
                    self.assertTrue(proof.accepted, proof.reason)
                    if cause == 'quest': f.client.quest_id.return_value = 99
                    if cause == 'goal': f.goal += 1
                    if cause == 'target': f.target = XYZ(f.target.x+40, f.target.y, f.target.z)
                    if cause == 'zone': f.client.zone_name.return_value = 'Other/Zone'
                    if cause == 'objective': f.objective = SAMPLES[1][0]
                    if cause == 'title': f.title = SAMPLES[1][1]
                    if cause == 'action': f.prompt = SAMPLES[1][2]
                    if cause == 'missing': f.range_visible = False
                    self.assertFalse(await f.quester._send_quest_interaction_x(
                        f.client, XYZ(*proof.target), proof))
                    self.assertEqual(f.keys, [])
                finally:
                    f.stack.close()

    async def test_unknown_does_not_require_both_action_kinds_to_be_unknown(self):
        for sample in (
                ('Use Task Device', SAMPLES[0][1], SAMPLES[0][2]),
                (SAMPLES[1][0], 'Other Device', 'Press X to Use'),
                (SAMPLES[1][0], 'Other Device', 'Press X to Enter'),
                (SAMPLES[1][0], 'Other Device', 'Press X to Climb')):
            with self.subTest(sample=sample):
                f = self.fixture(sample)
                try:
                    proof = await f.object_ready(gap=0)
                    self.assertTrue(proof.accepted, proof.reason)
                    self.assertTrue(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
                    self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
                    self.assertEqual(f.keys, [Keycode.X])
                finally:
                    f.stack.close()

    async def test_change_during_final_popup_read_cannot_authorize_unknown_x(self):
        for cause in ('task', 'prompt'):
            with self.subTest(cause=cause):
                f = self.fixture()
                try:
                    proof = await f.object_ready(gap=0)
                    self.assertTrue(proof.accepted, proof.reason)
                    changed = False
                    async def popup(_client):
                        nonlocal changed
                        value = f.prompt
                        if not changed:
                            changed = True
                            if cause == 'task': f.goal += 1
                            else: f.prompt = SAMPLES[1][2]
                        return value
                    f.quester.read_popup.side_effect = popup
                    self.assertFalse(await f.quester._send_quest_interaction_x(f.client, f.target, proof))
                    self.assertTrue(changed)
                    self.assertEqual(f.keys, [])
                finally:
                    f.stack.close()
