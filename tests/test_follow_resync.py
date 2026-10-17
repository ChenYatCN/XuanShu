"""Fresh follow preflight retires a stale zone mismatch before friend input."""
import asyncio
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from loguru import logger
from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from tests.test_per_follower_probe import PerFollowerProbeTests as Fixture
from tests.test_dungeon_local_tp import REAL_SLEEP
from tests.test_full_chain_415_restore import entry


class FollowResyncTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp

    async def scenario(self, *, aligned=True, far=False, change=None, same_instance=True, change_at=1):
        self.q.zone = 'World/Z05'
        self.q.quest_party_observed_zone = self.q.zone
        self.p2.zone = 'World/Z04'
        self.q.position = XYZ(5000 if far else 0, 0, 0)
        self.session = None
        self.changed = False
        self.local_inputs = []
        self.friend_inputs = []
        self.cancel_states = []
        self.body_reads = 0
        self.resync_reads = 0
        for index, member in enumerate((self.q, self.p2)):
            member.body.read_base_address = AsyncMock(return_value=1000 + index)
        original_body = self.p2.body
        direct_zone_reads = 0

        async def follower_zone():
            nonlocal direct_zone_reads
            caller = next(frame.function for frame in inspect.stack(context=0)
                          if frame.filename.endswith('XuanShu.py'))
            if caller == '_follow_quester_session':
                direct_zone_reads += 1
                if direct_zone_reads == 1 and aligned:
                    self.p2.zone, self.p2.area_id = self.q.zone, self.q.area_id
                    return 'World/Z04'  # The stale observation which selected friend-follow.
            return self.p2.zone
        self.p2.zone_name.side_effect = follower_zone

        async def position():
            self.body_reads += 1
            if any(f.function == 'fresh_follow_state' for f in inspect.stack(context=0)):
                self.resync_reads += 1
            if change and not change.startswith('final-') and not self.changed and self.resync_reads == change_at:
                self.changed = True
                if change == 'zone': self.p2.zone = 'World/Z06'
                if change == 'token': self.p2.area_id += 1
                if change == 'session':
                    self.session = SimpleNamespace(transition={}, confirmed=False,
                        entry_evidence=False, potion_areas={}, follow_attempts={})
                if change == 'body':
                    self.p2.body = SimpleNamespace(position=AsyncMock(return_value=self.p2.position),
                        read_base_address=AsyncMock(return_value=9876))
                if change == 'address': original_body.read_base_address.return_value = 9876
                if change == 'loading': self.p2.is_loading.return_value = True
                if change == 'stop': self.p2.questing_status = False
                if change == 'cancel': raise asyncio.CancelledError()
                if change == 'nonfinite': return XYZ(float('nan'), 0, 0)
            return self.p2.position
        original_body.position.side_effect = position
        if change and change.startswith('final-'):
            async def address():
                if not self.changed and self.resync_reads == change_at:
                    self.changed = True
                    if change == 'final-zone': self.p2.zone = 'World/Z06'
                    if change == 'final-loading': self.p2.is_loading.return_value = True
                    if change == 'final-token': self.p2.area_id += 1
                    if change == 'final-body':
                        self.p2.body = SimpleNamespace(position=AsyncMock(return_value=self.p2.position),
                            read_base_address=AsyncMock(return_value=9876))
                return 1001
            original_body.read_base_address.side_effect = address

        async def teleport(point):
            self.local_inputs.append((self.q.zone, self.p2.zone, tuple(point)))
            self.p2.position = point
        self.p2.teleport.side_effect = teleport

        async def friend(member, **_):
            self.friend_inputs.append((self.q.zone, self.p2.zone))
            member.zone, member.area_id, member.position = self.q.zone, self.q.area_id, self.q.position

        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
            if self.clock > 30: self.done.set()

        def record(message):
            line = str(message)
            if 'decision=cancel' in line:
                self.cancel_states.append((line, dict(self.p2._quest_follower_probe)))
                if 'stale-resync-reference' in line:
                    self.p2.questing_status = False  # Inspect this cancellation before a new loop requalifies.
                    self.done.set()
            if 'decision=complete' in line: self.done.set()
        sink = logger.add(record, level='DEBUG')
        self.addCleanup(logger.remove, sink)
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[self.q, self.p2]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False), is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=friend,
            clients_share_live_area=AsyncMock(side_effect=lambda a,b:
                a.zone == b.zone and (same_instance or bool(self.friend_inputs))),
            dungeon_session_for=lambda _: self.session,
            observe_dungeon_session=AsyncMock(side_effect=lambda _: self.session),
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda:self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError,
                TimeoutError=TimeoutError, timeout=asyncio.timeout, current_task=asyncio.current_task)),
            members=[self.q, self.p2], change_party_equipment=AsyncMock(return_value=True),
            prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=Mock())
        task = asyncio.create_task(follow(self.p2, self.q, True))
        waiter = asyncio.create_task(self.done.wait())
        try:
            completed, _ = await asyncio.wait([task, waiter], timeout=2,
                return_when=asyncio.FIRST_COMPLETED)
            self.assertTrue(completed, 'follow did not reach an observable decision')
            if task in completed and not task.cancelled(): task.result()
        finally:
            for pending in (task, waiter): pending.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)

    async def test_stale_z04_then_fresh_z05_near_confirms_without_friend_or_noop_tp(self):
        await self.scenario()
        self.assertEqual(self.friend_inputs, [])
        self.assertEqual(self.local_inputs, [])
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(self.cancel_states, 'a stable fresh reference replaces the initial stale mismatch')
        self.assertFalse(any('stale-probe-reference' in s for s in self.logs))
        self.assertTrue(any('own-live-area-confirmed' in s for s in self.logs))

    async def test_fresh_same_instance_far_uses_local_alignment(self):
        await self.scenario(far=True)
        self.assertEqual(self.friend_inputs, [])
        self.assertEqual(self.local_inputs, [('World/Z05', 'World/Z05', (5000, 0, 0))])
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(self.cancel_states)

    async def test_real_cross_zone_uses_friend_and_never_local_xyz(self):
        await self.scenario(aligned=False)
        self.assertEqual(self.friend_inputs, [('World/Z05', 'World/Z04')])
        self.assertEqual(self.local_inputs, [])

    async def test_equal_zone_without_live_instance_proof_keeps_friend_pipeline(self):
        await self.scenario(same_instance=False)
        self.assertEqual(self.friend_inputs, [('World/Z05', 'World/Z05')])
        self.assertEqual(self.local_inputs, [])

    async def test_resync_changes_cancel_without_input_failure_backoff_or_owner_leak(self):
        for change in ('zone', 'token', 'session', 'body', 'address', 'loading', 'stop', 'nonfinite'):
            with self.subTest(change=change):
                self.setUp()
                await self.scenario(change=change)
                self.assertTrue(self.changed)
                self.assertEqual(self.friend_inputs, [])
                self.assertEqual(self.local_inputs, [])
                self.assertTrue(self.cancel_states)
                line, state = self.cancel_states[0]
                self.assertIn('stale-resync-reference', line)
                self.assertEqual(state.get('failures', 0), 0)
                self.assertEqual(state.get('retry_at', 0), 0)
                self.assertTrue(state['pending'])
                self.assertFalse(state.get('verified', False))
                self.assertFalse(get_client_automation_ownership(self.p2).locked)
                self.stack.close()

    async def test_user_cancellation_during_resync_propagates_without_friend_input(self):
        await self.scenario(change='cancel')
        self.assertTrue(self.changed)
        self.assertEqual(self.friend_inputs, [])
        self.assertEqual(self.local_inputs, [])
        self.assertFalse(get_client_automation_ownership(self.p2).locked)

    async def test_late_body_or_zone_change_before_local_tp_is_cancelled(self):
        for change in ('zone', 'body', 'address', 'loading'):
            with self.subTest(change=change):
                self.setUp()
                await self.scenario(far=True, change=change, change_at=3)
                self.assertTrue(self.changed)
                self.assertEqual(self.friend_inputs, [])
                self.assertEqual(self.local_inputs, [])
                self.assertTrue(self.p2._quest_follower_probe['pending'])
                self.assertFalse(self.p2._quest_follower_probe.get('verified', False))
                self.assertTrue(any('stale-resync-reference' in s for s in self.logs))
                self.stack.close()

    async def test_final_address_await_cannot_allow_cross_zone_or_unsafe_xyz(self):
        for change in ('final-zone', 'final-loading', 'final-token', 'final-body'):
            with self.subTest(change=change):
                self.setUp()
                await self.scenario(far=True, change=change, change_at=3)
                self.assertTrue(self.changed)
                self.assertEqual(self.local_inputs, [])
                self.assertEqual(self.friend_inputs, [])
                self.assertTrue(any('stale-resync-reference' in s for s in self.logs))
                self.stack.close()


del Fixture
