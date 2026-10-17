"""Manual quest TP must keep searching instead of starting multi-kilometre walks."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import teleport_math as movement
from tests import test_walk_navmap_fallback as walk_fixture
from tests import test_manual_tp_questing_off as manual_fixture


class ManualApproachLimitTests(unittest.IsolatedAsyncioTestCase):
    setUp = walk_fixture.WalkNavmapFallbackTests.setUp
    sleep = walk_fixture.WalkNavmapFallbackTests.sleep
    solve = walk_fixture.WalkNavmapFallbackTests.solve
    plan = walk_fixture.WalkNavmapFallbackTests.plan
    teleport = walk_fixture.WalkNavmapFallbackTests.teleport
    goto = walk_fixture.WalkNavmapFallbackTests.goto
    manual_entry = manual_fixture.ManualTPQuestingOffTests.manual_entry

    async def test_observed_manual_distances_do_not_plan_or_walk(self):
        plans = []
        self.on_plan = lambda: plans.append(True)
        for remaining in (12723, 8778):
            with self.subTest(remaining=remaining):
                self.position = XYZ(self.target.x-remaining, self.target.y, self.target.z)
                self.events.clear()
                self.assertFalse(await movement._walk_remaining_to_target(
                    self.client, self.target, object(), self.zone, 55, require_questing=False))
                self.assertFalse(any(kind == 'walk' for kind, _ in self.events))
        self.assertEqual(plans, [])
        self.assertTrue(any('decision=reject-approach-walk' in message
                            and 'source=manual' in message for message in self.messages))

    async def test_manual_short_approach_keeps_existing_actual_walk(self):
        self.position = self.safe  # About 284u in 3D, within the existing short-walk fixture.
        self.avoid = []
        self.assertTrue(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55, require_questing=False))
        self.assertTrue(any(kind == 'walk' for kind, _ in self.events))

    async def test_far_landing_does_not_preempt_a_later_near_landing(self):
        self.client.questing_status = False
        self.avoid = []
        points = []
        async def land(dest):
            points.append(dest)
            self.position = (XYZ(self.target.x-8778, self.target.y, self.target.z)
                             if len(points) == 1 else self.safe)
        self.client.teleport = land
        with patch.object(movement, 'load_wad', AsyncMock(return_value=SimpleNamespace(
                get_file=AsyncMock(return_value=b'nav')))), \
             patch.object(movement, 'parse_nav_data', return_value=([self.safe], [])):
            await self.manual_entry()(self.client, [])
        self.assertGreaterEqual(len(points), 2)
        self.assertLessEqual(movement.calc_Distance(self.position, self.target), movement._WALK_GAP_MIN)
        self.assertTrue(any(kind == 'walk' for kind, _ in self.events))
        self.assertTrue(any('remaining-distance-too-large' in line for line in self.messages))

    async def test_all_manual_landings_far_return_clean_failure_without_astar(self):
        self.client.questing_status = False
        self.position = XYZ(self.target.x-12723, self.target.y, self.target.z)
        async def land(dest):
            self.events.append(('tp', dest))
            # Server accepts a remote point but never a usable final approach landing.
            self.position = XYZ(self.position.x+10, self.target.y, self.target.z)
        self.client.teleport = land
        plans = []
        self.on_plan = lambda: plans.append(True)
        with patch.object(movement, 'load_wad', AsyncMock(return_value=SimpleNamespace(
                get_file=AsyncMock(return_value=b'nav')))), \
             patch.object(movement, 'parse_nav_data', return_value=([self.safe], [])):
            result = await movement.navmap_tp(self.client, self.target, require_questing=False)
        self.assertIs(result, False)
        self.assertEqual(plans, [])
        self.assertFalse(any(kind == 'walk' for kind, _ in self.events))
        self.assertGreater(len(self.events), 1)

    async def test_automatic_existing_short_approach_is_unchanged(self):
        self.position = self.safe
        self.avoid = []
        self.assertTrue(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55))
        self.assertTrue(any(kind == 'walk' for kind, _ in self.events))

    async def test_manual_500u_boundary_keeps_short_walk_but_501u_does_not_plan(self):
        self.avoid = []
        self.position = XYZ(self.target.x-500, self.target.y, self.target.z)
        self.path = [tuple(self.position), tuple(self.target)]
        self.assertTrue(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55, require_questing=False))
        self.assertTrue(any(kind == 'walk' for kind, _ in self.events))
        self.events.clear()
        plans = []
        self.on_plan = lambda: plans.append(True)
        self.position = XYZ(self.target.x-501, self.target.y, self.target.z)
        self.assertFalse(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55, require_questing=False))
        self.assertEqual(plans, [])
        self.assertEqual(self.events, [])

    async def test_manual_position_becoming_remote_during_plan_cancels_before_walk_input(self):
        self.avoid = []
        self.position = self.safe
        self.on_plan = lambda: setattr(self, 'position', XYZ(self.target.x-8778, self.target.y, self.target.z))
        self.assertFalse(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55, require_questing=False))
        self.assertFalse(any(kind == 'walk' for kind, _ in self.events))
