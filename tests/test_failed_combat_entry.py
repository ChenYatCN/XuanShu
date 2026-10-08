import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from wizwalker.memory.memory_objects.enums import DuelPhase


class ClientState(SimpleNamespace):
    __eq__ = object.__eq__
    __ne__ = object.__ne__


class FailedCombatEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        detector = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'detect_combat')
        self.helper = self.client('p1', 1)
        self.quester = self.client('p2', 2)
        self.other_helper = self.client('p3', 3)
        self.helper.just_entered_combat = 0
        self.helper.entity_detect_combat_status = True
        self.helper.client_being_helped = self.quester
        self.helper.original_location_before_combat = 'p1-safe'
        self.quester.helper_clients = [self.helper, self.other_helper]
        self.locations = {1: 'p1-safe', 3: 'p3-safe'}
        self.circle = AsyncMock(return_value=(None, None))
        self.joinable = AsyncMock(return_value=False)
        self.group = [self.helper]
        self.namespace = {
            'Client': ClientState,
            'SprintyClient': lambda client: client,
            'asyncio': SimpleNamespace(sleep=AsyncMock(), wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError),
            'DuelPhase': DuelPhase,
            'time': SimpleNamespace(time=lambda: 15),
            'logger': Mock(),
            'combat_group_for': lambda client: self.group,
            'quest_party_enabled': False,
            'current_quest_party': lambda: SimpleNamespace(
                questers=[self.quester], hitters=[self.helper]),
            'original_client_locations': self.locations,
            'nearest_duel_circle_distance_and_xyz': self.circle,
            'is_duel_circle_joinable': self.joinable,
            'hitter_client': None,
            'is_free': AsyncMock(return_value=True),
            'clear_post_combat_phase': AsyncMock(),
            'collect_wisps_with_limit': AsyncMock(),
        }
        exec(compile(ast.Module(body=[detector], type_ignores=[]),
                     'XuanShu.py', 'exec'), self.namespace)

    def client(self, title, pid):
        return ClientState(
            title=title, process_id=pid, questing_status=True,
            just_entered_combat=None, entity_detect_combat_status=False,
            client_being_helped=None, original_location_before_combat=None,
            quest_party_battle_sync_state='success', helper_clients=[],
            just_left_combat=False, invincible_combat_timer=False,
            duel_circle_joinable=True, in_solo_zone=False,
            in_battle=AsyncMock(return_value=False),
            duel=SimpleNamespace(duel_phase=AsyncMock(return_value=DuelPhase.planning)),
            zone_name=AsyncMock(return_value='Dungeon/Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=f'{title}-safe')),
            teleport=AsyncMock(),
        )

    async def detect_once(self, client):
        calls = 0

        async def stop_after_iteration(delay):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise asyncio.CancelledError

        self.namespace['asyncio'].sleep.side_effect = stop_after_iteration
        with self.assertRaises(asyncio.CancelledError):
            await self.namespace['detect_combat'](client)

    def assert_released(self):
        self.assertIsNone(self.helper.just_entered_combat)
        self.assertFalse(self.helper.entity_detect_combat_status)
        self.assertIsNone(self.helper.client_being_helped)
        self.assertIsNone(self.helper.original_location_before_combat)
        self.assertNotIn(self.helper, self.quester.helper_clients)
        self.assertEqual(self.locations, {3: 'p3-safe'})
        self.assertFalse(self.helper.just_left_combat)
        self.assertFalse(self.helper.invincible_combat_timer)
        self.namespace['clear_post_combat_phase'].assert_not_awaited()
        self.namespace['collect_wisps_with_limit'].assert_not_awaited()

    async def test_closed_circle_releases_failed_helper_only(self):
        await self.detect_once(self.helper)
        self.assert_released()
        self.assertFalse(self.quester.duel_circle_joinable)
        self.assertEqual(self.quester.helper_clients, [self.other_helper])
        self.assertTrue(self.helper.duel_circle_joinable)
        self.helper.teleport.assert_not_awaited()

    async def test_orphaned_entry_marker_no_longer_blocks_questing(self):
        self.helper.client_being_helped = None
        self.quester.helper_clients.remove(self.helper)
        await self.detect_once(self.helper)
        self.assert_released()
        self.joinable.assert_not_awaited()

    async def test_failed_entry_is_not_reclassified_in_same_iteration(self):
        self.circle.return_value = (600, 'duel')
        await self.detect_once(self.helper)
        self.assert_released()
        self.circle.assert_not_awaited()

    async def test_battle_started_during_joinability_check_is_preserved(self):
        self.helper.in_battle.side_effect = [False, True]
        await self.detect_once(self.helper)
        self.assertIsNone(self.helper.just_entered_combat)
        self.assertTrue(self.helper.entity_detect_combat_status)
        self.assertIs(self.helper.client_being_helped, self.quester)
        self.assertIn(self.helper, self.quester.helper_clients)
        self.assertEqual(self.locations[1], 'p1-safe')
        self.assertTrue(self.quester.duel_circle_joinable)

    async def test_actual_battle_preserves_post_combat_state(self):
        self.helper.in_battle.return_value = True
        self.helper.duel_circle_joinable = False
        self.circle.return_value = (600, 'duel')
        await self.detect_once(self.helper)
        self.assertIsNone(self.helper.just_entered_combat)
        self.assertTrue(self.helper.entity_detect_combat_status)
        self.assertIs(self.helper.client_being_helped, self.quester)
        self.assertEqual(self.helper.original_location_before_combat, 'p1-safe')
        self.assertEqual(self.locations[1], 'p1-safe')
        self.joinable.assert_not_awaited()

    async def test_entry_grace_period_is_preserved(self):
        self.helper.just_entered_combat = 10
        await self.detect_once(self.helper)
        self.assertEqual(self.helper.just_entered_combat, 10)
        self.assertTrue(self.helper.entity_detect_combat_status)
        self.joinable.assert_not_awaited()
        self.circle.assert_not_awaited()

    async def test_joinable_pending_circle_is_not_rejected(self):
        self.joinable.return_value = True
        await self.detect_once(self.helper)
        self.assertEqual(self.helper.just_entered_combat, 0)
        self.assertTrue(self.helper.entity_detect_combat_status)
        self.assertIs(self.helper.client_being_helped, self.quester)
        self.assertIn(1, self.locations)

    async def test_party_timeout_preserves_active_retry(self):
        self.namespace['quest_party_enabled'] = True
        self.helper.quest_party_battle_sync_state = 'trying'
        await self.detect_once(self.helper)
        self.assertIsNone(self.helper.just_entered_combat)
        self.assertFalse(self.helper.entity_detect_combat_status)
        self.assertEqual(self.helper.quest_party_battle_sync_state, 'trying')
        self.assertNotIn(self.helper, self.quester.helper_clients)
        self.assertEqual(self.locations, {3: 'p3-safe'})
        self.joinable.assert_not_awaited()
        self.assertTrue(self.quester.duel_circle_joinable)

    async def test_party_timeout_resets_completed_sync(self):
        self.namespace['quest_party_enabled'] = True
        await self.detect_once(self.helper)
        self.assertIsNone(self.helper.quest_party_battle_sync_state)

    def prepare_teleport(self):
        self.helper.just_entered_combat = None
        self.helper.entity_detect_combat_status = False
        self.helper.client_being_helped = None
        self.helper.original_location_before_combat = None
        self.locations.pop(1)
        self.circle.return_value = (600, 'duel')
        self.group = [self.quester, self.helper]

    async def test_rejected_teleport_releases_failed_helper(self):
        self.prepare_teleport()
        self.helper.teleport.side_effect = ValueError('teleport rejected')
        await self.detect_once(self.quester)
        self.assert_released()
        self.helper.teleport.assert_awaited_once_with('duel')
        self.assertTrue(self.quester.entity_detect_combat_status)
        self.assertTrue(self.quester.duel_circle_joinable)

    async def test_successful_teleport_keeps_pending_entry(self):
        self.prepare_teleport()
        await self.detect_once(self.quester)
        self.assertEqual(self.helper.just_entered_combat, 15)
        self.assertTrue(self.helper.entity_detect_combat_status)
        self.assertIs(self.helper.client_being_helped, self.quester)
        self.assertIn(self.helper, self.quester.helper_clients)
        self.assertEqual(self.locations[1], 'p1-safe')

    async def test_shared_target_move_is_not_interrupted_by_duel_circle_pull(self):
        self.prepare_teleport()
        self.helper.quest_party_target_sync_active = True
        await self.detect_once(self.quester)
        self.helper.teleport.assert_not_awaited()
        self.assertIsNone(self.helper.just_entered_combat)
        self.assertFalse(self.helper.entity_detect_combat_status)

    async def test_post_combat_cleanup_is_not_interrupted_by_circle_pull(self):
        self.prepare_teleport()
        self.helper.post_combat_cleanup_active = True
        await self.detect_once(self.quester)
        self.helper.teleport.assert_not_awaited()
        self.assertIsNone(self.helper.just_entered_combat)

    async def test_ended_circle_does_not_reassert_battle_or_pull_other_group(self):
        self.prepare_teleport()
        self.quester.duel.duel_phase.return_value = DuelPhase.ended
        await self.detect_once(self.quester)
        self.assertFalse(self.quester.entity_detect_combat_status)
        self.helper.teleport.assert_not_awaited()
        self.assertIsNone(self.helper.just_entered_combat)


if __name__ == '__main__':
    unittest.main()
