import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import utils
from src.questing import Quester, claim_quest_recovery


class PotionInstanceSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p1', questing_status=True,
            zone_name=AsyncMock(return_value='Shop'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            mouse_handler=AsyncMock(), root_window=object(),
            potion_return_context={'zone': 'Dungeon/Room', 'snapshot': (42, 7, 'Talk'),
                                   'zone_id': 123, 'group_zone': 'Dungeon/Room'})
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                      is_control_grayed=AsyncMock(return_value=False))
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.closed = stack.enter_context(patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=False)))
        stack.enter_context(patch.object(utils, 'get_window_from_path', AsyncMock(return_value=self.button)))
        stack.enter_context(patch.object(utils, 'is_free', AsyncMock(return_value=True)))
        self.snapshot = stack.enter_context(patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=(42, 8, 'Next'))))
        self.zone_id = stack.enter_context(patch.object(utils, 'potion_zone_id', AsyncMock(return_value=123)))
        stack.enter_context(patch.object(utils.asyncio, 'sleep', AsyncMock()))
        async def arrive(_):
            self.client.zone_name.return_value = 'Dungeon/Room'
        self.client.mouse_handler.click_window.side_effect = arrive

    async def test_same_name_different_zone_id_is_rejected(self):
        self.zone_id.return_value = 999
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertFalse(hasattr(self.client, 'potion_dungeon_returned'))

    async def test_different_quest_identity_is_rejected(self):
        self.snapshot.return_value = (99, 8, 'New instance')
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))

    async def test_same_zone_without_readable_task_never_resumes(self):
        self.snapshot.return_value = None
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))

    async def test_disabled_red_button_never_clicked(self):
        self.button.is_control_grayed.return_value = True
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_red_button_never_clicked(self):
        self.client.is_loading.return_value = True
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_closed_after_red_click_stops_without_other_teleport(self):
        self.closed.side_effect = [False, True]
        with patch.object(utils, 'recall_to_teleport_mark', AsyncMock()) as mark:
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        mark.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_awaited_once()

    async def test_verified_return_preserves_instance_state_and_requires_party_check(self):
        hitter = SimpleNamespace(questing_status=True)
        self.client.quest_party_hitters = [hitter]
        self.client.potion_return_context['dungeon_state'] = {'zone': 'Dungeon/Room', 'since': 100}
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertEqual(self.client.potion_dungeon_returned[2], {id(hitter)})
        self.assertEqual(self.client.quest_dungeon_recovery['snapshot'], (42, 8, 'Next'))
        self.assertIsNone(self.client.quest_dungeon_recovery['since'])

    async def test_original_context_is_required(self):
        self.client.potion_return_context = None
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_unclassified_buy_uses_verified_red_return(self):
        self.client.stats = SimpleNamespace(potion_max=AsyncMock(return_value=0),
                                           potion_charge=AsyncMock(return_value=0))
        with patch.object(utils, 'recall_to_teleport_mark', AsyncMock()) as mark:
            self.assertTrue(await utils.buy_potions(self.client, original_zone='Dungeon/Room'))
        mark.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.button)
        self.assertEqual(self.client.potion_return_context['returned_snapshot'], (42, 8, 'Next'))
        self.assertTrue(self.client.questing_status)

    async def test_unclassified_buy_rejects_wrong_instance_without_mark_fallback(self):
        self.client.stats = SimpleNamespace(potion_max=AsyncMock(return_value=0),
                                           potion_charge=AsyncMock(return_value=0))
        self.zone_id.return_value = 999
        with patch.object(utils, 'recall_to_teleport_mark', AsyncMock()) as mark:
            self.assertFalse(await utils.buy_potions(self.client, original_zone='Dungeon/Room'))
        mark.assert_not_awaited()
        self.assertFalse(self.client.questing_status)
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)

    async def test_solo_return_does_not_wait_for_unreachable_hitter(self):
        self.client.in_solo_zone = True
        self.client.quest_party_hitters = [SimpleNamespace(questing_status=True)]
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertIsNone(self.client.potion_dungeon_returned)

    def room_peer(self, zone='Dungeon/Room'):
        peer = SimpleNamespace(title='p2', questing_status=True,
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value=zone))
        self.client.quest_party_quester = peer
        self.client.quest_party_status_session = object()
        self.client.potion_return_context['peer_areas'] = {id(peer): ('saved-area',)}
        return peer

    async def test_first_room_return_follows_quest_through_multiple_rooms(self):
        self.room_peer()
        async def arrive(_):
            self.client.zone_name.return_value = 'Dungeon/Entrance'
        self.client.mouse_handler.click_window.side_effect = arrive
        async def advance(client, zone, *, reenter):
            client.zone_name.return_value = ('Dungeon/Middle' if zone == 'Dungeon/Entrance'
                                           else 'Dungeon/Room')
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock(side_effect=advance)) as tp, \
             patch.object(utils, 'clients_share_live_area', AsyncMock(return_value=True)), \
             patch.object(utils.time, 'monotonic', side_effect=range(300)):
            self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertEqual([call.args[1] for call in tp.await_args_list],
                         ['Dungeon/Entrance', 'Dungeon/Middle'])
        self.assertEqual(self.client.potion_dungeon_returned[0], 'Dungeon/Room')

    async def test_catches_up_to_peer_that_moved_to_next_room(self):
        peer = self.room_peer('Dungeon/Next')
        self.client.potion_return_context['dungeon_state'] = {'zone': 'Dungeon/Room'}
        async def advance(client, zone, *, reenter):
            client.zone_name.return_value = await peer.zone_name()
            self.zone_id.return_value = 456
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock(side_effect=advance)) as tp, \
             patch.object(utils, 'clients_share_live_area', AsyncMock(return_value=True)):
            self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        tp.assert_awaited_once_with(self.client, 'Dungeon/Room', reenter=False)
        self.assertEqual(self.client.quest_party_group_dungeon_zone, 'Dungeon/Next')
        self.assertEqual(self.client.quest_dungeon_recovery['zone'], 'Dungeon/Next')
        self.assertEqual(self.client.potion_dungeon_returned[0], 'Dungeon/Next')
        self.assertEqual(self.client.potion_return_context['zone_id'], 456)

    async def test_same_room_without_instance_proof_does_not_tp_or_resume(self):
        self.room_peer()
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock()) as tp, \
             patch.object(utils, 'clients_share_live_area', AsyncMock(return_value=False)), \
             patch.object(utils.time, 'monotonic', side_effect=range(0, 600, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        tp.assert_not_awaited()

    async def test_wrong_quest_in_first_room_does_not_tp(self):
        self.room_peer()
        async def arrive(_):
            self.client.zone_name.return_value = 'Dungeon/Entrance'
        self.client.mouse_handler.click_window.side_effect = arrive
        self.snapshot.return_value = (99, 8, 'Other quest')
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock()) as tp:
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        tp.assert_not_awaited()

    async def test_unverified_peer_is_not_followed(self):
        self.room_peer('Dungeon/Next')
        self.client.potion_return_context['peer_areas'] = {}
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock()) as tp:
            self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        tp.assert_not_awaited()

    async def test_stuck_room_retries_with_walk_and_times_out(self):
        self.room_peer('Dungeon/Next')
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock()) as tp, \
             patch.object(utils.time, 'monotonic', side_effect=range(0, 600, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertGreater(tp.await_count, 1)
        self.assertFalse(tp.await_args_list[0].kwargs['reenter'])
        self.assertTrue(tp.await_args_list[1].kwargs['reenter'])

    async def test_user_stop_during_room_catchup_prevents_resume(self):
        self.room_peer('Dungeon/Next')
        async def stop(client, zone, *, reenter):
            client.questing_status = False
        with patch.object(utils, '_potion_dungeon_room_tp', AsyncMock(side_effect=stop)) as tp:
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        tp.assert_awaited_once()

    async def test_room_tp_cancels_movement_when_client_stops(self):
        import asyncio
        cancelled = []
        async def moving(client, *, reenter):
            client.questing_status = False
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        with patch('src.teleport_math.navmap_tp', AsyncMock(side_effect=moving)) as tp:
            await utils._potion_dungeon_room_tp(self.client, 'Shop', reenter=False)
        tp.assert_awaited_once_with(self.client, reenter=False)
        self.assertEqual(cancelled, [True])


class PotionRoutingSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bumbles_refill_routes_to_dungeon_and_releases_lock(self):
        zone = 'Lemuria/Interiors/LM_Z07_BumblesMind'
        client = SimpleNamespace(title='p1', questing_status=True,
            zone_name=AsyncMock(return_value=zone),
            stats=SimpleNamespace(reference_level=AsyncMock(return_value=100),
                                  current_gold=AsyncMock(return_value=100000)))
        with ExitStack() as stack:
            prepare = stack.enter_context(patch.object(utils, 'prepare_potion_dungeon_return', AsyncMock(return_value=True)))
            mark = stack.enter_context(patch.object(utils, 'ensure_teleport_mark', AsyncMock()))
            for name in ('navigate_to_ravenwood', 'navigate_to_commons_from_ravenwood', 'navigate_to_potions'):
                stack.enter_context(patch.object(utils, name, AsyncMock()))
            buy = stack.enter_context(patch.object(utils, 'buy_potions', AsyncMock(return_value=True)))
            self.assertTrue(await utils.refill_potions(client))
        prepare.assert_awaited_once_with(client, zone, require_snapshot=True)
        buy.assert_awaited_once_with(client, True, original_zone=zone, dungeon_return=True)
        mark.assert_not_awaited()
        self.assertFalse(client.refilling_potions)
        self.assertIsNone(client.quest_recovery_owner)

    async def test_unreadable_original_task_prevents_dungeon_departure(self):
        zone = 'Dungeon/Room'
        client = SimpleNamespace(title='p1', questing_status=True,
            quest_party_group_dungeon_zone=zone, zone_name=AsyncMock(return_value=zone),
            stats=SimpleNamespace(reference_level=AsyncMock(return_value=100),
                                  current_gold=AsyncMock(return_value=100000)))
        with patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=None)), \
             patch.object(utils, 'navigate_to_ravenwood', AsyncMock()) as travel:
            self.assertFalse(await utils.refill_potions(client))
        travel.assert_not_awaited()
        self.assertFalse(client.questing_status)
        self.assertFalse(client.refilling_potions)
        self.assertIsNone(client.quest_recovery_owner)

    async def test_promptless_bumbles_mind_is_not_treated_as_normal_map(self):
        client = SimpleNamespace()
        self.assertTrue(utils.potion_dungeon_return_required(client, 'Lemuria/Interiors/LM_Z07_BumblesMind'))
        self.assertFalse(utils.potion_dungeon_return_required(client, 'WizardCity/WC_Hub'))
        self.assertFalse(utils.potion_dungeon_return_required(client, 'World/Interiors/Shop'))

    async def test_refilling_prevents_other_recovery_claim(self):
        client = SimpleNamespace(refilling_potions=True)
        self.assertFalse(claim_quest_recovery(client, 'mainline_finder'))
        self.assertFalse(hasattr(client, 'quest_recovery_owner'))

    async def test_hitter_refill_does_not_block_quester_movement(self):
        hitter = SimpleNamespace(refilling_potions=True)
        client = SimpleNamespace(quest_party_hitters=[hitter], quest_party_probe_pending=False,
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='Dungeon/Room'),
            quest_party_group_dungeon_zone='Dungeon/Room')
        self.assertFalse(await Quester._quest_party_probe_blocks_movement(SimpleNamespace(client=client)))
        self.assertFalse(client.quest_party_probe_pending)

    async def test_closed_mark_failure_never_waits_for_cooldown_or_sends_keys(self):
        client = SimpleNamespace(title='p1', send_key=AsyncMock())
        with patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=True)), \
             patch.object(utils, 'wait_for_teleport_mark_timer', AsyncMock()) as timer:
            self.assertFalse(await utils.recall_to_teleport_mark(client, expected_zone='Dungeon/Room'))
        timer.assert_not_awaited()
        client.send_key.assert_not_awaited()

    async def test_global_guard_closed_marker_stops_mark_retry(self):
        client = SimpleNamespace(title='p1', send_key=AsyncMock(), _xuanshu_dungeon_closed=True)
        with patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=False)), \
             patch.object(utils, 'wait_for_teleport_mark_timer', AsyncMock()) as timer:
            self.assertFalse(await utils.recall_to_teleport_mark(client, expected_zone='Dungeon/Room'))
        timer.assert_not_awaited()


class ClosedDungeonPopupTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_caption_uses_single_acknowledgment_not_retry(self):
        caption = SimpleNamespace(value='区域加载失败，地下城已关闭。', is_visible=AsyncMock(return_value=True))
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                get_windows_with_name=AsyncMock(return_value=[caption]))
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        client = SimpleNamespace(title='p1', is_loading=AsyncMock(return_value=False),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[modal])),
            mouse_handler=AsyncMock(), _xuanshu_zone_retry_users=1, questing_status=True)
        async def lookup(_root, path):
            return button if path[-1] == 'rightButton' else False
        with patch.object(utils, 'read_control_text', AsyncMock(side_effect=lambda c: c.value)), \
             patch.object(utils, 'get_window_from_path', AsyncMock(side_effect=lookup)):
            from src.script_popups import close_automation_popup
            self.assertTrue(await close_automation_popup(client))
        client.mouse_handler.click_window.assert_awaited_once_with(button)
        self.assertTrue(client._xuanshu_dungeon_closed)
        self.assertFalse(client.questing_status)

    async def test_retryable_files_caption_is_not_closed_dungeon(self):
        caption = SimpleNamespace(value='区域正在加载必要的文件，请稍后重试。', is_visible=AsyncMock(return_value=True))
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                get_windows_with_name=AsyncMock(return_value=[caption]))
        client = SimpleNamespace(root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[modal])))
        with patch.object(utils, 'read_control_text', AsyncMock(side_effect=lambda c: c.value)):
            self.assertFalse(await utils.closed_dungeon_popup(client, dismiss=True))
        self.assertFalse(hasattr(client, '_xuanshu_dungeon_closed'))
