import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from wizwalker import XYZ, Keycode
from wizwalker.memory import DuelPhase
from src.combat_targeting import TargetingSprintyCombat, UpstreamSprintyCombat
from src.automation_ownership import automation_owner
from src.hotkey_groups import client_available
from src.questing import claim_quest_recovery, release_quest_recovery
from src.task_lifecycle import gather_owned


class PartyEntryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hitter = SimpleNamespace(
            title='p2', process_id=2, questing_status=True,
            in_battle=AsyncMock(return_value=False), is_loading=AsyncMock(return_value=False),
            entity_detect_combat_status=False, just_entered_combat=None,
            quest_party_battle_sync_state=None, client_being_helped=None,
            zone_name=AsyncMock(return_value='Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(1, 2, 3))),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = SimpleNamespace(
            title='p1', process_id=1, questing_status=True, in_solo_zone=False,
            in_battle=AsyncMock(return_value=True), is_loading=AsyncMock(return_value=False),
            quest_party_battle_started_at=asyncio.get_running_loop().time() - 1.5,
            zone_name=AsyncMock(return_value='Room'), helper_clients=[],
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(10, 20, 30))))
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        helpers = [n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                   and n.name in ('clear_post_combat_phase', 'rescue_missing_party_hitters',
                                  'battle_entry_context', 'battle_entry_snapshot', 'remember_battle_entry',
                                  'watch_party_battle')]
        for client in (self.hitter, self.quester):
            client.quest_id = AsyncMock(return_value=100)
            client.goal_id = AsyncMock(return_value=1)
            client.duel = SimpleNamespace(duel_id_full=AsyncMock(return_value=77))
            client.quest_party_battle_entry_recovery = None
        self.hitter.original_location_before_combat = XYZ(1, 2, 3)
        self.release = Mock(wraps=release_quest_recovery)
        self.ns = dict(Client=object, asyncio=asyncio, Keycode=Keycode, XYZ=XYZ,
            time=__import__('time'), logger=Mock(), quest_party_enabled=True,
            original_client_locations={}, walker=SimpleNamespace(clients=[self.quester, self.hitter]),
            current_quest_party=lambda *_: SimpleNamespace(
                questers=[self.quester], hitter_assignments=[(self.hitter, self.quester)]),
            clients_share_live_area=AsyncMock(return_value=True),
            combat_group_for=lambda *_: [self.quester, self.hitter],
            client_available=client_available, automation_owner=automation_owner,
            gather_owned=gather_owned, claim_quest_recovery=claim_quest_recovery,
            release_quest_recovery=self.release)
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'XuanShu.py', 'exec'), self.ns)
        await self.arm()
        detect = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == 'detect_combat')
        pending = next(n for n in ast.walk(detect) if isinstance(n, ast.If)
                       and ast.unparse(n.test) == 'p.just_entered_combat is not None')
        check = ast.parse('async def check_pending_entry(p):\n    pass').body[0]
        # Keep the detector's loop context for failure-path continue statements.
        check.body = ast.parse('for _ in range(1):\n    pass').body
        check.body[0].body = [pending]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[check], type_ignores=[])),
                     'XuanShu.py', 'exec'), self.ns)
        follower = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == '_follow_quester_session')
        gate = next(n for n in ast.walk(follower) if isinstance(n, ast.If)
                    and 'hitter.entity_detect_combat_status' in ast.unparse(n.test))
        admission = ast.parse('async def waiting_for_entry(hitter):\n    return False').body[0]
        admission.body[0].value = gate.test
        exec(compile(ast.fix_missing_locations(ast.Module(body=[admission], type_ignores=[])),
                     'XuanShu.py', 'exec'), self.ns)

    async def test_auto_quest_post_combat_uses_a_then_d(self):
        await self.ns['clear_post_combat_phase'](self.hitter)
        self.assertEqual([c.kwargs['key'] for c in self.hitter.send_key.await_args_list], [Keycode.A, Keycode.D])
        self.assertFalse(self.hitter.post_combat_movement_active)

    async def arm(self, age=8):
        state = await self.ns['battle_entry_snapshot'](self.hitter, self.quester, XYZ(10, 20, 30))
        self.ns['remember_battle_entry'](self.hitter, state)
        self.hitter.quest_party_battle_entry_recovery['normal_tp_at'] -= age

    async def test_recovery_after_normal_grace_returns_exact_saved_target(self):
        async def arrive(position):
            if position.z == 30:
                self.hitter.in_battle.return_value = True
        self.hitter.teleport.side_effect = arrive
        self.quester.body.position.return_value = XYZ(99, 99, 99)
        await self.ns['rescue_missing_party_hitters'](self.quester)
        positions = [c.args[0] for c in self.hitter.teleport.await_args_list]
        self.assertEqual([(p.x, p.y, p.z) for p in positions], [(0, 0, -10000), (10, 20, 30)])
        self.assertFalse(self.hitter.quest_party_battle_rescue_active)
        self.release.assert_called_once_with(self.hitter, 'party_battle')
        self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)
        self.hitter.send_key.assert_not_awaited()

    async def test_cancelled_movement_never_returns_to_old_battle(self):
        started = asyncio.Event()
        async def hold(position):
            started.set()
            await asyncio.Event().wait()
        self.hitter.teleport.side_effect = hold
        task = asyncio.create_task(self.ns['rescue_missing_party_hitters'](self.quester))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.hitter.teleport.await_count, 1)
        self.assertEqual(self.hitter.teleport.await_args.args[0].z, -10000)
        self.assertFalse(self.hitter.quest_party_battle_rescue_active)
        self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)
        self.assertFalse(self.hitter.entity_detect_combat_status)

    async def test_failed_entry_clears_stale_flags_and_saved_location_after_timeout(self):
        self.hitter.just_entered_combat = self.ns['time'].time() - 8
        self.hitter.entity_detect_combat_status = True
        self.hitter.client_being_helped = self.quester
        self.hitter.quest_party_battle_sync_state = 'success'
        self.quester.helper_clients = [self.hitter]
        self.ns['original_client_locations'][2] = XYZ(1, 2, 3)
        await self.ns['check_pending_entry'](self.hitter)
        self.assertIsNone(self.hitter.just_entered_combat)
        self.assertFalse(self.hitter.entity_detect_combat_status)
        self.assertIsNone(self.hitter.client_being_helped)
        self.assertIsNone(self.hitter.quest_party_battle_sync_state)
        self.assertEqual(self.quester.helper_clients, [])
        self.assertNotIn(2, self.ns['original_client_locations'])

    async def test_confirmed_entry_keeps_battle_flag(self):
        self.hitter.just_entered_combat = self.ns['time'].time() - 8
        self.hitter.entity_detect_combat_status = True
        self.hitter.in_battle.return_value = True
        await self.ns['check_pending_entry'](self.hitter)
        self.assertIsNone(self.hitter.just_entered_combat)
        self.assertTrue(self.hitter.entity_detect_combat_status)

    async def test_follower_does_not_wait_forever_on_unconfirmed_battle_flag(self):
        self.hitter.entity_detect_combat_status = True
        self.hitter.just_entered_combat = self.ns['time'].time()
        self.assertTrue(await self.ns['waiting_for_entry'](self.hitter))
        self.hitter.just_entered_combat -= 8
        await self.ns['check_pending_entry'](self.hitter)
        self.assertFalse(await self.ns['waiting_for_entry'](self.hitter))
        self.hitter.just_entered_combat = None
        self.assertFalse(await self.ns['waiting_for_entry'](self.hitter))
        self.hitter.in_battle.return_value = True
        self.assertTrue(await self.ns['waiting_for_entry'](self.hitter))

    async def test_failed_coordinate_sync_can_recover_only_in_same_dungeon(self):
        self.quester.quest_party_group_dungeon_zone = 'Room'
        self.hitter.quest_party_battle_sync_state = 'failed'
        self.ns['clients_share_live_area'].return_value = False
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()
        self.ns['clients_share_live_area'].return_value = True
        async def arrive(position):
            if position.z == 30:
                self.hitter.in_battle.return_value = True
        self.hitter.teleport.side_effect = arrive
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertTrue(self.hitter.in_battle.return_value)
        self.assertEqual(self.hitter.teleport.await_count, 2)

    async def test_coordinate_sync_in_progress_is_not_interrupted(self):
        self.quester.quest_party_group_dungeon_zone = 'Room'
        self.hitter.quest_party_battle_sync_state = 'trying'
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()

    async def test_shared_target_movement_is_not_interrupted_by_entry_rescue(self):
        self.quester.quest_party_target_sync_active = True
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()
        self.quester.quest_party_target_sync_active = False
        self.quester.quest_party_battle_started_at = asyncio.get_running_loop().time() - 1.5
        self.hitter.quest_party_target_sync_active = True
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()

    async def test_no_completed_normal_tp_never_resets(self):
        self.hitter.quest_party_battle_entry_recovery = None
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()

    async def test_first_seven_seconds_and_ongoing_normal_tp_never_reset(self):
        state = self.hitter.quest_party_battle_entry_recovery
        state['normal_tp_at'] = asyncio.get_running_loop().time() - 6.5
        await self.ns['rescue_missing_party_hitters'](self.quester)
        state['normal_tp_at'] -= 2
        state['normal_tp_active'] = True
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()

    async def test_normal_join_clears_record_without_special_tp(self):
        self.hitter.in_battle.return_value = True
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()
        self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)

    async def test_context_changes_clear_record_without_teleport(self):
        mutations = [
            lambda: setattr(self.hitter, 'questing_status', False),
            lambda: setattr(self.quester, 'questing_status', False),
            lambda: setattr(self.quester.in_battle, 'return_value', False),
            lambda: setattr(self.hitter.zone_name, 'return_value', 'Other'),
            lambda: setattr(self.quester.zone_name, 'return_value', 'Other'),
            lambda: setattr(self.hitter.quest_id, 'return_value', 200),
            lambda: setattr(self.quester.goal_id, 'return_value', 2),
            lambda: setattr(self.quester.duel.duel_id_full, 'return_value', 78),
            lambda: setattr(self.hitter, 'quest_party_status_session', object()),
            lambda: setattr(self.hitter, 'quest_party_quester', object()),
            lambda: setattr(self.hitter, 'is_running', lambda: False),
            lambda: setattr(self.ns['walker'], 'clients', [self.hitter]),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(change=i):
                await self.asyncSetUp()
                mutate()
                await self.ns['rescue_missing_party_hitters'](self.quester)
                self.hitter.teleport.assert_not_awaited()
                self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)

    async def test_changed_group_invalidates_record(self):
        self.ns['combat_group_for'] = lambda *_: [self.quester]
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()
        self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)

    async def test_disconnected_client_watcher_clears_own_record_and_exits(self):
        self.ns['walker'].clients = [self.quester]
        await asyncio.wait_for(self.ns['watch_party_battle'](self.hitter), timeout=0.5)
        self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)
        self.hitter.teleport.assert_not_awaited()

    async def test_unreadable_frame_retains_exhausted_retry_cap(self):
        state = self.hitter.quest_party_battle_entry_recovery
        state['attempts'] = 2
        self.quester.duel.duel_id_full.side_effect = RuntimeError('temporarily unreadable')
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertIs(self.hitter.quest_party_battle_entry_recovery, state)
        self.quester.duel.duel_id_full.side_effect = None
        snapshot = await self.ns['battle_entry_snapshot'](self.hitter, self.quester, XYZ(10, 20, 30))
        self.ns['remember_battle_entry'](self.hitter, snapshot)
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(self.hitter.quest_party_battle_entry_recovery['attempts'], 2)
        self.hitter.teleport.assert_not_awaited()

    async def test_loading_or_other_recovery_does_not_teleport(self):
        self.hitter.is_loading.return_value = True
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.is_loading.return_value = False
        self.hitter.quest_recovery_owner = 'other'
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.hitter.teleport.assert_not_awaited()
        self.assertEqual(self.hitter.quest_recovery_owner, 'other')

    async def test_battle_end_stop_loading_or_zone_change_during_reset_cancels_return(self):
        mutations = [
            lambda: setattr(self.quester.in_battle, 'return_value', False),
            lambda: setattr(self.hitter, 'questing_status', False),
            lambda: setattr(self.hitter.zone_name, 'return_value', 'Other'),
            lambda: setattr(self.hitter.is_loading, 'return_value', True),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(change=i):
                await self.asyncSetUp()
                async def leave(position):
                    mutate()
                self.hitter.teleport.side_effect = leave
                await self.ns['rescue_missing_party_hitters'](self.quester)
                self.assertEqual(self.hitter.teleport.await_count, 1)
                self.assertFalse(self.hitter.quest_party_battle_rescue_active)
                self.assertFalse(self.hitter.entity_detect_combat_status)
                self.assertIsNone(self.hitter.quest_recovery_owner)

    async def test_stop_while_waiting_for_input_lock_cancels_without_tp(self):
        async with automation_owner(self.hitter, 'test-busy'):
            task = asyncio.create_task(self.ns['rescue_missing_party_hitters'](self.quester))
            await asyncio.sleep(0.05)
            self.hitter.questing_status = False
            await asyncio.wait_for(task, timeout=0.5)
            self.hitter.teleport.assert_not_awaited()
            self.assertIsNone(self.hitter.quest_party_battle_entry_recovery)
            self.assertIsNone(self.hitter.quest_recovery_owner)
            self.assertFalse(self.hitter.quest_party_battle_rescue_active)

    async def test_only_unjoined_client_moves(self):
        from copy import copy
        joined = copy(self.hitter)
        joined.title = 'p3'
        joined.in_battle = AsyncMock(return_value=True)
        joined.teleport = AsyncMock()
        self.ns['walker'].clients.append(joined)
        self.ns['combat_group_for'] = lambda *_: [self.quester, self.hitter, joined]
        self.hitter.quest_party_battle_entry_recovery = None
        await self.arm()
        joined.quest_party_battle_entry_recovery = dict(self.hitter.quest_party_battle_entry_recovery)
        async def arrive(position):
            if position.z == 30:
                self.hitter.in_battle.return_value = True
        self.hitter.teleport.side_effect = arrive
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(self.hitter.teleport.await_count, 2)
        joined.teleport.assert_not_awaited()
        self.assertIsNone(joined.quest_party_battle_entry_recovery)

    async def test_retry_cap_cooldown_and_repeated_normal_tp_do_not_restart(self):
        await self.ns['rescue_missing_party_hitters'](self.quester)
        state = self.hitter.quest_party_battle_entry_recovery
        self.assertEqual(state['attempts'], 1)
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(self.hitter.teleport.await_count, 2)
        snapshot = await self.ns['battle_entry_snapshot'](self.hitter, self.quester, XYZ(10, 20, 30))
        self.ns['remember_battle_entry'](self.hitter, snapshot)
        state = self.hitter.quest_party_battle_entry_recovery
        self.assertEqual(state['attempts'], 1)
        state['next_at'] = 0
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(state['attempts'], 2)
        self.assertEqual(self.hitter.teleport.await_count, 4)
        snapshot = await self.ns['battle_entry_snapshot'](self.hitter, self.quester, XYZ(10, 20, 30))
        self.ns['remember_battle_entry'](self.hitter, snapshot)
        await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(self.hitter.teleport.await_count, 4)
        warnings = [c.args[0] for c in self.ns['logger'].warning.call_args_list]
        self.assertEqual(sum('停止本次特殊恢复' in w for w in warnings), 1)
        self.quester.duel.duel_id_full.return_value = 78
        snapshot = await self.ns['battle_entry_snapshot'](self.hitter, self.quester, XYZ(10, 20, 30))
        self.ns['remember_battle_entry'](self.hitter, snapshot)
        self.assertEqual(self.hitter.quest_party_battle_entry_recovery['attempts'], 0)


class PartyRoundGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_hitter_does_not_execute_priorities(self):
        combat = object.__new__(TargetingSprintyCombat)
        combat.client = SimpleNamespace(
            questing_status=True, quest_party_hitters=[SimpleNamespace(
                client_object=SimpleNamespace(global_id_full=AsyncMock(return_value=2)))],
            in_battle=AsyncMock(return_value=True),
            duel=SimpleNamespace(duel_phase=AsyncMock(side_effect=[DuelPhase.planning, DuelPhase.ended])))
        combat.get_members = AsyncMock(return_value=[SimpleNamespace(owner_id=AsyncMock(return_value=1))])
        with patch.object(UpstreamSprintyCombat, 'handle_round', new_callable=AsyncMock) as execute, patch(
            'src.combat_targeting.asyncio.sleep', new_callable=AsyncMock
        ):
            await combat.handle_round()
        execute.assert_not_awaited()
        combat.client.duel.duel_phase.side_effect = None
        combat.client.duel.duel_phase.return_value = DuelPhase.planning
        combat.get_members.return_value.append(SimpleNamespace(owner_id=AsyncMock(return_value=2)))
        with patch.object(UpstreamSprintyCombat, 'handle_round', new_callable=AsyncMock) as execute:
            await combat.handle_round()
        execute.assert_awaited_once()
