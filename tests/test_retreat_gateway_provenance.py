"""Real rejected TP -> retreat must hand its measured approach to the existing gateway."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from tests.test_transition_fallback_provenance import TransitionFallbackProvenanceTests as Base


class RetreatGatewayTests(Base):
    def setUp(self):
        super().setUp()
        self.client.quest_id.return_value = 122723090200526961
        self.goal = 740756935
        self.client.zone_name.return_value = 'Darkmoor/DM_Z00_OutsidersCamp'
        self.target = XYZ(-1311.09, 1674.37, .30)
        self.objective = '击败 当前任务目标 地点：另一区域'
        self.title, self.prompt = 'Zone of Fortune', '点击进入'
        self.nearby(273)
        self.events = []
        self.stack.enter_context(patch('src.collision.CollisionWorld', return_value=Mock()))
        self.stack.enter_context(patch('src.collision.get_collision_data', AsyncMock(return_value=b'')))
        self.stack.enter_context(patch.object(movement, '_resolve_player_radius', AsyncMock(return_value=45)))
        self.stack.enter_context(patch('src.entity_collision.build_zone_static_shapes', return_value=[]))
        self.stack.enter_context(patch('src.collision_math.blocking_volumes_at', return_value=[]))
        self.stack.enter_context(patch('src.collision_math.find_walkable_teleport_point', Mock(
            side_effect=[(self.target, 'direct'), (None, 'no-strict')])))
        self.stack.enter_context(patch.object(movement, 'load_wad', AsyncMock(side_effect=RuntimeError('no nav file'))))

    async def run_retreat(self, *, change=None, move=True):
        async def teleport(point):
            self.events.append(XYZ(*point))
            if len(self.events) > 1:
                if move:
                    self.position = XYZ(*point)
                self.range_visible = True
                if change:
                    change()
        self.client.teleport = teleport
        with patch('src.questing.collision_tp', movement.collision_tp):
            return await self.quester.move_until_quest_interaction(self.client, self.target)

    async def test_real_retreat_273_to_70_is_current_gateway_and_single_x(self):
        outcome = await self.run_retreat()
        self.assertAlmostEqual(movement.calc_Distance(self.position, self.target), 70)
        self.assertGreaterEqual(len(self.events), 2)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(outcome, 'ready', proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-target-gateway')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_probe_visible_at_start_retreat(self):
        self.range_visible = True
        outcome = await self.run_retreat()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertEqual(outcome, 'ready', proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-target-gateway')

    def caller(self):
        self.quester.teleport_to_quest_target = AsyncMock(return_value='ready')
        self.quester.handle_questing_zone_change = AsyncMock()
        self.quester._recover_door_approach = AsyncMock()

    async def test_no_ack_can_retry_quester_once_then_confirm_real_zone_entry(self):
        await self.run_retreat()
        self.caller()
        def enter_on_retry():
            if self.keys.count(Keycode.X) == 2:
                self.client.zone_name.return_value = 'Dungeon/Room1'
        self.on_x = enter_on_retry
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_no_ack_after_both_attempts_stays_unconfirmed_and_never_spams(self):
        await self.run_retreat()
        self.caller()
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])

    async def test_first_loading_ack_never_retries(self):
        await self.run_retreat()
        self.caller()
        self.on_x = lambda: setattr(self.client.is_loading, 'return_value', True)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_already_inside_an_old_session_is_not_new_entry_ack(self):
        await self.run_retreat()
        self.caller()
        # The earlier movement dispatcher is outside this entry round.
        with patch('src.questing.dungeon_member_inside', side_effect=[False, True] + [True]*1000):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])
        self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_new_confirmed_session_is_entry_ack(self):
        await self.run_retreat()
        self.caller()
        with patch('src.questing.dungeon_member_inside', side_effect=lambda _c: bool(self.keys)):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_new_stable_area_in_same_zone_is_entry_ack(self):
        await self.run_retreat()
        self.caller()
        async def token(_c):
            return (await self.client.zone_name(), 200 if self.keys else 100, 2, 0)
        with patch('src.questing._party_area_token', token):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_retreat_wrong_direction_and_changed_task_never_publish_proof(self):
        for cause in ('away', 'goal', 'quest', 'target', 'body', 'session', 'reference'):
            with self.subTest(cause=cause):
                self.setUp()
                self.session = SimpleNamespace(transition={})
                def change():
                    if cause == 'away': self.position = XYZ(self.target.x-350, self.target.y, self.target.z)
                    if cause == 'goal': self.goal += 1
                    if cause == 'quest': self.client.quest_id.return_value = 99
                    if cause == 'target': self.target = XYZ(self.target.x+100, self.target.y, self.target.z)
                    if cause == 'body': self.client.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position))
                    if cause == 'session': self.session = SimpleNamespace(transition={})
                    if cause == 'reference': self.session.transition = {'new': True}
                await self.run_retreat(change=change)
                self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_fresh_prompt_change_after_retreat_rejects_old_proof(self):
        await self.run_retreat()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.title = 'Different Entrance'
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_cancelled_first_x_is_reserved_and_never_replayed(self):
        await self.run_retreat()
        self.caller()
        def cancel():
            raise asyncio.CancelledError()
        self.on_x = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.auto_quest_solo()
        self.on_x = lambda: None
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_changed_prompt_task_body_reference_or_safety_after_x_cannot_retry(self):
        for cause in ('prompt', 'goal', 'quest', 'target', 'body', 'reference', 'stop', 'battle', 'owner'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.run_retreat()
                self.caller()
                def change():
                    if cause == 'prompt': self.title = 'Different Entrance'
                    if cause == 'goal': self.goal += 1
                    if cause == 'quest': self.client.quest_id.return_value = 99
                    if cause == 'target': self.target = XYZ(self.target.x+100, self.target.y, self.target.z)
                    if cause == 'body': self.client.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position))
                    if cause == 'reference': self.session = SimpleNamespace(transition={}, confirmed=False)
                    if cause == 'stop': self.client.questing_status = False
                    if cause == 'battle': self.client.in_battle.return_value = True
                    if cause == 'owner': self.client.quest_recovery_owner = 'other'
                self.on_x = change
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X])
                self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
                self.stack.close()

    async def test_delayed_loading_ack_prevents_retry(self):
        await self.run_retreat()
        self.caller()
        def load(_delay):
            if self.keys and self.clock > 3:
                self.client.is_loading.return_value = True
        self.after_sleep = load
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_readable_old_popup_without_actual_retreat_movement_never_qualifies(self):
        self.range_visible = True
        await self.run_retreat(move=False)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_retreat_proof_hands_off_to_this_source_only_once(self):
        await self.run_retreat()
        approach = self.client._quest_target_approach
        context = approach['context']
        self.session = SimpleNamespace(transition=dict(source_zone=approach['zone'],
            quester_task=approach['task'][:2]), confirmed=False)
        self.quester._handoff_entry_approach(approach, context, self.session)
        handed = approach['context']
        self.assertNotEqual(handed, context)
        self.assertTrue((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.session.transition = dict(self.session.transition)
        self.quester._handoff_entry_approach(approach, handed, self.session)
        self.assertEqual(approach['context'], handed)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [])

    async def test_entry_wait_keeps_only_this_proof_until_its_bounded_deadline(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                self.setUp()
                await self.run_retreat()
                self.caller()
                def age():
                    self.client._quest_target_approach['confirmed_at'] -= 10
                    if expired:
                        self.client._quest_interaction_retry['entry_deadline'] = -1
                self.on_x = age
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X] if expired else [Keycode.X, Keycode.X])
                self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
                self.stack.close()

    async def test_invalid_or_unstable_same_zone_area_never_proves_entry(self):
        for kind in ('invalid-address', 'unstable', 'observation-generation-only'):
            with self.subTest(kind=kind):
                self.setUp()
                await self.run_retreat()
                self.caller()
                reads = []
                async def token(_c):
                    reads.append(1)
                    address = 0 if kind == 'invalid-address' else 100 if kind == 'observation-generation-only' else len(reads)
                    return (await self.client.zone_name(), address, 2, int(bool(self.keys)))
                with patch('src.questing._party_area_token', token):
                    await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [] if kind == 'unstable' else [Keycode.X])
                self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
                self.stack.close()

    async def test_area_change_before_real_send_is_not_a_post_send_ack(self):
        await self.run_retreat()
        self.caller()
        address = [100]
        async def token(_c):
            return (await self.client.zone_name(), address[0], 2, 0)
        original = self.quester._send_dungeon_entry_x
        async def changed(*args, **kwargs):
            address[0] = 200
            return await original(*args, **kwargs)
        with patch('src.questing._party_area_token', token), patch.object(
                self.quester, '_send_dungeon_entry_x', changed):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [])
        self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_observed_transient_prompt_or_owner_change_ends_entry_round(self):
        for cause in ('prompt', 'owner', 'control'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.run_retreat()
                self.caller()
                started = self.clock
                def transient(_delay):
                    changed = bool(self.keys) and started+3 < self.clock < started+6
                    if cause == 'prompt': self.title = 'Different Entrance' if changed else 'Zone of Fortune'
                    if cause == 'owner': self.client.quest_recovery_owner = 'other' if changed else None
                    if cause == 'control': self.client._quest_movement_control = 'new' if changed else None
                self.after_sleep = transient
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X])
                self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
                self.stack.close()

    async def test_warning_confirmation_stays_in_the_entry_observation_loop(self):
        from src.paths import npc_range_path, dungeon_warning_path
        await self.run_retreat()
        self.caller()
        warning = [False]
        original = self.client.send_key
        async def send(key, seconds):
            await original(key, seconds)
            if key == Keycode.ENTER:
                warning[0] = False
                self.client.is_loading.return_value = True
        self.client.send_key = send
        self.on_x = lambda: warning.__setitem__(0, True)
        visible = AsyncMock(side_effect=lambda _c, path: path == npc_range_path and self.range_visible
            or path == dungeon_warning_path and warning[0])
        with patch('src.questing.is_visible_by_path', visible):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X, Keycode.ENTER])
        self.assertTrue(self.client._quest_interaction_retry.get('entry_acknowledged'))

    async def test_entry_area_read_cannot_bypass_final_task_prompt_safety_control_checks(self):
        for cause in ('goal', 'prompt', 'safety', 'control'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.run_retreat()
                self.caller()
                reads = []
                async def token(_c):
                    reads.append(1)
                    if len(reads) == 2:
                        if cause == 'goal': self.goal += 1
                        if cause == 'prompt': self.title = 'Different Entrance'
                        if cause == 'safety': self.client.refilling_potions = True
                        if cause == 'control': self.client._quest_movement_control = 'new'
                    return (await self.client.zone_name(), 100, 2, 0)
                with patch('src.questing._party_area_token', token):
                    await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [])
                self.assertFalse(self.client._quest_interaction_retry.get('entry_acknowledged'))
                self.stack.close()


del Base


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RetreatGatewayTests(name) for name in RetreatGatewayTests.__dict__
        if name.startswith('test_'))
