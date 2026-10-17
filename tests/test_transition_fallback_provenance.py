"""Fallback return codes cannot erase a measured, current-target transition approach."""
import asyncio
import inspect
import unittest
from copy import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.paths import npc_range_path
from tests.test_stale_interaction_movement import Fixture
from tests.test_dungeon_local_tp import DungeonFixture
from src import quest_party as party
from src.questing import Quester
from src.paths import team_up_button_path


class TransitionFallbackProvenanceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective = '前往下一个区域 地点：任务地点'
        self.title, self.prompt = '世界之门', '按下 或 互动'
        self.nearby(2618)
        self.range_visible = False
        self.session = None
        self.stack.enter_context(patch('src.quest_party.dungeon_session_for', lambda _c: self.session))
        self.stack.enter_context(patch('src.questing.dungeon_session_for', lambda _c: self.session))
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))

    async def fallback(self, gap=147, *, outcome='failed', move=True, visible=True, change=None):
        requested = XYZ(*self.target)
        async def transport(*_args, **_kwargs):
            state = getattr(self.client, '_quest_target_first_move', None)
            self.assertIsNotNone(state, 'baseline must precede fallback movement')
            if move:
                self.position = XYZ(requested.x-gap, requested.y, requested.z)
            elif hasattr(self.client, 'teleport'):
                await self.client.teleport(requested)
            self.range_visible = visible
            if change:
                change()
            return outcome  # Physical approach succeeds without exact-arrow arrival.
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            return await self.quester.move_until_quest_interaction(self.client, requested)

    async def test_observed_world_gate_and_entry_fallbacks_become_ready_and_x_once(self):
        for title, prompt, gap in (('世界之门', '按下 或 互动', 147),
                                   ('Power Core', '点击 进入', 159), ('Power Core', '点击 进入', 91),
                                   ('Power Core', '点击 进入', 0), ('Unlisted Entrance', 'Press X to Enter', 175)):
            for outcome in ('failed', 'exhausted'):
                with self.subTest(title=title, gap=gap, outcome=outcome):
                    self.setUp()
                    self.title, self.prompt = title, prompt
                    self.assertEqual(await self.fallback(gap, outcome=outcome), 'ready')
                    proof = await self.quester._interaction_eligibility(self.client, self.target)
                    self.assertTrue(proof.accepted, proof.reason)
                    self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                    self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                    self.assertEqual(self.keys, [Keycode.X])
                    self.stack.close()

    async def test_fallback_can_handoff_a_required_gateway_for_a_collect_task(self):
        self.objective = '收集 齿轮 地点：另一地区 (0/3)'
        self.assertEqual(await self.fallback(), 'ready')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(proof.reason, 'accepted-current-target-gateway')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_real_collision_strict_retreat_and_nav_fallbacks_use_observed_body_progress(self):
        for route in ('strict', 'retreat', 'navmap'):
            with self.subTest(route=route):
                self.setUp()
                events, outcomes = [], []
                endpoint = XYZ(self.target.x-147, self.target.y, self.target.z)
                async def teleport(point):
                    events.append(XYZ(*point))
                    if len(events) > 1:
                        self.position = XYZ(*point)
                self.client.teleport = teleport
                self.stack.enter_context(patch('src.collision.CollisionWorld', return_value=Mock()))
                self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'')))
                self.stack.enter_context(patch.object(movement, '_resolve_player_radius', AsyncMock(return_value=45)))
                self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
                self.stack.enter_context(patch('src.collision_math.blocking_volumes_at', return_value=[]))
                solve = Mock(side_effect=([(self.target, 'direct'), (endpoint, 'strict')]
                    if route == 'strict' else [(self.target, 'direct'), (None, 'no-strict')] if route == 'retreat'
                    else RuntimeError('no collision data')))
                self.stack.enter_context(patch('src.collision_math.find_walkable_teleport_point', solve))
                self.stack.enter_context(patch.object(movement, '_walk_remaining_to_target', AsyncMock(return_value=False)))
                self.stack.enter_context(patch.object(movement, 'load_wad', AsyncMock(side_effect=RuntimeError('no nav file'))))
                async def transport(client, target, **kwargs):
                    outcome = await movement.collision_tp(client, target, **kwargs)
                    outcomes.append(outcome)
                    self.range_visible = True  # HUD becomes readable at the handoff.
                    return outcome
                with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
                    result = await self.quester.move_until_quest_interaction(self.client, self.target)
                self.assertEqual(outcomes, ['failed' if route == 'strict' else 'ready'])
                self.assertGreaterEqual(len(events), 2)
                self.assertLessEqual(movement.calc_Distance(self.position, self.target), 250)
                self.assertEqual(result, 'ready')
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted, proof.reason)
                self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_failed_ordinary_alias_is_not_promoted_by_transition_repair(self):
        self.title, self.prompt = '在营地休息', '按下 或 交谈'
        self.objective = '休息 篝火 地点：任务地点'
        self.assertEqual(await self.fallback(147), 'failed')
        self.assertEqual(self.keys, [])

    async def test_noop_coordinate_write_only_or_wrong_direction_never_mints_a_proof(self):
        for kind in ('no-op-far', 'no-op-at-target', 'coordinate-write-only', 'away', 'tiny', 'outside-range'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'no-op-at-target': self.position = self.target
                self.client.teleport = AsyncMock()  # Requested coordinates do not move the observed body.
                move = kind not in ('no-op-far', 'no-op-at-target', 'coordinate-write-only')
                gap = {'away': 3000, 'tiny': 2617, 'outside-range': 251}.get(kind, 147)
                await self.fallback(gap, move=move)
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertFalse(proof.accepted, proof.reason)
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_failed_movement_without_a_current_transition_cannot_keep_provenance(self):
        for title, prompt, visible in (('Other NPC', 'Press X to Talk', True),
                                       ('Other Object', 'Press X to Use', True), ('世界之门', '按下 或 互动', False)):
            with self.subTest(title=title, visible=visible):
                self.setUp()
                self.title, self.prompt = title, prompt
                await self.fallback(147, visible=visible)
                self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
                self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_task_body_or_reference_change_during_fallback_never_publishes_provenance(self):
        for cause in ('quest', 'goal', 'target', 'zone', 'body', 'session', 'reference', 'stop', 'loading', 'battle'):
            with self.subTest(cause=cause):
                self.setUp()
                self.session = SimpleNamespace(transition={})
                def change():
                    if cause == 'quest': self.client.quest_id.return_value = 999
                    if cause == 'goal': self.goal += 1
                    if cause == 'target': self.target = XYZ(1100, 2100, 3)
                    if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                    if cause == 'body': self.client.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position))
                    if cause == 'session': self.session = SimpleNamespace(transition={})
                    if cause == 'reference': self.session.transition = {'fresh': True}
                    if cause == 'stop': self.client.questing_status = False
                    if cause == 'loading': self.client.is_loading.return_value = True
                    if cause == 'battle': self.client.in_battle.return_value = True
                await self.fallback(change=change)
                self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_existing_approach_is_revoked_after_body_session_or_reference_changes(self):
        for cause in ('body', 'session', 'reference', 'shared-task'):
            with self.subTest(cause=cause):
                self.setUp()
                self.session = SimpleNamespace(transition={})
                self.assertEqual(await self.fallback(outcome='ready'), 'ready')
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted, proof.reason)
                if cause == 'body': self.client.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position))
                if cause == 'session': self.session = SimpleNamespace(transition={})
                if cause == 'reference': self.session.transition = {'fresh': True}
                if cause == 'shared-task': self.session.transition['movement_task'] = await movement._movement_task_snapshot(self.client)
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertIsNone(self.client._quest_target_approach)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_cancelled_transition_input_still_reserves_the_single_x(self):
        await self.fallback(outcome='ready')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        def cancel(): raise asyncio.CancelledError()
        self.on_x = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.quester._send_quest_interaction_x(self.client, self.target, proof)
        self.on_x = lambda: None
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_name_matched_transition_fallback_is_ready_and_sends_only_once(self):
        self.title, self.prompt, self.objective = 'Power Core', 'Press X to Enter', 'Enter Power Core'
        self.assertEqual(await self.fallback(159), 'ready')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_reference_change_does_not_rearm_sent_name_matched_transition(self):
        self.title, self.prompt, self.objective = 'Power Core', 'Press X to Enter', 'Enter Power Core'
        await self.fallback(159)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.session = SimpleNamespace(transition={})
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_reference_change_in_last_async_read_prevents_x(self):
        await self.fallback(outcome='ready')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        reads = []
        async def changed(*_):
            if inspect.currentframe().f_back.f_code.co_name == '_quest_interaction_eligibility':
                reads.append(1)
                if len(reads) == 2:  # Both send-time proofs; mutate the final one.
                    self.session = SimpleNamespace(transition={})
            return False
        with patch.object(movement, '_movement_control_changed', changed):
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_last_peer_read_cannot_invalidate_an_earlier_members_body(self):
        await self.fallback(outcome='ready')
        self.quester.current_leader_client = self.client
        peers = []
        for title in ('p4', 'p5'):
            peer = copy(self.client)
            peer.title, peer.body, peer.send_key = title, copy(self.client.body), AsyncMock()
            peer._quest_target_approach = dict(self.client._quest_target_approach,
                context=movement._target_approach_context(peer, self.client))
            peers.append(peer)
        self.quester.clients = [self.client, *peers]
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        original, reads = self.quester._interaction_eligibility, []
        async def eligibility(client, *args, **kwargs):
            value = await original(client, *args, **kwargs)
            if client is peers[-1]:
                reads.append(1)
                if len(reads) == 2:
                    peers[0].body = copy(peers[0].body)
            return value
        self.quester._interaction_eligibility = eligibility
        self.assertFalse(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertEqual(self.keys, [])
        for peer in peers:
            peer.send_key.assert_not_awaited()


class EntryFallbackHandoffTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.target = XYZ(1000, 2000, 3)
        self.visible = False
        self.teamup = True
        self.q.position = XYZ(self.target.x-2618, self.target.y, self.target.z)
        self.h.position = XYZ(self.target.x-159, self.target.y, self.target.z)
        self.q.quest_position.position.return_value = self.target
        self.reader = Quester(self.q, [self.q], None)
        self.reader.read_quest_txt = AsyncMock(return_value='Go to next area')
        self.reader.read_popup = AsyncMock(return_value='Press X to Enter')
        visible = AsyncMock(side_effect=lambda c, path: self.visible and
            (path == npc_range_path or path == team_up_button_path and self.teamup))
        for module in ('src.questing', 'src.teleport_math'):
            self.stack.enter_context(patch(module+'.is_visible_by_path', visible))
            self.stack.enter_context(patch(module+'.get_popup_title', AsyncMock(return_value='Power Core')))
        self.stack.enter_context(patch.object(movement, '_movement_objective', AsyncMock(return_value='Go to next area')))
        self.stack.enter_context(patch.object(movement, 'get_window_from_path', AsyncMock(
            return_value=SimpleNamespace(maybe_text=AsyncMock(return_value='Press X to Enter')))))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Go to next area')))
        self.stack.enter_context(patch.object(self.reader, '_npc_dialogue_visible', AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.teleport_math.is_free', AsyncMock(return_value=True)))

    async def approach(self):
        async def move(*_, **__):
            self.q.position = XYZ(self.target.x-159, self.target.y, self.target.z)
            self.visible = True
            return 'failed'
        with patch('src.questing.collision_tp', AsyncMock(side_effect=move)):
            self.assertEqual(await self.reader.move_until_quest_interaction(self.q, self.target), 'ready')
        proof = await self.reader._interaction_eligibility(self.q, self.target)
        self.assertEqual(proof.reason, 'accepted-task-target-enter')
        return proof

    async def test_real_sigil_preparation_preserves_proven_approach_and_shared_x_once(self):
        proof = await asyncio.wait_for(self.approach(), 3)
        members = await asyncio.wait_for(self.reader._prepare_sigil_entry(interaction=proof), 3)
        self.assertEqual(members, [self.q, self.h])
        self.assertTrue((await self.reader._interaction_eligibility(
            self.q, self.target, expected=proof)).accepted)
        self.assertTrue(await self.reader._send_dungeon_entry_x(members, interaction=proof))
        self.assertFalse(await self.reader._send_dungeon_entry_x(members, interaction=proof))
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_sigil_handoff_does_not_allow_a_later_reference_replacement(self):
        proof = await self.approach()
        members = await self.reader._prepare_sigil_entry(interaction=proof)
        session = party.dungeon_session_for(self.q)
        session.transition = dict(session.transition)
        self.assertFalse(await self.reader._send_dungeon_entry_x(members, interaction=proof))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_real_non_teamup_caller_hands_off_before_shared_x(self):
        self.teamup = False
        await self.approach()
        for module, name in (('src.mainline_progress', 'log_mainline_progress'),
                             ('src.mainline_recovery', 'maybe_recover_mainline'),
                             ('src.questing', 'close_npc_quest_menu'),
                             ('src.questing', 'close_automation_popup'),
                             ('src.questing', 'is_potion_needed'),
                             ('src.questing', 'is_spiral_door_open')):
            self.stack.enter_context(patch(module+'.'+name, AsyncMock(return_value=False)))
        for name in ('handle_pending_dungeon_confirmation', '_quest_party_probe_blocks_movement',
                     'handle_questing_zone_change'):
            self.stack.enter_context(patch.object(self.reader, name, AsyncMock(return_value=False)))
        self.reader.teleport_to_quest_target = AsyncMock(return_value='ready')
        for c in (self.q, self.h):
            c.send_key.side_effect = lambda *_, c=c: setattr(c, 'zone', 'Dungeon/Room1')
        await asyncio.wait_for(self.reader.auto_quest_solo(), 4)
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)

