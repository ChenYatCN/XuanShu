import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.questing import Quester
from wizwalker import XYZ, Keycode


class QuestPartyDungeonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch('src.questing.clients_share_live_area', AsyncMock(return_value=True))
        self.proof = patcher.start()
        self.addCleanup(patcher.stop)
        titles = patch('src.questing.get_popup_title', AsyncMock(return_value='Dungeon Entrance'))
        self.title_reader = titles.start()
        self.addCleanup(titles.stop)

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
        quester_client.in_battle.return_value = hitter.in_battle.return_value = False
        hitter.quest_party_confirmed_dungeon_transition = None
        quester_client.zone_name.return_value = 'Dungeon/RoomA'
        hitter.zone_name.return_value = 'Dungeon/RoomB'
        quester_client.body.position.return_value = hitter.body.position.return_value = XYZ(0, 0, 0)
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
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
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
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        client.quest_party_probe_pending = True
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        _, late = self.make_party()
        late.zone_name.return_value = 'World/Entrance'
        client.quest_party_hitters.append(late)
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        other, other_hitter = self.make_party()
        other.client.quest_party_probe_pending = True
        other.client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        other_hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await other._quest_party_probe_blocks_movement())
        self.assertTrue(client.quest_party_probe_pending)
        late.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())

    async def test_ordinary_zone_probe_holds_quester_for_existing_follower_result(self):
        quester, hitter = self.make_party()
        quester.client.quest_party_probe_pending = True
        hitter.questing_status = False
        hitter.is_loading.return_value = True
        hitter.zone_name.return_value = 'Previous/Area'
        self.proof.return_value = False
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.proof.assert_not_awaited()
        hitter.zone_name.assert_not_awaited()
        self.assertTrue(quester.client.quest_party_probe_pending)  # Independent follower still probes.

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
            quester.read_popup.side_effect = None
            self.title_reader.side_effect = lambda p: 'Dungeon Entrance' if p is client else 'Other Entrance'
            self.assertFalse(await quester.enter_party_dungeon([client, hitter]))
        client.send_key.assert_not_awaited()
        hitter.send_key.assert_not_awaited()

    async def test_hidden_team_up_never_enters_or_marks_group(self):
        from src.paths import team_up_wait_path, team_up_button_path
        quester, hitter = self.make_party()
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p not in (team_up_wait_path, team_up_button_path))):
            self.assertFalse(await quester.enter_party_dungeon([quester.client, hitter]))
        self.assertIsNone(quester.client.quest_party_group_dungeon_zone)
        quester.client.send_key.assert_not_awaited()

    async def test_ordinary_entrance_without_team_up_still_waits_for_assigned_hitter(self):
        from src.paths import team_up_wait_path, team_up_button_path
        quester, hitter = self.make_party()
        quester._mainline_identity = AsyncMock(return_value=None)
        quester.quest_interaction_ready = AsyncMock(return_value=True)
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p not in (team_up_wait_path, team_up_button_path))), \
                patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertFalse(await quester.enter_party_dungeon([quester.client]))
            self.assertFalse(await quester.enter_party_dungeon([quester.client, hitter]))
        quester.client.send_key.assert_not_awaited()
        hitter.send_key.assert_not_awaited()
        self.assertIsNone(quester.client.quest_party_group_dungeon_zone)

    async def test_ordinary_entrance_sends_both_keys_without_claiming_sigil_evidence(self):
        from src.paths import team_up_wait_path, team_up_button_path
        quester, hitter = self.make_party()
        client = quester.client
        hitter.zone_name.return_value = client.zone_name.return_value
        quester._mainline_identity = AsyncMock(return_value=None)
        quester._confirm_dungeon_entry = AsyncMock()
        quester.quest_interaction_ready = AsyncMock(return_value=True)
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        for member in (client, hitter):
            async def press(*_, member=member):
                self.assertTrue(client.quest_party_target_sync_active)
                self.assertTrue(hitter.quest_party_target_sync_active)
                member.zone_name.return_value = 'House/Interior'
                member.is_loading.side_effect = [True, True, False, False, False, False]
            member.send_key.side_effect = press
        with patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p not in (team_up_wait_path, team_up_button_path))), \
                patch('src.questing.is_free', AsyncMock(return_value=True)):
            other_group = AsyncMock()
            self.assertTrue(await quester.enter_party_dungeon([client, hitter, other_group]))
            other_group.send_key.assert_not_awaited()
        client.send_key.assert_awaited_once()
        hitter.send_key.assert_awaited_once()
        self.assertIsNone(client.quest_party_group_dungeon_zone)
        self.assertFalse(client.quest_party_probe_pending)
        self.assertFalse(client.quest_party_target_sync_active)
        self.assertFalse(hitter.quest_party_target_sync_active)

    async def test_first_hitter_departing_during_later_prompt_read_blocks_all_entry_keys(self):
        quester, hitter = self.make_party()
        _, second = self.make_party()
        second.title = 'p4'
        client = quester.client
        quester.client.quest_party_hitters.append(second)
        for member in (hitter, second):
            member.zone_name.return_value = client.zone_name.return_value
        quester._mainline_identity = AsyncMock(return_value=None)
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        def title(member):
            if member is second:
                hitter.zone_name.return_value = 'Other/Area'
            return 'Dungeon Entrance'
        self.title_reader.side_effect = title
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)):
            self.assertFalse(await quester.enter_party_dungeon([client, hitter, second]))
        for member in (client, hitter, second):
            member.send_key.assert_not_awaited()

    async def test_full_party_return_to_verified_entry_zone_releases_dungeon_marker(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        client.quest_dungeon_recovery = {'entry_zone': 'World/Entrance', 'entered': True}
        client.zone_name.return_value = 'World/Entrance'
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        self.assertEqual(client.quest_party_group_dungeon_zone, 'Dungeon/RoomA')
        hitter.zone_name.return_value = 'World/Entrance'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.assertIsNone(client.quest_party_group_dungeon_zone)
        self.assertIsNone(hitter.quest_party_group_dungeon_zone)
        self.assertIsNone(client.quest_dungeon_recovery)

    async def test_new_room_keeps_verified_entry_source(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.quest_party_group_dungeon_zone = 'Dungeon/RoomA'
        client.quest_dungeon_recovery = {'entry_zone': 'World/Entrance', 'entered': True}
        client.zone_name.return_value = hitter.zone_name.return_value = 'Dungeon/RoomB'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())
        self.assertEqual(client.quest_party_group_dungeon_zone, 'Dungeon/RoomB')
        self.assertEqual(client.quest_dungeon_recovery['entry_zone'], 'World/Entrance')

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
        client.quest_party_group_dungeon_zone = 'World/Entrance'
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
        self.assertEqual(client.quest_party_group_dungeon_zone, 'World/Entrance')
        client.is_loading.side_effect = hitter.is_loading.side_effect = None
        self.assertTrue(await quester._quest_party_probe_blocks_movement())
        hitter.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await quester._quest_party_probe_blocks_movement())

    def countdown_party(self):
        quester, hitter = self.make_party()
        client = quester.client
        client.zone_name.return_value = hitter.zone_name.return_value = 'World/Entrance'
        client.quest_party_target_sync_active = hitter.quest_party_target_sync_active = False
        client.quest_party_dungeon_entry_active = False
        quester._mainline_identity = AsyncMock(return_value=None)
        quester._confirm_dungeon_entry = AsyncMock()
        quester.read_popup = AsyncMock(return_value='Press X to Enter')
        return quester, client, hitter

    async def test_ten_second_countdown_keeps_both_members_locked_through_loading(self):
        from src.paths import dungeon_warning_path
        quester, client, hitter = self.countdown_party()
        now = [0.0]
        pressed = set()
        for member, delay in ((client, 10.0), (hitter, 10.2)):
            async def press(key, duration, member=member):
                self.assertEqual(key, Keycode.X)
                pressed.add(id(member))
            async def loading(member=member, delay=delay):
                if id(member) not in pressed:
                    return False
                self.assertTrue(client.quest_party_dungeon_entry_active)
                self.assertTrue(client.quest_party_target_sync_active)
                self.assertTrue(hitter.quest_party_target_sync_active)
                if now[0] < delay:
                    return False
                if now[0] < delay + .2:
                    return True
                member.zone_name.return_value = 'Dungeon/RoomA'
                return False
            member.send_key.side_effect = press
            member.is_loading.side_effect = loading
        async def tick(seconds):
            self.assertTrue(client.quest_party_dungeon_entry_active)
            self.assertTrue(client.quest_party_target_sync_active)
            self.assertTrue(hitter.quest_party_target_sync_active)
            client.teleport.assert_not_awaited()
            hitter.teleport.assert_not_awaited()
            now[0] += seconds
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p != dungeon_warning_path)), \
                patch('src.questing.time', SimpleNamespace(monotonic=lambda: now[0])), \
                patch('src.questing.asyncio.sleep', new=tick):
            self.assertTrue(await quester.enter_party_dungeon([client, hitter]))
        self.assertGreaterEqual(now[0], 10.4)
        self.assertFalse(client.quest_party_dungeon_entry_active)
        self.assertFalse(client.quest_party_target_sync_active)
        self.assertFalse(hitter.quest_party_target_sync_active)
        self.assertFalse(client.quest_party_probe_pending)
        self.assertEqual(client.quest_party_group_dungeon_zone, 'Dungeon/RoomA')
        client.send_key.assert_awaited_once_with(Keycode.X, .1)
        hitter.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_fast_zone_change_without_loading_sample_is_not_an_entry_timeout(self):
        quester, client, hitter = self.countdown_party()
        for member in (client, hitter):
            async def press(*_, member=member):
                member.zone_name.return_value = 'Dungeon/RoomA'
            member.send_key.side_effect = press
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), \
                patch('src.questing.asyncio.sleep', AsyncMock()) as pause, \
                patch('src.questing.logger.warning') as warning:
            self.assertTrue(await quester.enter_party_dungeon([client, hitter]))
        pause.assert_not_awaited()
        warning.assert_not_called()
        self.assertFalse(client.quest_party_probe_pending)
        self.assertFalse(client.quest_party_dungeon_entry_active)

    async def test_cancellation_during_countdown_releases_only_this_entry_lock(self):
        from src.paths import dungeon_warning_path
        quester, client, hitter = self.countdown_party()
        async def cancel(_):
            self.assertTrue(client.quest_party_dungeon_entry_active)
            self.assertTrue(client.quest_party_target_sync_active)
            self.assertTrue(hitter.quest_party_target_sync_active)
            raise asyncio.CancelledError
        with patch('src.questing.is_free', AsyncMock(return_value=True)), \
                patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p != dungeon_warning_path)), \
                patch('src.questing.asyncio.sleep', new=cancel):
            with self.assertRaises(asyncio.CancelledError):
                await quester.enter_party_dungeon([client, hitter])
        self.assertFalse(client.quest_party_dungeon_entry_active)
        self.assertFalse(client.quest_party_target_sync_active)
        self.assertFalse(hitter.quest_party_target_sync_active)
        self.assertTrue(client.quest_party_probe_pending)
        client.teleport.assert_not_awaited()
        hitter.teleport.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
