import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker.memory.memory_objects.enums import WindowFlags
from src.questing import Quester


def realm_button(name, checked=False, disabled=False):
    flags = WindowFlags.visible | (WindowFlags.disabled if disabled else WindowFlags(0))
    return SimpleNamespace(
        name=AsyncMock(return_value=name),
        is_visible=AsyncMock(return_value=True),
        flags=AsyncMock(return_value=flags),
        maybe_checked=AsyncMock(return_value=checked),
    )


class RotatingRealmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            title='p1', root_window=object(), questing_status=True,
            quest_recovery_owner=None, quest_party_hitters=[],
            quest_party_probe_pending=False,
        )
        self.quester = Quester(self.client, [self.client], None)

    async def test_current_preferred_realm_chooses_backup(self):
        panel = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            children=AsyncMock(return_value=[realm_button('btnRealm1', True), realm_button('btnRealm0')]),
        )
        with patch('src.questing.get_window_from_path', AsyncMock(return_value=panel)):
            current, choices = await self.quester._rotating_realm_choices(self.client)
        self.assertEqual(current, 'btnRealm1')
        self.assertEqual(choices, ['btnRealm0'])

    async def test_unreadable_current_realm_refuses_change(self):
        panel = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            children=AsyncMock(return_value=[realm_button('btnRealm1'), realm_button('btnRealm0')]),
        )
        right = realm_button('btnRealmRight', disabled=True)
        async def get_window(_, path):
            return right if path[-1] == 'btnRealmRight' else panel
        with patch('src.questing.get_window_from_path', side_effect=get_window):
            with self.assertRaisesRegex(RuntimeError, '无法确认当前 Realm'):
                await self.quester._rotating_realm_choices(self.client)

    async def test_change_releases_lock_after_success_and_progress_check(self):
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=[(1, 2, 'old'), (1, 3, 'new')])
        self.quester._perform_rotating_realm_change = AsyncMock()
        with patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
            self.assertTrue(await self.quester.change_realm_for_rotating(self.client))
        self.quester._perform_rotating_realm_change.assert_awaited_once_with(self.client)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_change_failure_releases_lock_and_does_not_start_another_change(self):
        self.quester._dungeon_quest_snapshot = AsyncMock(return_value=(1, 2, 'old'))
        self.quester._perform_rotating_realm_change = AsyncMock(side_effect=RuntimeError('UI unavailable'))
        with patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
            self.assertTrue(await self.quester.change_realm_for_rotating(self.client))
            self.client.quest_recovery_owner = 'nightmare_krok'
            self.assertFalse(await self.quester.change_realm_for_rotating(self.client))
        self.assertEqual(self.quester._perform_rotating_realm_change.await_count, 1)

    async def test_hitter_ack_is_required_before_releasing_lock(self):
        hitter = SimpleNamespace(title='p2')
        self.client.quest_party_hitters = [hitter]
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=[(1, 2, 'old'), (1, 3, 'new')])
        self.quester._perform_rotating_realm_change = AsyncMock()

        async def acknowledge():
            while getattr(self.client, 'quest_rotating_realm_sync', None) is None:
                await asyncio.sleep(0)
            sync = self.client.quest_rotating_realm_sync
            self.assertEqual(self.client.quest_recovery_owner, 'rotating_realm')
            sync['done'].add(id(hitter))
            self.client.quest_party_realm_unsynced.discard(id(hitter))

        task = asyncio.create_task(acknowledge())
        try:
            with patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
                self.assertTrue(await self.quester.change_realm_for_rotating(self.client))
            await task
        finally:
            task.cancel()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.quest_party_realm_unsynced)


if __name__ == '__main__':
    unittest.main()
