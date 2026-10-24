"""Ordinary party entrances never downgrade to quester-only input."""
import unittest

from loguru import logger
from wizwalker import Keycode, XYZ
from tests.test_dungeon_entry_flow import EntryFlowTests
from tests.test_dungeon_local_tp import client


class PartyEntryAllReadyTests(EntryFlowTests):
    async def test_singleton_sender_uses_existing_whole_group_checks_and_shared_x(self):
        await self.reader.prepare_party_dungeon_entry()
        self.h.prompt = False
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q]))
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.h.prompt = True
        self.assertTrue(await self.reader._send_dungeon_entry_x([self.q]))
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.h.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(await self.reader._send_dungeon_entry_x([self.q]))
        self.assertEqual(self.q.send_key.await_count, 1)

    async def test_three_ready_members_all_receive_one_x(self):
        third = client(3, self.target)
        third.position = self.target
        third.prompt = third.teamup = True
        third.title_text = self.q.title_text
        third.quest_party_quester = self.q
        third.quest_party_status_session = self.h.quest_party_status_session
        self.q.quest_party_hitters = [self.h, third]
        for member in (self.q, self.h, third):
            member.hotkey_quest_clients = [self.q, self.h, third]
        members = await self.reader.prepare_party_dungeon_entry()
        self.assertEqual(members, [self.q, self.h, third])
        self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        for member in members:
            member.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_wait_logs_identify_hitter_and_existing_condition(self):
        await self.reader.prepare_party_dungeon_entry()
        self.reader.teleport_to_quest_target.side_effect = lambda *a, **kw: None
        cases = [('prompt', False, 'interaction-prompt'), ('position', XYZ(0, 0, -10000), 'distance'),
                 ('feeding_pet_status', True, 'feeding_pet_status'),
                 ('refilling_potions', True, 'refilling_potions'),
                 ('questing_status', False, 'questing_status=False')]
        for name, value, condition in cases:
            with self.subTest(condition=condition):
                previous = getattr(self.h, name)
                messages = []
                sink = logger.add(lambda message: messages.append(message.record['message']))
                try:
                    setattr(self.h, name, value)
                    self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
                finally:
                    setattr(self.h, name, previous)
                    logger.remove(sink)
                self.assertTrue(any('p2' in line and condition in line for line in messages), messages)
                self.q.send_key.assert_not_awaited()
                self.h.send_key.assert_not_awaited()

    async def test_real_single_client_and_single_client_solo_zone_still_send_x(self):
        self.q.quest_party_hitters = []
        self.q.hotkey_quest_clients = [self.q]
        for solo in (False, True):
            with self.subTest(solo=solo):
                self.q.in_solo_zone = solo
                members = await self.reader.prepare_party_dungeon_entry()
                self.assertEqual(members, [self.q])
                self.assertTrue(await self.reader._send_dungeon_entry_x(members))
        self.assertEqual(self.q.send_key.await_count, 2)
        self.h.send_key.assert_not_awaited()

    async def test_first_prepare_logs_the_actual_blocked_hitter_condition(self):
        for flag, condition in [('loading', 'loading'), ('refilling_potions', 'refilling_potions'),
                                ('questing_status', 'questing_status=False')]:
            with self.subTest(condition=condition):
                messages = []
                sink = logger.add(lambda message: messages.append(message.record['message']))
                try:
                    if flag == 'loading':
                        self.h.is_loading.return_value = True
                    else:
                        setattr(self.h, flag, flag != 'questing_status')
                    self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
                finally:
                    self.h.is_loading.return_value = False
                    self.h.refilling_potions = False
                    self.h.questing_status = True
                    logger.remove(sink)
                self.assertTrue(any('p2' in line and condition in line for line in messages), messages)
                self.q.send_key.assert_not_awaited()
                self.h.send_key.assert_not_awaited()

    async def test_multiple_missing_prompts_keep_one_deduplicated_wait_message(self):
        await self.reader.prepare_party_dungeon_entry()
        self.reader.teleport_to_quest_target.side_effect = lambda *a, **kw: None
        self.q.prompt = self.h.prompt = False
        messages = []
        sink = logger.add(lambda message: messages.append(message.record['message'])
            if message.record['level'].name == 'WARNING' else None)
        try:
            self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
            self.assertEqual(await self.reader.prepare_party_dungeon_entry(), [])
        finally:
            logger.remove(sink)
        waits = [line for line in messages if 'condition=interaction-prompt' in line]
        self.assertEqual(len(waits), 1, waits)
        self.assertIn('p1', waits[0])
        self.q.send_key.assert_not_awaited()
        self.h.send_key.assert_not_awaited()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PartyEntryAllReadyTests(name)
        for name in PartyEntryAllReadyTests.__dict__ if name.startswith('test_'))
