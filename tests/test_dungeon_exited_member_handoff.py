"""Exited followers cannot revoke the remaining quester's later movement."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger

from src import quest_party as party
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.questing import Quester
from tests.test_dungeon_party_transition import PartyTransitionFixture, REAL_SLEEP
from tests.test_dungeon_local_tp import client


class ExitedMemberHandoffTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        for name in ('src.mainline_recovery.maybe_recover_mainline',
                     'src.questing.close_npc_quest_menu', 'src.questing.close_automation_popup',
                     'src.questing.is_spiral_door_open', 'src.questing.is_potion_needed'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.caller = Quester(self.q, [self.q], None)
        self.caller._try_deimos_interaction = AsyncMock(return_value=False)
        self.caller._hold_arrived_interaction_rejection = AsyncMock(return_value=False)
        self.caller._search_deimos_interaction_prompt = AsyncMock(return_value=False)
        self.caller._recover_door_approach = AsyncMock(return_value=False)
        self.caller.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.caller.handle_questing_zone_change = AsyncMock()
        self.caller._npc_dialogue_visible = AsyncMock(return_value=False)

    async def exit_hitter(self):
        self.h.zone, self.h.area_id = 'Outside', 100
        await party.observe_dungeon_session(self.h)

    async def test_second_and_third_observations_preserve_later_transition(self):
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.q.quest_position.position.return_value)
        await self.exit_hitter()
        self.assertIsNone(session.transition, 'First exit must invalidate the old group operation')
        self.assertTrue(await party.remember_dungeon_transition(
            self.q, self.q.quest_position.position.return_value))
        later = session.transition
        evidence = session.exit_evidence[id(self.h)]
        for observation in (2, 3):
            await party.observe_dungeon_session(self.h)
            self.assertIs(session.transition, later, f'exited member observation {observation} destroyed later work')
            self.assertEqual(session.exit_evidence[id(self.h)], evidence)
        self.assertEqual(session.phase, 'active')
        self.assertTrue(party.dungeon_member_inside(self.q))

    async def test_all_hitters_exited_quester_uses_ordinary_owner(self):
        session = await self.enter()
        await self.exit_hitter()
        owners = []
        async def navigate(client, target, **kwargs):
            owners.append(get_client_automation_ownership(client).owner_label)
            await client.teleport(target)
            return 'ready'
        self.caller.teleport_to_quest_target = navigate
        self.q.quest_party_probe_pending = True
        await self.caller.auto_quest_solo()
        self.assertEqual(owners, [None], 'Remaining quester must not reacquire shared movement')
        self.assertIsNone(session.transition)
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(session.phase, 'active')
        self.assertFalse(session.quester_out)

    async def test_ordinary_movement_completes_during_repeated_exited_observations(self):
        session = await self.enter()
        await self.exit_hitter()
        entered, finish = asyncio.Event(), asyncio.Event()
        completed = []
        async def navigate(client, target, **kwargs):
            entered.set()
            await finish.wait()  # The navigator has not moved the game client yet.
            await client.teleport(target)
            completed.append(True)
            return 'ready'
        self.caller.teleport_to_quest_target = navigate
        task = asyncio.create_task(self.caller.auto_quest_solo())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            for _ in range(3):
                await party.observe_dungeon_session(self.h)
                await REAL_SLEEP(0)
            finish.set()
            await asyncio.wait_for(task, timeout=2)
            self.assertEqual(completed, [True], 'Follower polling cancelled the next movement')
            self.assertEqual(tuple(self.q.position), (9000, 9000, 0))
            self.assertEqual(session.phase, 'active')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_hitter_still_inside_preserves_shared_movement(self):
        session = await self.enter()
        owners = []
        async def navigate(client, target, **kwargs):
            owners.append(get_client_automation_ownership(client).owner_label)
            await client.teleport(target)
            return 'ready'
        self.caller.teleport_to_quest_target = navigate
        await self.caller.auto_quest_solo()
        self.assertEqual(owners, ['dungeon-shared-target'])
        self.assertIsNotNone(session.transition)
        self.assertEqual(session.exited, set())
        self.assertFalse(get_client_automation_ownership(self.q).locked)

    async def test_one_exited_hitter_does_not_release_remaining_group(self):
        third = client(3, self.h.quest_position.position.return_value)
        third.quest_party_quester = self.q
        third.quest_party_status_session = self.owner
        self.q.quest_party_hitters.append(third)
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h, third], evidence=True)
        for member in session.members:
            member.zone, member.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        await self.exit_hitter()
        owners = []
        async def navigate(moving, target, **kwargs):
            owners.append(get_client_automation_ownership(moving).owner_label)
            await moving.teleport(target)
            return 'ready'
        self.caller.teleport_to_quest_target = navigate
        await self.caller.auto_quest_solo()
        self.assertEqual(owners, ['dungeon-shared-target'])
        self.assertNotIn(id(third), session.exited)

    async def test_shared_operation_logs_loading_and_zone_edges_without_sending_x(self):
        session = await self.enter()
        lines = []
        sink = logger.add(lambda message: lines.append(str(message)), level='DEBUG')
        try:
            async def navigate(moving, target, **kwargs):
                moving.is_loading.return_value = True
                moving.zone = 'Outside'
                await REAL_SLEEP(0)
                return 'changed'
            self.reader.teleport_to_quest_target = navigate
            await self.reader.move_dungeon_member()
        finally:
            logger.remove(sink)
        for event in ('begin', 'tp-call', 'tp-return', 'loading-edge', 'zone-change', 'end'):
            self.assertTrue(any('[dungeon_operation]' in line and f'event={event} ' in line
                                and 'client=p2 ' in line for line in lines), event)
        self.h.send_key.assert_not_awaited()
        self.assertEqual(session.phase, 'active')

    async def test_diagnostic_read_failure_does_not_replace_movement_result(self):
        await self.enter()
        async def navigate(moving, target, **kwargs):
            moving.zone_name.side_effect = RuntimeError('transient zone read failure')
            return 'changed'
        self.reader.teleport_to_quest_target = navigate
        try:
            result = await self.reader.move_dungeon_member()
        except RuntimeError as exc:
            self.fail(f'Diagnostic error escaped instead of preserving the movement result: {exc}')
        self.assertFalse(result)
        self.assertFalse(get_client_automation_ownership(self.h).locked)

    async def test_handoff_waits_for_existing_shared_navigator_cleanup(self):
        session = await self.enter()
        snapshot = await self.caller._confirmed_dungeon_target(session)
        entered, drained = asyncio.Event(), asyncio.Event()
        async def navigate(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await REAL_SLEEP(0)
                drained.set()
        self.caller.teleport_to_quest_target = navigate
        old = asyncio.create_task(self.caller.move_dungeon_member(_quester_snapshot=snapshot))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await self.exit_hitter()
            self.q.quest_party_probe_pending = True
            self.assertFalse(await self.caller._quest_party_probe_blocks_movement())
            self.assertTrue(drained.is_set(), 'Probe release ran before the old navigator drained')
            self.assertFalse(get_client_automation_ownership(self.q).locked)
            self.assertFalse(await asyncio.wait_for(old, timeout=2))
        finally:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)

    async def test_source_recovery_nested_claim_drains_before_handoff(self):
        session = await self.enter()
        await party.remember_dungeon_transition(self.q, self.q.quest_position.position.return_value)
        self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
        entered, drained = asyncio.Event(), asyncio.Event()
        async def navigate(moving, target, **kwargs):
            # Model the input boundary's nested claim used by real door approach.
            async with automation_owner(moving, 'door-approach'):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await REAL_SLEEP(0)
                    drained.set()
        self.caller.teleport_to_quest_target = navigate
        old = asyncio.create_task(self.caller.recover_dungeon_transition())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            ownership = get_client_automation_ownership(self.q)
            self.assertEqual(ownership.owner_label, 'door-approach')
            self.assertIn('dungeon-source-entry', ownership._owner_labels)
            await self.exit_hitter()
            self.q.quest_party_probe_pending = True
            self.assertFalse(await self.caller._quest_party_probe_blocks_movement())
            self.assertTrue(drained.is_set(), 'Nested source recovery outlived probe handoff')
            self.assertFalse(ownership.locked)
            self.assertFalse(await asyncio.wait_for(old, timeout=2))
        finally:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
