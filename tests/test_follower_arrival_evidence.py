"""A retained live-area proof cannot certify a far-away follower as reunited."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import unittest
from wizwalker import XYZ
from src import utils
from src.questing import Quester
from tests.test_per_follower_probe import PerFollowerProbeTests as Fixture
from tests.test_dungeon_local_tp import REAL_SLEEP
from tests.test_full_chain_415_restore import entry


class FollowerArrivalEvidenceTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp

    async def scenario(self, *, far=False, p3_fails=False, later_reunion=False,
                       unreadable_name=False, name_missing=False, modal=False):
        self.q.quest_party_observed_zone = self.q.zone
        self.p2.zone = self.q.zone
        self.p2.area_id = self.q.area_id
        if modal or far and not p3_fails:
            self.p3.zone, self.p3.area_id = self.q.zone, self.q.area_id
        if far and p3_fails:
            self.p3.teleport = AsyncMock()  # Keep observed distance far after failed lookup.
        self.p3.position = XYZ(10000, 0, 0) if far else XYZ(0, 0, 0)
        # Actual live-area helper sees entity identity, then retains area tokens.
        self.p2.get_base_entity_list.return_value = [self.q.client_object]
        self.p3.get_base_entity_list.return_value = [self.q.client_object]
        if name_missing:
            self.q.wizard_name = None
        self.names, self.reads = [], []
        async def prepare(client):
            self.reads.append(client)
            if not unreadable_name:
                client.wizard_name = 'Mason Flame'
        async def friend(client, **kwargs):
            self.counts[id(client)] += 1
            self.names.append(kwargs.get('name'))
            if client is self.p3 and p3_fails:
                if far:
                    client.zone, client.area_id = self.q.zone, self.q.area_id
                raise ValueError('quester-not-found')
            client.zone, client.area_id, client.position = self.q.zone, self.q.area_id, self.q.position
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        def status(_h, _q, text):
            if later_reunion and self.p3._quest_follower_probe.get('failures', 0):
                self.p3.zone, self.p3.area_id, self.p3.position = self.q.zone, self.q.area_id, self.q.position
            if self.clock >= 45:
                self.done.set()
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[self.q, self.p2, self.p3]), questing_status=True,
            gear_switching_in_solo_zones=False,
            quest_friend_icons={'p1': {'icon_list':2, 'icon_index':0}},
            use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(side_effect=lambda c,p: modal and c is self.p3 and p==['MessageBoxModalWindow']),
            is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=friend, clients_share_live_area=utils.clients_share_live_area,
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda:self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError,
                TimeoutError=TimeoutError, timeout=asyncio.timeout, current_task=asyncio.current_task)),
            members=[self.q,self.p2,self.p3], change_party_equipment=AsyncMock(return_value=True),
            prepare_quester_name=prepare, remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=Mock(side_effect=status))
        tasks=[asyncio.create_task(follow(h,self.q,primary))
               for h,primary in ((self.p2,True),(self.p3,False))]
        try:
            await asyncio.wait_for(self.done.wait(), 1)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def test_same_zone_far_failed_lookup_cannot_complete_from_retained_instance(self):
        await self.scenario(far=True,p3_fails=True)
        self.assertGreater(self.counts[id(self.p3)],0)
        self.assertTrue(self.p3._quest_follower_probe['pending'])
        self.assertFalse(any('follower=p3 ' in s and 'decision=complete' in s for s in self.logs))
        self.assertFalse(self.q.in_solo_zone)

    async def test_same_instance_far_keeps_local_catchup_then_confirms_nearby(self):
        await self.scenario(far=True)
        self.assertFalse(self.p3._quest_follower_probe['pending'])
        self.assertEqual(self.counts[id(self.p3)],0)
        self.p3.teleport.assert_awaited_once()

    async def test_p2_complete_p3_lookup_retry_remain_independent(self):
        await self.scenario(p3_fails=True)
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertTrue(self.p3._quest_follower_probe['pending'])
        self.assertGreater(self.p3._quest_follower_probe['retry_at'],0)

    async def test_failed_lookup_then_external_real_reunion_clears_own_backoff(self):
        await self.scenario(p3_fails=True,later_reunion=True)
        self.assertFalse(self.p3._quest_follower_probe['pending'])
        self.assertEqual(self.p3._quest_follower_probe['failures'],0)
        self.assertEqual(self.p3._quest_follower_probe['retry_at'],0)
        self.assertEqual(self.counts[id(self.p3)],1)

    async def test_missing_name_is_read_even_when_icon_configured(self):
        await self.scenario(name_missing=True)
        self.assertEqual(self.reads,[self.q])
        self.assertEqual(self.names,['Mason Flame'])

    async def test_unreadable_name_waits_without_icon_only_input(self):
        await self.scenario(name_missing=True,unreadable_name=True)
        self.assertEqual(self.counts[id(self.p3)],0)
        self.assertTrue(self.p3._quest_follower_probe['pending'])

    async def test_same_instance_nearby_but_visible_modal_is_not_complete(self):
        await self.scenario(modal=True)
        self.assertTrue(self.p3._quest_follower_probe['pending'])
        self.assertEqual(self.counts[id(self.p3)],0)


del Fixture
