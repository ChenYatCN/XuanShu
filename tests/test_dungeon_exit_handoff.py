"""Confirmed quester exits hand local followers back to the existing flow."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.paths import npc_range_path
from src import quest_party as party
from src.questing import Quester
import tests.test_dungeon_local_tp as fixtures


def local_goal(c, destination='Dungeon/Room1'):
    goal = SimpleNamespace(goal_destination_zone=AsyncMock(return_value=destination))
    quest = SimpleNamespace(goal_data=AsyncMock(return_value={7: goal}))
    c.quest_manager = AsyncMock(return_value=SimpleNamespace(
        quest_data=AsyncMock(return_value={42: quest})))
    return goal


class ExitGoalTests(fixtures.DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def test_only_valid_local_goal_moves_after_quester_exit(self):
        for destination, expected in [('Dungeon/Room1', True), ('Outside', False), ('', False)]:
            with self.subTest(destination=destination):
                self.setUp()
                await self.enter()
                self.q.zone = 'Outside'
                await party.observe_dungeon_session(self.q)
                local_goal(self.h, destination)
                reader = Quester(self.h, [self.h], None)
                reader.teleport_to_quest_target = AsyncMock()
                reader.handle_questing_zone_change = AsyncMock()
                self.assertEqual(await reader.move_dungeon_member(require_local_goal=True), expected)
                self.assertEqual(reader.teleport_to_quest_target.await_count, int(expected))

    async def test_unreadable_or_changed_goal_never_uses_old_target(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                self.setUp()
                await self.enter()
                goal = local_goal(self.h)
                if changed:
                    async def switch():
                        self.h.quest_id_value = 99
                        return 'Dungeon/Room1'
                    goal.goal_destination_zone.side_effect = switch
                else:
                    goal.goal_destination_zone.side_effect = RuntimeError('unreadable')
                reader = Quester(self.h, [self.h], None)
                reader.teleport_to_quest_target = AsyncMock()
                self.assertFalse(await reader.move_dungeon_member(require_local_goal=True))
                reader.teleport_to_quest_target.assert_not_awaited()

    async def test_nonfinite_target_never_moves_after_exit(self):
        await self.enter()
        local_goal(self.h)
        self.h.quest_position.position.return_value = XYZ(float('nan'), 1, 2)
        reader = Quester(self.h, [self.h], None)
        reader.teleport_to_quest_target = AsyncMock()
        self.assertFalse(await reader.move_dungeon_member(require_local_goal=True))
        reader.teleport_to_quest_target.assert_not_awaited()

    async def test_unreadable_target_hands_off_instead_of_restarting_local_loop(self):
        await self.enter()
        local_goal(self.h)
        self.h.quest_position.position.side_effect = RuntimeError('unreadable target')
        self.assertFalse(await Quester(self.h, [self.h], None).move_dungeon_member(require_local_goal=True))

    async def test_quester_exit_still_waits_for_probe_then_releases_worker(self):
        await self.enter()
        self.q.zone = 'Outside'
        self.q.quest_party_quest_worker_zone = 'Outside'
        self.q.quest_party_probe_pending = True
        reader = Quester(self.q, [self.q], None)
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.q.quest_party_probe_pending = False
        self.assertFalse(await reader._quest_party_probe_blocks_movement())
        self.assertTrue(party.dungeon_member_inside(self.h))
        reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        reader.handle_questing_zone_change = AsyncMock()
        reader.teleport_to_quest_target = AsyncMock(side_effect=lambda c, target: setattr(c, 'position', target))
        reader.read_popup = AsyncMock(return_value='Press X to Talk')
        reader.handle_npc_talking_quests = AsyncMock(return_value=False)
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'):
            self.stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.stack.enter_context(patch('src.questing.is_visible_by_path',
            AsyncMock(side_effect=lambda _, path: path == npc_range_path)))
        self.stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Talk to NPC')))
        await reader.auto_quest_solo()
        reader.teleport_to_quest_target.assert_awaited_once_with(self.q, self.q.quest_position.position.return_value)
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        reader.handle_npc_talking_quests.assert_awaited_once()


class ExitFollowerTests(fixtures.DungeonFixture, unittest.IsolatedAsyncioTestCase):
    follow_until = fixtures.FollowerTests.follow_until
    async def test_exit_without_local_goal_probes_and_rejoins(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.quest_party_quest_worker_zone = 'Outside'
        self.q.quest_party_probe_pending = True
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        async def friend(*_, **__):
            self.h.zone = self.q.zone
        async def followed(_target):
            self.assertEqual(self.h.zone, self.q.zone)
        self.h.teleport.side_effect = followed
        def status(_h, _q, text):
            if text == '已归队':
                done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()) as move:
            await self.follow_until(done, primary=True, when_status=status,
                extra={'teleport_to_friend_from_list': AsyncMock(side_effect=friend)})
        move.assert_not_awaited()
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_observed_zone, 'Outside')
        self.assertEqual(len(session.exited), 2)
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_friend_denied_uses_existing_hub_exit_and_cleans_member(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        from src.utils import FriendBusyOrInstanceClosed
        async def end(key, *_):
            self.assertEqual(key, Keycode.END)
            self.h.zone = 'World/Hub'
        self.h.send_key.side_effect = end
        def status(_h, _q, text):
            if id(self.h) in session.exited:
                done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()) as move:
            await self.follow_until(done, primary=True, when_status=status, extra={
                'teleport_to_friend_from_list': AsyncMock(side_effect=FriendBusyOrInstanceClosed())})
        move.assert_not_awaited()
        self.h.teleport.assert_not_awaited()
        self.q.body.position.assert_not_awaited()
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertIsNone(party.dungeon_session_for(self.h))
        self.assertEqual([call.args for call in self.h.send_key.await_args_list], [(Keycode.END,), (Keycode.END,)])

    async def test_valid_local_goal_finishes_before_handoff(self):
        local_goal(self.h)
        await fixtures.FollowerTests.test_quester_exits_first_hitter_continues_own_exit_then_normal_follow_resumes(self)

    async def test_equal_paths_require_live_reunion_before_cleanup(self):
        session = await self.enter()
        party.record_dungeon_exit(self.q, 'Dungeon/Room1', 'Dungeon/Room2')
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = self.q.zone
        self.h.get_base_entity_list.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=1))]
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        def status(_h, _q, text):
            if text == '已归队': done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()) as move:
            await self.follow_until(done, primary=True, when_status=status)
        move.assert_not_awaited()
        self.assertEqual(len(session.exited), 2)
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_hub_timeout_during_loading_records_late_exit_without_second_end(self):
        session = await self.enter()
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        from src.utils import FriendBusyOrInstanceClosed
        async def slow_load(_reader):
            self.h.is_loading.return_value = True
            raise TimeoutError('slow hub load')
        def status(_h, _q, text):
            if id(self.h) in session.exited:
                done.set()
            elif self.h.is_loading.return_value:
                self.h.zone = 'World/Hub'
                self.h.is_loading.return_value = False
        with patch.object(Quester, 'handle_questing_zone_change', slow_load):
            await self.follow_until(done, primary=True, when_status=status, extra={
                'teleport_to_friend_from_list': AsyncMock(side_effect=FriendBusyOrInstanceClosed())})
        self.assertEqual([call.args for call in self.h.send_key.await_args_list], [(Keycode.END,), (Keycode.END,)])
        self.assertIsNone(party.dungeon_session_for(self.h))


if __name__ == '__main__':
    unittest.main()
