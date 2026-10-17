"""Slower packaged scheduling cannot publish old arrival evidence or fake TP failures."""
import asyncio
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from loguru import logger
from src import utils
from src.automation_ownership import get_client_automation_ownership
from tests.test_per_follower_probe import PerFollowerProbeTests as Fixture
from tests.test_dungeon_local_tp import REAL_SLEEP
from tests.test_full_chain_415_restore import entry


class FollowerZoneRaceTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp

    async def scenario(self, *, change=None, verified_then_loading=False, real_failure=False, pair=False,
                       during_verification=False, final_session_race=False, input_session_race=False):
        self.cancel_states, self.request_times = [], []
        self.proof_checkpoints=[]
        self.cancel_request_counts, self.started_guard = [],False
        self.changed = False
        if verified_then_loading or during_verification or final_session_race:
            self.p2.zone, self.p2.area_id = self.q.zone, self.q.area_id
        self.proximity_read = False
        if final_session_race:
            async def position():
                self.proximity_read = True
                return self.q.position
            self.q.body.position.side_effect = position
        if during_verification:
            async def position():
                if not self.changed:
                    self.changed = True
                    self.q.zone, self.q.area_id = 'Newer/Area',300
                return self.q.position
            self.q.body.position.side_effect = position
        self.q.quest_party_observed_zone = self.q.zone
        async def tick(seconds):
            self.clock += seconds
            await REAL_SLEEP(0)
        async def scheduled(awaitable, timeout):
            # Simulate the scheduling gap between collecting the probe context
            # and the child task obtaining input ownership in the packaged app.
            if (getattr(getattr(awaitable, 'cr_code', None), 'co_name', '') == 'attempt_probe'
                    and not self.changed and change in ('quester','follower','source','session')):
                request_client = awaitable.cr_frame.f_locals['request_client']
                if request_client is self.p2:
                    self.changed = True
                    if change == 'quester':
                        self.q.zone, self.q.area_id = 'Newer/Area', 300
                    elif change == 'follower':
                        self.p2.zone, self.p2.area_id = 'Changed/Source', 400
                    elif change == 'source':
                        self.session.transition = {}
                    elif change == 'session':
                        self.session = SimpleNamespace(transition={},confirmed=False,entry_evidence=False,
                            potion_areas={},follow_attempts={})
                    await REAL_SLEEP(0)
            return await asyncio.wait_for(awaitable, timeout)
        async def friend(client, **_):
            self.counts[id(client)] += 1
            self.request_times.append((client.title,self.clock))
            if real_failure:
                if real_failure=='read-gap' and self.counts[id(client)]==2: self.done.set()
                if real_failure == 'timeout': return False
                if real_failure in ('ui','click','classification-read'):
                    raise utils.FriendLookupError('friend-ui-not-ready' if real_failure=='ui' else 'friend-tp-click-failed')
                raise utils.FriendLookupError('quester-not-found',candidate_count=0)
            client.zone, client.area_id = self.q.zone, self.q.area_id
        def status(h, _q, text):
            if final_session_race and self.changed and text=='已归队': self.done.set()
            if verified_then_loading and h is self.p2:
                if not self.changed and text == '已归队':
                    self.changed = True
                    self.q.zone, self.q.area_id = 'Newer/Area',300
                    self.q.is_loading.return_value = True
                elif self.changed and text == '等待切换世界':
                    self.done.set()
            active = (self.p2,self.p3) if pair else (self.p2,)
            if not verified_then_loading and not real_failure and all(
                    self.counts[id(c)] and not c._quest_follower_probe['pending'] for c in active):
                self.done.set()
            if self.clock > 100:
                self.done.set()
        def record(message):
            line = str(message)
            if 'decision=start' in line: self.started_guard=True
            if during_verification and 'reason=stale-probe-reference' in line:
                self.done.set()
            if 'decision=cancel' in line and 'stale-probe' in line:
                self.cancel_states.append((line,dict(self.p2._quest_follower_probe),
                    get_client_automation_ownership(self.p2).locked))
                self.cancel_request_counts.append(self.counts[id(self.p2)])
            if final_session_race and self.changed and 'reason=stale-probe-reference' in line:
                self.proof_checkpoints.append(dict(self.p2._quest_follower_probe))
                self.done.set()
            if real_failure and real_failure!='read-gap' and 'decision=retry' in line:
                self.done.set()
        sink = logger.add(record,level='DEBUG');self.addCleanup(logger.remove,sink)
        namespace = dict(walker=SimpleNamespace(clients=[self.q,self.p2,self.p3]),questing_status=True,
            gear_switching_in_solo_zones=False,quest_friend_icons={},use_potions=False,buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False),is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False),is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=friend,
            clients_share_live_area=AsyncMock(side_effect=lambda a,b:a.zone==b.zone and a.area_id==b.area_id),
            asyncio=SimpleNamespace(sleep=tick,get_running_loop=lambda:SimpleNamespace(time=lambda:self.clock),
                wait_for=scheduled,CancelledError=asyncio.CancelledError,TimeoutError=TimeoutError,
                timeout=asyncio.timeout,current_task=asyncio.current_task))
        if change in ('source','session') or final_session_race or input_session_race:
            self.session = SimpleNamespace(transition={},confirmed=False,entry_evidence=False,
                potion_areas={},follow_attempts={})
            namespace.update(dungeon_session_for=lambda _:self.session,
                observe_dungeon_session=AsyncMock(side_effect=lambda _:self.session))
        if real_failure=='classification-read':
            self.read_gap=False
            async def quest_id():
                if (self.counts[id(self.p2)] and not self.read_gap
                        and any(frame.function=='reference_current' for frame in inspect.stack(context=0))):
                    self.read_gap=True
                    raise ValueError('temporary second classification read failure')
                return self.q.quest_id_value
            self.q.quest_id.side_effect=quest_id
        if real_failure=='read-gap' or final_session_race or input_session_race:
            self.read_gap = False
            async def token(client):
                if (real_failure=='read-gap' and client is self.p2 and not self.read_gap
                        and self.p2._quest_follower_probe.get('failures',0)):
                    self.read_gap = True
                    raise ValueError('temporary area read failure')
                if (final_session_race and self.proximity_read and not self.changed
                        and inspect.currentframe().f_back.f_code.co_name=='refresh_probe_reference'):
                    self.changed=True
                    self.session = SimpleNamespace(transition={},confirmed=False,entry_evidence=False,
                        potion_areas={},follow_attempts={})
                if (input_session_race and self.started_guard and not self.changed and client is self.q
                        and inspect.currentframe().f_back.f_code.co_name=='reference_current'):
                    self.changed=True
                    self.session = SimpleNamespace(transition={},confirmed=False,entry_evidence=False,
                        potion_areas={},follow_attempts={})
                return await utils._party_area_token(client)
            namespace['_party_area_token']=token
        if change == 'cleanup':
            async def window(_root,_path):
                if not self.changed:
                    self.changed = True
                    self.q.zone,self.q.area_id = 'Newer/Area',300
                    raise RuntimeError('cleanup context changed before request')
                return None
            namespace['get_window_from_path'] = window
        follow = entry('_follow_quester_session',namespace,members=[self.q,self.p2,self.p3],
            change_party_equipment=AsyncMock(return_value=True),prepare_quester_name=AsyncMock(),
            remove_party_status=Mock(),restart_quest_worker_after_probe=Mock(),
            update_party_status=Mock(side_effect=status))
        active=(self.p2,self.p3) if pair else (self.p2,)
        tasks=[asyncio.create_task(follow(h,self.q,h is self.p2)) for h in active]
        waiter=asyncio.create_task(self.done.wait())
        try:
            completed,_=await asyncio.wait([waiter,*tasks],timeout=1,return_when=asyncio.FIRST_COMPLETED)
            self.assertTrue(completed,'follower did not reach the test checkpoint')
        finally:
            for task in [waiter,*tasks]: task.cancel()
            await asyncio.gather(waiter,*tasks,return_exceptions=True)

    async def test_verified_arrival_is_invalidated_before_loading_skip_diagnostic(self):
        await self.scenario(verified_then_loading=True)
        line=next(s for s in reversed(self.logs) if 'reason=quester-loading' in s)
        self.assertIn('quester_zone=Newer/Area',line)
        self.assertIn('same_zone=False',line)
        self.assertIn('arrival_evidence=unavailable',line)
        self.assertIn('same_instance=unavailable',line)
        self.assertIn('distance_to_quester=unavailable',line)
        self.assertFalse(self.p2._quest_follower_probe.get('verified',True))
        self.assertTrue(self.p2._quest_follower_probe['pending'])

    async def test_reference_change_before_request_is_cancelled_without_failure_or_backoff(self):
        await self.scenario(change='quester')
        self.assertEqual(len(self.cancel_states),1)
        _,state,locked=self.cancel_states[0]
        self.assertEqual(state.get('failures',0),0)
        self.assertEqual(state.get('retry_at',0),0)
        self.assertFalse(locked)
        self.assertFalse(any('own-friend-request-failed' in s for s in self.logs))
        self.assertEqual(self.counts[id(self.p2)],1)
        self.assertFalse(self.p2._quest_follower_probe['pending'])

    async def test_real_friend_failure_preserves_existing_retry(self):
        await self.scenario(real_failure=True)
        self.assertEqual(self.p2._quest_follower_probe['failures'],1)
        self.assertGreater(self.p2._quest_follower_probe['retry_at'],self.clock)
        self.assertEqual(self.counts[id(self.p2)],1)
        self.assertFalse(self.cancel_states)

    async def test_packaged_scheduling_cancels_old_source_and_new_probe_succeeds(self):
        await self.scenario(change='source')
        self.assertEqual(len(self.cancel_states),1)
        self.assertEqual(self.counts[id(self.p2)],1)
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(any('own-friend-request-failed' in s for s in self.logs))

    async def test_p2_source_zone_cancellation_does_not_block_p3(self):
        await self.scenario(change='follower',pair=True)
        self.assertEqual(len(self.cancel_states),1)
        self.assertEqual(list(self.counts.values()),[1,1])
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(self.p3._quest_follower_probe['pending'])
        self.assertFalse(any('follower=p3 ' in line for line,_,_ in self.cancel_states))

    async def test_session_replacement_cancels_only_its_old_probe(self):
        await self.scenario(change='session')
        self.assertEqual(len(self.cancel_states),1)
        self.assertEqual(self.counts[id(self.p2)],1)
        self.assertFalse(self.p2._quest_follower_probe['pending'])

    async def test_zone_changes_during_proximity_reads_cannot_publish_old_complete(self):
        await self.scenario(during_verification=True)
        self.assertFalse(self.p2._quest_follower_probe.get('verified',True))
        self.assertTrue(self.p2._quest_follower_probe['pending'])
        line=next(s for s in reversed(self.logs) if 'reason=stale-probe-reference' in s)
        self.assertIn('same_zone=False',line)
        self.assertIn('arrival_evidence=unavailable',line)
        self.assertNotIn('same_instance=True',line)
        self.assertEqual(self.counts[id(self.p2)],0)

    async def test_reference_change_during_ui_cleanup_is_stale_not_friend_failure(self):
        await self.scenario(change='cleanup')
        self.assertEqual(len(self.cancel_states),1)
        self.assertEqual(self.counts[id(self.p2)],1)
        self.assertFalse(any('own-friend-request-failed' in s for s in self.logs))

    async def test_real_ui_failure_keeps_retry(self):
        await self.scenario(real_failure='ui')
        self.assertEqual(self.p2._quest_follower_probe['failures'],1)
        self.assertGreater(self.p2._quest_follower_probe['retry_at'],self.clock)

    async def test_real_click_failure_keeps_retry(self):
        await self.scenario(real_failure='click')
        self.assertEqual(self.p2._quest_follower_probe['failures'],1)
        self.assertGreater(self.p2._quest_follower_probe['retry_at'],self.clock)

    async def test_real_arrival_timeout_keeps_retry(self):
        await self.scenario(real_failure='timeout')
        self.assertEqual(self.p2._quest_follower_probe['failures'],1)
        self.assertGreater(self.p2._quest_follower_probe['retry_at'],self.clock)

    async def test_transient_area_read_does_not_erase_real_failure_backoff(self):
        await self.scenario(real_failure='read-gap')
        self.assertTrue(self.read_gap)
        self.assertEqual(len(self.request_times),2)
        self.assertGreaterEqual(self.request_times[1][1]-self.request_times[0][1],2)

    async def test_session_swap_during_final_token_read_cannot_publish_verified(self):
        await self.scenario(final_session_race=True)
        self.assertTrue(self.changed)
        # A later loop may legitimately requalify the new session; inspect the
        # old-reference cancellation atomically at its diagnostic checkpoint.
        self.assertTrue(self.proof_checkpoints)
        self.assertFalse(self.proof_checkpoints[0].get('verified',True))
        self.assertTrue(self.proof_checkpoints[0]['pending'])

    async def test_unreadable_second_classification_read_preserves_real_failure(self):
        await self.scenario(real_failure='classification-read')
        self.assertTrue(self.read_gap)
        self.assertEqual(self.p2._quest_follower_probe.get('failures',0),1)
        self.assertGreater(self.p2._quest_follower_probe.get('retry_at',0),self.clock)

    async def test_session_swap_on_final_input_guard_cancels_before_friend_request(self):
        await self.scenario(input_session_race=True)
        self.assertTrue(self.changed)
        self.assertEqual(self.cancel_request_counts,[0])
        self.assertEqual(self.counts[id(self.p2)],1)


del Fixture
