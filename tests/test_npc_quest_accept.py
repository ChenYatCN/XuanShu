import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.questing import Quester
from wizwalker import Keycode


class NpcQuestAcceptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.client = SimpleNamespace(title='p1', questing_status=True,
            quest_recovery_owner=None, root_window=object(),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            zone_name=AsyncMock(return_value='Novus/Area'), send_key=AsyncMock())
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            is_control_grayed=AsyncMock(return_value=False))
        self.quester = Quester(self.client, [self.client], None)
        self.quester._window_text = AsyncMock(return_value='接受')
        self.quester._click_ui_window = AsyncMock()
        self.quester._mainline_identity = AsyncMock(return_value=(43, '', '', {'number': 2}, True))
        self.lookup = AsyncMock(return_value=self.button)
        for patcher in (
            patch('src.questing.time.monotonic', side_effect=lambda: self.now),
            patch('src.questing.get_window_from_path', self.lookup),
            patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)),
            patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_decline_button_preserves_side_accept_setting_in_each_worker_mode(self):
        from src.paths import decline_quest_path
        for questing, dialogue in ((True, False), (False, True), (True, True)):
            for accept in (False, True):
                with self.subTest(questing=questing, dialogue=dialogue, accept=accept):
                    self.client.questing_status = questing
                    self.client.auto_dialogue_running = dialogue
                    self.client.hotkey_accept_sidequests = accept
                    self.client.quest_invitation_state = None
                    self.client.send_key.reset_mock()
                    self.quester._click_ui_window.reset_mock()
                    with (patch('src.questing.is_visible_by_path', AsyncMock(
                            side_effect=lambda client, path: path == decline_quest_path)),
                          patch('src.questing.asyncio.sleep', AsyncMock())):
                        await self.quester._advance_npc_dialogue(self.client)
                    if accept:
                        self.quester._click_ui_window.assert_awaited_once()
                        self.client.send_key.assert_not_awaited()
                    else:
                        self.quester._click_ui_window.assert_not_awaited()
                        self.client.send_key.assert_awaited_once_with(Keycode.ESC)

    async def test_unindexed_side_quest_acceptance_is_confirmed_from_owned_quests(self):
        owned = {42: object()}
        self.client.quest_manager = AsyncMock(return_value=SimpleNamespace(
            quest_data=AsyncMock(side_effect=lambda: dict(owned))))
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = False
        owned[90001] = object()  # Accepted sidequest does not become the tracked quest.
        await self.quester._quest_dialogue_blocks_movement(self.client)
        self.now = .31
        self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertTrue(self.client.quest_invitation_state['confirmed'])
        self.quester._mainline_identity.assert_not_awaited()

    async def test_click_without_quest_change_does_not_confirm_or_enter_finder(self):
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = False
        self.client.mainline_last_turn_in_snapshot = (42, 'old', None, 'old')
        self.quester._continue_mainline_chain = AsyncMock()
        await self.quester._quest_dialogue_blocks_movement(self.client)
        self.now = .31
        self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertFalse(self.client.quest_invitation_state['confirmed'])
        self.quester._continue_mainline_chain.assert_not_awaited()

    async def test_closing_failed_offer_allows_a_new_offer(self):
        self.client.quest_invitation_state = {'failed': True, 'next_at': 0.0}
        self.button.is_visible.return_value = False
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = True
        await self.quester._advance_npc_dialogue(self.client)
        self.quester._click_ui_window.assert_awaited_once()

    async def test_accept_without_decline_button_is_clicked(self):
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, self.button)
        self.client.send_key.assert_not_awaited()

    async def test_two_workers_share_input_and_retry_cooldown(self):
        other = Quester(self.client, [self.client], None)
        other._window_text = self.quester._window_text
        other._click_ui_window = self.quester._click_ui_window
        await asyncio.gather(self.quester._advance_npc_dialogue(self.client),
                             other._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_awaited_once()

    async def test_visible_offer_blocks_movement_even_after_quest_update(self):
        await self.quester._quest_dialogue_blocks_movement(self.client)
        self.client.quest_id.return_value = 43
        self.now = 5.0
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.quester._mainline_identity.assert_not_awaited()

    async def test_new_mainline_id_must_stabilize_after_offer_closes(self):
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = False
        self.client.quest_id.return_value = 43
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.now = .2
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.now = .31
        self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertTrue(self.client.quest_invitation_state['confirmed'])

    async def test_three_accept_attempts_then_safe_exit_without_more_clicks(self):
        for point in (0, 1.6, 3.2, 4.8, 9.0):
            self.now = point
            await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.quester._click_ui_window.await_count, 3)
        self.client.send_key.assert_awaited_once_with(Keycode.ESC, .1)

    async def test_fast_accept_clicks_still_allow_server_response_before_exit(self):
        for point in (0, .31, .62, 1.0):
            self.now = point
            await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.quester._click_ui_window.await_count, 3)
        self.client.send_key.assert_not_awaited()
        self.now = 4.6
        await self.quester._advance_npc_dialogue(self.client)
        self.client.send_key.assert_awaited_once_with(Keycode.ESC, .1)

    async def test_changing_snapshot_restarts_short_stability_window(self):
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = False
        self.client.quest_id.return_value = 43
        await self.quester._quest_dialogue_blocks_movement(self.client)
        self.now = .2
        self.client.goal_id.return_value = 8
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.now = .31
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.now = .51
        self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))

    async def test_loading_or_other_recovery_never_clicks(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
        self.client.is_loading.return_value = False
        self.client.quest_recovery_owner = 'mainline_finder'
        self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_not_awaited()

    async def test_finder_waits_for_acceptance_instead_of_reading_mainline(self):
        from src.paths import advance_dialog_path
        self.client.mainline_finder_enabled = True
        with patch('src.questing.is_visible_by_path', AsyncMock(
                side_effect=lambda client, path: path == advance_dialog_path)):
            self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.quester._mainline_identity.assert_not_awaited()
        self.quester._click_ui_window.assert_awaited_once()

    async def test_ordinary_dialogue_advances_without_accepting_offer(self):
        self.quester._window_text.return_value = '继续'
        await self.quester._advance_npc_dialogue(self.client)
        self.client.send_key.assert_awaited_once_with(Keycode.SPACEBAR)
        self.quester._click_ui_window.assert_not_awaited()

    async def test_ordinary_complete_does_not_wait_for_hitter_probe(self):
        self.quester._window_text.return_value = '完成'
        self.client.quest_party_probe_pending = True
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_awaited_once_with(self.client, self.button)
        self.assertTrue(self.client.quest_party_probe_pending)

    async def test_pending_probe_complete_keeps_shared_cooldown_and_limit(self):
        self.quester._window_text.return_value = '完成'
        self.client.quest_party_probe_pending = True
        other = Quester(self.client, [self.client], None)
        other._window_text = self.quester._window_text
        other._click_ui_window = self.quester._click_ui_window
        for self.now in (0, .3, 1.5, 3, 5):
            await asyncio.gather(self.quester._advance_npc_dialogue(self.client),
                                 other._advance_npc_dialogue(self.client))
        self.assertEqual(self.quester._click_ui_window.await_count, 3)
        self.assertTrue(self.client.quest_party_probe_pending)

    async def test_selected_mainline_complete_still_waits_for_probe(self):
        self.quester._window_text.return_value = '完成'
        self.client.quest_party_probe_pending = True
        self.client.mainline_finder_enabled = True
        self.client.npc_mainline_menu_selection = {'zone': 'Novus/Area', 'failed': False}
        self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_not_awaited()

    async def test_pending_probe_complete_keeps_other_safety_guards(self):
        self.quester._window_text.return_value = '完成'
        self.client.quest_party_probe_pending = True
        for name, blocked, restored in (
                ('refilling_potions', True, False),
                ('quest_party_battle_rescue_active', True, False),
                ('quest_recovery_owner', 'mainline_finder', None),
                ('questing_status', False, True)):
            with self.subTest(guard=name):
                setattr(self.client, name, blocked)
                self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
                setattr(self.client, name, restored)
        for name in ('is_loading', 'in_battle'):
            with self.subTest(guard=name):
                getattr(self.client, name).return_value = True
                self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
                getattr(self.client, name).return_value = False
        self.quester._click_ui_window.assert_not_awaited()

    async def test_unconfirmed_acceptance_retries_original_npc_before_movement(self):
        self.client.mainline_finder_enabled = True
        await self.quester._advance_npc_dialogue(self.client)
        self.button.is_visible.return_value = False
        self.client.mainline_last_turn_in_snapshot = (42, 'Novus/Area', None, 'NPC')
        self.quester._mainline_identity.return_value = (42, '', '', {'number': 1}, True)
        self.quester._continue_mainline_chain = AsyncMock()
        await self.quester._quest_dialogue_blocks_movement(self.client)
        self.now = 3.1
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertFalse(self.client.quest_invitation_state['confirmed'])
        self.quester._continue_mainline_chain.assert_awaited_once_with(
            self.client, self.client.mainline_last_turn_in_snapshot)


if __name__ == '__main__':
    unittest.main()
