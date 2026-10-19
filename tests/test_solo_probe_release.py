"""Freeze real completion/gear side effects before testing the release race."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src import utils
from tests.test_dungeon_local_tp import client, ClientStub, REAL_SLEEP
from tests.test_full_chain_415_restore import entry
from wizwalker import XYZ


class ProbeReleaseFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.q, self.p2, self.p3 = [client(i, XYZ(100, 200, 0)) for i in (1, 2, 3)]
        self.q.quest_party_hitters = [self.p2, self.p3]
        self.q.zone, self.q.area_id = 'Target/Area', 200
        self.q.quest_party_observed_zone = self.q.quest_party_quest_worker_zone = self.q.zone
        self.q.quest_party_probe_pending = True
        self.events = []
        player = SimpleNamespace(object_name=AsyncMock(return_value='Player Object'))
        self.q.get_base_entity_list.return_value = [player]
        self.player = player
        self.tasks = []
        self.frames = {}
        self.controls = {}
        proof = AsyncMock(side_effect=lambda a, b: a.zone == b.zone and a.area_id == b.area_id)
        self.stack.enter_context(patch.object(utils, 'clients_share_live_area', proof))
        self.stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))
        original_set = ClientStub.__setattr__
        def changed(c, name, value):
            if c is self.q:
                if name == 'in_solo_zone': self.events.append(('solo', value))
                if name == 'quest_party_probe_pending' and value is False:
                    self.events.append(('release', None))
            original_set(c, name, value)
        self.stack.enter_context(patch.object(ClientStub, '__setattr__', changed))
        async def gear(c, index):
            self.assertIs(c, self.q)
            self.events.append(('gear-start', index))
            await REAL_SLEEP(0)
            self.events.append(('gear-completed', index))
            return True
        self.gear = gear
        for member, primary in ((self.p2, True), (self.p3, False)):
            parked = asyncio.Event()
            permit = asyncio.Event()
            async def tick(_delay, parked=parked, permit=permit):
                parked.set()
                await permit.wait()
                permit.clear()
            namespace = dict(walker=SimpleNamespace(clients=[self.q, self.p2, self.p3]),
                questing_status=True, gear_switching_in_solo_zones=True, quest_friend_icons={},
                use_potions=False, buy_potions=False, close_automation_popup=AsyncMock(return_value=False),
                is_free=AsyncMock(return_value=True), is_visible_by_path=AsyncMock(return_value=False),
                clients_share_live_area=proof,
                asyncio=SimpleNamespace(sleep=tick, get_running_loop=asyncio.get_running_loop,
                    current_task=asyncio.current_task, timeout=asyncio.timeout,
                    wait_for=asyncio.wait_for, TimeoutError=TimeoutError, CancelledError=asyncio.CancelledError))
            follow = entry('_follow_quester_session', namespace, members=[self.q, self.p2, self.p3],
                change_party_equipment=gear, prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
                restart_quest_worker_after_probe=lambda q: self.events.append(('restart', None)),
                update_party_status=Mock())
            task = asyncio.create_task(follow(member, self.q, primary))
            self.tasks.append(task)
            await asyncio.wait_for(parked.wait(), 2)
            frame = dict(task.get_stack()[0].f_locals)
            original = frame['complete_zone_probe']
            async def observed(*args, _original=original, **kwargs):
                self.events.append(('complete', args[0]))
                return await _original(*args, **kwargs)
            release = frame['release_ordinary_probe_after_solo_check']
            cells = dict(zip(release.__code__.co_freevars, release.__closure__))
            cells['complete_zone_probe'].cell_contents = observed
            frame['complete_zone_probe'] = observed
            self.frames[id(member)] = frame
            self.controls[id(member)] = (parked, permit)
            await frame['refresh_probe_reference']()
        self.events.clear()

    async def asyncTearDown(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def result(self, member, outcome, *, current=False):
        state = member._quest_follower_probe
        if outcome == 'unavailable':
            state.update(pending=current, terminal=None if current else 'unavailable',
                friend_outcome='unavailable', release_pending=current, verified=False)
        elif outcome == 'arrived':
            state.update(pending=False, terminal=None, verified=True, friend_outcome='arrived',
                release_pending=False, completion_reference=state['reference'])
        else:
            state.update(pending=True, terminal=None, verified=False, friend_outcome=None,
                release_pending=False)

    async def release(self, member=None):
        return await self.frames[id(member or self.p2)]['release_ordinary_probe_after_solo_check']()

    async def tick(self, member=None):
        parked, permit = self.controls[id(member or self.p2)]
        parked.clear()
        permit.set()
        await asyncio.wait_for(parked.wait(), 2)

    async def reunite(self):
        for h in (self.p2, self.p3):
            h.zone, h.area_id, h.position = self.q.zone, self.q.area_id, XYZ(*self.q.position)
            await self.frames[id(h)]['refresh_probe_reference']()
        self.q.get_base_entity_list.return_value = [self.player, self.player]


class SoloProbeOracleTests(ProbeReleaseFixture):
    async def test_o1_existing_solo_completion_order_and_once(self):
        self.result(self.p2, 'unavailable', current=True)
        self.result(self.p3, 'unavailable')
        self.assertTrue(await self.release())
        self.assertEqual(self.events, [('complete', True), ('gear-start', 1),
            ('gear-completed', 1), ('solo', True), ('release', None), ('restart', None)])
        self.assertTrue(self.q.in_solo_zone)
        self.assertTrue(self.q.quest_party_solo_gear_active)
        self.assertFalse(self.q.quest_party_probe_pending)

    async def test_o2_existing_multiplayer_completion_keeps_set2_off(self):
        await self.reunite()
        self.result(self.p2, 'pending')
        self.result(self.p3, 'pending')
        self.events.clear()
        self.assertTrue(await self.frames[id(self.p2)]['complete_zone_probe'](False))
        self.assertEqual(self.events, [('complete', False), ('solo', False), ('release', None)])
        self.assertFalse(self.q.in_solo_zone)

    async def test_o3_existing_multiplayer_rejoin_restores_set0(self):
        await self.reunite()
        self.q.in_solo_zone = self.q.quest_party_solo_gear_active = True
        self.events.clear()
        self.assertTrue(await self.frames[id(self.p2)]['complete_zone_probe'](False))
        self.assertEqual(self.events, [('complete', False), ('gear-start', 0),
            ('gear-completed', 0), ('solo', False), ('release', None)])
        self.assertFalse(self.q.quest_party_solo_gear_active)


class SoloProbeReleaseTests(ProbeReleaseFixture):
    async def test_r1_terminal_cannot_ignore_expected_peer_not_initialized_yet(self):
        self.result(self.p2, 'unavailable')
        del self.p3._quest_follower_probe
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)
        self.assertNotIn(('complete', True), self.events)

    async def test_pending_peer_still_blocks_terminal_release(self):
        self.result(self.p2, 'unavailable')
        self.result(self.p3, 'pending')
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)

    async def test_r2_peer_cleanup_still_owns_its_result(self):
        self.result(self.p2, 'unavailable')
        self.result(self.p3, 'unavailable')
        self.p3._friend_follow_failure = dict(state='cleanup-active', generation=1, token='test-token')
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)
        self.assertNotIn(('gear-start', 1), self.events)

    async def test_r3_all_terminal_results_reenter_existing_completion_not_direct_clear(self):
        self.result(self.p2, 'unavailable')
        self.result(self.p3, 'unavailable')
        await self.tick()
        self.assertEqual(self.events, [('complete', True), ('gear-start', 1),
            ('gear-completed', 1), ('solo', True), ('release', None), ('restart', None)])

    async def test_r4_current_arrived_peer_allows_multiplayer_without_set2(self):
        self.p3.zone, self.p3.area_id = self.q.zone, self.q.area_id
        await self.frames[id(self.p3)]['refresh_probe_reference']()
        self.q.get_base_entity_list.return_value = [self.player, self.player]
        self.result(self.p2, 'unavailable')
        self.result(self.p3, 'arrived')
        await self.tick()
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertFalse(self.q.in_solo_zone)
        self.assertNotIn(('gear-start', 1), self.events)

    async def test_r5_old_reference_unavailable_does_not_complete_current_probe(self):
        self.result(self.p3, 'unavailable')
        self.q._party_area_generation = 1
        await self.frames[id(self.p2)]['refresh_probe_reference']()
        self.result(self.p2, 'unavailable')
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)

    async def test_r6_next_zone_cannot_inherit_previous_solo_aggregate(self):
        self.result(self.p2, 'unavailable', current=True)
        self.result(self.p3, 'unavailable')
        self.assertTrue(await self.release())
        self.q.zone, self.q.area_id = 'Target/Next', 201
        self.q.quest_party_probe_pending = True
        await self.frames[id(self.p2)]['refresh_probe_reference']()
        self.result(self.p2, 'unavailable')
        self.events.clear()
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)

    async def test_r7_repeated_terminal_tick_does_not_repeat_completion_or_gear(self):
        self.result(self.p2, 'unavailable', current=True)
        self.result(self.p3, 'unavailable')
        self.assertTrue(await self.release())
        self.events.clear()
        await self.tick()
        self.assertFalse(await self.release())
        self.assertNotIn(('complete', True), self.events)
        self.assertNotIn(('gear-start', 1), self.events)

    async def test_concurrent_terminals_use_only_existing_primary_completion(self):
        self.result(self.p2, 'unavailable')
        self.result(self.p3, 'unavailable')
        await asyncio.gather(self.tick(self.p2), self.tick(self.p3))
        self.assertEqual(self.events.count(('complete', True)), 1)
        self.assertEqual(self.events.count(('gear-completed', 1)), 1)
        self.assertEqual(self.events.count(('release', None)), 1)
        self.assertEqual(self.events.count(('restart', None)), 1)

    async def test_unknown_result_is_not_unavailable(self):
        self.result(self.p2, 'unavailable')
        self.p3._quest_follower_probe.update(pending=False, terminal=None, friend_outcome=None)
        await self.tick()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertNotIn(('release', None), self.events)

    async def test_arrived_peer_does_not_relabel_unknown_primary_as_unavailable(self):
        self.p3.zone, self.p3.area_id = self.q.zone, self.q.area_id
        await self.frames[id(self.p3)]['refresh_probe_reference']()
        self.q.get_base_entity_list.return_value = [self.player, self.player]
        self.result(self.p2, 'pending')
        self.p2._quest_follower_probe['release_pending'] = True
        self.result(self.p3, 'arrived')
        self.assertTrue(await self.release())
        self.assertTrue(self.p2._quest_follower_probe['pending'])
        self.assertIsNone(self.p2._quest_follower_probe['terminal'])
        self.assertIsNone(self.p2._quest_follower_probe['friend_outcome'])
        self.assertNotIn(('gear-start', 1), self.events)

    async def test_unknown_with_unavailable_peer_returns_to_existing_retry_scheduler(self):
        self.result(self.p2, 'pending')
        self.p2._quest_follower_probe['release_pending'] = True
        self.result(self.p3, 'unavailable')
        await self.tick()
        self.assertFalse(self.p2._quest_follower_probe['release_pending'])
        self.assertTrue(self.p2._quest_follower_probe['pending'])
        self.assertIsNone(self.p2._quest_follower_probe['friend_outcome'])
        self.assertIsNone(self.p2._quest_follower_probe['terminal'])
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.events, [])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([loader.loadTestsFromTestCase(SoloProbeOracleTests),
                              loader.loadTestsFromTestCase(SoloProbeReleaseTests)])
