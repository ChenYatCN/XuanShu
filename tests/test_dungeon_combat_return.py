"""A recorded dungeon's old battle return cannot override new group progress."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src import quest_party as party
from test_dungeon_local_tp import DungeonFixture, client
from test_full_chain_415_restore import entry


class DungeonCombatReturnTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def capture(self, member=None):
        await self.enter()
        await party.capture_dungeon_return(member or self.h)

    async def test_unchanged_dungeon_context_allows_existing_return(self):
        await self.capture()
        self.assertTrue(await party.dungeon_return_is_current(self.h))

    async def test_questers_own_unchanged_context_allows_return(self):
        await self.capture(self.q)
        self.assertTrue(await party.dungeon_return_is_current(self.q))

    async def test_own_group_quest_progress_invalidates_hitter_return(self):
        await self.capture()
        self.q.quest_id_value += 1
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_own_group_goal_progress_invalidates_hitter_return(self):
        await self.capture()
        self.q.goal_id.return_value = 8
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_own_group_target_progress_invalidates_hitter_return(self):
        await self.capture()
        self.q.quest_position.position.return_value = XYZ(4000, 5000, 0)
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_member_room_change_invalidates_return(self):
        await self.capture()
        self.h.zone = 'Dungeon/Room2'
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_quester_room_change_invalidates_hitter_return(self):
        await self.capture()
        self.q.zone = 'Dungeon/Room2'
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_cleared_session_invalidates_saved_dungeon_return(self):
        await self.capture()
        party.clear_dungeon_session(self.h)
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_replaced_session_invalidates_saved_dungeon_return(self):
        await self.capture()
        old = party.dungeon_session_for(self.h)
        party.clear_dungeon_session(self.h)
        for member in (self.q, self.h):
            member.zone = 'Outside'
        new = await self.enter()
        self.assertIsNot(new, old)
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_group_reassignment_invalidates_saved_return(self):
        await self.capture()
        self.q.quest_party_hitters = []
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_stopped_member_invalidates_saved_return(self):
        await self.capture()
        self.h.questing_status = False
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_unrelated_group_progress_does_not_invalidate_return(self):
        await self.capture()
        unrelated = client(3, XYZ(500, 600, 0))
        unrelated.quest_id_value = 99
        unrelated.zone = 'OtherDungeon/Room2'
        self.assertTrue(await party.dungeon_return_is_current(self.h))

    async def test_hitters_independent_task_does_not_change_shared_return_context(self):
        await self.capture()
        self.h.quest_id_value += 1
        self.h.goal_id.return_value = 8
        self.h.quest_position.position.return_value = XYZ(4000, 5000, 0)
        self.assertTrue(await party.dungeon_return_is_current(self.h))

    async def test_no_recorded_dungeon_preserves_legacy_return(self):
        await party.capture_dungeon_return(self.h)
        self.q.quest_id_value += 1
        self.h.zone = 'AnotherZone'
        self.assertTrue(await party.dungeon_return_is_current(self.h))

    async def test_unconfirmed_entry_preserves_legacy_return(self):
        await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        await party.capture_dungeon_return(self.h)
        self.q.quest_id_value += 1
        self.assertTrue(await party.dungeon_return_is_current(self.h))

    async def test_unreadable_saved_dungeon_context_cannot_return(self):
        await self.enter()
        self.q.quest_id.side_effect = ValueError('quest memory unavailable')
        await party.capture_dungeon_return(self.h)
        self.q.quest_id.side_effect = lambda: self.q.quest_id_value
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_unreadable_live_context_cannot_return(self):
        await self.capture()
        self.q.goal_id.side_effect = ValueError('goal memory unavailable')
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_missing_numeric_quest_identity_cannot_authorize_return(self):
        await self.enter()
        self.q.quest_id_value = None
        await party.capture_dungeon_return(self.h)
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_replaced_instance_in_same_zone_invalidates_old_position(self):
        await self.capture()
        self.h.area_id += 1
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_loading_round_trip_invalidates_old_instance_position(self):
        await self.capture()
        self.h.is_loading.return_value = True
        await party.observe_dungeon_session(self.h)
        from src.utils import observe_party_area
        await observe_party_area(self.h)
        self.h.is_loading.return_value = False
        await observe_party_area(self.h)
        self.assertFalse(await party.dungeon_return_is_current(self.h))

    async def test_failed_join_discards_old_return_without_fallback_movement(self):
        await self.capture()
        original = XYZ(10, 20, 30)
        locations = {self.h.process_id: original}
        self.h.original_location_before_combat = original
        self.h.entity_detect_combat_status = True
        # This changes while the actual join checker is waiting seven seconds.
        async def wait(_):
            self.q.quest_id_value += 1
        checker = entry('is_duel_circle_joinable', namespace={
            'asyncio': SimpleNamespace(sleep=AsyncMock(side_effect=wait)),
            'SprintyClient': lambda member: member,
            'original_client_locations': locations,
        }, nearest_duel_circle_distance_and_xyz=AsyncMock(return_value=(900, XYZ(0, 0, 0))))
        self.assertFalse(await checker(self.h))
        self.h.teleport.assert_not_awaited()
        self.assertNotIn(self.h.process_id, locations)
        self.assertIsNone(self.h.original_location_before_combat)
        self.assertFalse(self.h.entity_detect_combat_status)


if __name__ == '__main__':
    unittest.main()
