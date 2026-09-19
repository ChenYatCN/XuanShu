import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ, Keycode
from src.questing import Quester


class SpecialExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_three_require_time_and_attempts_and_fire_once(self):
        zones = {
            'Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove': (3124.726, -4718.231, 36.200),
            'Celestia/CL_Z09_Science_Center': (-1067.512, 343.759, -449.800),
            'Celestia/Interiors/CL_Z10i3_Kingdom_Of_The_Crabs': (3184.987, -9931.280, -1014.007),
        }
        for zone, target in zones.items():
            with self.subTest(zone=zone), ExitStack() as stack:
                client = AsyncMock()
                client.title = 'p1'
                client.zone_name.return_value = zone
                client.is_loading.return_value = False
                client.body.position.return_value = XYZ(0, 0, 0)
                quester = Quester(client, [client], None)
                for name, value in [('collision_tp', None), ('is_free', True), ('is_spiral_door_open', False)]:
                    stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=value)))
                interacted = [False]
                async def visible(*_):
                    return client.teleport.await_count > 0
                stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=visible)))
                stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(side_effect=lambda *_: 'next' if interacted[0] else 'same')))
                client.send_key.side_effect = lambda *_: interacted.__setitem__(0, True)
                stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)))
                for now in (0, 4, 8):
                    await quester.teleport_to_quest_target(client, XYZ(2000, 0, 0))
                client.teleport.assert_not_awaited()
                for now in (11, 15):
                    await quester.teleport_to_quest_target(client, XYZ(2000, 0, 0))
                client.teleport.assert_awaited_once()
                xyz = client.teleport.await_args.args[0]
                self.assertEqual((xyz.x, xyz.y, xyz.z), target)
                if 'Crabs' in zone:
                    client.send_key.assert_awaited_once_with(Keycode.X, .1)
                else:
                    client.send_key.assert_not_awaited()

    async def test_progress_resets_stall(self):
        client = AsyncMock()
        client.zone_name.return_value = 'Celestia/CL_Z09_Science_Center'
        client.is_loading.return_value = False
        client.body.position.return_value = XYZ(0, 0, 0)
        quester = Quester(client, [client], None)
        with ExitStack() as stack:
            for name, value in [('collision_tp', None), ('is_free', True), ('is_spiral_door_open', False), ('is_visible_by_path', False)]:
                stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=value)))
            stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(side_effect=lambda *_: str(now))))
            stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)))
            for now in (0, 12, 24, 36):
                await quester.teleport_to_quest_target(client, XYZ(2000, 0, 0))
            client.teleport.assert_not_awaited()
