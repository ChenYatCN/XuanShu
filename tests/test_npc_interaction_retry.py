import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, Orient, XYZ
from src.questing import Quester


class NPCInteractionRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.camera = SimpleNamespace(
            orientation=AsyncMock(return_value=Orient(0.1, 0.0, 0.5)),
            update_orientation=AsyncMock(),
        )
        self.client = SimpleNamespace(
            title="p1",
            questing_status=True,
            send_key=AsyncMock(),
            quest_id=AsyncMock(return_value=10),
            goal_id=AsyncMock(return_value=20),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(1, 2, 3))),
            game_client=SimpleNamespace(selected_camera_controller=AsyncMock(return_value=self.camera)),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value="Press X to Talk")
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester._mainline_turn_in_snapshot = AsyncMock(return_value=None)
        self.quester._continue_mainline_chain = AsyncMock()
        self.clock = 0.0

        def monotonic():
            self.clock += 0.25
            return self.clock

        self.patches = [
            patch("src.questing.time", SimpleNamespace(monotonic=monotonic)),
            patch("src.questing.asyncio.sleep", new=AsyncMock()),
            patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True)),
            patch("src.questing.is_visible_by_path", new=AsyncMock(return_value=True)),
            patch("src.questing.exit_menus", new=AsyncMock()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_first_retry_adjusts_position_and_view_then_stops_on_progress(self):
        self.quester.read_quest_txt = AsyncMock(
            side_effect=lambda _client: "Next objective" if self.client.send_key.await_count >= 3 else "Talk"
        )
        self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertEqual(
            [call.args for call in self.client.send_key.await_args_list],
            [(Keycode.X, 0.1), (Keycode.A, 0.3), (Keycode.X, 0.15)],
        )
        self.camera.update_orientation.assert_awaited_once()
        updated = self.camera.update_orientation.await_args.args[0]
        self.assertAlmostEqual(updated.yaw, 0.62)

    async def test_goal_id_progress_needs_no_adjustment(self):
        self.quester.read_quest_txt = AsyncMock(return_value="Talk")
        self.client.goal_id.side_effect = lambda: 21 if self.client.send_key.await_count else 20
        self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.client.send_key.assert_awaited_once_with(Keycode.X, 0.1)
        self.camera.update_orientation.assert_not_awaited()

    async def test_three_failed_retries_log_once_and_block_same_quest_state(self):
        self.quester.read_quest_txt = AsyncMock(return_value="Talk")
        with patch("src.questing.logger") as log:
            self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
            self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
            log.error.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in self.client.send_key.await_args_list],
            [Keycode.X, Keycode.A, Keycode.X, Keycode.A, Keycode.X, Keycode.A, Keycode.X],
        )
        self.assertEqual(self.camera.update_orientation.await_count, 3)

    async def test_wrong_visible_target_is_not_pressed_again(self):
        self.quester.read_quest_txt = AsyncMock(return_value="Talk")
        self.quester.quest_interaction_ready.return_value = False
        with patch("src.questing.logger") as log:
            self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
            log.error.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in self.client.send_key.await_args_list],
            [Keycode.X, Keycode.A, Keycode.A, Keycode.A],
        )


if __name__ == "__main__":
    unittest.main()
