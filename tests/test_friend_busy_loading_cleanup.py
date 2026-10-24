"""Confirmed Busy cleanup is terminal even when the game reports loading."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from src.automation_ownership import automation_owner,get_client_automation_ownership
from tests import test_friend_busy_click as click_cases
from tests import test_friend_busy_receipt as receipt_cases


class BusyCleanupReleaseTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        click_cases.FriendBusyClickTests.setUpClass.__func__(cls)

    make_cleanup=click_cases.FriendBusyClickTests.make_cleanup
    settle=click_cases.FriendBusyClickTests.settle

    async def test_verified_busy_loading_closes_and_releases_own_receipt(self):
        f=self.make_cleanup();f.client.is_loading.return_value=True
        self.assertTrue(await self.settle(f))
        self.assertFalse(f.modal.visible)
        self.assertEqual(len(f.clicks),1)
        self.assertFalse(getattr(f.client,'_friend_follow_failure',None))
        self.assertNotIn('friend_failure',f.state)
        self.assertTrue(any('decision=cleanup-released' in s for s in f.logs))
        decisions=[s.split('decision=',1)[1].split()[0] for s in f.logs]
        self.assertLess(decisions.index('modal-detected'),decisions.index('modal-dismiss-attempt'))
        self.assertLess(decisions.index('modal-dismiss-attempt'),decisions.index('modal-dismissed'))
        self.assertLess(decisions.index('modal-dismissed'),decisions.index('cleanup-released'))

    async def test_busy_cleanup_error_releases_receipt_and_owner(self):
        f=self.make_cleanup()
        f.client.mouse_handler.click_window=AsyncMock(side_effect=RuntimeError('read failed'))
        await self.settle(f)
        self.assertFalse(getattr(f.client,'_friend_follow_failure',None))
        self.assertNotIn('friend_failure',f.state)
        self.assertFalse(get_client_automation_ownership(f.client).locked)
        self.assertEqual(f.state.get('friend_outcome'),'unavailable')

    async def test_cancellation_after_busy_detection_releases_own_cleanup(self):
        f=self.make_cleanup()
        f.client.mouse_handler.click_window=AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):await self.settle(f)
        self.assertFalse(getattr(f.client,'_friend_follow_failure',None))
        self.assertNotIn('friend_failure',f.state)
        self.assertFalse(get_client_automation_ownership(f.client).locked)

    async def test_state_block_releases_cleanup_without_unsafe_input(self):
        f=self.make_cleanup();f.client.quest_recovery_owner='other-owner'
        await self.settle(f)
        self.assertEqual(f.clicks,[])
        self.assertFalse(getattr(f.client,'_friend_follow_failure',None))
        self.assertTrue(any('decision=cleanup-released' in s for s in f.logs))

    async def test_replaced_receipt_releases_only_old_cleanup(self):
        f=self.make_cleanup();new_operation=None
        original=f.ns['logger'].debug
        def replace(line):
            nonlocal new_operation
            original(line)
            if 'decision=modal-detected' in line:
                new_operation=dict(f.operation,generation=2,token=f'{id(f.client):x}-2',
                                   state='request-sent',modal=None,busy_failed=False)
                f.client._friend_request_generation=2
                f.client._friend_follow_failure=new_operation
                f.state['friend_failure']=new_operation
        f.ns['logger'].debug=replace
        await self.settle(f)
        self.assertEqual(f.clicks,[])
        self.assertIs(f.client._friend_follow_failure,new_operation)
        self.assertIs(f.state['friend_failure'],new_operation)
        self.assertEqual(new_operation['state'],'request-sent')
        self.assertNotIn('friend_outcome',f.state)

    async def test_cancel_during_owner_wait_releases_receipt_not_other_owner(self):
        f=self.make_cleanup();f.operation['busy_failed']=True
        entered=asyncio.Event();release=asyncio.Event()
        async def holder():
            async with automation_owner(f.client,'other-owner'):
                entered.set();await release.wait()
        owner_task=asyncio.create_task(holder());await entered.wait()
        task=asyncio.create_task(self.settle(f));await asyncio.sleep(0)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertFalse(getattr(f.client,'_friend_follow_failure',None))
            self.assertEqual(get_client_automation_ownership(f.client).owner_label,'other-owner')
        finally:
            release.set();await owner_task

    async def test_unrelated_modal_loading_never_gets_busy_input_exception(self):
        f=self.make_cleanup(caption='Unrelated confirmation');f.client.is_loading.return_value=True
        self.assertFalse(await self.settle(f));self.assertEqual(f.clicks,[])
        self.assertFalse(any('decision=modal-dismiss-attempt' in s for s in f.logs))

    async def test_manual_disappearance_releases_without_click(self):
        f=self.make_cleanup(manual=True)
        self.assertTrue(await self.settle(f))
        self.assertEqual(f.clicks,[])
        self.assertTrue(any('decision=already-gone' in s for s in f.logs))
        self.assertFalse(getattr(f.client,'_friend_follow_failure',None))

    async def test_new_reference_is_not_blocked_by_old_busy_backoff(self):
        f=self.make_cleanup();f.operation['reference']=('old target',)
        f.state['reference']=('old target',)
        await self.settle(f)
        self.assertTrue(f.ns['friend_request_blocked'](f.client))
        f.state['reference']=('new target',)
        f.clock[0]+=61  # Preserve the first Busy cooldown across a reference change.
        self.assertFalse(f.ns['friend_request_blocked'](f.client))

    async def test_second_busy_budget_survives_request_generation_and_requires_new_reference(self):
        f=self.make_cleanup();f.operation['reference']=('target',);f.state['reference']=('target',)
        await self.settle(f);f.clock[0]+=61
        self.assertFalse(f.ns['friend_request_blocked'](f.client))
        f.operation=dict(client=f.client,quester=f.operation['quester'],generation=2,
            token=f'{id(f.client):x}-2',modal=None,clicked=False,deadline=f.clock[0]+30,
            state='request-sent',busy_until=0,reference=('target',))
        f.client._friend_request_generation=2;f.client._friend_follow_failure=f.operation
        f.state['friend_failure']=f.operation;f.modal.visible=True
        await self.settle(f)
        self.assertEqual(f.operation['busy_failures'],2)
        f.clock[0]+=3600
        self.assertTrue(f.ns['friend_request_blocked'](f.client))
        f.state['reference']=None  # An unreadable frame cannot grant a new budget.
        self.assertTrue(f.ns['friend_request_blocked'](f.client))
        f.state['reference']=('new target',)
        self.assertFalse(f.ns['friend_request_blocked'](f.client))


class BusyFollowRegressionTests(unittest.IsolatedAsyncioTestCase):
    setUp=receipt_cases.FriendBusyReceiptTests.setUp
    scenario=receipt_cases.FriendBusyReceiptTests.scenario
    event_times=receipt_cases.FriendBusyReceiptTests.event_times

    async def test_repeated_busy_same_reference_stops_after_one_retry(self):
        await self.scenario(stop_at=220)
        self.assertEqual(len(self.request_times),2)
        self.assertFalse(getattr(self.p2,'_friend_follow_failure',None))
        self.assertTrue(self.event_times('reason=busy-unavailable'))

    async def test_busy_loading_terminal_status_replaces_transferring(self):
        await self.scenario(mutation='loading')
        self.assertEqual(self.clicks,[('p2','rightButton')])
        self.assertFalse(getattr(self.p2,'_friend_follow_failure',None))
        self.assertEqual(self.p2._quest_follower_probe.get('friend_outcome'),'unavailable')
        self.assertTrue(any('好友传送失败' in text for _,text in self.statuses))
        self.assertNotEqual(self.statuses[-1][1],'正在好友传送')

    async def test_busy_retry_response_error_cannot_rearm_a_third_request(self):
        await self.scenario(stop_at=220,retry_without_busy=True)
        self.assertEqual(len(self.request_times),2)
        self.assertFalse(getattr(self.p2,'_friend_follow_failure',None))
        self.assertTrue(self.p2._friend_follow_busy_backoff['retry_sent'])
        self.assertTrue(self.event_times('reason=busy-unavailable'))

    async def test_busy_retry_confirmed_arrival_clears_failure_budget(self):
        await self.scenario(retry=True)
        self.assertEqual(len(self.request_times),2)
        self.assertTrue(self.event_times('decision=arrival-confirmed'))
        self.assertFalse(getattr(self.p2,'_friend_follow_busy_backoff',None))

    async def test_successful_arrival_still_retires_without_busy_budget(self):
        await self.scenario(kind='none',request_error='none',arrive_first=True,move_after_arrival=True)
        self.assertTrue(self.event_times('decision=arrival-confirmed'))
        self.assertFalse(getattr(self.p2,'_friend_follow_failure',None))
        self.assertFalse(getattr(self.p2,'_friend_follow_busy_backoff',None))
        self.assertGreater(self.p2.teleport.await_count,0)
