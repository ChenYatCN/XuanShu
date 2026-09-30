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
        self.now = 2
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.now = 3.1
        self.assertFalse(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertTrue(self.client.quest_invitation_state['confirmed'])

    async def test_three_accept_attempts_then_safe_exit_without_more_clicks(self):
        for point in (0, 1.6, 3.2, 4.8, 9.0):
            self.now = point
            await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.quester._click_ui_window.await_count, 3)
        self.client.send_key.assert_awaited_once_with(Keycode.ESC, .1)

    async def test_loading_or_other_recovery_never_clicks(self):
        self.client.is_loading.return_value = True
        self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
        self.client.is_loading.return_value = False
        self.client.quest_recovery_owner = 'mainline_finder'
        self.assertFalse(await self.quester._advance_npc_dialogue(self.client))
        self.quester._click_ui_window.assert_not_awaited()

    async def test_finder_waits_for_acceptance_instead_of_reading_mainline(self):
        from src.paths import advance_dialog_path
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

    async def test_unconfirmed_acceptance_retries_original_npc_before_movement(self):
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
