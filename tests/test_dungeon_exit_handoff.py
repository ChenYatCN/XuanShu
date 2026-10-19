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
        reader.read_quest_txt = AsyncMock(return_value='Talk to NPC')
        reader.handle_npc_talking_quests = AsyncMock(return_value=True)
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'):
            self.stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.stack.enter_context(patch('src.questing.is_visible_by_path',
            AsyncMock(side_effect=lambda _, path: path == npc_range_path)))
        self.stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Talk to NPC')))
        self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(return_value='Talk to NPC')))
        await reader.auto_quest_solo()
        reader.teleport_to_quest_target.assert_awaited_once_with(self.q, self.q.quest_position.position.return_value)
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        reader.handle_npc_talking_quests.assert_awaited_once()
        self.assertTrue(reader.handle_npc_talking_quests.await_args.kwargs['post_x'])

    async def test_quester_out_readable_popup_does_not_require_title_for_x(self):
        await self.enter()
        self.q.zone = 'Outside'
        self.q.quest_party_quest_worker_zone = 'Outside'
        self.q.quest_party_probe_pending = True
        reader = Quester(self.q, [self.q], None)
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.q.quest_party_probe_pending = False
        self.assertFalse(await reader._quest_party_probe_blocks_movement())
        reader.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        reader.handle_questing_zone_change = AsyncMock()
        reader.teleport_to_quest_target = AsyncMock(side_effect=lambda c, target: setattr(c, 'position', target))
        reader.read_popup = AsyncMock(return_value='Press X to Talk')
        reader.read_quest_txt = AsyncMock(return_value='Talk to NPC')
        reader.handle_npc_talking_quests = AsyncMock(return_value=False)
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'):
            self.stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.stack.enter_context(patch('src.questing.is_visible_by_path',
            AsyncMock(side_effect=lambda _, path: path == npc_range_path)))
        self.stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
        self.stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(return_value='Talk to NPC')))
        self.stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(return_value='')))
        await reader.auto_quest_solo()
        reader.teleport_to_quest_target.assert_awaited_once()
        self.q.send_key.assert_awaited_once_with(Keycode.X, .1)
        reader.handle_npc_talking_quests.assert_awaited_once()
        self.assertTrue(reader.handle_npc_talking_quests.await_args.kwargs['post_x'])


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
        repeat = asyncio.Event()
        def repeated_status(_h, _q, text):
            repeat.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(repeat, primary=True, when_status=repeated_status)
        self.assertEqual(self.h.send_key.await_count, 2)
        self.assertIsNone(party.dungeon_session_for(self.h))
        self.assertEqual(session.phase, 'retired')
        self.assertEqual(session.exited, {id(self.q), id(self.h)})

    async def test_friend_busy_backoff_does_not_block_local_hub_exit(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        self.h._friend_follow_busy_backoff = {'busy_until': float('inf'), 'last_block': 'busy-backoff'}
        async def end(key, *_):
            self.h.zone = 'World/Hub'
        self.h.send_key.side_effect = end
        friend = AsyncMock()
        done = asyncio.Event()
        def status(_h, _q, text):
            if id(self.h) in session.exited:
                done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status,
                extra={'teleport_to_friend_from_list': friend})
        friend.assert_not_awaited()
        self.assertEqual(len(self.h.send_key.await_args_list), 2)
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_owner_busy_waits_then_local_hub_exit(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        self.h.quest_recovery_owner = 'manual'
        async def end(key, *_):
            self.h.zone = 'World/Hub'
        self.h.send_key.side_effect = end
        friend = AsyncMock()
        first = asyncio.Event()
        def waiting(_h, _q, text):
            if text == '等待已有恢复流程完成':
                first.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(first, primary=True, when_status=waiting,
                extra={'teleport_to_friend_from_list': friend})
        self.h.send_key.assert_not_awaited()
        friend.assert_not_awaited()
        self.h.quest_recovery_owner = None
        second = asyncio.Event()
        def released(_h, _q, text):
            if text == '已归队':
                second.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(second, primary=True, when_status=released,
                extra={'teleport_to_friend_from_list': friend})
        self.assertEqual(len(self.h.send_key.await_args_list), 2)
        friend.assert_not_awaited()
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_two_groups_retire_independently_during_exit(self):
        q3 = fixtures.client(3, XYZ(1, 2, 0))
        h3 = fixtures.client(4, XYZ(3, 4, 0))
        q3.quest_party_hitters = [h3]
        h3.quest_party_status_session = object()
        second = await party.begin_dungeon_entry(q3, [q3, h3], evidence=True)
        q3.zone = h3.zone = 'Dungeon/Room1'
        q3.area_id = h3.area_id = 200
        await party.observe_dungeon_session(q3)
        self.assertTrue(second.confirmed)
        first = await self.enter()
        party.retire_dungeon_session(first, reason='quester-out')
        self.assertEqual(first.phase, 'retired')
        self.assertIs(party.dungeon_session_for(q3), second)
        self.assertEqual(second.phase, 'active')
        self.assertIs(party.dungeon_session_for(h3), second)

    async def test_recreated_follower_reference_keeps_attempt_count(self):
        provenance = {
            'phase': 'confirmed', 'epoch': 1, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {self.h.process_id: 2}, 'arrived': set(),
        }
        reader = Quester(self.h, [self.h], None)
        self.h.zone = 'Dungeon/Room1'
        with patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertEqual(await reader._replay_quest_transition(provenance), 'exhausted')
        self.h.teleport.assert_not_awaited()

    def replay_client(self, index):
        member = fixtures.client(index, XYZ(5, 6, 0))
        member.zone = 'Dungeon/Room1'
        member.area_id = 100
        async def key(_key, _seconds):
            member.zone = 'Dungeon/Room2'
        member.send_key.side_effect = key
        return member

    async def test_two_followers_independently_replay_same_confirmed_provenance(self):
        h2 = self.replay_client(3)
        self.h.zone = 'Dungeon/Room1'
        self.h.area_id = 100
        async def h_key(_key, _seconds):
            self.h.zone = 'Dungeon/Room2'
        self.h.send_key.side_effect = h_key
        provenance = {
            'phase': 'confirmed', 'epoch': 8, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        with patch('src.questing.is_free', AsyncMock(return_value=True)), patch(
                'src.questing._movement_interaction_signature',
                AsyncMock(return_value=('Door', 'Press X to Use'))):
            first = await Quester(self.h, [self.h], None)._replay_quest_transition(provenance)
            second = await Quester(h2, [h2], None)._replay_quest_transition(provenance)
        self.assertEqual((first, second), ('arrived', 'arrived'))
        self.assertEqual(provenance['arrived'], {self.h.process_id, h2.process_id})
        self.assertEqual(provenance['attempts'][self.h.process_id], 1)
        self.assertEqual(provenance['attempts'][h2.process_id], 1)

    async def test_recreated_follower_reference_keeps_arrived_state(self):
        self.h.zone = 'Dungeon/Room1'
        provenance = {
            'phase': 'confirmed', 'epoch': 9, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {self.h.process_id: 1}, 'arrived': {self.h.process_id},
        }
        reader = Quester(self.h, [self.h], None)
        with patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertEqual(await reader._replay_quest_transition(provenance), 'arrived')
        self.h.teleport.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_recreated_follower_reference_cannot_reset_exhausted_local_replay(self):
        self.h.zone = 'Dungeon/Room1'
        provenance = {
            'phase': 'confirmed', 'epoch': 10, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {self.h.process_id: 2}, 'arrived': set(),
        }
        reader = Quester(self.h, [self.h], None)
        with patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertEqual(await reader._replay_quest_transition(provenance), 'exhausted')
        self.h.teleport.assert_not_awaited()

    async def test_local_transition_exhaustion_falls_back_to_friend_once(self):
        await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.q._quest_transition_provenance = {
            'phase': 'confirmed', 'epoch': 11, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {self.h.process_id: 2}, 'arrived': set(),
        }
        done = asyncio.Event()
        def status(_h, _q, text):
            if text == '正在好友传送':
                done.set()
        friend = AsyncMock()
        with patch.object(Quester, '_replay_quest_transition',
                AsyncMock(return_value='exhausted')) as replay:
            await self.follow_until(done, primary=True, when_status=status,
                extra={'teleport_to_friend_from_list': friend})
        replay.assert_awaited()
        friend.assert_awaited_once()

    async def test_active_dungeon_session_transition_precedes_ordinary_provenance(self):
        session = await self.enter()
        self.assertTrue(await party.remember_dungeon_transition(self.q, XYZ(5, 6, 0)))
        session.transition['entry_sent'] = True
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.q._quest_transition_provenance = {
            'phase': 'confirmed', 'epoch': 12, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        done = asyncio.Event()
        async def later():
            await asyncio.sleep(0.1)
            done.set()
        asyncio.create_task(later())
        with patch.object(Quester, '_replay_quest_transition',
                AsyncMock(return_value='arrived')) as replay:
            await self.follow_until(done, primary=True,
                extra={'teleport_to_friend_from_list': AsyncMock()})
        replay.assert_not_awaited()

    async def test_new_pending_transition_supersedes_old_epoch(self):
        reader = Quester(self.q, [self.q], None)
        old = {'phase': 'pending', 'epoch': 'old', 'source_zone': 'Dungeon/Room1',
               'deadline': float('inf'), 'ack_deadline': float('inf')}
        new = {'phase': 'pending', 'epoch': 'new', 'source_zone': 'Dungeon/Room1',
               'deadline': float('inf'), 'ack_deadline': float('inf')}
        self.q._quest_transition_provenance = old
        self.q._quest_transition_provenance = new
        self.q.zone = 'Dungeon/Room2'
        self.assertTrue(await reader._confirm_quest_transition_provenance())
        self.assertEqual(new['phase'], 'confirmed')
        self.assertEqual(old['phase'], 'pending')

    async def test_expired_pending_provenance_is_not_confirmed(self):
        reader = Quester(self.q, [self.q], None)
        self.q._quest_transition_provenance = {
            'phase': 'pending', 'epoch': 1, 'source_zone': 'Dungeon/Room1',
            'deadline': 0, 'ack_deadline': 0, 'attempts': {}, 'arrived': set(),
        }
        self.assertFalse(await reader._confirm_quest_transition_provenance())
        self.assertEqual(self.q._quest_transition_provenance['phase'], 'expired')

    async def test_pending_transition_without_zone_change_is_not_confirmed(self):
        reader = Quester(self.q, [self.q], None)
        self.q.zone = 'Dungeon/Room1'
        self.q._quest_transition_provenance = {
            'phase': 'pending', 'epoch': 2, 'source_zone': 'Dungeon/Room1',
            'deadline': float('inf'), 'ack_deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        self.assertFalse(await reader._confirm_quest_transition_provenance())
        self.assertEqual(self.q._quest_transition_provenance['phase'], 'pending')

    async def test_matching_zone_change_confirms_pending_epoch(self):
        reader = Quester(self.q, [self.q], None)
        self.q.zone = 'Dungeon/Room2'
        self.q._quest_transition_provenance = {
            'phase': 'pending', 'epoch': 3, 'source_zone': 'Dungeon/Room1',
            'deadline': float('inf'), 'ack_deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        self.assertTrue(await reader._confirm_quest_transition_provenance())
        self.assertEqual(self.q._quest_transition_provenance['phase'], 'confirmed')
        self.assertEqual(self.q._quest_transition_provenance['target_zone'], 'Dungeon/Room2')

    async def test_recovery_owner_zone_change_does_not_confirm_pending(self):
        reader = Quester(self.q, [self.q], None)
        self.q.zone = 'Dungeon/Room2'
        self.q.quest_recovery_owner = 'manual'
        self.q._quest_transition_provenance = {
            'phase': 'pending', 'epoch': 4, 'source_zone': 'Dungeon/Room1',
            'deadline': float('inf'), 'ack_deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        self.assertFalse(await reader._confirm_quest_transition_provenance())
        self.assertEqual(self.q._quest_transition_provenance['phase'], 'invalid')

    async def test_wrong_source_or_loading_follower_never_replays(self):
        source = Quester(self.h, [self.h], None)
        provenance = {
            'phase': 'confirmed', 'epoch': 5, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        self.h.zone = 'Dungeon/Other'
        with patch('src.questing.is_free', AsyncMock(return_value=True)):
            self.assertEqual(await source._replay_quest_transition(provenance), 'wrong-source')
        self.h.teleport.assert_not_awaited()
        self.h.zone = 'Dungeon/Room1'
        self.h.is_loading.return_value = True
        self.assertEqual(await source._replay_quest_transition(provenance), 'busy')
        self.h.teleport.assert_not_awaited()

    async def test_identity_mismatch_never_sends_task_x(self):
        source = Quester(self.h, [self.h], None)
        self.h.zone = 'Dungeon/Room1'
        provenance = {
            'phase': 'confirmed', 'epoch': 6, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        with patch('src.questing.is_free', AsyncMock(return_value=True)), patch(
                'src.questing._movement_interaction_signature',
                AsyncMock(return_value=('Wrong Door', 'Press X to Use'))):
            self.assertEqual(await source._replay_quest_transition(provenance), 'identity-mismatch')
        self.h.send_key.assert_not_awaited()

    async def test_follower_prefers_confirmed_local_transition_before_friend_tp(self):
        await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.q._quest_transition_provenance = {
            'phase': 'confirmed', 'epoch': 1, 'source_zone': 'Dungeon/Room1',
            'target_zone': 'Dungeon/Room2', 'entry_target': XYZ(5, 6, 0),
            'identity': ('Door', 'Press X to Use'), 'deadline': float('inf'),
            'attempts': {}, 'arrived': set(),
        }
        self.h._friend_follow_busy_backoff = {'busy_until': float('inf'), 'last_block': 'busy-backoff'}
        done = asyncio.Event()
        def status(_h, _q, text):
            if text == '已跟随进入当前任务房间':
                done.set()
        async def local_replay(_provenance):
            self.h.zone = 'Dungeon/Room2'
            return 'arrived'
        with patch.object(Quester, '_replay_quest_transition',
                AsyncMock(side_effect=local_replay)) as replay:
            await self.follow_until(done, primary=True, when_status=status)
        replay.assert_awaited_once()
        self.friend.assert_not_awaited()

    async def test_quester_inside_does_not_start_follower_release(self):
        session = await self.enter()
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        def status(_h, _q, text):
            done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        self.h.send_key.assert_not_awaited()
        self.friend.assert_not_awaited()
        self.assertNotIn(id(self.q), session.exited)
        self.assertNotIn(id(self.h), session.exited)

    async def test_loading_or_battle_follower_does_not_start_release(self):
        for blocked in ('loading', 'battle'):
            with self.subTest(blocked=blocked):
                self.setUp()
                session = await self.enter()
                self.q.quest_party_observed_zone = 'Dungeon/Room1'
                self.q.zone = 'Outside'
                local_goal(self.h, 'Outside')
                if blocked == 'loading':
                    self.h.is_loading.return_value = True
                else:
                    self.h.in_battle.return_value = True
                done = asyncio.Event()
                def status(_h, _q, text):
                    done.set()
                with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
                    await self.follow_until(done, primary=True, when_status=status)
                self.h.send_key.assert_not_awaited()
                self.friend.assert_not_awaited()
                self.assertNotIn(id(self.h), session.exited)

    async def test_already_out_follower_does_not_request_release(self):
        session = await self.enter()
        self.q.zone = self.h.zone = 'Outside'
        party.record_dungeon_exit(self.q, 'Dungeon/Room1', 'Outside')
        party.record_dungeon_exit(self.h, 'Dungeon/Room1', 'Outside')
        done = asyncio.Event()
        def status(_h, _q, text):
            done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        self.h.send_key.assert_not_awaited()
        self.friend.assert_not_awaited()
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_retired_session_during_hub_exit_stops_second_input(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.zone = 'Outside'
        local_goal(self.h, 'Outside')
        done = asyncio.Event()
        calls = []
        async def end(key, *_):
            calls.append(key)
            if len(calls) == 1:
                party.retire_dungeon_session(session)
                self.h.zone = 'World/Hub'
        self.h.send_key.side_effect = end
        def status(_h, _q, text):
            if text == '已归队':
                done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        self.assertEqual(calls, [Keycode.END])
        self.assertEqual(session.phase, 'retired')
        self.assertIsNone(party.dungeon_session_for(self.h))

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
