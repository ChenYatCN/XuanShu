import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src.questing import Quester


class FloatingLandExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_exit_recovery(self):
        zone = 'Celestia/CL_Z05_The_Floating_Land'
        for current_zone, position, free, expected in (
            (zone, XYZ(6420.986, -6956.173, -399.699), True, True),
            (zone, XYZ(6470.986, -6956.173, -399.699), True, True),
            (zone, XYZ(6592.342, -6749.908, -250.054), True, False),
            (zone, XYZ(6420.986, -6956.173, 0), True, False),
            ('Other', XYZ(6420.986, -6956.173, -399.699), True, False),
            (zone, XYZ(6420.986, -6956.173, -399.699), False, False),
        ):
            with self.subTest(zone=current_zone, position=position, free=free):
                client = SimpleNamespace(title='p1', zone_name=AsyncMock(return_value=current_zone),
                    body=SimpleNamespace(position=AsyncMock(return_value=position)), teleport=AsyncMock(),
                    is_loading=AsyncMock(return_value=False), send_key=AsyncMock())
                quester = Quester(client, [client], None)
                quester.move_until_quest_interaction = AsyncMock()
                with ExitStack() as stack:
                    for name, result in [('is_free', free), ('is_spiral_door_open', False),
                                         ('is_visible_by_path', False), ('get_quest_name', 'Go outside'),
                                         ('collision_tp', None)]:
                        stack.enter_context(patch('src.questing.' + name, new=AsyncMock(return_value=result)))
                    stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)))
                    for now in (0, 5):
                        await quester.teleport_to_quest_target(client, XYZ(9000, 0, 0))
                    client.teleport.assert_not_awaited()
                    for now in (11, 15):
                        await quester.teleport_to_quest_target(client, XYZ(9000, 0, 0))
                if expected:
                    client.teleport.assert_awaited_once()
                    xyz = client.teleport.await_args.args[0]
                    self.assertEqual((xyz.x, xyz.y, xyz.z), (6592.342, -6749.908, -250.054))
                else:
                    client.teleport.assert_not_awaited()
