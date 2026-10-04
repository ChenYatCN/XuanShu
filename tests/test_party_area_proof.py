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
        in_battle=AsyncMock(return_value=False),
        duel=SimpleNamespace(duel_id_full=AsyncMock(return_value=50),
                             participant_list=AsyncMock(return_value=[])),
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

    def prepare_shared_battle(self):
        for observer in (self.first, self.second):
            observer.in_battle.return_value = True
            observer.duel.participant_list.return_value = [
                SimpleNamespace(owner_id_full=AsyncMock(return_value=gid),
                                is_player=AsyncMock(return_value=True))
                for gid in (1, 2)
            ]

    async def test_same_live_battle_proves_area_without_rendered_entities(self):
        self.prepare_shared_battle()
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def test_same_zone_different_battles_do_not_prove_area(self):
        self.prepare_shared_battle()
        self.second.duel.duel_id_full.return_value = 51
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_invalid_duel_id_does_not_prove_area(self):
        self.prepare_shared_battle()
        for value in (0, None, True):
            with self.subTest(value=value):
                self.first.duel.duel_id_full.return_value = value
                self.second.duel.duel_id_full.return_value = value
                self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_both_rosters_must_contain_both_players(self):
        self.prepare_shared_battle()
        self.second.duel.participant_list.return_value.pop()
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_nonplayer_owner_ids_do_not_prove_shared_battle(self):
        self.prepare_shared_battle()
        self.second.duel.participant_list.return_value[1].is_player.return_value = False
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_stale_participant_does_not_hide_valid_battle_roster(self):
        self.prepare_shared_battle()
        self.first.duel.participant_list.return_value.insert(0, SimpleNamespace(
            is_player=AsyncMock(side_effect=MemoryInvalidated('old participant'))))
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def test_battle_transition_during_roster_read_rejects_proof(self):
        for change in ('loading', 'zone', 'duel', 'ended'):
            with self.subTest(change=change):
                self.setUp()
                self.prepare_shared_battle()
                roster = self.second.duel.participant_list.return_value
                async def read_roster():
                    if change == 'loading':
                        self.second.is_loading.return_value = True
                    elif change == 'zone':
                        self.second.zone_name.return_value = 'Other'
                    elif change == 'duel':
                        self.second.duel.duel_id_full.return_value = 51
                    else:
                        self.second.in_battle.return_value = False
                    return roster
                self.second.duel.participant_list.side_effect = read_roster
                self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_one_client_not_in_battle_does_not_use_roster_proof(self):
        self.prepare_shared_battle()
        self.second.in_battle.return_value = False
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_unreadable_battle_can_still_use_verified_return_proof(self):
        await self.prepare_return_proof()
        self.prepare_shared_battle()
        self.first.duel.participant_list.side_effect = MemoryInvalidated('old duel')
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))

    async def run_manual_sync(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'xyz_sync')
        namespace = dict(Client=object, asyncio=SimpleNamespace(
            gather=asyncio.gather, sleep=AsyncMock()), logger=Mock(),
            clients_share_live_area=utils.clients_share_live_area)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        await namespace['xyz_sync'](self.first, [self.second], turn_after=False)
        return namespace['logger']

    async def test_manual_sync_accepts_shared_battle_with_no_entities(self):
        self.prepare_shared_battle()
        await self.run_manual_sync()
        self.second.teleport.assert_awaited_once_with('XYZ', yaw=0)

    async def test_recovery_and_refill_remain_blocked_and_report_exact_cause(self):
        for actor in ('first', 'second'):
            for attr, value in (('refilling_potions', True), ('quest_recovery_owner', 'test_recovery')):
                with self.subTest(actor=actor, attr=attr):
                    self.setUp()
                    self.prepare_shared_battle()
                    setattr(getattr(self, actor), attr, value)
                    log = await self.run_manual_sync()
                    self.second.teleport.assert_not_awaited()
                    self.first.get_base_entity_list.assert_not_awaited()
                    warning = log.warning.call_args.args[0]
                    self.assertIn(getattr(self, actor).title, warning)
                    self.assertIn('补药' if attr == 'refilling_potions' else 'test_recovery', warning)

    async def test_recovery_started_during_coordinate_read_prevents_sync(self):
        self.prepare_shared_battle()
        async def position():
            self.second.quest_recovery_owner = 'test_recovery'
            return 'XYZ'
        self.first.body.position.side_effect = position
        await self.run_manual_sync()
        self.second.teleport.assert_not_awaited()

    async def test_equal_zone_and_zone_id_are_not_instance_proof(self):
        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))

    async def test_confirmed_area_survives_disappearing_battle_entities(self):
        self.first.get_base_entity_list.return_value = [self.second.client_object]
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))
        self.first.get_base_entity_list.return_value = []
        self.first.in_battle.return_value = True
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))
        self.assertTrue(await utils.clients_share_live_area(self.second, self.first))

    async def test_retained_area_requires_unchanged_tokens_on_both_clients(self):
        for actor in ('first', 'second'):
            for change in ('address', 'zone_id', 'loading', 'zone'):
                with self.subTest(actor=actor, change=change):
                    self.setUp()
                    self.first.get_base_entity_list.return_value = [self.second.client_object]
                    self.assertTrue(await utils.clients_share_live_area(self.first, self.second))
                    self.first.get_base_entity_list.return_value = []
                    member = getattr(self, actor)
                    area = await member.client_object.client_zone()
                    if change == 'address':
                        area.read_base_address.return_value = 9999
                    elif change == 'zone_id':
                        area.zone_id.return_value = 999
                    elif change == 'loading':
                        member.is_loading.return_value = True
                        self.assertFalse(await utils.clients_share_live_area(self.first, self.second))
                        member.is_loading.return_value = False
                    else:
                        member.zone_name.return_value = 'Outside'
                        await utils.observe_party_area(member)
                        member.zone_name.return_value = 'Dungeon/Room'
                    self.assertFalse(await utils.clients_share_live_area(self.first, self.second))
                    self.assertFalse(await utils.clients_share_live_area(self.second, self.first))

    async def test_unreadable_area_tokens_cannot_retain_presence(self):
        self.first.get_base_entity_list.return_value = [self.second.client_object]
        area = await self.first.client_object.client_zone()
        area.read_base_address.return_value = None
        self.assertTrue(await utils.clients_share_live_area(self.first, self.second))
        self.first.get_base_entity_list.return_value = []
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
        # A real refill leaves and returns; it invalidates ordinary live proof.
        self.first.zone_name.return_value = 'Shop'
        await utils.observe_party_area(self.first)
        self.first.zone_name.return_value = 'Dungeon/Room'
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

    async def test_manual_xyz_sync_does_not_require_live_area_proof(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'xyz_sync')
        namespace = dict(Client=object, asyncio=asyncio, logger=Mock(),
                         clients_share_live_area=AsyncMock(return_value=False))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        await namespace['xyz_sync'](self.first, [self.second], turn_after=False)
        self.second.teleport.assert_awaited_once_with('XYZ', yaw=0)
        self.second.send_key.assert_not_awaited()
        namespace['clients_share_live_area'].assert_not_awaited()

    async def test_manual_sync_does_not_compare_target_zone_or_battle(self):
        self.second.zone_name.return_value = 'Different/Zone'
        self.second.duel.duel_id_full.side_effect = MemoryInvalidated('unreadable duel')
        await self.run_manual_sync()
        self.second.teleport.assert_awaited_once_with('XYZ', yaw=0)
        self.first.get_base_entity_list.assert_not_awaited()
        self.second.get_base_entity_list.assert_not_awaited()
        self.second.duel.duel_id_full.assert_not_awaited()
        self.second.zone_name.assert_not_awaited()

    async def test_source_transition_during_coordinate_read_still_prevents_sync(self):
        async def position():
            self.first.zone_name.return_value = 'New/Zone'
            return 'XYZ'
        self.first.body.position.side_effect = position
        await self.run_manual_sync()
        self.second.teleport.assert_not_awaited()

    async def test_manual_sync_without_foreground_keeps_turning_selected_peers_only(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'xyz_sync')
        namespace = dict(Client=object, asyncio=SimpleNamespace(
            gather=asyncio.gather, sleep=AsyncMock()), logger=Mock(),
            Keycode=SimpleNamespace(A='A', D='D'),
            clients_share_live_area=AsyncMock(side_effect=AssertionError('manual proof must not run')))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        self.second.zone_name.return_value = 'Different/Zone'
        await namespace['xyz_sync'](None, [self.first, self.second], turn_after=True)
        self.first.teleport.assert_not_awaited()
        self.first.send_key.assert_not_awaited()
        self.second.teleport.assert_awaited_once_with('XYZ', yaw=0)
        self.assertEqual([call.kwargs['key'] for call in self.second.send_key.await_args_list], ['A', 'D'])

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
