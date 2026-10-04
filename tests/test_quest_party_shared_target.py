import ast
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src.questing import Quester, claim_quest_recovery, release_quest_recovery
from src.utils import FriendBusyOrInstanceClosed


class ClientState(SimpleNamespace):
    __eq__ = object.__eq__
    __ne__ = object.__ne__


class SharedQuestTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000, 2000, 3000)
        self.quester_client = self.client('p1')
        self.hitter = self.client('p2')
        self.quester_client.quest_party_hitters = [self.hitter]
        self.quester_client.quest_party_group_dungeon_zone = 'Dungeon/Room'
        self.quester = Quester(self.quester_client, [self.quester_client], None)
        self.quester.teleport_to_quest_target = AsyncMock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.proof = stack.enter_context(patch('src.questing.clients_share_live_area', AsyncMock(return_value=True)))
        self.free = stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))

    def client(self, title):
        return ClientState(title=title, questing_status=True, refilling_potions=False,
            quest_recovery_owner=None, in_solo_zone=False, potion_dungeon_returned=None,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))))

    async def test_both_start_concurrently_with_same_target_and_quester_context(self):
        started = []
        both_started = asyncio.Event()

        async def move(member, xyz, leader_client=None):
            self.assertIs(xyz, self.target)
            self.assertIs(leader_client, self.quester_client)
            self.assertTrue(member.quest_party_target_sync_active)
            started.append(member.title)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        self.quester.teleport_to_quest_target.side_effect = move
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.assertEqual(started, ['p1', 'p2'])
        self.hitter.quest_position.position.assert_not_awaited()
        self.quester_client.body.position.assert_not_awaited()
        self.assertIsNone(self.quester_client.quest_recovery_owner)
        self.assertIsNone(self.hitter.quest_recovery_owner)
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)

    async def test_existing_target_recovery_can_still_claim_ownership(self):
        async def move(member, *args, **kwargs):
            self.assertTrue(claim_quest_recovery(member, 'lemuria_dungeon'))
            release_quest_recovery(member, 'lemuria_dungeon')

        self.quester.teleport_to_quest_target.side_effect = move
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))

    async def test_unproved_same_name_instance_does_not_move_either_client(self):
        self.proof.return_value = False
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_busy_or_departed_hitter_pauses_both_clients(self):
        for attr, value in (('refilling_potions', True), ('questing_status', False),
                            ('quest_recovery_owner', 'potion_refill'),
                            ('quest_party_target_sync_active', True),
                            ('potion_dungeon_returned', ('Dungeon/Room', 100, set()))):
            with self.subTest(attr=attr):
                previous = getattr(self.hitter, attr, False)
                setattr(self.hitter, attr, value)
                self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
                setattr(self.hitter, attr, previous)
        self.hitter.zone_name.return_value = 'Other'
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_transition_during_proof_prevents_either_teleport(self):
        async def proof(*args):
            self.quester_client.is_loading.return_value = True
            return True
        self.proof.side_effect = proof
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_not_awaited()
        self.assertIsNone(self.quester_client.quest_recovery_owner)
        self.assertIsNone(self.hitter.quest_recovery_owner)

    async def test_normal_or_solo_zone_keeps_existing_quester_only_move(self):
        for attr, value in (('in_solo_zone', True), ('quest_party_group_dungeon_zone', None)):
            with self.subTest(attr=attr):
                previous = getattr(self.quester_client, attr)
                setattr(self.quester_client, attr, value)
                self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
                self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target)
                self.quester.teleport_to_quest_target.reset_mock()
                setattr(self.quester_client, attr, previous)
        self.proof.assert_not_awaited()

    async def test_recovery_started_during_proof_prevents_both_moves(self):
        async def proof(*args):
            self.quester_client.quest_recovery_owner = 'lemuria_dungeon'
            return True
        self.proof.side_effect = proof
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_not_awaited()
        self.assertEqual(self.quester_client.quest_recovery_owner, 'lemuria_dungeon')
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)

    async def test_failure_cancels_sibling_and_releases_both_owners(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def move(member, *args, **kwargs):
            if member is self.hitter:
                await started.wait()
                raise ValueError('rejected')
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.quester.teleport_to_quest_target.side_effect = move
        with self.assertRaises(ValueError):
            await self.quester.teleport_party_to_quest_target(self.target)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)
        self.assertIsNone(self.quester_client.quest_recovery_owner)
        self.assertIsNone(self.hitter.quest_recovery_owner)

    async def test_cancellation_releases_both_owners(self):
        self.quester.teleport_to_quest_target.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.teleport_party_to_quest_target(self.target)
        self.assertIsNone(self.quester_client.quest_recovery_owner)
        self.assertIsNone(self.hitter.quest_recovery_owner)
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)

    async def test_auto_quest_reads_only_p1_target_and_runs_shared_movement(self):
        client = self.quester_client
        client.use_potions = False
        client.auto_pet_status = False
        client.entity_detect_combat_status = False
        for name in (
            '_maybe_handle_overgrown_estate', '_maybe_handle_darkmoor_castle',
            '_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
            '_maybe_handle_bumbles_pet', 'handle_pending_dungeon_confirmation',
            '_maybe_recover_lemuria_navigation', '_quest_party_probe_blocks_movement',
            '_maybe_handle_no_blood_hideout', '_maybe_handle_bumbles_mind',
            '_maybe_handle_tamarin_house', '_maybe_handle_panopticon_book',
            '_maybe_handle_darkmoor_cantrips', '_mainline_sync_blocks_movement',
            '_maybe_handle_callisto', '_maybe_recover_mainline', '_maybe_photo_giant_vat',
            '_maybe_recover_nightmare', '_maybe_reenter_quest_trigger',
            '_maybe_refresh_stalled_dungeon_quest',
        ):
            setattr(self.quester, name, AsyncMock(return_value=False))
        self.quester.overgrown_estate_paused = Mock(return_value=True)
        with ExitStack() as stack:
            for name in ('close_npc_quest_menu', 'close_automation_popup',
                         'is_spiral_door_open', 'is_potion_needed'):
                stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
            stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
            await self.quester.auto_quest_solo()
        client.quest_position.position.assert_awaited_once()
        self.hitter.quest_position.position.assert_not_awaited()
        self.assertEqual(self.quester.teleport_to_quest_target.await_count, 2)
        for call in self.quester.teleport_to_quest_target.await_args_list:
            self.assertIs(call.args[1], self.target)
            self.assertIs(call.kwargs['leader_client'], client)


class SameInstanceFollowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.function = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.AsyncFunctionDef)
                            and node.name == '_follow_quester_session')

    async def run_follow(self, proof, teleport_error=None):
        hitter = ClientState(title='p2', questing_status=True, quest_party_battle_sync_state='failed',
            entity_detect_combat_status=False, just_entered_combat=None,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=2000)),
            teleport=AsyncMock(side_effect=teleport_error), mouse_handler=AsyncMock())
        quester = ClientState(title='p1', questing_status=True, in_solo_zone=False,
            quest_party_observed_zone='Dungeon/Room', quest_party_quest_worker_zone='Dungeon/Room',
            quest_party_probe_pending=False, quest_party_group_dungeon_zone='Dungeon/Room',
            quest_recovery_owner=None, wizard_name='Wizard',
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=True),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=0)))
        reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
                                 get_truncated_quest_objectives=AsyncMock(return_value='Defeat boss'))
        friend = AsyncMock()
        status = Mock()
        clock = Mock(time=Mock(side_effect=range(100, 10000, 100)))
        namespace = dict(Client=object, asyncio=asyncio, time=SimpleNamespace(time=lambda: 0),
            members=None, questing_status=True, use_potions=False, logger=Mock(),
            walker=SimpleNamespace(clients=[quester, hitter]),
            Quester=Mock(return_value=reader, overgrown_estate_paused=Quester.overgrown_estate_paused),
            observe_party_area=AsyncMock(), close_automation_popup=AsyncMock(return_value=False),
            clients_share_live_area=AsyncMock(return_value=proof), is_free=AsyncMock(return_value=True),
            update_party_status=status, remove_party_status=Mock(),
            claim_quest_recovery=claim_quest_recovery, release_quest_recovery=release_quest_recovery,
            calc_Distance=lambda a, b: abs(a - b), gear_switching_in_solo_zones=False,
            is_visible_by_path=AsyncMock(return_value=False), spiral_door_teleport_path=[],
            resolve_quester_friend_icon=lambda *args: None, quest_friend_icons={},
            teleport_to_friend_from_list=friend, is_friend_teleport_error=AsyncMock(return_value=False),
            FriendBusyOrInstanceClosed=FriendBusyOrInstanceClosed,
            friend_follow_retry_delay=lambda failures: 5)
        exec(compile(ast.Module(body=[self.function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        ticks = 0

        async def tick(delay):
            nonlocal ticks
            if delay == 0.5:
                ticks += 1
                if ticks > 3:
                    hitter.questing_status = False

        with patch.object(asyncio, 'sleep', tick), \
             patch.object(asyncio, 'get_running_loop', return_value=clock):
            await namespace['_follow_quester_session'](hitter, quester, False)
        return hitter, friend, status

    async def test_failed_battle_sync_stays_local_in_confirmed_instance(self):
        hitter, friend, status = await self.run_follow(True)
        friend.assert_not_awaited()
        self.assertEqual(hitter.teleport.await_count, 3)
        hitter.teleport.assert_awaited_with(0)
        self.assertNotIn('正在好友传送', [call.args[2] for call in status.call_args_list])

    async def test_local_teleport_rejection_never_falls_back_to_friend(self):
        hitter, friend, _ = await self.run_follow(True, ValueError('rejected'))
        friend.assert_not_awaited()
        self.assertEqual(hitter.teleport.await_count, 3)

    async def test_unconfirmed_instance_still_allows_existing_friend_recovery(self):
        hitter, friend, status = await self.run_follow(False)
        hitter.teleport.assert_not_awaited()
        self.assertIn('正在好友传送', [call.args[2] for call in status.call_args_list])
        friend.assert_awaited()


if __name__ == '__main__':
    unittest.main()
