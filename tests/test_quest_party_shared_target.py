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
from src.paths import advance_dialog_path


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
            quest_party_target_sync_active=False,
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

    async def test_unproved_hitter_instance_does_not_block_quester_or_move_hitter(self):
        self.proof.return_value = False
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)

    async def test_busy_or_departed_hitter_is_skipped_without_pausing_quester(self):
        for attr, value in (('refilling_potions', True), ('questing_status', False),
                            ('quest_recovery_owner', 'potion_refill'),
                            ('quest_party_target_sync_active', True),
                            ('post_combat_cleanup_active', True),
                            ('potion_dungeon_returned', ('Dungeon/Room', 100, set()))):
            with self.subTest(attr=attr):
                previous = getattr(self.hitter, attr, False)
                setattr(self.hitter, attr, value)
                self.quester.teleport_to_quest_target.reset_mock()
                self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
                self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)
                setattr(self.hitter, attr, previous)
        self.hitter.zone_name.return_value = 'Other'
        self.quester.teleport_to_quest_target.reset_mock()
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)

    async def test_source_post_combat_cleanup_does_not_start_group_movement(self):
        self.quester_client.post_combat_cleanup_active = True
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_transition_during_optional_hitter_proof_skips_only_hitter(self):
        async def proof(*args):
            self.quester_client.is_loading.return_value = True
            return True
        self.proof.side_effect = proof
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)
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

    async def test_recovery_started_after_leader_move_during_optional_proof_skips_hitter(self):
        async def proof(*args):
            self.quester_client.quest_recovery_owner = 'lemuria_dungeon'
            return True
        self.proof.side_effect = proof
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)
        self.assertEqual(self.quester_client.quest_recovery_owner, 'lemuria_dungeon')
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)

    async def test_hitter_failure_does_not_cancel_quester_and_releases_its_flags(self):
        started = asyncio.Event()
        failed = asyncio.Event()

        async def move(member, *args, **kwargs):
            if member is self.hitter:
                await started.wait()
                failed.set()
                raise ValueError('rejected')
            started.set()
            await failed.wait()

        self.quester.teleport_to_quest_target.side_effect = move
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.assertTrue(failed.is_set())
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)
        self.assertIsNone(self.quester_client.quest_recovery_owner)
        self.assertIsNone(self.hitter.quest_recovery_owner)

    async def test_slow_hitter_presence_read_does_not_delay_quester(self):
        cancelled = asyncio.Event()
        async def blocked(*_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.proof.side_effect = blocked
        self.assertTrue(await asyncio.wait_for(self.quester.teleport_party_to_quest_target(self.target), .2))
        self.assertTrue(cancelled.is_set())
        self.quester.teleport_to_quest_target.assert_awaited_once_with(self.quester_client, self.target, leader_client=self.quester_client)

    async def test_quester_own_busy_state_still_blocks_its_input(self):
        for attr, value in (('refilling_potions', True), ('questing_status', False),
                            ('quest_recovery_owner', 'potion_refill'),
                            ('potion_dungeon_returned', ('Dungeon/Room', 100, set()))):
            previous = getattr(self.quester_client, attr)
            setattr(self.quester_client, attr, value)
            self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
            setattr(self.quester_client, attr, previous)
        self.quester.teleport_to_quest_target.assert_not_awaited()

    async def test_goal_change_cancels_both_old_movements(self):
        identity = [99, 7]
        self.quester_client.quest_id = AsyncMock(side_effect=lambda: identity[0])
        self.quester_client.goal_id = AsyncMock(side_effect=lambda: identity[1])
        started = []
        cancelled = []
        both = asyncio.Event()
        async def move(member, *_args, **_kwargs):
            started.append(member.title)
            if len(started) == 2:
                identity[1] = 8
                both.set()
            try:
                await both.wait()
                await asyncio.Event().wait()
            finally:
                cancelled.append(member.title)
        self.quester.teleport_to_quest_target.side_effect = move
        self.assertFalse(await asyncio.wait_for(self.quester.teleport_party_to_quest_target(self.target), 1))
        self.assertCountEqual(cancelled, ['p1', 'p2'])
        self.assertFalse(self.quester_client.quest_party_target_sync_active)
        self.assertFalse(self.hitter.quest_party_target_sync_active)

    async def test_quester_failure_still_cancels_outstanding_hitter_movement(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def move(member, *_args, **_kwargs):
            if member is self.quester_client:
                await started.wait()
                raise ValueError('quester move failed')
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.quester.teleport_to_quest_target.side_effect = move
        with self.assertRaises(ValueError):
            await self.quester.teleport_party_to_quest_target(self.target)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.hitter.quest_party_target_sync_active)

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
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
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

    async def test_auto_quest_uses_ready_interaction_before_reentry_or_shared_movement(self):
        client = self.quester_client
        client.use_potions = False
        client.auto_pet_status = False
        client.entity_detect_combat_status = False
        client.quest_id = AsyncMock(return_value=42)
        client.goal_id = AsyncMock(return_value=0)
        client.send_key = AsyncMock()
        client.body.position.return_value = self.target
        self.quester.read_popup = AsyncMock()
        self.quester.handle_party_dungeon_interaction = AsyncMock(return_value=False)
        self.quester.new_world_doors = AsyncMock(return_value=True)
        for name in (
            '_maybe_handle_overgrown_estate', '_maybe_handle_darkmoor_castle',
            '_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
            '_maybe_handle_bumbles_pet', 'handle_pending_dungeon_confirmation',
            '_maybe_recover_lemuria_navigation', '_quest_party_probe_blocks_movement',
            '_maybe_handle_no_blood_hideout', '_maybe_handle_bumbles_mind',
            '_maybe_handle_tamarin_house', '_maybe_handle_panopticon_book',
            '_maybe_handle_darkmoor_cantrips', '_mainline_sync_blocks_movement',
            '_maybe_enter_avalon_grain_map', '_maybe_handle_callisto',
            '_maybe_recover_mainline', '_maybe_photo_giant_vat',
            '_maybe_recover_nightmare', '_maybe_reenter_quest_trigger',
            '_maybe_refresh_stalled_dungeon_quest',
        ):
            setattr(self.quester, name, AsyncMock(return_value=False))
        peer_started = asyncio.Event()
        async def other_group():
            peer_started.set()
            await asyncio.Future()
        peer = asyncio.create_task(other_group())
        await peer_started.wait()
        try:
            with ExitStack() as stack:
                for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_potion_needed'):
                    stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
                stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
                stack.enter_context(patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)))
                stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)))
                stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
                door = stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock()))
                objective = stack.enter_context(patch('src.questing.get_quest_name', AsyncMock()))
                title = stack.enter_context(patch('src.questing.get_popup_title', AsyncMock()))
                for text, name, prompt, world in (
                    ('Collect Mushrooms in Forest', 'Mushrooms', 'Press X to Collect', False),
                    ('Use Chest in Forest', 'Chest', 'Press X to Open', False),
                    ('Go To Boat in Harbor', 'Boat', 'Press X to Enter Boat', False),
                    ('使用 魔法艇 地点：湖边', '魔法艇', '按下 X 使用魔法艇', False),
                    ('前往 马利骨 地点：公共区', '世界之门', '按下 X 传送', True),
                ):
                    with self.subTest(prompt=prompt):
                        door.side_effect = [False, world]
                        objective.return_value = text
                        title.return_value = name
                        self.quester.read_popup.return_value = prompt
                        client.send_key.reset_mock()
                        self.quester._trigger_reentry[id(client)] = {'old': True}
                        await self.quester.auto_quest_solo()
                        client.send_key.assert_awaited_once()
                        self.assertNotIn(id(client), self.quester._trigger_reentry)
                        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()
                        self.quester.teleport_to_quest_target.assert_not_awaited()
                        self.assertFalse(peer.done())
                self.quester.new_world_doors.assert_awaited_once_with(client)
                self.hitter.quest_position.position.assert_not_awaited()
                self.hitter.body.position.assert_not_awaited()
        finally:
            peer.cancel()
            await asyncio.gather(peer, return_exceptions=True)


class SameInstanceFollowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.function = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.AsyncFunctionDef)
                            and node.name == '_follow_quester_session')

    async def run_follow(self, proof, teleport_error=None, *, quest_ids=None, in_battle=True, distance=2000, interaction=None, recovery_owner=None, group_confirmed=True, friend_error=None, source_free=True, source_dialogue=False, source_loading=False, source_refill=False, character_selection=False, probe_pending=False, primary=False, extra_hitter=False):
        hitter = ClientState(title='p2', questing_status=True, quest_party_battle_sync_state='failed',
            entity_detect_combat_status=False, just_entered_combat=None,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=distance)),
            teleport=AsyncMock(side_effect=teleport_error), mouse_handler=AsyncMock())
        quester = ClientState(title='p1', questing_status=True, in_solo_zone=False,
            quest_party_observed_zone='Dungeon/Room', quest_party_quest_worker_zone='Dungeon/Room',
            quest_party_probe_pending=probe_pending, quest_party_group_dungeon_zone='Dungeon/Room' if group_confirmed else None,
            quest_recovery_owner=recovery_owner, wizard_name='Wizard',
            refilling_potions=source_refill, _character_selection_active=character_selection,
            is_loading=AsyncMock(return_value=source_loading), in_battle=AsyncMock(return_value=in_battle),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=0)))
        quester.quest_party_dungeon_interaction = interaction
        quester.quest_party_hitters = [hitter]
        if extra_hitter:
            quester.quest_party_hitters.append(ClientState(title='p5', questing_status=True,
                is_loading=AsyncMock(return_value=False),
                zone_name=AsyncMock(return_value='Dungeon/PreviousRoom')))
        self.follow_quester = quester
        if quest_ids is not None:
            quester.quest_id = AsyncMock(side_effect=quest_ids)
            quester.goal_id = AsyncMock(return_value=7)
        reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
                                 _resume_party_dungeon_interaction=AsyncMock(return_value=bool(interaction and not interaction.get('warned'))),
                                 get_truncated_quest_objectives=AsyncMock(return_value='Defeat boss'))
        friend = AsyncMock(side_effect=friend_error)
        status = Mock()
        clock = Mock(time=Mock(side_effect=range(100, 10000, 100)))
        namespace = dict(Client=object, asyncio=asyncio, time=SimpleNamespace(time=lambda: 0),
            members=None, questing_status=True, use_potions=False, logger=Mock(),
            walker=SimpleNamespace(clients=[quester, hitter]),
            Quester=Mock(return_value=reader, overgrown_estate_paused=Quester.overgrown_estate_paused),
            observe_party_area=AsyncMock(), close_automation_popup=AsyncMock(return_value=False),
            clients_share_live_area=AsyncMock(return_value=proof),
            is_free=AsyncMock(side_effect=lambda client: source_free if client is quester else True),
            update_party_status=status, remove_party_status=Mock(),
            claim_quest_recovery=claim_quest_recovery, release_quest_recovery=release_quest_recovery,
            calc_Distance=lambda a, b: abs(a - b), gear_switching_in_solo_zones=False,
            is_visible_by_path=AsyncMock(side_effect=lambda client, path:
                source_dialogue and client is quester and path == advance_dialog_path),
            advance_dialog_path=advance_dialog_path, spiral_door_teleport_path=[],
            resolve_quester_friend_icon=lambda *args: None, quest_friend_icons={},
            teleport_to_friend_from_list=friend, is_friend_teleport_error=AsyncMock(return_value=False),
            close_friend_windows=AsyncMock(return_value=False),
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
            await namespace['_follow_quester_session'](hitter, quester, primary)
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

    async def test_source_dialogue_allows_independent_friend_probe(self):
        hitter, friend, status = await self.run_follow(False, in_battle=False,
            source_free=False, source_dialogue=True, probe_pending=True, primary=True,
            group_confirmed=False, friend_error=ValueError('friend list not ready'))
        friend.assert_awaited()
        hitter.teleport.assert_not_awaited()
        self.assertTrue(self.follow_quester.quest_party_probe_pending)
        self.assertFalse(self.follow_quester.in_solo_zone)
        self.assertIn('正在好友传送', [call.args[2] for call in status.call_args_list])

    async def test_primary_arrival_does_not_release_gate_before_another_assigned_hitter(self):
        hitter, _, _ = await self.run_follow(True, in_battle=False,
            probe_pending=True, primary=True, group_confirmed=False, extra_hitter=True)
        self.assertTrue(self.follow_quester.quest_party_probe_pending)
        self.assertFalse(self.follow_quester.in_solo_zone)
        hitter.teleport.assert_awaited()

    async def test_secondary_hitter_can_catch_up_while_primary_probe_is_pending(self):
        hitter, _, status = await self.run_follow(True, in_battle=False,
            probe_pending=True, primary=False, group_confirmed=False)
        hitter.teleport.assert_awaited()
        self.assertTrue(self.follow_quester.quest_party_probe_pending)
        self.assertNotIn('等待单人区域探测', [call.args[2] for call in status.call_args_list])

    async def test_non_dialogue_busy_source_still_blocks_friend_probe(self):
        hitter, friend, _ = await self.run_follow(False, in_battle=False,
            source_free=False, probe_pending=True, primary=True, group_confirmed=False)
        friend.assert_not_awaited()
        hitter.teleport.assert_not_awaited()
        self.assertTrue(self.follow_quester.quest_party_probe_pending)

    async def test_dialogue_follow_keeps_loading_refill_and_recovery_guards(self):
        for extra in ({'source_loading': True}, {'source_refill': True},
                      {'character_selection': True}, {'recovery_owner': 'nightmare_krok'}):
            with self.subTest(guard=extra):
                hitter, friend, _ = await self.run_follow(False, in_battle=False,
                    source_free=False, source_dialogue=True, probe_pending=True,
                    primary=True, group_confirmed=False, **extra)
                friend.assert_not_awaited()
                hitter.teleport.assert_not_awaited()
                self.assertTrue(self.follow_quester.quest_party_probe_pending)

    async def test_dialogue_battle_does_not_enable_unconfirmed_coordinate_sync(self):
        hitter, friend, _ = await self.run_follow(False, in_battle=True,
            source_free=False, source_dialogue=True, group_confirmed=False,
            probe_pending=True, primary=True)
        friend.assert_not_awaited()
        hitter.teleport.assert_not_awaited()

    async def test_task_change_follows_quester_coordinates_even_inside_old_distance_limit(self):
        hitter, friend, _ = await self.run_follow(True, quest_ids=[99, 100, 100], in_battle=False, distance=400)
        hitter.teleport.assert_awaited_once_with(0)
        friend.assert_not_awaited()

    async def test_active_split_recovery_runs_on_follower_loop_only(self):
        hitter, friend, status = await self.run_follow(False, interaction={'phase': 'transition', 'warned': False})
        friend.assert_not_awaited()
        hitter.teleport.assert_not_awaited()
        self.assertEqual([call.args[2] for call in status.call_args_list][1:],
                         ['打手正在补跟切区，本组等待区域同步'] * 3)

    async def test_old_prompt_wait_no_longer_blocks_local_follow_or_battle_rescue(self):
        hitter, friend, _ = await self.run_follow(True, in_battle=False, interaction={'phase': 'waiting'})
        friend.assert_not_awaited()
        self.assertEqual(hitter.teleport.await_count, 3)
        hitter, friend, _ = await self.run_follow(True, in_battle=True, interaction={'phase': 'waiting'})
        self.assertEqual(hitter.teleport.await_count, 3)
        friend.assert_not_awaited()

    async def test_expired_known_party_transition_retries_friend_without_classifying_quester_solo(self):
        hitter, friend, status = await self.run_follow(False, in_battle=False,
            interaction={'phase': 'transition', 'warned': True}, group_confirmed=False,
            friend_error=FriendBusyOrInstanceClosed())
        self.assertGreater(friend.await_count, 0, [call.args[2] for call in status.call_args_list])
        hitter.teleport.assert_not_awaited()
        self.assertFalse(self.follow_quester.in_solo_zone)
        self.assertNotIn('单人区域，任务端独立执行', [call.args[2] for call in status.call_args_list])

    async def test_prompt_wait_does_not_block_existing_special_recovery_collaboration(self):
        hitter, friend, status = await self.run_follow(True, in_battle=False,
            interaction={'phase': 'waiting'}, recovery_owner='nightmare_krok')
        self.assertEqual([call.args[2] for call in status.call_args_list][1:],
                         ['等待 NightmareKrok 恢复'] * 3)
        friend.assert_not_awaited()
        hitter.teleport.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
