import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.questing import Quester


class QuestPartyDungeonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch('src.questing.clients_share_live_area', AsyncMock(return_value=True))
        self.proof = patcher.start()
        self.addCleanup(patcher.stop)

    def make_party(self):
        quester_client = AsyncMock()
        hitter = AsyncMock()
        quester_client.title = 'p1'
        hitter.title = 'p2'
        quester_client.quest_party_hitters = [hitter]
        quester_client.questing_status = hitter.questing_status = True
        quester_client.in_solo_zone = False
        quester_client.quest_party_probe_pending = False
        quester_client.quest_party_quest_worker_zone = None
        quester_client.quest_party_group_dungeon_zone = None
        quester_client.quest_party_dungeon_interaction = None
        quester_client.quest_recovery_owner = hitter.quest_recovery_owner = None
        quester_client.post_combat_cleanup_active = hitter.post_combat_cleanup_active = False
        quester_client.refilling_potions = False
        hitter.refilling_potions = False
        quester_client.potion_dungeon_returned = None
        hitter.potion_dungeon_returned = None
        quester_client.is_loading.return_value = False
        hitter.is_loading.return_value = False
        hitter.quest_party_confirmed_dungeon_transition = None
        quester_client.zone_name.return_value = 'Dungeon/RoomA'
        hitter.zone_name.return_value = 'Dungeon/RoomB'
        return Quester(quester_client, [quester_client], None), hitter

    async def test_entry_requires_same_destination_and_live_instance_not_merely_leaving_source(self):
        quester, hitter = self.make_party()
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'
        ))
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertTrue(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'))
        self.proof.return_value = False
        self.assertFalse(await quester.party_dungeon_entry_complete(
            [quester.client, hitter], 'World/Entrance'))

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
            'World/Entrance', 'Dungeon/RoomA'
        )
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertTrue(await quester.party_hitters_confirmed_dungeon_transition(
            'World/Entrance'
        ))
        self.assertFalse(await quester.party_hitters_confirmed_dungeon_transition(
            'World/OtherEntrance'
        ))
        second_hitter = AsyncMock()
        second_hitter.is_loading.return_value = False
        second_hitter.questing_status = True
        second_hitter.zone_name.return_value = 'Dungeon/RoomA'
        second_hitter.quest_party_confirmed_dungeon_transition = None
        quester.client.quest_party_hitters.append(second_hitter)
        self.assertFalse(await quester.party_hitters_confirmed_dungeon_transition(
            'World/Entrance'
        ))
        second_hitter.quest_party_confirmed_dungeon_transition = (
            'World/Entrance', 'Dungeon/RoomA'
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

    async def test_next_zone_waits_for_this_partys_hitters_before_releasing_movement(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        client.quest_party_quest_worker_zone = 'World/Entrance'
        client.quest_party_probe_pending = True
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.assertFalse(client.quest_party_probe_pending)
        self.assertEqual(client.quest_party_quest_worker_zone, 'Dungeon/RoomA')

        client.zone_name.return_value = 'Dungeon/RoomC'
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.assertTrue(client.quest_party_probe_pending)
        self.assertEqual(client.quest_party_quest_worker_zone, 'Dungeon/RoomA')
        hitter.zone_name.return_value = 'Dungeon/RoomC'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.assertEqual(client.quest_party_group_dungeon_zone, 'Dungeon/RoomC')
        self.assertFalse(client.quest_party_probe_pending)
        client.zone_name.return_value = None
        self.assertTrue(await quester._quest_party_probe_blocks_movement())

    async def test_same_named_separate_instance_and_read_failure_keep_transition_pending(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.quest_party_probe_pending = True
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.proof.return_value = False
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.assertTrue(client.quest_party_probe_pending)
        self.proof.side_effect = ValueError('instance unreadable')
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.proof.side_effect = None
        self.proof.return_value = True
        self.assertFalse(await quester._quest_party_probe_blocks_movement())

    async def test_one_arrived_hitter_cannot_release_multi_hitter_party_or_block_other_group(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.quest_party_probe_pending = True
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        _, late = self.make_party()
        late.zone_name.return_value = 'World/Entrance'
        client.quest_party_hitters.append(late)
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        other, other_hitter = self.make_party()
        other.client.quest_party_probe_pending = True
        other_hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await other._quest_party_probe_blocks_movement())
        self.assertTrue(client.quest_party_probe_pending)
        late.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())

    async def test_confirmed_solo_area_keeps_independent_quester_policy(self):
        quester, _ = self.make_party()
        quester.client.in_solo_zone = True
        quester.client.quest_party_quest_worker_zone = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.proof.assert_not_awaited()

    async def test_partial_or_unready_entrance_list_never_sends_quester_x(self):
        quester, hitter = self.make_party()
        client = quester.client
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        quester._mainline_identity = AsyncMock(return_value=None)
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)):
            self.assertFalse(await quester.enter_party_dungeon([client]))
            hitter.refilling_potions = True
            self.assertFalse(await quester.enter_party_dungeon([client, hitter]))
            hitter.refilling_potions = False
            self.proof.return_value = False
            self.assertFalse(await quester.enter_party_dungeon([client, hitter]))
            self.proof.return_value = True
            quester.read_popup.side_effect = lambda p: 'Press X to Enter' if p is client else 'Press X to Talk'
            self.assertFalse(await quester.enter_party_dungeon([client, hitter]))
        client.send_key.assert_not_awaited()
        hitter.send_key.assert_not_awaited()

    async def test_source_transition_during_hitter_proof_prevents_both_entry_inputs(self):
        quester, hitter = self.make_party()
        client = quester.client
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        quester._mainline_identity = AsyncMock(return_value=None)
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        async def source_starts_loading(*_):
            client.is_loading.return_value = True
            return True
        self.proof.side_effect = source_starts_loading
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)):
            self.assertFalse(await quester.enter_party_dungeon([client, hitter]))
        client.send_key.assert_not_awaited()
        hitter.send_key.assert_not_awaited()

    async def test_entrance_collection_uses_only_ready_same_instance_assigned_hitters(self):
        quester, hitter = self.make_party()
        client = quester.client
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        with patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertEqual(await quester.prepare_party_dungeon_entry(), [client, hitter])
        hitter.teleport.assert_awaited_once()
        for blocked in ('different_instance', 'refilling'):
            with self.subTest(blocked=blocked):
                hitter.teleport.reset_mock()
                self.proof.return_value = blocked != 'different_instance'
                hitter.refilling_potions = blocked == 'refilling'
                clock = iter(range(100))
                with patch('src.questing.time', SimpleNamespace(monotonic=lambda: next(clock))), \
                        patch('src.questing.asyncio.sleep', AsyncMock()), \
                        patch('src.questing.is_free', AsyncMock(return_value=True)):
                    self.assertEqual(await quester.prepare_party_dungeon_entry(), [client])
                hitter.teleport.assert_not_awaited()
        client.send_key.assert_not_awaited()

    async def test_real_entry_sends_both_x_and_waits_for_late_hitter_before_next_step(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.zone_name.return_value = hitter.zone_name.return_value = 'World/Entrance'
        quester._mainline_identity = AsyncMock(return_value=None)
        quester._confirm_dungeon_entry = AsyncMock()
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        for member in (client, hitter):
            async def press(*_, member=member):
                member.is_loading.side_effect = [True, True, False, False, False, False]
                if member is client:
                    member.zone_name.return_value = 'Dungeon/RoomA'
            member.send_key.side_effect = press
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)):
            self.assertTrue(await quester.enter_party_dungeon([client, hitter]))
        client.send_key.assert_awaited_once()
        hitter.send_key.assert_awaited_once()
        client.is_loading.side_effect = hitter.is_loading.side_effect = None
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())


if __name__ == '__main__':
    unittest.main()
