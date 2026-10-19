"""Task-related interaction handoff and dispatch; all game I/O is fake."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src import quest_party as party
from tests.test_stale_interaction_movement import Fixture
from tests import test_dungeon_entry_flow as entry_fixture


class CurrentTaskInteractionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective = 'Use Task Lever'
        self.title = 'Task Lever'
        self.prompt = 'Press X to Use'
        self.nearby(400)
        self.stack.enter_context(patch('src.questing.safe_click_window', AsyncMock()))
        self.quester.leader_wait_for_free = AsyncMock()
        self.stack.enter_context(patch('src.questing.get_quest_name',
            AsyncMock(side_effect=lambda _: self.objective)))
        self.stack.enter_context(patch.object(movement, '_movement_objective',
            AsyncMock(side_effect=lambda _: self.objective), create=True))
        self.stack.enter_context(patch.object(movement, '_movement_interaction_signature',
            AsyncMock(side_effect=lambda *_a, **_k: (self.title, self.prompt))))

    async def handoff(self):
        task = await movement._movement_task_snapshot(self.client)
        return await movement._movement_interaction_handoff(self.client, self.target, task)

    async def test_400u_visible_prompt_is_a_current_task_interaction(self):
        self.objective, self.title, self.prompt = 'Talk to Task NPC', 'Unrelated NPC', 'Press X to Talk'
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_400u_collect_prompt_is_not_semantically_vetoed(self):
        self.title, self.prompt = 'Unrelated Flower', '按下 采集'
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_correct_npc_handoff_then_real_dialogue_progress(self):
        self.objective, self.title, self.prompt = 'Talk to Task NPC', 'Task NPC', 'Press X to Talk'
        self.assertTrue(await self.handoff())
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertEqual(self.goal, 8)

    async def test_correct_use_once_without_creating_dungeon_session(self):
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])
        self.assertIsNone(party.dungeon_session_for(self.client))
        self.assertFalse(getattr(self.client, '_quest_party_entry', None))

    async def test_title_change_does_not_require_a_second_semantic_gate(self):
        self.objective = 'Use Task Lever or Task Switch'
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.keys, [Keycode.X])

    async def test_unrelated_direct_npc_cannot_create_recovery_context(self):
        self.objective, self.title, self.prompt = 'Talk to Task NPC', 'Unrelated NPC', 'Press X to Talk'
        self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertEqual(self.keys, [])
        self.assertIsNone(getattr(self.client, '_quest_interaction_retry', None))

    async def test_leader_visible_prompt_dispatches_once_without_evaluator(self):
        self.title = 'Unrelated NPC'
        self.quester.current_leader_client = self.client
        with patch.object(self.quester, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('evaluator consulted'))):
            await self.quester.handle_normal_quests([], False)
        self.assertEqual(self.keys, [Keycode.X])

    async def test_leader_correct_use_keeps_one_x(self):
        self.quester.current_leader_client = self.client
        self.on_x = lambda: setattr(self, 'goal', 8)
        await self.quester.handle_normal_quests([], False)
        self.assertEqual(self.keys, [Keycode.X])

    async def test_leader_original_control_proof_cannot_be_replaced(self):
        self.quester.current_leader_client = self.client
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        pet = SimpleNamespace(position=self.client.body.position,
                              parent_client_object=AsyncMock(return_value=parent))
        with patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))):
            self.assertFalse(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertEqual(self.keys, [])

    def group_peer(self):
        peer = SimpleNamespace(**vars(self.client))
        if hasattr(peer, '_xuanshu_automation_ownership'):
            del peer._xuanshu_automation_ownership
        peer.title = 'p4'
        peer.send_key = AsyncMock()
        self.quester.clients = [self.client, peer]
        self.quester.current_leader_client = self.client
        return peer

    async def test_leader_batch_does_not_drop_peer_after_first_member_progress(self):
        peer = self.group_peer()
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.on_x = lambda: setattr(self, 'goal', 8)
        self.assertTrue(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])
        peer.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_leader_cannot_authorize_same_coordinates_in_another_zone(self):
        peer = self.group_peer()
        peer.zone_name = AsyncMock(return_value='Other/Zone')
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertEqual(self.keys, [])
        peer.send_key.assert_not_awaited()

    async def test_final_input_rejects_changed_snapshot_fields(self):
        for cause in ('quest', 'goal', 'zone', 'objective', 'target', 'title', 'action'):
            with self.subTest(cause=cause):
                self.setUp()
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted)
                if cause == 'quest': self.client.quest_id.return_value = 99
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other'
                if cause == 'objective': self.objective = 'Use Task Lever now'
                if cause == 'target': self.target = XYZ(1001, 2000, 3)
                if cause == 'title': self.title = 'Other'
                if cause == 'action': self.prompt = 'Press X to Talk'
                self.assertFalse(await self.quester._send_quest_interaction_x(
                    self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_tp_failure_appearing_after_handoff_revokes_final_x(self):
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        task = await movement._movement_task_snapshot(self.client)
        self.client._quest_tp_rejected = dict(
            key=(task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)),
            control=None, walks=1, anchor=self.position, origin=self.position,
            rejected=True, confirmed=False, ready=False, announced=False,
            origin_signature=(self.title, self.prompt))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_unsafe_loading_cannot_grant_common_handoff(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await self.handoff())

    async def test_other_input_owner_prevents_handoff(self):
        import asyncio
        from src.automation_ownership import automation_owner
        entered, release = asyncio.Event(), asyncio.Event()
        async def other():
            async with automation_owner(self.client, 'combat'):
                entered.set()
                await release.wait()
        task = asyncio.create_task(other())
        await entered.wait()
        try:
            self.assertFalse(await self.handoff())
        finally:
            release.set()
            await task


class EntranceEligibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Compose the full existing fixture without rediscovering its tests.
        self.entry = entry_fixture.EntryFlowTests()
        self.entry.setUp()
        self.addCleanup(self.entry.stack.close)
        self.entry.setup_caller()

    async def test_far_entry_is_only_pressed_after_navigation_reaches_target(self):
        f = self.entry
        f.fast_arrival()
        f.q.quest_position.position.return_value = XYZ(10000, 600, 0)
        f.reader.read_quest_txt.return_value = 'Use Task Lever'
        await f.reader.auto_quest_solo()
        self.assertTrue(f.events, 'normal navigation must run before any interaction')
        f.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        f.h.send_key.assert_not_awaited()

    async def test_correct_use_cannot_prepare_hitters_unrelated_sigil(self):
        f = self.entry
        f.q.title_text, f.q.teamup = 'Task Lever', False
        f.reader.read_quest_txt.return_value = 'Use Task Lever'
        f.reader.read_popup.side_effect = lambda c: 'Press X to Use' if c is f.q else 'Press X to Enter'
        def advance(key, seconds):
            if key == Keycode.X: f.q.goal_id.return_value = 8
        f.q.send_key.side_effect = advance
        await f.reader.auto_quest_solo()
        f.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        f.h.send_key.assert_not_awaited()
        self.assertEqual(f.resets(), [])
        self.assertTrue(all(c is f.q and kind == 'approach' for c, kind, point in f.events))
        self.assertIsNone(party.dungeon_session_for(f.q))

    async def test_correct_enter_cannot_capture_hitters_different_sigil(self):
        f = self.entry
        f.q.teamup = False  # Generic current-task entrance, no quester Team Up.
        f.h.title_text = 'Unrelated Dungeon'
        await f.reader.auto_quest_solo()
        f.q.send_key.assert_not_awaited()
        f.h.send_key.assert_not_awaited()
        self.assertEqual(f.resets(), [])
        self.assertTrue(all(c is f.q and kind == 'approach' for c, kind, point in f.events))
        self.assertFalse(await party.dungeon_entry_waiting(f.q))

    async def test_confirmed_dungeon_ordinary_use_has_no_entry_side_effects(self):
        f = self.entry
        session = await f.enter()
        source = session.transition
        for c in (f.q, f.h):
            c.title_text, c.teamup = 'Task Lever', False
        f.reader.read_quest_txt.return_value = 'Use Task Lever'
        f.reader.read_popup.side_effect = lambda _: 'Press X to Use'
        f.reader._confirmed_dungeon_target = AsyncMock(return_value=await movement._movement_task_snapshot(f.q))
        f.reader.move_dungeon_member = AsyncMock(return_value=True)
        f.reader._recover_quest_x_direction = AsyncMock(return_value=False)
        await f.reader.auto_quest_solo()
        f.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        f.h.send_key.assert_not_awaited()
        self.assertIs(party.dungeon_session_for(f.q), session)
        self.assertIs(session.transition, source)
        self.assertIsNone(session.entry_wait)
        self.assertIsNone(getattr(f.q, '_quest_party_entry', None))
