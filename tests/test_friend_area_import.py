"""Use the application's real area-check global, without injecting its name."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src import utils
from src.questing import Quester
from test_dungeon_local_tp import DungeonFixture, REAL_SLEEP
from test_full_chain_415_restore import APP, entry

REAL_AREA_CHECK = utils.clients_share_live_area


class FriendAreaImportTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        # Mock memory surfaces, never replace or inject the area-check function.
        self.stack.enter_context(patch.object(utils, 'clients_share_live_area', REAL_AREA_CHECK))
        self.stack.enter_context(patch('src.questing.clients_share_live_area', REAL_AREA_CHECK))
        self.stack.enter_context(patch.object(utils, 'SprintyClient', lambda c: c))
        self.q.get_base_entity_list.return_value = [self.h.client_object]
        self.h.get_base_entity_list.return_value = [self.q.client_object]

    def check(self):
        return entry('hitter_is_in_quester_area', hitter=self.h, quester=self.q)

    async def test_real_app_import_and_area_check_resolve_existing_function(self):
        self.assertIs(APP.__dict__.get('clients_share_live_area'), REAL_AREA_CHECK)
        self.assertTrue(await self.check()())

    async def follow_until_probe_result(self, done):
        clock = [0.0]
        async def tick(seconds):
            clock[0] += seconds
            await REAL_SLEEP(0)
        self.friend = AsyncMock(side_effect=lambda *_args, **_kwargs: done.set())
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[self.q, self.h]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False,
            buy_potions=False, close_automation_popup=AsyncMock(return_value=False),
            is_free=AsyncMock(return_value=True), is_visible_by_path=AsyncMock(return_value=False),
            is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=self.friend,
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError)),
            members=[self.q, self.h], change_party_equipment=AsyncMock(return_value=True),
            prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=Mock())
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            task = asyncio.create_task(follow(self.h, self.q, True))
            try:
                await asyncio.wait_for(done.wait(), 1.0)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                results = await asyncio.gather(task, return_exceptions=True)
                self.assertIsInstance(results[0], asyncio.CancelledError)

    async def test_actual_reunion_clears_probe_and_resumes_existing_movement(self):
        await self.enter()
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = self.q.zone
        self.q.quest_party_quest_worker_zone = 'Dungeon/Room1'
        self.q.quest_party_probe_pending = True
        done = asyncio.Event()
        async def move(_reader):
            self.assertFalse(self.q.quest_party_probe_pending)
            self.assertEqual(self.q.quest_party_quest_worker_zone, self.q.zone)
            done.set()
            return True
        with patch.object(Quester, 'move_dungeon_member', move):
            await self.follow_until_probe_result(done)
        self.friend.assert_not_awaited()
        self.assertFalse(await Quester(self.q, [self.q], None)._quest_party_probe_blocks_movement())

    async def test_different_zones_keep_friend_follow_flow(self):
        self.q.zone, self.h.zone = 'Room2', 'Room1'
        self.q.quest_party_observed_zone = self.q.zone
        self.q.quest_party_probe_pending = True
        self.assertFalse(await self.check()())
        await self.follow_until_probe_result(asyncio.Event())
        self.friend.assert_awaited_once()
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_same_zone_without_instance_proof_keeps_probe(self):
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = self.q.zone
        self.q.quest_party_probe_pending = True
        self.q.get_base_entity_list.return_value = []
        self.h.get_base_entity_list.return_value = []
        self.q.area_id, self.h.area_id = 201, 202
        self.assertFalse(await self.check()())
        await self.follow_until_probe_result(asyncio.Event())
        self.friend.assert_awaited_once()
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_memory_failure_does_not_claim_reunion_or_clear_probe(self):
        self.q.quest_party_probe_pending = True
        self.q.zone_name.side_effect = RuntimeError('area read failed')
        self.assertFalse(await self.check()())
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_cancellation_propagates_without_claiming_reunion(self):
        self.q.quest_party_probe_pending = True
        self.h.zone_name.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.check()()
        self.assertTrue(self.q.quest_party_probe_pending)


if __name__ == '__main__':
    unittest.main()
