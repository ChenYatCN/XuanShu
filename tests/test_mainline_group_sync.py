import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.questing import Quester


class MainlineGroupSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.clients = []
        for title in ('p2', 'p3', 'p4', 'p5'):
            client = SimpleNamespace(
                title=title, questing_status=True, quest_recovery_owner=None,
                mainline_chain_retry_active=False,
                quest_id=AsyncMock(return_value=151),
                zone_name=AsyncMock(return_value='Empyrea/Next'),
            )
            self.clients.append(client)
        for client in self.clients:
            client.quest_mainline_sync_members = self.clients
        self.quester = Quester(self.clients[0], [self.clients[0]], None)
        self.quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.quester._maybe_recover_mainline = AsyncMock(return_value=True)
        self.quester._mainline_identity = AsyncMock(side_effect=self.identity)
        self.visible = patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False))
        self.time = patch('src.questing.time.monotonic', side_effect=lambda: self.now)
        self.visible.start()
        self.time.start()
        self.addCleanup(self.visible.stop)
        self.addCleanup(self.time.stop)

    async def identity(self, client):
        quest_id = await client.quest_id()
        row = {'world': 'Empyrea', 'number': quest_id, 'english': 'Next'} if quest_id in (150, 151) else None
        return quest_id, 'key', 'Next', row, True

    async def check(self, client=None, at=None):
        if at is not None:
            self.now = at
        return await self.quester._mainline_sync_blocks_movement(client or self.clients[0])

    async def test_one_hitter_three_questers_wait_until_all_match(self):
        p2, p3, p4, _ = self.clients
        for client in (p2, p3, p4):
            client.quest_mainline_sync_members = [p2, p3, p4]
        p4.quest_id.return_value = 150
        self.assertTrue(await self.check(at=0))
        self.assertTrue(await self.check(at=4))
        self.quester._maybe_recover_mainline.assert_not_awaited()
        p4.quest_id.return_value = 151
        self.assertTrue(await self.check(at=4.1))
        self.assertFalse(await self.check(at=7.2))

    async def test_one_leads_many_unmatched_member_only_recovers_itself(self):
        p4 = self.clients[2]
        p4.quest_id.return_value = 0
        self.assertTrue(await self.check(at=0))
        self.assertTrue(await self.check(at=4))
        self.quester._maybe_recover_mainline.assert_not_awaited()
        self.assertTrue(await self.check(p4, at=4.1))
        self.quester._maybe_recover_mainline.assert_awaited_once_with(p4)
        p4.quest_id.return_value = 151
        self.assertTrue(await self.check(at=4.2))
        self.assertFalse(await self.check(at=7.3))

    async def test_dialogue_resets_only_affected_clients_stability(self):
        await self.check(at=0)
        self.assertFalse(await self.check(at=4))
        p4 = self.clients[2]
        async def blocked(client):
            return client is p4
        self.quester._quest_dialogue_blocks_movement.side_effect = blocked
        self.assertTrue(await self.check(at=5))
        self.quester._quest_dialogue_blocks_movement.side_effect = None
        self.quester._quest_dialogue_blocks_movement.return_value = False
        self.assertTrue(await self.check(at=5.1))
        self.assertFalse(await self.check(at=8.2))

    async def test_unmatched_id_never_passes_even_if_all_equal(self):
        for client in self.clients:
            client.quest_id.return_value = 999
        self.assertTrue(await self.check(at=0))
        self.assertTrue(await self.check(at=4))

    async def test_previous_mainline_only_retries_affected_npc_client(self):
        p2, p3, p4, _ = self.clients
        for client in (p2, p3, p4):
            client.quest_mainline_sync_members = [p2, p3, p4]
        p4.quest_id.return_value = 150
        p4.mainline_last_turn_in_snapshot = (150, 'Empyrea/Next', None, 'npc')
        self.quester._continue_mainline_chain = AsyncMock()
        await self.check(at=0)
        await self.check(p4, at=4)
        self.quester._continue_mainline_chain.assert_awaited_once_with(
            p4, p4.mainline_last_turn_in_snapshot, expected_id=151
        )
        await self.check(p4, at=5)
        self.quester._continue_mainline_chain.assert_awaited_once()

    async def test_solo_worker_does_not_read_next_target_while_group_waits(self):
        client = self.clients[0]
        client.quest_position = SimpleNamespace(position=AsyncMock())
        self.quester._mainline_sync_blocks_movement = AsyncMock(return_value=True)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        with (patch('src.questing.close_npc_quest_menu', new=AsyncMock(return_value=False)),
              patch('src.questing.close_automation_popup', new=AsyncMock(return_value=False)),
              patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False))):
            await self.quester.auto_quest_solo()
        client.quest_position.position.assert_not_awaited()
        self.quester._mainline_sync_blocks_movement.assert_awaited_once_with(client)
