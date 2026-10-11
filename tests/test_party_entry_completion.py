"""Retire only a proven party entrance; allow genuinely new task/group/target contexts."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import questing as q
from src.automation_ownership import get_client_automation_ownership


class PartyEntryCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(100, 200, 0)
        def client(title):
            return SimpleNamespace(title=title, questing_status=True, quest_recovery_owner=None,
                refilling_potions=False, post_combat_cleanup_active=False, potion_dungeon_returned=None,
                quest_party_status_session=object(),
                quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
                zone_name=AsyncMock(return_value='World/Entrance'),
                body=SimpleNamespace(position=AsyncMock(return_value=self.target)),
                quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)),
                is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
                send_key=AsyncMock())
        self.leader, self.peer = client('p1'), client('p2')
        self.leader.quest_party_hitters = [self.peer]
        self.peer.quest_party_quester = self.leader
        self.leader.quest_party_group_dungeon_zone = 'World/Entrance'
        self.leader.quest_party_quest_worker_task = object()
        self.state = {'zone': 'World/Entrance', 'attempts': {}}
        self.shared = {'zone': 'World/Entrance', 'xyz': self.target}
        self.leader.quest_party_dungeon_interaction = self.state
        self.leader.quest_party_shared_target = self.shared
        self.runner = q.Quester(self.leader, [self.leader, self.peer], None)
        self.runner._mainline_identity = AsyncMock(return_value=None)
        self.runner._confirm_dungeon_entry = AsyncMock()
        self.runner._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Enter task room'))
        self.runner._maybe_refresh_stalled_dungeon_quest = AsyncMock(return_value=False)
        self.runner.party_dungeon_entry_visible = AsyncMock(return_value=False)
        self.runner.quest_interaction_ready = AsyncMock(return_value=True)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.proof = stack.enter_context(patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(q, 'is_free', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(return_value=True)))
        stack.enter_context(patch.object(q, 'get_popup_title', AsyncMock(return_value='Entrance')))
        for member in (self.leader, self.peer):
            def enter(*_, member=member):
                member.zone_name.return_value = 'Dungeon/RoomA'
            member.send_key.side_effect = enter

    async def enter(self):
        return await self.runner.enter_party_dungeon([self.leader, self.peer], self.leader)

    async def test_real_entry_completion_clears_exact_old_context_and_source_failures(self):
        failure = {'signature': ((42, 7, 'World/Entrance'),)}
        other_failure = {'signature': ((42, 7, 'Other/Room'),)}
        self.runner._quest_movement_waiting.update({id(self.leader): failure, id(self.peer): failure, 123: other_failure})
        self.assertTrue(await self.enter())
        self.assertIsNone(self.leader.quest_party_dungeon_interaction)
        self.assertIsNone(self.leader.quest_party_shared_target)
        self.assertEqual(self.runner._quest_movement_waiting, {123: other_failure})
        completed = self.leader._quest_party_entry_completed
        self.assertEqual(completed['identity'], (42, 7))
        self.assertEqual((completed['from_zone'], completed['to_zone']), ('World/Entrance', 'Dungeon/RoomA'))
        self.assertFalse(self.leader.quest_party_probe_pending)
        self.assertEqual(self.leader.quest_party_quest_worker_zone, 'Dungeon/RoomA')
        for member in (self.leader, self.peer):
            self.assertFalse(get_client_automation_ownership(member).locked)
            self.assertFalse(member.quest_party_target_sync_active)

    async def test_completed_same_entrance_does_not_send_x_again(self):
        await self.enter()
        for member in (self.leader, self.peer):
            member.zone_name.return_value = 'World/Entrance'
            member.send_key.reset_mock()
        self.assertEqual(await self.runner.prepare_party_dungeon_entry(self.leader), [])
        self.assertFalse(await self.enter())
        self.leader.send_key.assert_not_awaited()
        self.peer.send_key.assert_not_awaited()
        self.runner._maybe_refresh_stalled_dungeon_quest.assert_awaited()

    async def test_goal_group_or_real_target_change_allows_new_entrance(self):
        await self.enter()
        baseline = self.leader._quest_party_entry_completed
        self.leader.zone_name.return_value = 'World/Entrance'
        for change in ('goal', 'session', 'target'):
            with self.subTest(change=change):
                self.leader._quest_party_entry_completed = dict(baseline)
                self.leader.goal_id.return_value = 7
                session = self.peer.quest_party_status_session
                self.leader.quest_position.position.return_value = self.target
                if change == 'goal':
                    self.leader.goal_id.return_value = 8
                elif change == 'session':
                    self.peer.quest_party_status_session = object()
                else:
                    self.leader.quest_position.position.return_value = XYZ(500, 200, 0)
                self.assertFalse(await self.runner._completed_party_entry_blocks(self.leader))
                if change == 'target':
                    self.assertIsNotNone(self.leader._quest_party_entry_completed)
                else:
                    self.assertIsNone(self.leader._quest_party_entry_completed)
                self.peer.quest_party_status_session = session

    async def test_reverse_transition_cannot_overwrite_completed_original_entrance(self):
        await self.enter()
        other_target = XYZ(500, 200, 0)
        await self.runner._complete_party_entry(self.leader, 'Dungeon/RoomA', 'World/Entrance',
            (42, 7), other_target, [self.leader, self.peer])
        self.assertEqual(len(self.leader._quest_party_entry_completed['entrances']), 2)
        self.leader.zone_name.return_value = 'World/Entrance'
        self.assertTrue(await self.runner._completed_party_entry_blocks(self.leader, self.target))
        self.leader.zone_name.return_value = 'Dungeon/RoomA'
        self.assertTrue(await self.runner._completed_party_entry_blocks(self.leader, other_target))

    async def test_real_progress_on_same_goal_retires_completed_entrances(self):
        await self.enter()
        self.leader.zone_name.return_value = 'World/Entrance'
        self.runner._dungeon_quest_snapshot.return_value = (42, 7, 'Enter task room (1/2)')
        self.assertFalse(await self.runner._completed_party_entry_blocks(self.leader))
        self.assertIsNone(self.leader._quest_party_entry_completed)

    async def test_another_source_room_is_not_blocked_by_previous_completed_entry(self):
        await self.enter()
        self.leader.zone_name.return_value = 'Dungeon/RoomA'
        self.assertFalse(await self.runner._completed_party_entry_blocks(self.leader))
        self.assertIsNotNone(self.leader._quest_party_entry_completed)

    async def test_missing_live_proof_before_x_cannot_mark_completed(self):
        self.proof.return_value = False
        self.assertFalse(await self.enter())
        self.assertIsNone(getattr(self.leader, '_quest_party_entry_completed', None))
        self.leader.send_key.assert_not_awaited()
        self.assertIs(self.leader.quest_party_shared_target, self.shared)

    async def test_one_member_transition_without_destination_proof_remains_pending(self):
        self.peer.send_key.side_effect = lambda *_: setattr(self.peer.zone_name, 'return_value', 'Dungeon/Other')
        self.assertTrue(await self.enter())
        self.assertTrue(self.leader.quest_party_probe_pending)
        self.assertIsNone(getattr(self.leader, '_quest_party_entry_completed', None))
        self.assertIs(self.leader.quest_party_shared_target, self.shared)

    async def test_cleanup_preserves_newer_interaction_and_potion_provenance(self):
        current = {'zone': 'Dungeon/RoomA'}
        self.leader.quest_party_dungeon_interaction = current
        self.leader.quest_party_shared_target = current
        provenance = {'entry_zone': 'World/Entrance', 'entered': True}
        self.leader.quest_dungeon_recovery = provenance
        await self.runner._complete_party_entry(self.leader, 'World/Entrance', 'Dungeon/RoomA',
            (42, 7), self.target, [self.leader, self.peer], self.state, self.shared)
        self.assertIs(self.leader.quest_party_dungeon_interaction, current)
        self.assertIs(self.leader.quest_party_shared_target, current)
        self.assertIs(self.leader.quest_dungeon_recovery, provenance)

    async def test_cancel_entry_releases_only_its_inputs_and_never_completes(self):
        started = asyncio.Event()
        async def wait(*_):
            started.set()
            await asyncio.Future()
        self.leader.send_key.side_effect = wait
        task = asyncio.create_task(self.enter())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(getattr(self.leader, '_quest_party_entry_completed', None))
        for member in (self.leader, self.peer):
            self.assertFalse(get_client_automation_ownership(member).locked)
            self.assertFalse(member.quest_party_target_sync_active)

    async def test_standalone_worker_stop_cleans_its_completed_entry(self):
        del self.leader.quest_party_quest_worker_task
        await self.runner._complete_party_entry(self.leader, 'World/Entrance', 'Dungeon/RoomA',
            (42, 7), self.target, [self.leader, self.peer], self.state, self.shared)
        self.leader.questing_status = False
        await self.runner.auto_quest(False, False)
        self.assertIsNone(self.leader._quest_party_entry_completed)
