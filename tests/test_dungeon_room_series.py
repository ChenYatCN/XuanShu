"""Known suffix-room names are positive evidence, never an exit classifier."""
import unittest
from unittest.mock import AsyncMock, patch

from src import quest_party as party
from src.questing import Quester
import tests.test_dungeon_local_tp as fixtures


KAMBATA = 'Polaris/Interiors/PL_Z06_KambataUnderwater'
BABA = 'Polaris/Interiors/PL_Z02_BabaYagaStatuary'
WATERWORKS = 'WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_'
MUSEUM = 'Marleybone/Interiors/MB_MuseumStorage_'
SERIES_PAIRS = ((KAMBATA, KAMBATA+'02'), (BABA, BABA+'02'),
                (WATERWORKS+'01', WATERWORKS+'01a'),
                (WATERWORKS+'01a', WATERWORKS+'02'), (MUSEUM+'A', MUSEUM+'B'))


class RoomSeriesNamesTests(unittest.TestCase):
    def test_known_full_path_numeric_letter_and_numeric_letter_suffixes(self):
        for before, after in (*SERIES_PAIRS, (MUSEUM+'B', MUSEUM+'C')):
            with self.subTest(before=before, after=after):
                self.assertTrue(party.same_dungeon_room_series(before, after))
                self.assertTrue(party.same_dungeon_room_series(after, before))

    def test_unknown_common_prefix_directory_or_trailing_character_is_not_evidence(self):
        pairs = (
            ('Polaris/Interiors/House1', 'Polaris/Interiors/House2'),
            ('Polaris/Interiors/PL_Z06_KambataUnderwater', 'Polaris/Interiors/PL_Z06_KambataHall'),
            (KAMBATA, KAMBATA+'Other02'),
            (KAMBATA, KAMBATA+'_A'),
            (MUSEUM+'A', MUSEUM+'Annex'),
            (MUSEUM+'A', MUSEUM+'AB'),
            (MUSEUM+'A', 'OtherWorld/Interiors/MB_MuseumStorage_B'),
            (WATERWORKS+'01', WATERWORKS.replace('Gauntlet1/', 'Gauntlet2/')+'02'),
            ('World/Interiors/Library_A', 'World/Interiors/Library_B'),
            ('World/Area1', 'World/Area2'),
            ('World/AreaA', 'World/AreaB'),
            (None, MUSEUM+'B'), ('', MUSEUM+'B'),
        )
        for before, after in pairs:
            with self.subTest(before=before, after=after):
                self.assertFalse(party.same_dungeon_room_series(before, after))


