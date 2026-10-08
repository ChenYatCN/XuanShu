import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker.memory.memory_objects.enums import DuelPhase
from src.questing import Quester, is_free_leader_questing, read_dialogue_text
from src.utils import reconcile_combat_state


def source_function(name, namespace):
    tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
    return namespace[name]


class ClientState(SimpleNamespace):
    __eq__ = object.__eq__
    __ne__ = object.__ne__


class CombatStateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.client = ClientState(
            title='p3', process_id=3, questing_status=True,
            entity_detect_combat_status=True, just_entered_combat=None,
            just_left_combat=True, invincible_combat_timer=True,
            client_being_helped=None, original_location_before_combat='old position',
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Khrysalis/Interiors/KR_Z12_I03_Galleries'),
            duel=SimpleNamespace(duel_phase=AsyncMock(return_value=DuelPhase.ended)),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7))
        for patcher in (
                patch('src.utils.time.monotonic', side_effect=lambda: self.now),
                patch('src.utils.time.time', side_effect=lambda: self.now)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def settle(self):
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 1.5
        return await reconcile_combat_state(self.client)

    async def test_readable_ended_duel_releases_only_own_stale_state(self):
        peer = ClientState(title='p4', entity_detect_combat_status=True)
        helped = ClientState(helper_clients=[self.client, peer])
        self.client.client_being_helped = helped
        locations = {3: 'old position', 4: 'other position'}
        self.assertFalse(await reconcile_combat_state(self.client, locations))
        self.now += 1.5
        self.assertTrue(await reconcile_combat_state(self.client, locations))
        self.assertFalse(self.client.entity_detect_combat_status)
        self.assertFalse(self.client.just_left_combat)
        self.assertFalse(self.client.invincible_combat_timer)
        self.assertIsNone(self.client.client_being_helped)
        self.assertIsNone(self.client.original_location_before_combat)
        self.assertEqual(locations, {4: 'other position'})
        self.assertEqual(helped.helper_clients, [peer])
        self.assertTrue(peer.entity_detect_combat_status)

    async def test_actual_battle_does_not_clear_detected_state(self):
        self.client.duel.duel_phase.return_value = DuelPhase.planning
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 100
        self.assertFalse(await reconcile_combat_state(self.client))
        self.assertTrue(self.client.entity_detect_combat_status)

    async def test_in_battle_false_from_failed_read_is_not_ended_proof(self):
        self.client.duel.duel_phase.side_effect = ValueError('duel address unreadable')
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 100
        self.assertFalse(await reconcile_combat_state(self.client))
        self.assertTrue(self.client.entity_detect_combat_status)
        self.assertIsNone(self.client._combat_ended_observation)

    async def test_pending_entry_keeps_seven_second_grace_then_needs_stability(self):
        self.client.just_entered_combat = self.now
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 6.9
        self.assertFalse(await reconcile_combat_state(self.client))
        self.assertTrue(self.client.entity_detect_combat_status)
        self.now += .2
        self.assertTrue(await self.settle())
        self.assertIsNone(self.client.just_entered_combat)

    async def test_loading_zone_change_and_battle_restart_stable_timer(self):
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 2
        self.client.is_loading.return_value = True
        self.assertFalse(await reconcile_combat_state(self.client))
        self.client.is_loading.return_value = False
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 2
        self.client.zone_name.return_value = 'Other/Room'
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 2
        self.client.duel.duel_phase.return_value = DuelPhase.planning
        self.assertFalse(await reconcile_combat_state(self.client))
        self.client.duel.duel_phase.return_value = DuelPhase.ended
        self.assertTrue(await self.settle())

    async def test_active_recovery_refill_and_input_owners_are_preserved(self):
        for name, value in (
                ('_character_selection_active', True), ('refilling_potions', True),
                ('quest_party_battle_rescue_active', True), ('quest_party_target_sync_active', True),
                ('post_combat_movement_active', True), ('post_combat_cleanup_active', True),
                ('quest_recovery_owner', 'potion_refill')):
            with self.subTest(guard=name):
                setattr(self.client, name, value)
                self.assertFalse(await reconcile_combat_state(self.client))
                self.now += 10
                self.assertFalse(await reconcile_combat_state(self.client))
                self.assertTrue(self.client.entity_detect_combat_status)
                delattr(self.client, name)

    async def test_new_entry_during_duel_read_is_not_cleared(self):
        self.assertFalse(await reconcile_combat_state(self.client))
        self.now += 2
        async def phase():
            self.client.just_entered_combat = self.now
            return DuelPhase.ended
        self.client.duel.duel_phase.side_effect = phase
        self.assertFalse(await reconcile_combat_state(self.client))
        self.assertTrue(self.client.entity_detect_combat_status)

    async def test_return_confirmation_marker_is_never_removed_by_state_repair(self):
        marker = ('Khrysalis/Interiors/KR_Z12_I03_Galleries', 10, {4})
        self.client.potion_dungeon_returned = marker
        self.assertTrue(await self.settle())
        self.assertIs(self.client.potion_dungeon_returned, marker)

    async def test_real_quest_gate_recovers_without_reinjection(self):
        quester = Quester(self.client, [self.client], None)
        quester._advance_npc_dialogue = AsyncMock(return_value=False)
        with patch('src.questing.read_dialogue_text', AsyncMock(return_value='')), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)):
            self.assertTrue(await quester._quest_dialogue_blocks_movement(self.client))
            self.now += 1.6
            self.assertTrue(await quester._quest_dialogue_blocks_movement(self.client))
            self.assertFalse(self.client.entity_detect_combat_status)
            self.now += 3.1
            self.assertFalse(await quester._quest_dialogue_blocks_movement(self.client))

    async def test_real_quest_gate_keeps_post_combat_cleanup_barrier(self):
        self.client.entity_detect_combat_status = False
        self.client.post_combat_cleanup_active = True
        with patch('src.questing.read_dialogue_text', AsyncMock(return_value='')), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)):
            self.assertFalse(await is_free_leader_questing(self.client))

    async def test_hidden_dialogue_or_hidden_parent_does_not_retain_old_text(self):
        window, parent = AsyncMock(), AsyncMock()
        self.client.root_window = object()
        window.maybe_text.return_value = 'old boss dialogue'
        window.get_parents.return_value = [parent]
        for own_visible, parent_visible, expected in ((False, True, ''), (True, False, ''),
                                                      (True, True, 'old boss dialogue')):
            with self.subTest(visible=(own_visible, parent_visible)):
                window.is_visible.return_value = own_visible
                parent.is_visible.return_value = parent_visible
                with patch('src.questing.get_window_from_path', AsyncMock(return_value=window)):
                    self.assertEqual(await read_dialogue_text(self.client), expected)

    async def test_restart_worker_calibrates_selected_client_before_questing(self):
        await reconcile_combat_state(self.client)
        self.now += 1.5
        locations = {3: 'old position', 1: 'unrelated group'}
        async def start(*args):
            self.assertFalse(self.client.entity_detect_combat_status)
            self.assertEqual(locations, {1: 'unrelated group'})
        quester = SimpleNamespace(auto_quest=AsyncMock(side_effect=start))
        run = source_function('run_questing_worker', {
            'Client': object, 'party_enabled': True, 'logger': Mock(),
            'reconcile_combat_state': reconcile_combat_state,
            'original_client_locations': locations, 'Quester': Mock(return_value=quester),
            'ignore_pet_level_up': False, 'only_play_dance_game': False})
        await run(self.client)
        quester.auto_quest.assert_awaited_once()


class PostCombatCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ClientState(
            title='p3', process_id=3, questing_status=True,
            just_entered_combat=None, entity_detect_combat_status=True,
            just_left_combat=False, client_being_helped=None,
            invincible_combat_timer=False, original_location_before_combat='old safe',
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            teleport=AsyncMock())
        self.locations = {3: 'old safe', 1: 'other group'}
        self.wisps = AsyncMock()
        self.timeouts = []
        async def bounded(awaitable, timeout):
            self.timeouts.append(timeout)
            return await asyncio.wait_for(awaitable, min(timeout, .05))
        async def tick(delay):
            if delay == .25:
                self.ticks += 1
                if self.ticks > 1:
                    raise asyncio.CancelledError
        self.ticks = 0
        self.namespace = {
            'Client': object, 'SprintyClient': lambda client: client,
            'asyncio': SimpleNamespace(sleep=tick, wait_for=bounded, TimeoutError=asyncio.TimeoutError),
            'combat_group_for': lambda client: [client], 'original_client_locations': self.locations,
            'nearest_duel_circle_distance_and_xyz': AsyncMock(return_value=(None, None)),
            'is_free': AsyncMock(return_value=True), 'clear_post_combat_phase': AsyncMock(),
            'collect_wisps_with_limit': self.wisps, 'logger': Mock()}
        self.detect = source_function('detect_combat', self.namespace)

    async def once(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.detect(self.client)

    def assert_released(self):
        self.assertFalse(self.client.entity_detect_combat_status)
        self.assertFalse(self.client.post_combat_cleanup_active)
        self.assertFalse(self.client.invincible_combat_timer)
        self.assertEqual(self.locations, {1: 'other group'})

    async def test_wisp_timeout_drains_collection_and_restores_movement(self):
        drained = asyncio.Event()
        async def collect(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.wisps.side_effect = collect
        await self.once()
        self.assertTrue(drained.is_set())
        self.assertEqual(self.timeouts, [3, 3, 3])
        self.assert_released()
        self.client.teleport.assert_awaited_once_with('old safe')

    async def test_collection_read_error_releases_cleanup_for_supervisor_retry(self):
        self.wisps.side_effect = ValueError('stale wisp')
        with self.assertRaisesRegex(ValueError, 'stale wisp'):
            await self.detect(self.client)
        self.assert_released()
        self.client.teleport.assert_not_awaited()

    async def test_cancelled_collection_drains_and_releases_own_state(self):
        started, drained = asyncio.Event(), asyncio.Event()
        async def collect(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.wisps.side_effect = collect
        task = asyncio.create_task(self.detect(self.client))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(drained.is_set())
        self.assert_released()

    async def test_old_safe_position_timeout_cancels_move_and_releases_state(self):
        drained = asyncio.Event()
        async def teleport(*args):
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.client.teleport.side_effect = teleport
        await self.once()
        self.assertTrue(drained.is_set())
        self.assert_released()

    async def test_refill_started_during_cleanup_never_returns_to_old_position(self):
        async def collect(*args, **kwargs):
            self.client.refilling_potions = True
        self.wisps.side_effect = collect
        await self.once()
        self.client.teleport.assert_not_awaited()
        self.assertTrue(self.client.refilling_potions)
        self.assert_released()

    async def test_loading_or_dialogue_after_nudge_does_not_collect_or_move(self):
        self.namespace['is_free'].side_effect = [True, False]
        await self.once()
        self.wisps.assert_not_awaited()
        self.client.teleport.assert_not_awaited()
        self.assertTrue(self.client.just_left_combat)
        self.assertTrue(self.client.entity_detect_combat_status)

    async def test_running_nudge_is_not_competed_with_by_wisp_cleanup(self):
        async def nudge(client):
            client.post_combat_movement_active = True
        self.namespace['clear_post_combat_phase'].side_effect = nudge
        await self.once()
        self.wisps.assert_not_awaited()
        self.client.teleport.assert_not_awaited()
        self.assertTrue(self.client.just_left_combat)

    async def test_gate_repaired_flag_drops_only_own_old_location_cache(self):
        self.client.entity_detect_combat_status = False
        self.client.original_location_before_combat = None
        await self.once()
        self.assertEqual(self.locations, {1: 'other group'})
        self.client.teleport.assert_not_awaited()


class DetectionSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresponsive_state_read_times_out_without_clearing_protection(self):
        client = ClientState(title='p3', entity_detect_combat_status=True,
                             is_loading=AsyncMock(return_value=False))
        drained = asyncio.Event()
        async def zone():
            try:
                await asyncio.Future()
            finally:
                drained.set()
        client.zone_name = zone
        actual_timeout = asyncio.timeout
        with patch('src.utils.asyncio.timeout', side_effect=lambda _: actual_timeout(.02)):
            self.assertFalse(await reconcile_combat_state(client))
        self.assertTrue(drained.is_set())
        self.assertTrue(client.entity_detect_combat_status)
        self.assertIsNone(client._combat_ended_observation)

    async def test_nudge_does_not_compete_with_running_post_combat_cleanup(self):
        client = ClientState(post_combat_cleanup_active=True, send_key=AsyncMock())
        fn = source_function('clear_post_combat_phase', {'Client': object})
        await fn(client)
        client.send_key.assert_not_awaited()

    async def test_one_client_read_failure_retries_without_cancelling_peer(self):
        client, peer = ClientState(title='p3'), ClientState(title='p1')
        clients = [client, peer]
        retried, peer_running = asyncio.Event(), asyncio.Event()
        calls = 0
        async def detector(current):
            nonlocal calls
            if current is peer:
                peer_running.set()
                await asyncio.Future()
            calls += 1
            if calls == 1:
                raise ValueError('temporary entity read')
            retried.set()
            await asyncio.Future()
        async def sleep(_):
            await asyncio.sleep(0)
        refresh = Mock()
        fn = source_function('supervise_combat_detection', {
            'asyncio': SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
            'time': SimpleNamespace(monotonic=lambda: 100), 'logger': Mock(),
            'walker': SimpleNamespace(clients=clients), 'detect_combat': detector,
            'refresh_character_memory': refresh})
        first, second = asyncio.create_task(fn(client)), asyncio.create_task(fn(peer))
        try:
            await asyncio.wait_for(retried.wait(), 1)
            await asyncio.wait_for(peer_running.wait(), 1)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            refresh.assert_called_once_with(client)
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())

    async def test_removed_client_is_not_retried(self):
        client = ClientState(title='p3')
        detector = AsyncMock()
        fn = source_function('supervise_combat_detection', {
            'walker': SimpleNamespace(clients=[]), 'detect_combat': detector})
        await fn(client)
        detector.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
