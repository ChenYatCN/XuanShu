"""Scoped rescue gates; no real game input or instance inference from XY."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from wizwalker import XYZ, Keycode
from wizwalker.memory.memory_objects.enums import DuelPhase
from src.quest_party import resolve_quest_party, dungeon_session_for, dungeon_member_inside
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests.test_scoped_runtime import load_function

REAL_SLEEP = asyncio.sleep


class BattleRescueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = 1000
        self.events = []
        self.join = True
        self.on_input = lambda client, kind: None
        def client(index):
            result = SimpleNamespace(title=f'p{index}', process_id=index, questing_status=True,
                account_nick='quester' if index in (1, 3) else 'hitter',
                zone='Dungeon/Room', instance=(index + 1) // 2, battle=index in (1, 3),
                loading=False, in_solo_zone=False, quest_party_probe_pending=False,
                quest_party_battle_started_at=996, quest_party_battle_rescue_active=False,
                quest_party_battle_rescue_at=0, invincible_combat_timer=False,
                entity_detect_combat_status=False, just_entered_combat=None,
                client_being_helped=None, helper_clients=[], position=XYZ(index * 100, 0, 0))
            result.quest_id = AsyncMock(return_value=42)
            result.goal_id = AsyncMock(return_value=7)
            result.duel_circle_joinable = True
            result.in_battle = AsyncMock(side_effect=lambda: result.battle)
            result.is_loading = AsyncMock(side_effect=lambda: result.loading)
            result.zone_name = AsyncMock(side_effect=lambda: result.zone)
            result.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: result.position))
            result.duel = SimpleNamespace(duel_id_full=AsyncMock(return_value=10 + result.instance))
            result.duel.duel_phase = AsyncMock(return_value=DuelPhase.planning)
            async def teleport(point):
                self.events.append((result.title, 'tp', tuple(point)))
                result.position = point
                self.on_input(result, 'tp')
                q = self.clients[0 if index == 2 else 2]
                if index in (2, 4) and tuple(point) == tuple(q.position) and self.join:
                    result.battle = True
            async def key(**kwargs):
                self.events.append((result.title, 'key', kwargs['key']))
                self.on_input(result, 'key')
                await REAL_SLEEP(0)
            result.teleport, result.send_key = teleport, key
            return result
        self.clients = [client(i) for i in range(1, 5)]
        self.q1, self.h1, self.q2, self.h2 = self.clients
        for q, h in ((self.q1, self.h1), (self.q2, self.h2)):
            q.hotkey_quest_clients = h.hotkey_quest_clients = [q, h]
            q.quest_party_hitters = [h]
            h.quest_party_hitters = []
        self.log = Mock()
        self.log.bind.return_value = self.log
        self.resolutions = []
        def party(members=None):
            self.resolutions.append(members)
            return resolve_quest_party(self.clients if members is None else members, enabled=True,
                quester_titles=['account:quester'], hitter_titles=['account:hitter'])
        async def sleep(seconds):
            self.clock += seconds
            await REAL_SLEEP(0)
        async def same_area(q, h):
            return q.zone == h.zone and q.instance == h.instance
        async def area(c):
            return c.zone, c.instance, c.process_id
        self.ns = dict(asyncio=SimpleNamespace(get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
            sleep=sleep, timeout=asyncio.timeout, create_task=asyncio.create_task,
            gather=asyncio.gather, current_task=asyncio.current_task, CancelledError=asyncio.CancelledError),
            time=SimpleNamespace(time=lambda: self.clock), Client=object, XYZ=XYZ, Keycode=Keycode,
            logger=self.log, walker=SimpleNamespace(clients=self.clients), quest_party_enabled=True,
            current_quest_party=party, original_client_locations={}, capture_dungeon_return=AsyncMock(),
            clients_share_live_area=same_area, _party_area_token=area,
            dungeon_session_for=dungeon_session_for, dungeon_member_inside=dungeon_member_inside,
            DuelPhase=DuelPhase, _movement_position=lambda c:c.body.position(),
            _movement_control_changed=AsyncMock(return_value=False),
            automation_owner=automation_owner, get_client_automation_ownership=get_client_automation_ownership,
            SprintyClient=lambda _: None)
        self.group = load_function('combat_group_for', self.ns)
        load_function('party_battle_pair_mode', self.ns)
        try:
            load_function('battle_rescue_diagnostic', self.ns)
        except StopIteration:
            pass  # The pre-fix source has no diagnostics.
        self.rescue = load_function('rescue_missing_party_hitters', self.ns)

    def messages(self):
        return [call.args[0] for call in self.log.info.call_args_list if call.args]

    async def entry(self, client):
        """Execute the actual detector prefix; exclude unrelated circle detection."""
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'detect_combat')
        loop = next(n for n in fn.body if isinstance(n, ast.While))
        branch = next(n for n in loop.body if isinstance(n, ast.If))
        boundary = next(i for i, n in enumerate(branch.body) if isinstance(n, ast.If)
            and any(isinstance(v, ast.Attribute) and v.attr == 'just_entered_combat' for v in ast.walk(n.test)))
        branch.body = branch.body[:boundary]
        loop.body = loop.body[:loop.body.index(branch) + 1] + [ast.Return(value=None)]
        ast.fix_missing_locations(fn)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), self.ns)
        await self.ns['detect_combat'](client)

    async def test_anchor_join_timeout_retires_stale_just_entered_combat(self):
        p = self.h1
        p.just_entered_combat = self.clock - 8
        p.in_battle = AsyncMock(return_value=False)
        p.client_being_helped = None
        p.entity_detect_combat_status = True
        p.just_left_combat = False
        calls = []
        async def sleep(_seconds):
            calls.append(1)
            if len(calls) >= 2:
                raise StopAsyncIteration
            await REAL_SLEEP(0)
        ns = dict(self.ns)
        ns.update(
            asyncio=SimpleNamespace(sleep=sleep, gather=asyncio.gather),
            rescue_missing_party_hitters=AsyncMock(),
            nearest_duel_circle_distance_and_xyz=AsyncMock(return_value=(None, None)),
            is_free=AsyncMock(return_value=False),
        )
        detect = load_function('detect_combat', ns)
        with self.assertRaises(StopAsyncIteration):
            await detect(p)
        self.assertIsNone(p.just_entered_combat)
        self.assertFalse(p.entity_detect_combat_status)

    async def test_scope_difference_alone_does_not_remove_p1_quester_identity(self):
        global_party = self.ns['current_quest_party']()
        local_party = self.ns['current_quest_party']([self.q1, self.h1])
        self.assertIn(self.q1, global_party.questers)
        self.assertIn(self.q1, local_party.questers)

    async def test_detector_uses_same_scoped_party_for_entry_and_rescue(self):
        await self.entry(self.q1)
        self.assertTrue(self.resolutions)
        self.assertTrue(all(members is not None for members in self.resolutions))
        self.assertTrue(all(self.h2 not in members for members in self.resolutions))

    async def test_two_groups_rescue_only_own_missing_hitter(self):
        await asyncio.gather(self.rescue(self.q1), self.rescue(self.q2))
        self.assertTrue(self.h1.battle and self.h2.battle)
        destinations = {title: point for title, kind, point in self.events if kind == 'tp' and point[2] != -3000}
        self.assertEqual(destinations['p2'], tuple(self.q1.position))
        self.assertEqual(destinations['p4'], tuple(self.q2.position))

    async def test_same_zone_different_instance_never_teleports(self):
        self.h1.instance = 99
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('instance-unconfirmed' in m for m in self.messages()))

    def confirmed_session(self, members, *, confirmed=True, phase='active', exited=()):
        return SimpleNamespace(quester=self.q1, confirmed=confirmed, phase=phase,
            generation=1, members=tuple(members), exited=set(exited))

    async def test_confirmed_dungeon_session_member_does_not_require_pairwise_instance_refresh(self):
        self.q1.battle = False
        self.h1.battle = True
        session = self.confirmed_session((self.q1, self.h1))
        self.q1.quest_party_hitters = [self.h1]
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertTrue(any('confirmed-dungeon-session-membership' in m for m in self.messages()))
        self.assertTrue(any(c[0] == 'p1' and c[1] == 'tp' for c in self.events))

    async def test_all_missing_confirmed_session_members_get_independent_rescue_candidates(self):
        self.q1.battle = False
        self.h1.battle = True
        self.q1.quest_party_hitters = [self.h1, self.h2]
        self.h2.quest_party_quester = self.q1
        self.h2.hotkey_quest_clients = [self.q1, self.h1, self.h2]
        self.q2.quest_party_hitters = []
        self.q1.quest_party_battle_started_at = 0
        self.ns['current_quest_party'] = lambda *_: SimpleNamespace(
            questers=(self.q1,),
            hitter_assignments=((self.h1, self.q1), (self.h2, self.q1)))
        session = self.confirmed_session((self.q1, self.h1, self.h2))
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1, self.h2) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        moved = {title for title, kind, _ in self.events if kind == 'tp'}
        self.assertIn('p1', moved)
        self.assertIn('p4', moved)
        self.assertTrue(any('confirmed-dungeon-session-membership' in m for m in self.messages()))

    async def test_unconfirmed_dungeon_session_still_rejects_without_instance_evidence(self):
        self.q1.battle = False
        self.h1.battle = True
        session = self.confirmed_session((self.q1, self.h1), confirmed=False)
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('instance-unconfirmed' in m for m in self.messages()))

    async def test_exited_member_cannot_use_confirmed_session_fallback(self):
        self.q1.battle = False
        self.h1.battle = True
        session = self.confirmed_session((self.q1, self.h1), exited={id(self.q1)})
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('instance-unconfirmed' in m for m in self.messages()))

    async def test_retired_session_cannot_use_confirmed_session_fallback(self):
        self.q1.battle = False
        self.h1.battle = True
        session = self.confirmed_session((self.q1, self.h1), phase='retired')
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('instance-unconfirmed' in m for m in self.messages()))

    async def test_cross_zone_confirmed_session_member_still_rejects(self):
        self.q1.battle = False
        self.h1.battle = True
        self.h1.zone = 'Dungeon/Other'
        session = self.confirmed_session((self.q1, self.h1))
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c in (self.q1, self.h1) else None
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('zone-mismatch' in m for m in self.messages()))

    async def test_other_session_same_zone_cannot_use_confirmed_session_fallback(self):
        self.q1.battle = False
        self.h1.battle = True
        session = self.confirmed_session((self.q1, self.h1))
        other = self.confirmed_session((self.q1, self.h1))
        self.q1.quest_party_battle_started_at = 0
        self.ns['dungeon_session_for'] = lambda c: session if c is self.q1 else other
        self.ns['dungeon_member_inside'] = lambda c: True
        self.ns['clients_share_live_area'] = AsyncMock(return_value=False)
        await self.rescue(self.q1)
        self.assertFalse(self.events)

    async def test_joined_hitter_does_not_emit_unrelated_role_diagnostics(self):
        self.h1.battle = True
        await self.rescue(self.h1)
        self.assertFalse(self.messages())
        self.assertFalse(self.events)

    async def test_quester_rescue_occupation_is_diagnosed_without_clearing_it(self):
        self.q1.quest_party_battle_rescue_active = True
        await self.rescue(self.q1)
        self.assertTrue(any('quester-rescue-active' in m for m in self.messages()))
        self.assertTrue(self.q1.quest_party_battle_rescue_active)
        self.assertFalse(self.events)

    async def test_diagnostics_repeat_at_thirty_seconds_and_groups_remain_independent(self):
        self.q1.quest_party_probe_pending = self.q2.quest_party_probe_pending = True
        await self.rescue(self.q1)
        await self.rescue(self.q2)
        self.clock += 29
        await self.rescue(self.q1)
        self.assertEqual(len(self.messages()), 2)
        self.clock += 1
        await self.rescue(self.q1)
        self.assertEqual(len(self.messages()), 3)
        self.assertIn('source=p2 target=p1', self.messages()[-1])

    async def test_solo_and_probe_skip_have_limited_diagnostics_and_can_recover(self):
        for flag, reason in (('in_solo_zone', 'solo-zone'), ('quest_party_probe_pending', 'probe-pending')):
            setattr(self.q1, flag, True)
            for _ in range(6):
                await self.rescue(self.q1)
                self.clock += .5
            self.assertEqual(sum(reason in m for m in self.messages()), 1)
            self.assertFalse(self.events)
            setattr(self.q1, flag, False)
        await self.rescue(self.q1)
        self.clock += 4
        await self.rescue(self.q1)
        self.assertTrue(self.h1.battle)

    async def test_loading_recovery_owner_and_invulnerability_skip_then_clear(self):
        for flag, reason in (('loading', 'loading'), ('quest_recovery_owner', 'recovery-owner'),
                             ('invincible_combat_timer', 'invulnerable-timer')):
            setattr(self.h1, flag, True)
            await self.rescue(self.q1)
            self.assertFalse(self.events)
            self.assertTrue(any(reason in m for m in self.messages()))
            setattr(self.h1, flag, False)
        await self.rescue(self.q1)
        self.assertTrue(self.h1.battle)

    async def test_rescue_active_skip_does_not_clear_someone_elses_ownership(self):
        self.h1.quest_party_battle_rescue_active = True
        await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(self.h1.quest_party_battle_rescue_active)
        self.assertTrue(any('rescue-active' in m for m in self.messages()))

    async def test_input_owner_busy_is_reported_and_not_overridden(self):
        async with automation_owner(self.h1, 'potion'):
            await self.rescue(self.q1)
        self.assertFalse(self.events)
        self.assertTrue(any('input-owner-busy' in m for m in self.messages()))

    async def test_circle_teleport_is_not_join_and_three_attempts_survive_cooldowns(self):
        self.join = False
        for _ in range(3):
            await self.rescue(self.q1)
            self.clock += 10
            self.rescue = load_function('rescue_missing_party_hitters', self.ns)
        count = len(self.events)
        await self.rescue(self.q1)
        self.assertEqual(len(self.events), count)
        self.assertFalse(self.h1.battle)
        self.assertTrue(any('attempts-exhausted' in m for m in self.messages()))

    async def test_failed_first_join_can_retry_after_cooldown_and_stop_on_real_battle(self):
        self.join = False
        await self.rescue(self.q1)
        count = len(self.events)
        await self.rescue(self.q1)
        self.assertEqual(len(self.events), count)
        self.join = True
        self.clock += 8
        await self.rescue(self.q1)
        self.assertTrue(self.h1.battle)
        count = len(self.events)
        await self.rescue(self.q1)
        self.assertEqual(len(self.events), count)

    async def test_battle_ends_mid_recovery_no_circle_tp_and_restore_only_own_origin(self):
        origin = tuple(self.h1.position)
        def ended(client, kind):
            if kind == 'key':
                self.q1.battle = False
                self.q1.position = XYZ(9000, 9000, 0)
        self.on_input = ended
        await self.rescue(self.q1)
        teleports = [point for _, kind, point in self.events if kind == 'tp']
        self.assertEqual(teleports[-1], origin)
        self.assertNotIn(tuple(self.q1.position), teleports)
        self.assertEqual(sum(kind == 'key' for _, kind, _ in self.events), 1)
        self.assertFalse(self.h1.quest_party_battle_rescue_active)

    async def test_area_changes_mid_recovery_do_not_restore_old_coordinates(self):
        def changed(client, kind):
            if kind == 'key': client.instance = 99
        self.on_input = changed
        await self.rescue(self.q1)
        self.assertEqual(sum(kind == 'tp' for _, kind, _ in self.events), 1)
        self.assertFalse(self.h1.quest_party_battle_rescue_active)

    async def test_cancellation_releases_owner_and_never_pulls_into_ended_battle(self):
        def cancel(client, kind):
            if kind == 'key':
                self.q1.battle = False
                asyncio.current_task().cancel()
        self.on_input = cancel
        task = asyncio.create_task(self.rescue(self.q1))
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(get_client_automation_ownership(self.h1).locked)
        self.assertFalse(self.h1.quest_party_battle_rescue_active)
        self.assertFalse(self.h1.entity_detect_combat_status)


if __name__ == '__main__':
    unittest.main()
