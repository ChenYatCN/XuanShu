import unittest
from unittest.mock import AsyncMock, patch

from src.questing import Quester


class QuestPartyDungeonTests(unittest.IsolatedAsyncioTestCase):
    def make_party(self):
        quester_client = AsyncMock()
        hitter = AsyncMock()
        quester_client.quest_party_hitters = [hitter]
        quester_client.is_loading.return_value = False
        hitter.is_loading.return_value = False
        hitter.quest_party_confirmed_dungeon_transition = None
        quester_client.zone_name.return_value = 'Dungeon/RoomA'
        hitter.zone_name.return_value = 'Dungeon/RoomB'
        return Quester(quester_client, [quester_client], None), hitter

    async def test_all_entered_even_when_their_dungeon_zones_differ(self):
        quester, hitter = self.make_party()
        self.assertTrue(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'
        ))

    async def test_incomplete_entry_keeps_the_existing_probe(self):
        quester, hitter = self.make_party()
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client], 'World/Entrance'
        ))
        hitter.zone_name.return_value = 'World/Entrance'
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'
        ))
        hitter.zone_name.return_value = 'Dungeon/RoomB'
        hitter.is_loading.return_value = True
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'
        ))
        hitter.is_loading.return_value = False
        hitter.zone_name.return_value = None
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'
        ))

    async def test_delayed_confirmation_requires_every_hitter_to_change_zone(self):
        quester, hitter = self.make_party()
        hitter.quest_party_confirmed_dungeon_transition = (
            'World/Entrance', 'Dungeon/RoomB'
        )
        self.assertTrue(await quester.party_hitters_confirmed_dungeon_transition(
            'World/Entrance'
        ))
        self.assertFalse(await quester.party_hitters_confirmed_dungeon_transition(
            'World/OtherEntrance'
        ))
        second_hitter = AsyncMock()
        second_hitter.is_loading.return_value = False
        second_hitter.zone_name.return_value = 'Dungeon/RoomC'
        second_hitter.quest_party_confirmed_dungeon_transition = None
        quester.client.quest_party_hitters.append(second_hitter)
        self.assertFalse(await quester.party_hitters_confirmed_dungeon_transition(
            'World/Entrance'
        ))
        second_hitter.quest_party_confirmed_dungeon_transition = (
            'World/Entrance', 'Dungeon/RoomC'
        )
        self.assertTrue(await quester.party_hitters_confirmed_dungeon_transition(
            'World/Entrance'
        ))

    async def test_pending_dungeon_confirmation_records_real_zone_change(self):
        quester, _ = self.make_party()
        client = quester.client
        client.quest_party_group_dungeon_zone = None
        client.zone_name.side_effect = ['World/Entrance', 'Dungeon/RoomA']
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
             patch('src.questing.click_window_by_path', AsyncMock()):
            self.assertTrue(await quester.handle_pending_dungeon_confirmation())
        self.assertEqual(
            client.quest_party_confirmed_dungeon_transition,
            ('World/Entrance', 'Dungeon/RoomA'),
        )
        self.assertTrue(client.quest_party_probe_pending)

    async def test_confirmed_dungeon_releases_gate_but_next_zone_rearms_it(self):
        quester, _ = self.make_party()
        client = quester.client
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        client.quest_party_quest_worker_zone = 'World/Entrance'
        client.quest_party_probe_pending = True
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.assertFalse(client.quest_party_probe_pending)
        self.assertEqual(client.quest_party_quest_worker_zone, 'Dungeon/RoomA')

        client.zone_name.return_value = 'Dungeon/RoomC'
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.assertIsNone(client.quest_party_group_dungeon_zone)
        self.assertTrue(client.quest_party_probe_pending)
        client.zone_name.return_value = None
        self.assertTrue(await quester._quest_party_probe_blocks_movement())


if __name__ == '__main__':
    unittest.main()
