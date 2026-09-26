import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.questing import Quester


class MainlineChainHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.position = XYZ(10, 20, 30)
        self.mainline = SimpleNamespace(mainline=AsyncMock(return_value=True))
        self.sidequest = SimpleNamespace(mainline=AsyncMock(return_value=False))
        self.client = SimpleNamespace(
            title="p1",
            questing_status=True,
            auto_dialogue_running=True,
            mainline_chain_retry_active=False,
            quest_party_probe_pending=False,
            quest_party_battle_rescue_active=False,
            quest_party_quest_worker_restart_requested=False,
            quest_id=AsyncMock(return_value=0),
            quest_manager=AsyncMock(return_value=SimpleNamespace(
                quest_data=AsyncMock(return_value={10: self.mainline, 11: self.mainline,
                                                   12: self.sidequest})
            )),
            zone_name=AsyncMock(return_value="Zone"),
            body=SimpleNamespace(position=AsyncMock(return_value=self.position)),
            is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False),
            send_key=AsyncMock(),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(return_value="Press X to Talk")
        self.snapshot = (10, "Zone", self.position, "same npc")
        self.patches = [
            patch("src.questing.get_popup_title", new=AsyncMock(return_value="Same NPC")),
            patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True)),
            patch("src.questing.is_visible_by_path", new=AsyncMock(
                side_effect=lambda _client, path: path == ["WorldView", "NPCRangeWin"]
            )),
            patch("src.questing.asyncio.sleep", new=AsyncMock()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_snapshot_requires_a_mainline_and_named_talk_prompt(self):
        self.client.quest_id.return_value = 10
        self.assertEqual(
            await self.quester._mainline_turn_in_snapshot(self.client, 10),
            self.snapshot,
        )
        self.assertIsNone(await self.quester._mainline_turn_in_snapshot(self.client, 12))
        self.quester.read_popup.return_value = "Press X to Enter"
        self.assertIsNone(await self.quester._mainline_turn_in_snapshot(self.client, 10))

    async def test_new_quest_or_missing_auto_dialogue_never_presses_x(self):
        self.client.quest_id.return_value = 11
        await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.quest_id.return_value = 0
        self.client.auto_dialogue_running = False
        await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.send_key.assert_not_awaited()

    async def test_wrong_npc_or_recovery_never_presses_x(self):
        self.client.quest_party_probe_pending = True
        await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.quest_party_probe_pending = False
        with patch("src.questing.get_popup_title", new=AsyncMock(return_value="Other NPC")):
            await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.send_key.assert_not_awaited()

    async def test_accepting_new_mainline_stops_after_one_x(self):
        async def quest_id():
            return 11 if self.client.send_key.await_count else 0
        self.client.quest_id.side_effect = quest_id
        await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.client.send_key.assert_awaited_once_with(Keycode.X, 0.15)
        self.assertFalse(self.client.mainline_chain_retry_active)

    async def test_sidequest_identity_stops_without_another_x(self):
        async def quest_id():
            return 12 if self.client.send_key.await_count else 0
        self.client.quest_id.side_effect = quest_id
        await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.assertEqual(self.client.send_key.await_count, 1)
        self.assertFalse(self.client.mainline_chain_retry_active)

    async def test_no_progress_has_a_two_press_limit(self):
        ticks = iter(range(0, 1000, 2))
        with patch("src.questing.time.monotonic", side_effect=lambda: next(ticks)):
            await self.quester._continue_mainline_chain(self.client, self.snapshot)
        self.assertEqual(self.client.send_key.await_count, 2)
        self.assertFalse(self.client.mainline_chain_retry_active)

    async def test_regular_npc_dialogue_has_no_extra_x(self):
        self.client.quest_id.return_value = 12
        self.quester.read_quest_txt = AsyncMock(
            side_effect=lambda _client: "Next objective" if self.client.send_key.await_count else "Talk"
        )
        self.client.quest_position = SimpleNamespace(
            position=AsyncMock(return_value=self.position)
        )
        with patch("src.questing.exit_menus", new=AsyncMock()), \
             patch.object(self.quester, "_continue_mainline_chain", new=AsyncMock()) as handoff:
            self.assertTrue(await self.quester.handle_npc_talking_quests(
                self.client, [self.client]
            ))
        self.client.send_key.assert_awaited_once_with(Keycode.X, 0.1)
        handoff.assert_awaited_once_with(self.client, None)


if __name__ == "__main__":
    unittest.main()
