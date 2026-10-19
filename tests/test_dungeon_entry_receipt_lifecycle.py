"""Entry receipts cannot outlive the actual stopped/retired/hooked lifecycle."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src import quest_party as party
from src.questing import Quester
from src.automation_ownership import get_client_automation_ownership
from tests.test_dungeon_entry_flow import EntryFlowTests


class EntryReceiptLifecycleTests(EntryFlowTests):
    def setUp(self):
        super().setUp()
        self.setup_caller()
        # Only the game loading/arrival outcome is external. Classification,
        # party preparation, session creation and sending X are real code.
        self.reader._await_dungeon_entry_outcome = AsyncMock(return_value=False)

    async def interact(self, reader=None):
        return await (reader or self.reader)._try_deimos_interaction(self.q, [self.q])

    def apply_roles(self, active):
        from tests.test_full_chain_415_restore import entry
        resolved = party.QuestParty([self.q], [self.h], [], [(self.h, self.q)])
        apply = entry('apply_questing_roles', dict(walker=SimpleNamespace(clients=[self.q, self.h])),
                      current_quest_party=lambda _members=None: resolved)
        apply(active, None)

    async def test_a1_fresh_qualified_entry_sends_x_exactly_once(self):
        self.assertTrue(await self.interact())
        self.assertEqual(self.q.send_key.await_count, 1)
        self.assertEqual(self.q.send_key.await_args.args, (Keycode.X, .1))
        self.assertTrue(party.dungeon_session_for(self.q).transition['entry_sent'])

    async def test_a2_active_session_sent_entry_never_spams_even_after_deadline(self):
        await self.interact()
        session = party.dungeon_session_for(self.q)
        self.clock += 60
        for _ in range(4):
            await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), session)
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_a3_stop_during_pending_outcome_then_restart_same_entrance(self):
        entered_wait = asyncio.Event()
        async def pending(*_):
            entered_wait.set()
            await asyncio.Event().wait()
        self.reader._await_dungeon_entry_outcome.side_effect = pending
        task = asyncio.create_task(self.interact())
        try:
            await asyncio.wait_for(entered_wait.wait(), 2)
            old = party.dungeon_session_for(self.q)
            self.assertEqual(self.q.send_key.await_count, 1)
            self.apply_roles(False)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.apply_roles(True)
            # The normal startup probe completes before this qualified entry.
            self.q.quest_party_probe_pending = self.h.quest_party_probe_pending = False
            self.reader._await_dungeon_entry_outcome.side_effect = None
            self.reader._await_dungeon_entry_outcome.return_value = False
            await self.interact()
            await self.interact()
            new = party.dungeon_session_for(self.q)
            self.assertIsNotNone(new)
            self.assertIsNot(new, old)
            self.assertEqual(self.q.send_key.await_count, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_a4_retired_session_cannot_suppress_new_entry(self):
        await self.interact()
        old = party.dungeon_session_for(self.q)
        party.retire_dungeon_session(old, reason='test-ended')
        await self.interact()
        new = party.dungeon_session_for(self.q)
        self.assertIsNotNone(new)
        self.assertGreater(new.generation, old.generation)
        self.assertEqual(self.q.send_key.await_count, 2,
                         getattr(self.q, '_logging_blocks', None))

    async def test_a5_actual_reinit_clears_orphan_entry_receipt(self):
        from tests.test_full_chain_415_restore import entry, codes, APP
        import inspect
        await self.interact()
        party.clear_dungeon_session(self.q)
        self.q.window_handle = 123
        attached = SimpleNamespace(object_name=AsyncMock(return_value='PlayerObject'))
        camera = SimpleNamespace(attached_client_object=AsyncMock(return_value=attached))
        self.q.game_client = SimpleNamespace(user_id=AsyncMock(return_value=0),
            elastic_camera_controller=AsyncMock(return_value=camera))
        self.q.client_object.speed_multiplier = AsyncMock(return_value=1.0)
        code = next(c for c in codes(inspect.unwrap(APP.main).__code__)
                    if c.co_name == '_init_client_attrs')
        settings = dict.fromkeys(code.co_freevars)
        settings.update({k: v for k, v in dict(client_speeds={}, launched_account_map={},
            combat_playstyle_overrides={}, default_config=object(), active_combat_playstyle=None).items()
            if k in settings})
        initialize = entry('_init_client_attrs', dict(client_speeds={}, launched_account_map={},
            combat_playstyle_overrides={}, default_config=object(), active_combat_playstyle=None), **settings)
        await initialize(self.q)
        self.assertIsNone(getattr(self.q, '_deimos_quest_x', None))
        self.q.questing_status = True
        self.q.quest_party_hitters = [self.h]
        self.assertIsNotNone(await self.reader._deimos_quest_prompt(self.q))
        self.assertTrue(await self.interact())
        self.assertEqual(self.q.send_key.await_count, 2,
            (getattr(self.q, '_logging_blocks', None), getattr(self.q, '_dungeon_entry_flow_logs', None)))

    async def test_a6_worker_recreation_does_not_refund_live_session_x(self):
        await self.interact()
        reader = Quester(self.q, [self.q], None)
        reader.read_quest_txt = self.reader.read_quest_txt
        reader._await_dungeon_entry_outcome = AsyncMock(return_value=False)
        await self.interact(reader)
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_a7_ordinary_evaluator_is_not_in_entrance_permission(self):
        with patch.object(self.reader, '_interaction_eligibility',
                          AsyncMock(side_effect=AssertionError('legacy evaluator reached'))):
            await self.interact()
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_cancelled_before_send_does_not_leave_unowned_receipt(self):
        preparing = asyncio.Event()
        async def pending():
            preparing.set()
            await asyncio.Event().wait()
        with patch.object(self.reader, 'prepare_party_dungeon_entry', AsyncMock(side_effect=pending)):
            task = asyncio.create_task(self.interact())
            try:
                await asyncio.wait_for(preparing.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.q.send_key.await_count, 0)
        await self.interact()
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_inflight_prepare_does_not_allow_a_second_entry_owner(self):
        original = self.reader.prepare_party_dungeon_entry
        preparing, release = asyncio.Event(), asyncio.Event()
        async def pending():
            preparing.set()
            await release.wait()
            return await original()
        with patch.object(self.reader, 'prepare_party_dungeon_entry', AsyncMock(side_effect=pending)):
            task = asyncio.create_task(self.interact())
            try:
                await asyncio.wait_for(preparing.wait(), 2)
                await self.interact()
                self.assertEqual(self.q.send_key.await_count, 0)
                release.set()
                await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_cancelled_in_real_sender_preflight_refunds_only_unsent_entry(self):
        original = self.reader._sigil_entry_readiness
        preflight = asyncio.Event()
        async def readiness(state, **kwargs):
            if get_client_automation_ownership(self.q).owner_label == 'dungeon-entry':
                preflight.set()
                await asyncio.Event().wait()
            return await original(state, **kwargs)
        with patch.object(self.reader, '_sigil_entry_readiness', AsyncMock(side_effect=readiness)):
            task = asyncio.create_task(self.interact())
            try:
                await asyncio.wait_for(preflight.wait(), 2)
                self.assertEqual(self.q.send_key.await_count, 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        session = party.dungeon_session_for(self.q)
        self.assertFalse(session.transition.get('entry_sent'))
        await self.interact()
        await self.interact()
        self.assertEqual(self.q.send_key.await_count, 1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(EntryReceiptLifecycleTests(name)
        for name in EntryReceiptLifecycleTests.__dict__ if name.startswith('test_'))
