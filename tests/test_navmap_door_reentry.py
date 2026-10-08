import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.teleport_math import navmap_tp


class NavmapDoorReentryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(10, 20, 0)
        self.position = XYZ(10, 20, -1)
        self.zone = 'Khrysalis/Interiors/KR_Z11_I06_WeftTowerTop'
        self.free = True
        self.reject = False
        self.client = SimpleNamespace(title='p3',
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)))
        async def teleport(xyz):
            if not self.reject:
                self.position = xyz
        async def walk(x, y):
            self.zone = 'Khrysalis/KR_Z11_TheHive'
        self.client.teleport = AsyncMock(side_effect=teleport)
        self.client.goto = AsyncMock(side_effect=walk)
        self.wad = SimpleNamespace(get_file=AsyncMock(return_value=b'nav'))
        self.vertices = [XYZ(0, 0, 0), XYZ(1000, 0, 0), XYZ(0, 1000, 0)]
        self.edges = [(0, 1), (0, 2)]
        self.load = AsyncMock(return_value=self.wad)
        self.spiral = AsyncMock()
        for item in (
            patch('src.teleport_math.is_free', new=AsyncMock(side_effect=lambda c: self.free)),
            patch('src.teleport_math.load_wad', new=self.load),
            patch('src.teleport_math.parse_nav_data', side_effect=lambda data: (self.vertices, self.edges)),
            patch('src.teleport_math.asyncio.sleep', new=AsyncMock()),
            patch('src.teleport_math.fallback_spiral_tp', new=self.spiral),
        ):
            item.start()
            self.addCleanup(item.stop)

    async def test_normal_manual_near_target_behaviour_is_unchanged(self):
        await navmap_tp(self.client, self.target)
        self.client.teleport.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.load.assert_not_awaited()

    async def test_door_reentry_retreats_to_nav_landing_then_walks_into_transition(self):
        await navmap_tp(self.client, self.target, reenter=True)
        self.client.teleport.assert_awaited_once()
        landing = self.client.teleport.await_args.args[0]
        self.assertGreater(abs(landing.x - self.target.x), 5)
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)
        self.assertEqual(self.zone, 'Khrysalis/KR_Z11_TheHive')
        self.spiral.assert_not_awaited()

    async def test_reentry_without_nav_data_does_not_use_unbounded_spiral(self):
        self.load.side_effect = ValueError('missing nav data')
        with self.assertRaisesRegex(ValueError, '未读到导航数据'):
            await navmap_tp(self.client, self.target, reenter=True)
        self.client.teleport.assert_not_awaited()
        self.spiral.assert_not_awaited()

    async def test_reentry_rejected_landings_are_bounded_and_do_not_walk(self):
        self.reject = True
        await navmap_tp(self.client, self.target, reenter=True)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.goto.assert_not_awaited()
        self.spiral.assert_not_awaited()

    async def test_nearby_nav_nodes_do_not_fake_a_retreat(self):
        self.vertices = [self.target]
        self.edges = []
        await navmap_tp(self.client, self.target, reenter=True)
        self.client.teleport.assert_not_awaited()
        self.client.goto.assert_not_awaited()

    async def test_loading_transition_after_retreat_prevents_followup_walk(self):
        async def transition(xyz):
            self.position = xyz
            self.zone = 'Khrysalis/KR_Z11_TheHive'
        self.client.teleport.side_effect = transition
        await navmap_tp(self.client, self.target, reenter=True)
        self.client.goto.assert_not_awaited()
        self.assertEqual(self.client.teleport.await_count, 1)

    async def test_dialogue_after_retreat_prevents_followup_walk(self):
        async def dialogue(xyz):
            self.position = xyz
            self.free = False
        self.client.teleport.side_effect = dialogue
        await navmap_tp(self.client, self.target, reenter=True)
        self.client.goto.assert_not_awaited()

    async def test_normal_far_direct_teleport_is_unchanged(self):
        self.position = XYZ(1000, 2000, 0)
        await navmap_tp(self.client, self.target)
        self.client.teleport.assert_awaited_once_with(self.target)
        self.load.assert_not_awaited()

    async def test_normal_manual_navigation_fallback_still_lands_then_walks(self):
        self.position = XYZ(1000, 2000, 0)
        async def direct_rejected(xyz):
            if self.client.teleport.await_count > 1:
                self.position = xyz
        self.client.teleport.side_effect = direct_rejected
        await navmap_tp(self.client, self.target)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertIs(self.client.teleport.await_args_list[0].args[0], self.target)
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)

