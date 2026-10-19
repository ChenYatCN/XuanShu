"""Qualified exterior entrances replace a completed, unobserved old dungeon."""
import asyncio
from itertools import count
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from wizwalker import XYZ, Keycode
from src import quest_party as party
from src.questing import Quester
from src.automation_ownership import get_client_automation_ownership
from tests.test_dungeon_entry_receipt_lifecycle import EntryReceiptLifecycleTests as Base
from tests.test_dungeon_local_tp import client, REAL_SLEEP


class NewDungeonSessionTests(Base):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(party, '_DUNGEON_GENERATIONS', count(6)))
        self.h3 = client(3, self.target)
        self.h3.position = self.target
        self.h3.prompt = self.h3.teamup = True
        self.h3.title_text = self.q.title_text
        self.h3.quest_party_quester = self.q
        self.h3.quest_party_status_session = self.h.quest_party_status_session
        self.members = [self.q, self.h, self.h3]
        self.q.quest_party_hitters = [self.h, self.h3]
        for member in self.members:
            member.hotkey_quest_clients = self.members

    async def previous_dungeon(self):
        await self.interact()
        old = party.dungeon_session_for(self.q)
        self.assertEqual(old.generation, 6)
        for member in self.members:
            member.zone, member.area_id, member.prompt = 'Dungeon/Room1', 200, False
        await party.observe_dungeon_session(self.q)
        self.assertTrue(old.confirmed)
        old.entry_wait = None  # Arrival countdown has completed.
        return old

    def new_entrance(self, zone='Outside'):
        self.q.goal_id.return_value = 8
        for member in self.members:
            member.zone, member.area_id = zone, 100 if zone == 'Outside' else 201
            member.position = XYZ(1500, 1600, 0)
            member.quest_position.position.return_value = member.position
            member.prompt = member.teamup = True
            member.title_text = 'Next Dungeon'

    async def test_new_external_popup_retires_generation_six_and_shared_x_uses_seven(self):
        old = await self.previous_dungeon()
        self.new_entrance()  # Missed exit observations leave generation 6 attached.
        lines = []
        sink = logger.add(lambda message: lines.append(str(message)), level='DEBUG')
        try:
            await self.interact()
        finally:
            logger.remove(sink)
        new = party.dungeon_session_for(self.q)
        self.assertIsNot(new, old)
        self.assertEqual(old.phase, 'retired')
        self.assertEqual(new.generation, 7)
        self.assertTrue(new.transition['entry_sent'])
        for member in self.members:
            self.assertIs(party.dungeon_session_for(member), new)
            self.assertEqual(member.send_key.await_count, 2)
            self.assertEqual(member.send_key.await_args.args, (Keycode.X, .1))
        self.assertTrue(any("phase=shared-x members=['p1', 'p2', 'p3']" in line for line in lines))
        self.assertFalse(any('stage=session-reused' in line and 'generation=6 ' in line for line in lines))

    async def test_old_shared_worker_drains_before_new_entrance_x(self):
        old = await self.previous_dungeon()
        started, drained = asyncio.Event(), asyncio.Event()
        worker = Quester(self.h, [self.h], None)
        async def moving(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await REAL_SLEEP(.01)
                drained.set()
        worker.teleport_to_quest_target = moving
        task = asyncio.create_task(worker.move_dungeon_member())
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual(get_client_automation_ownership(self.h).owner_label, 'dungeon-shared-target')
            self.new_entrance()
            await self.interact()
            self.assertIsNot(party.dungeon_session_for(self.q), old)
            self.assertTrue(drained.is_set())
            self.assertTrue(task.done())
            self.assertFalse(get_client_automation_ownership(self.h).locked)
            self.assertEqual(self.h.send_key.await_count, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_internal_room_popup_does_not_start_another_generation(self):
        old = await self.previous_dungeon()
        self.new_entrance(zone='Dungeon/UnlistedRoom2')
        await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), old)
        self.assertEqual(old.generation, 6)

    async def test_quester_still_inside_does_not_retire_with_external_hitters(self):
        old = await self.previous_dungeon()
        self.new_entrance()
        self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
        await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), old)
        self.assertEqual(old.phase, 'active')

    async def test_same_old_entry_context_never_gets_a_fresh_generation(self):
        old = await self.previous_dungeon()
        for member in self.members:
            member.zone, member.area_id, member.prompt = 'Outside', 100, True
        await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), old)
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_new_popup_title_alone_is_not_a_new_lifecycle(self):
        old = await self.previous_dungeon()
        for member in self.members:
            member.zone, member.area_id, member.prompt = 'Outside', 100, True
            member.title_text = 'Different Popup Title'
        await self.interact()
        self.assertIs(party.dungeon_session_for(self.q), old)

    async def test_all_members_need_stable_exterior_evidence_before_retiring(self):
        first = True
        for unsafe in ('hitter-inside', 'loading', 'battle', 'potions', 'different-instance', 'unreadable-area'):
            with self.subTest(unsafe=unsafe):
                if not first:
                    self.stack.close()
                    self.setUp()
                first = False
                old = await self.previous_dungeon()
                self.new_entrance()
                if unsafe == 'hitter-inside':
                    self.h3.zone, self.h3.area_id = 'Dungeon/Room2', 201
                elif unsafe == 'loading':
                    self.h3.is_loading.return_value = True
                elif unsafe == 'battle':
                    self.h3.in_battle.return_value = True
                elif unsafe == 'potions':
                    self.h3.refilling_potions = True
                elif unsafe == 'different-instance':
                    self.proof.return_value = False
                else:
                    self.h3.client_object.client_zone.side_effect = ValueError('area unreadable')
                await self.interact()
                self.assertIs(party.dungeon_session_for(self.q), old)
                self.assertNotEqual(old.phase, 'retired')

    async def test_original_entry_context_survives_ordinary_receipt_and_shared_target(self):
        old = await self.previous_dungeon()
        original_key = getattr(old, 'entry_key', None)
        # Ordinary X receipts and the current shared target replace transient
        # records while the immutable exterior entry context must survive.
        self.q._deimos_quest_x = dict(key=('ordinary',), attempts=1)
        self.assertTrue(await party.remember_dungeon_transition(self.q, self.target))
        self.new_entrance()
        await self.interact()
        self.assertEqual(old.entry_key, original_key)
        self.assertEqual(party.dungeon_session_for(self.q).generation, 7)

    async def test_concurrent_new_entrance_is_reserved_while_old_owner_drains(self):
        from src.automation_ownership import automation_owner
        old = await self.previous_dungeon()
        started, release = asyncio.Event(), asyncio.Event()
        async def previous_input():
            async with automation_owner(self.h, 'dungeon-shared-target'):
                started.set()
                await release.wait()
        owner = asyncio.create_task(previous_input())
        first = None
        try:
            await started.wait()
            self.new_entrance()
            first = asyncio.create_task(self.interact())
            for _ in range(20):
                if old.phase == 'retired':
                    break
                await REAL_SLEEP(0)
            self.assertEqual(old.phase, 'retired')
            await self.interact()
            self.assertIsNone(party.dungeon_session_for(self.q))
            self.assertEqual(self.q.send_key.await_count, 1)
            release.set()
            await first
            self.assertEqual(party.dungeon_session_for(self.q).generation, 7)
            self.assertEqual(self.q.send_key.await_count, 2)
        finally:
            release.set()
            if first is not None:
                first.cancel()
            await asyncio.gather(owner, *([first] if first else []), return_exceptions=True)

    async def test_recovery_starting_during_exterior_proof_keeps_old_session(self):
        old = await self.previous_dungeon()
        self.new_entrance()
        original = self.reader._deimos_quest_prompt
        reads = 0
        async def popup(member):
            nonlocal reads
            result = await original(member)
            reads += 1
            if reads == 2:
                self.h3.quest_recovery_owner = 'other-recovery'
            return result
        with patch.object(self.reader, '_deimos_quest_prompt', popup):
            await self.interact()
        self.assertTrue(party.dungeon_session_for(self.q) is old)
        self.assertEqual(old.phase, 'active')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(NewDungeonSessionTests(name)
        for name in NewDungeonSessionTests.__dict__ if name.startswith('test_'))
