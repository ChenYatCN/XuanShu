import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src.paths import all_quests_sort_button_path, quest_two_button_path
from src.questing import Quester


class DungeonQuestRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.title = "p1"
        self.client.questing_status = True
        self.client.quest_dungeon_recovery = None
        for name in (
            "quest_party_probe_pending", "quest_party_battle_rescue_active",
            "quest_party_quest_worker_restart_requested", "post_combat_movement_active",
            "auto_dialogue_running",
        ):
            setattr(self.client, name, False)
        self.client.zone_name.return_value = "Dungeon/RoomA"
        self.client.quest_id.return_value = 42
        self.client.goal_id.return_value = 7
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = False
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(return_value="Defeat 0/3")
        self.now = 0.0
        self.menu_open = False

        async def send_key(key, *args, **kwargs):
            if key == Keycode.Q:
                self.menu_open = not self.menu_open

        async def visible(_client, path):
            return self.menu_open and (
                path == all_quests_sort_button_path
                or path == [*quest_two_button_path, "questInfoWindow", "wndQuestInfo", "txtGoal"]
            )

        self.client.send_key.side_effect = send_key
        self.visible = patch("src.questing.is_visible_by_path", side_effect=visible)
        self.free = patch("src.questing.is_free_leader_questing", new=AsyncMock(return_value=True))
        self.spiral = patch("src.questing.is_spiral_door_open", new=AsyncMock(return_value=False))
        self.click = patch("src.questing.click_window_by_path", new=AsyncMock())
        self.clock = patch("src.questing.time", SimpleNamespace(monotonic=lambda: self.now))
        self.visible.start()
        self.free.start()
        self.spiral.start()
        self.clicked = self.click.start()
        self.clock.start()
        for item in (self.clock, self.click, self.spiral, self.free, self.visible):
            self.addCleanup(item.stop)

    async def arm(self):
        await self.quester._confirm_dungeon_entry(self.client, "World/Entrance")
        self.assertEqual(self.client.quest_dungeon_recovery["zone"], "Dungeon/RoomA")
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))

    async def test_confirmed_entry_and_three_minute_threshold(self):
        await self.quester._confirm_dungeon_entry(self.client, "Dungeon/RoomA")
        self.assertIsNone(self.client.quest_dungeon_recovery)
        await self.arm()
        self.now = 179.9
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()
        self.now = 180.0
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 2)
        self.clicked.assert_awaited_once_with(
            self.client,
            [*quest_two_button_path, "questInfoWindow", "wndQuestInfo", "txtGoal"],
        )
        self.assertFalse(self.menu_open)
        self.now = 181.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 2)
        self.now = 360.0
        self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.send_key.await_count, 4)

    async def test_goal_count_progress_resets_timer(self):
        await self.arm()
        self.now = 179.0
        self.quester.read_quest_txt.return_value = "Defeat 1/3"
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.now = 180.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_busy_state_defers_then_rechecks_progress(self):
        await self.arm()
        self.now = 181.0
        self.client.quest_party_probe_pending = True
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()
        self.client.quest_party_probe_pending = False
        self.quester.read_quest_txt.return_value = "Defeat 1/3"
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_unconfirmed_new_zone_disarms_recovery(self):
        await self.arm()
        self.client.zone_name.return_value = "World/Town"
        self.now = 300.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertIsNone(self.client.quest_dungeon_recovery)
        self.client.send_key.assert_not_awaited()

    async def test_party_confirmed_new_room_resets_timer(self):
        await self.arm()
        self.client.zone_name.return_value = "Dungeon/RoomB"
        self.client.quest_party_group_dungeon_zone = "Dungeon/RoomB"
        self.now = 300.0
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.assertEqual(self.client.quest_dungeon_recovery["zone"], "Dungeon/RoomB")
        self.assertIsNone(self.client.quest_dungeon_recovery["since"])
        self.assertFalse(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_priority_state_after_open_closes_without_click(self):
        await self.arm()
        self.now = 180.0
        with patch.object(
            self.quester, "_dungeon_recovery_blocked_for_open_menu",
            new=AsyncMock(return_value=True),
        ):
            self.assertTrue(await self.quester._maybe_refresh_stalled_dungeon_quest(self.client))
        self.clicked.assert_not_awaited()
        self.assertFalse(self.menu_open)
        self.assertEqual(self.client.send_key.await_count, 2)


if __name__ == "__main__":
    unittest.main()