class RoomSeriesSessionTests(fixtures.DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def enter(self, room=KAMBATA):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        for c in (self.q, self.h):
            c.zone, c.area_id = room, 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        return session

    async def test_known_series_preserves_session_and_requires_shared_room_goal(self):
        for before, after in SERIES_PAIRS:
            with self.subTest(before=before):
                self.setUp()
                session = await self.enter(before)
                self.q.zone, self.q.area_id = after, 201
                await party.observe_dungeon_session(self.q)
                self.assertIs(party.dungeon_session_for(self.q), session)
                self.assertIs(party.dungeon_session_for(self.h), session)
                self.assertEqual(session.exited, set())
                self.assertEqual(session.room_evidence[id(self.q)], ('room-series', before, after))
                with (patch.object(Quester, 'teleport_to_quest_target', AsyncMock()) as move,
                      patch.object(Quester, 'handle_questing_zone_change', AsyncMock())):
                    quester_reader = Quester(self.q, [self.q], None)
                    hitter_reader = Quester(self.h, [self.h], None)
                    self.assertFalse(await quester_reader.move_dungeon_member())
                    self.assertFalse(await hitter_reader.move_dungeon_member())
                    move.assert_not_awaited()
                    self.h.zone, self.h.area_id = after, 201
                    await party.observe_dungeon_session(self.h)
                    self.proof.return_value = False
                    self.assertFalse(await hitter_reader.move_dungeon_member())
                    move.assert_not_awaited()
                    self.proof.return_value = True
                    self.assertFalse(await quester_reader.move_dungeon_member())
                    self.assertTrue(await hitter_reader.move_dungeon_member())
                self.assertEqual(move.await_count, 1)
                call = move.await_args
                self.assertIs(call.args[0], self.h)
                target = self.q.quest_position.position.return_value
                self.assertEqual((call.args[1].x, call.args[1].y, call.args[1].z),
                                 (target.x, target.y, target.z))
                self.assertIs(call.kwargs.get('leader_client'), self.h)
                self.assertIs(party.dungeon_session_for(self.h), session)
                self.assertEqual(session.exited, set())

    async def test_unknown_names_and_script_counterexamples_keep_session_without_name_proof(self):
        pairs = (
            ('WizardCity/WC_Streets/WC_Drains/Z00_ClubHouse',
             'WizardCity/WC_Streets/WC_Drains/Z00_ClubBackyard'),
            ('Darkmoor/MainHall', 'Darkmoor/Armory'),
            ('Darkmoor/Armory', 'Darkmoor/Interiors/Staircase'),
            ('Polaris/Interiors/House1', 'Polaris/Interiors/House2'),
            (MUSEUM+'A', 'World/UnrecognizedArea'),
        )
        for before, after in pairs:
            with self.subTest(before=before):
                self.setUp()
                session = await self.enter(before)
                self.q.zone = after
                await party.observe_dungeon_session(self.q)
                self.assertIs(party.dungeon_session_for(self.q), session)
                self.assertTrue(party.dungeon_member_inside(self.q))
                self.assertEqual(session.exited, set())
                self.assertNotIn(id(self.q), session.room_evidence)

    async def test_return_to_source_wins_even_when_names_are_same_series(self):
        for c in (self.q, self.h):
            c.zone = MUSEUM+'A'
        session = await self.enter(MUSEUM+'B')
        self.q.zone = MUSEUM+'A'
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.exited)
        self.assertEqual(session.exit_evidence[id(self.q)][0], 'source-zone')
        self.assertNotIn(id(self.q), session.room_evidence)

    async def test_exit_confirmation_wins_even_when_names_are_same_series(self):
        session = await self.enter(MUSEUM+'A')
        party.record_dungeon_exit(self.h, MUSEUM+'A', MUSEUM+'B')
        self.h.zone = MUSEUM+'B'
        await party.observe_dungeon_session(self.h)
        self.assertIn(id(self.h), session.exited)
        self.assertEqual(session.exit_evidence[id(self.h)][0], 'GUI_InstanceExit')
        self.assertNotIn(id(self.h), session.room_evidence)

    async def test_known_name_does_not_confirm_entry_or_release_existing_probe(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=False)
        self.q.zone = self.h.zone = MUSEUM+'A'
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.assertFalse(party.dungeon_member_inside(self.q))
        session.entry_evidence = True
        await party.observe_dungeon_session(self.q)
        self.q.quest_party_quest_worker_zone = MUSEUM+'A'
        self.q.zone = MUSEUM+'B'
        reader = Quester(self.q, [self.q], None)
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(session.room_evidence[id(self.q)][0], 'room-series')
        self.q.quest_party_probe_pending = False
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.h.zone = MUSEUM+'B'
        await party.observe_dungeon_session(self.h)
        self.proof.return_value = False
        self.q.quest_party_probe_pending = False
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.proof.return_value = True
        self.q.quest_party_probe_pending = False
        self.assertFalse(await reader._quest_party_probe_blocks_movement())

    async def test_potion_trip_keeps_pause_and_name_cannot_prove_return(self):
        session = await self.enter(MUSEUM+'A')
        await party.pause_dungeon_for_potions(self.q)
        self.q.refilling_potions = True
        self.q.zone = 'Outside'
        await party.observe_dungeon_session(self.q)
        self.assertEqual(session.exited, set())
        self.q.refilling_potions = False
        self.q.zone, self.q.area_id = MUSEUM+'B', 201
        await party.finish_dungeon_potions(self.q, recalled=True)
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.potion_areas)
        self.assertFalse(party.dungeon_member_inside(self.q))
        self.assertNotIn(id(self.q), session.room_evidence)
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_unknown_transition_clears_only_the_current_positive_name_evidence(self):
        session = await self.enter(MUSEUM+'A')
        self.q.zone = MUSEUM+'B'
        await party.observe_dungeon_session(self.q)
        self.assertIn(id(self.q), session.room_evidence)
        self.q.zone = 'World/Unknown'
        await party.observe_dungeon_session(self.q)
        self.assertNotIn(id(self.q), session.room_evidence)
        self.assertEqual(session.exited, set())
        self.assertIs(party.dungeon_session_for(self.q), session)
