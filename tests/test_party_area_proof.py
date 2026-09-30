import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import MemoryInvalidated
from src import utils


def client(gid, address):
    area = SimpleNamespace(read_base_address=AsyncMock(return_value=address),
                           zone_id=AsyncMock(return_value=100))
    return SimpleNamespace(title=f'p{gid}', is_loading=AsyncMock(return_value=False),
        zone_name=AsyncMock(return_value='Dungeon/Room'),
        client_object=SimpleNamespace(global_id_full=AsyncMock(return_value=gid),
                                      client_zone=AsyncMock(return_value=area)),
        get_base_entity_list=AsyncMock(return_value=[]),
        body=SimpleNamespace(position=AsyncMock(return_value='XYZ'), yaw=AsyncMock(return_value=0)),
        teleport=AsyncMock(), send_key=AsyncMock(), quest_recovery_owner=None,
        refilling_potions=False, quest_party_hitters=[])


class PartyAreaProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.first = client(1, 1000)
        self.second = client(2, 2000)

    async def test_equal_zone_and_zone_id_are_not_instance_proof(self):
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_reciprocal_visibility_recovers_one_sided_missing_entity(self):
        self.second.get_base_entity_list.return_value = [self.first.client_object]
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def test_stale_entity_does_not_hide_next_valid_member(self):
        stale = SimpleNamespace(global_id_full=AsyncMock(side_effect=MemoryInvalidated('gone')))
        self.first.get_base_entity_list.return_value = [stale, self.second.client_object]
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def test_loading_during_entity_scan_rejects_proof(self):
        self.first.get_base_entity_list.return_value = [self.second.client_object]
        self.first.is_loading.side_effect = [False, True]
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def prepare_return_proof(self):
        self.first.quest_party_hitters = [self.second]
        self.first.get_base_entity_list.return_value = [self.second.client_object]
        with patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=(42, 7, 'Talk'))):
            self.assertTrue(await utils.prepare_potion_dungeon_return(self.first, 'Dungeon/Room'))
        self.first.get_base_entity_list.return_value = []
        self.first.potion_return_context['returned_snapshot'] = (42, 7, 'Talk')
        await utils.observe_party_area(self.first)
        self.first.potion_return_context['returned_area_token'] = await utils._party_area_token(self.first)

    async def test_verified_red_return_and_uninterrupted_peer_restore_far_party(self):
        await self.prepare_return_proof()
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def test_peer_loading_invalidates_saved_proof_even_in_same_named_zone(self):
        await self.prepare_return_proof()
        self.second.is_loading.return_value = True
        await utils.observe_party_area(self.second)
        self.second.is_loading.return_value = False
        await utils.observe_party_area(self.second)
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_peer_area_object_replaced_rejects_old_proof(self):
        await self.prepare_return_proof()
        area = await self.second.client_object.client_zone()
        area.read_base_address.return_value = 9999
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_returning_client_leaves_again_invalidates_old_return(self):
        await self.prepare_return_proof()
        self.first.zone_name.return_value = 'Outside'
        await utils.observe_party_area(self.first)
        self.first.zone_name.return_value = 'Dungeon/Room'
        await utils.observe_party_area(self.first)
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_no_predeparture_peer_evidence_does_not_authorize_sync(self):
        await self.prepare_return_proof()
        self.first.potion_return_context['peer_areas'].clear()
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_hitter_departure_records_assigned_quester(self):
        self.second.quest_party_quester = self.first
        self.second.get_base_entity_list.return_value = [self.first.client_object]
        with patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=(42, 7, 'Talk'))):
            await utils.prepare_potion_dungeon_return(self.second, 'Dungeon/Room')
        self.assertIn(id(self.first), self.second.potion_return_context['peer_areas'])

    async def test_manual_xyz_sync_cannot_bypass_live_area_gate(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'xyz_sync')
        namespace = dict(Client=object, asyncio=asyncio, logger=Mock(),
                         clients_share_live_area=AsyncMock(return_value=False))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        await namespace['xyz_sync'](self.first, [self.second], turn_after=False)
        self.second.teleport.assert_not_awaited()
        self.second.send_key.assert_not_awaited()

    async def test_automatic_battle_sync_checks_instance_before_coordinates(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'attempt_battle_coordinate_sync')
        namespace = dict(asyncio=asyncio, hitter=self.second, quester=self.first,
                         hitter_is_in_quester_area=AsyncMock(return_value=False),
                         hitter_near_quester=AsyncMock(return_value=True))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        self.assertFalse(await namespace['attempt_battle_coordinate_sync']('Dungeon/Room'))
        self.second.teleport.assert_not_awaited()
        self.first.body.position.assert_not_awaited()

    async def test_loading_after_xyz_read_prevents_manual_sync(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'xyz_sync')
        namespace = dict(Client=object, asyncio=asyncio, logger=Mock(),
                         clients_share_live_area=AsyncMock(return_value=True))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        async def position():
            self.first.is_loading.return_value = True
            return 'XYZ'
        self.first.body.position.side_effect = position
        await namespace['xyz_sync'](self.first, [self.second], turn_after=False)
        self.second.teleport.assert_not_awaited()

    async def test_return_confirmation_timeout_keeps_follower_alive(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        block = next(node for node in ast.walk(tree)
                     if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                     and node.test.id == 'return_markers')
        # Execute the actual return gate inside one iteration, so its continue
        # path can be tested without replacing the full follower state machine.
        loop = ast.For(target=ast.Name(id='_', ctx=ast.Store()),
                       iter=ast.List(elts=[ast.Constant(1)], ctx=ast.Load()),
                       body=[block], orelse=[])
        function = ast.AsyncFunctionDef(name='gate', args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[loop], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        self.second.questing_status = True
        status = Mock()
        namespace = dict(hitter=self.second, quester=self.first, logger=Mock(), now=31,
                         quester_zone='Dungeon/Room', hitter_zone='Dungeon/Room',
                         return_markers=[('Dungeon/Room', 0, {id(self.second)})],
                         hitter_is_in_quester_area=AsyncMock(return_value=False),
                         update_party_status=status)
        exec(compile(module, 'XuanShu.py', 'exec'), namespace)
        await namespace['gate']()
        self.assertTrue(self.second.questing_status)
        status.assert_called_once_with(self.second, self.first, '等待确认原副本，可手动归队')
        self.second.teleport.assert_not_awaited()

    async def test_unreadable_first_entity_tree_still_checks_other_client(self):
        self.first.get_base_entity_list.side_effect = MemoryInvalidated('old tree')
        self.second.get_base_entity_list.return_value = [self.first.client_object]
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))


if __name__ == '__main__':
    unittest.main()
