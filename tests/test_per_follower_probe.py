"""One follower's probe/retry cannot consume another follower's turn."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from wizwalker import XYZ
from src.questing import Quester
from src.automation_ownership import automation_owner, get_client_automation_ownership
from loguru import logger
from tests.test_dungeon_local_tp import client, REAL_SLEEP
from tests.test_full_chain_415_restore import entry, codes, APP
import inspect


REAL_PENDING = Quester.handle_pending_dungeon_confirmation


class PerFollowerProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.q, self.p2, self.p3 = [client(i, XYZ(500, 600, 0)) for i in (1, 2, 3)]
        self.q.quest_party_hitters = [self.p2, self.p3]
        self.q.zone, self.q.area_id = 'New/Area', 200
        self.q.quest_party_observed_zone = 'Old/Area'
        self.q.quest_party_probe_pending = True
        self.counts, self.statuses = {id(self.p2): 0, id(self.p3): 0}, []
        self.clock = 0
        self.done = asyncio.Event()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.logs = []
        sink = logger.add(lambda message: self.logs.append(str(message)), level='DEBUG')
        self.addCleanup(logger.remove, sink)
        self.stack.enter_context(patch.object(Quester, 'get_truncated_quest_objectives',
                                             AsyncMock(return_value='Use task switch')))
        self.pending = {id(self.p2): False, id(self.p3): False}
        async def pending(reader):
            return self.pending.get(id(reader.client), False)
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation', pending))

    async def run_pair(self, *, p2_fails=False, already_p2=False, valid_p3_pending=False,
                       p2_blocked=False):
        if already_p2:
            self.p2.zone, self.p2.area_id = self.q.zone, self.q.area_id
        self.pending[id(self.p3)] = valid_p3_pending
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        async def friend(c, **_):
            self.counts[id(c)] += 1
            if c is self.p2 and p2_fails:
                raise ValueError('Could not find friend for p2')
            c.zone, c.area_id = self.q.zone, self.q.area_id
        def status(h, q, text):
            self.statuses.append((h.title, text))
            p2_done = self.counts[id(self.p2)] > 0 or already_p2 or (p2_blocked and any(
                title == self.p2.title and message in ("等待战斗或加载", "等待好友归队输入所有权")
                for title, message in self.statuses))
            p3_done = self.counts[id(self.p3)] > 0 or valid_p3_pending
            if p2_done and p3_done or self.clock > 100:
                self.done.set()
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[self.q, self.p2, self.p3]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False), is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=friend,
            clients_share_live_area=AsyncMock(side_effect=lambda a,b: a.zone==b.zone and a.area_id==b.area_id),
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError,
                TimeoutError=TimeoutError, timeout=asyncio.timeout, current_task=asyncio.current_task)),
            members=[self.q, self.p2, self.p3], change_party_equipment=AsyncMock(return_value=True),
            prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=Mock(side_effect=status))
        tasks = [asyncio.create_task(follow(h, self.q, primary))
                 for h, primary in ((self.p2, True), (self.p3, False))]
        try:
            await asyncio.wait_for(self.done.wait(), 1.0)
            for _ in range(5): await REAL_SLEEP(0)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_both_followers_probe_after_quester_zone_change(self):
        await self.run_pair()
        self.assertEqual(list(self.counts.values()), [1, 1])

    async def test_one_failed_follower_does_not_cancel_other_active_probe(self):
        self.q.quest_party_probe_pending = True
        self.p2._quest_follower_probe = dict(
            quester=self.q, pending=False, terminal='unavailable')
        self.p3._quest_follower_probe = dict(
            quester=self.q, pending=True, terminal=None)
        gate = Quester(self.q, [self.q], None)
        with patch('src.questing.observe_dungeon_session', AsyncMock(return_value=None)):
            self.assertTrue(await gate._quest_party_probe_blocks_movement())
        self.p3._quest_follower_probe['pending'] = False
        self.q.quest_party_probe_pending = False
        with patch('src.questing.observe_dungeon_session', AsyncMock(return_value=None)):
            self.assertFalse(await gate._quest_party_probe_blocks_movement())

    async def test_p2_value_error_does_not_stop_p3_success(self):
        await self.run_pair(p2_fails=True)
        self.assertGreaterEqual(self.counts[id(self.p2)], 1)
        self.assertEqual(self.counts[id(self.p3)], 1)
        self.assertEqual(self.p3.zone, self.q.zone)
        self.assertNotEqual(self.p2.zone, self.q.zone)
        self.assertIsNot(self.p2._quest_follower_probe, self.p3._quest_follower_probe)
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertEqual(self.p2._quest_follower_probe['terminal'], 'unavailable')
        self.assertFalse(self.p3._quest_follower_probe['pending'])
        self.assertGreater(self.p2._quest_follower_probe['retry_at'], 0)
        self.assertEqual(self.p3._quest_follower_probe['retry_at'], 0)
        self.assertFalse(self.q.quest_party_probe_pending,
                         'the last terminal/successful ordinary probe releases movement')

    async def test_already_reunited_p2_is_not_probed_again(self):
        await self.run_pair(already_p2=True)
        self.assertEqual(list(self.counts.values()), [0, 1])

    async def test_valid_p3_pending_only_blocks_p3_own_probe(self):
        await self.run_pair(valid_p3_pending=True)
        self.assertEqual(list(self.counts.values()), [1, 0])
        messages = [s for s in self.logs if 'follower=p3 ' in s and 'own-visible-transition-modal' in s]
        self.assertEqual(len(messages), 1, 'unchanged skip reason must be throttled')
        self.assertIn('pending_transition=True', messages[0])

    async def test_p2_loading_battle_or_owner_does_not_block_p3_probe(self):
        for cause in ('loading', 'battle', 'owner'):
            with self.subTest(cause=cause):
                self.setUp()
                if cause == 'loading': self.p2.is_loading.return_value = True
                if cause == 'battle': self.p2.in_battle.return_value = True
                if cause == 'owner':
                    async with automation_owner(self.p2, 'combat'):
                        await self.run_pair(p2_blocked=True)
                        self.assertTrue(get_client_automation_ownership(self.p2).locked)
                else:
                    await self.run_pair(p2_blocked=True)
                self.assertEqual(list(self.counts.values()), [0, 1])
                self.assertFalse(get_client_automation_ownership(self.p3).locked)
                self.stack.close()

    async def test_disabled_quester_stops_both_followers_without_input(self):
        self.q.questing_status = False
        self.done.set()
        await self.run_pair()
        self.assertEqual(list(self.counts.values()), [0, 0])

    async def test_stale_pending_record_without_visible_modal_does_not_block_p3(self):
        self.p3._quest_pending_dungeon_transition = {'client': self.p2, 'zone': 'Old/Area'}
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation',
                                             REAL_PENDING))
        await self.run_pair()
        self.assertEqual(list(self.counts.values()), [1, 1])

    async def test_personal_completions_preserve_single_primary_equipment_restore(self):
        # Hold the first UI operation while the other follower confirms arrival.
        self.q.quest_party_solo_gear_active = True
        self.p2.zone = self.p3.zone = self.q.zone
        entered, release = asyncio.Event(), asyncio.Event()
        async def restore(*_args):
            entered.set()
            await release.wait()
            return True
        gear = AsyncMock(side_effect=restore)
        code = next(c for c in codes(inspect.unwrap(APP.main).__code__)
                    if c.co_name == 'complete_zone_probe')
        states = [{ 'pending': True } for _ in (self.p2, self.p3)]
        tasks = []
        for hitter, primary, state in zip((self.p2, self.p3), (True, False), states):
            values = dict(hitter=hitter, quester=self.q, is_probe_hitter=primary,
                last_quester_zone=self.q.zone, probe_state=state,
                follower_arrival_confirmed=AsyncMock(return_value=True), log_probe=Mock(),
                change_party_equipment=gear, update_party_status=Mock(),
                restart_quest_worker_after_probe=Mock())
            complete = entry('complete_zone_probe', dict(
                dungeon_session_for=lambda _: None,
                quest_party_members_ready=AsyncMock(return_value=True),
                resume_dungeon_member=Mock()),
                **{name: values[name] for name in code.co_freevars})
            tasks.append(asyncio.create_task(complete(False, None)))
        try:
            await asyncio.wait_for(entered.wait(), .5)
            for _ in range(5): await REAL_SLEEP(0)
            self.assertEqual(gear.await_count, 1)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        self.assertTrue(all(not state['pending'] for state in states))
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertFalse(self.q.quest_party_solo_gear_active)
