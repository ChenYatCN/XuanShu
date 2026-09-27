import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker import XYZ
from src.questing import Quester, claim_quest_recovery


class CrystalExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_only_and_once(self):
        for blocked in (False, True):
            client = AsyncMock()
            client.title = 'p1'
            client.zone_name.return_value = 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
            client.is_loading.return_value = False
            client.body.position.return_value = XYZ(0, 0, 0)
            client.quest_recovery_owner = None
            async def teleport_while_owned(_target):
                self.assertEqual(client.quest_recovery_owner, 'special_zone')
                self.assertFalse(claim_quest_recovery(client, 'dungeon_quest'))
            client.teleport.side_effect = teleport_while_owned
            quester = Quester(client, [client], None)
            with ExitStack() as stack:
                for name, value in [('collision_tp', None), ('is_free', True),
                                    ('is_spiral_door_open', blocked), ('is_visible_by_path', False),
                                    ('get_quest_name', 'Leave the tower')]:
                    stack.enter_context(patch('src.questing.' + name, new=AsyncMock(return_value=value)))
                stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: now)))
                for now in (0, 5):
                    await quester.teleport_to_quest_target(client, XYZ(2000, 0, 0))
                client.teleport.assert_not_awaited()
                for now in (11, 15):
                    await quester.teleport_to_quest_target(client, XYZ(2000, 0, 0))
                self.assertEqual(client.teleport.await_count, int(not blocked))
                self.assertIsNone(client.quest_recovery_owner)
                client.send_key.assert_not_awaited()
                if not blocked:
                    xyz = client.teleport.await_args.args[0]
                    self.assertEqual((xyz.x, xyz.y, xyz.z), (34.461, 1382.432, 0.123))
