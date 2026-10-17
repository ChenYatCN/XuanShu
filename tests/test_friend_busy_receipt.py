"""#77: real follow loop, sent request -> stale probe -> independently owned result."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from loguru import logger
from wizwalker import XYZ
from src import utils
from src.automation_ownership import get_client_automation_ownership
from tests.test_per_follower_probe import PerFollowerProbeTests as Fixture
from tests.test_friend_ui_cleanup import Window as BaseWindow
from tests.test_dungeon_local_tp import REAL_SLEEP
from tests.test_full_chain_415_restore import entry


class Window(BaseWindow):
    next_address = 10000

    def __init__(self, name, visible=True, children=(), text=''):
        super().__init__(name, visible, children)
        Window.next_address += 100
        self.address, self.text = Window.next_address, text

    async def read_base_address(self):
        return self.address


class FriendBusyReceiptTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp

    async def scenario(self, *, kind='busy', mutation=None, retry=False, caption='你的好友现在很忙。', request_error='busy', stale=False, show_delay=None, manual=False, stop_at=10, restart=False, arrive_first=False, move_after_arrival=False, regroup_in_backoff=False):
        self.client = self.p2
        self.session = None
        self.clicks, self.request_times, self.dismissal_owners = [], [], []
        self.retry_states = []
        self.events = []
        self.stale_at = None
        self.stale_done = False
        self.close_at = self.show_at = None
        self.mutated = False
        self.right = Window('rightButton')
        self.center = Window('centerButton', visible=kind == 'yesno')
        self.left = Window('leftButton', visible=False)
        self.caption = Window('CaptionText', text=caption if kind != 'unrelated' else 'You have been reported!')
        layout = Window('Layout', children=[self.right, self.center, self.left])
        adjustment = Window('AdjustmentWindow', children=[layout])
        self.modal = Window('MessageBoxModalWindow', visible=kind in ('preexisting', 'hidden-first'), children=[
            Window('messageBoxBG', children=[Window('messageBoxLayout', children=[
                Window('TextArea', children=[self.caption]), adjustment])])])
        self.root = Window('root', children=[self.modal])
        if kind == 'multiple':
            other = Window('MessageBoxModalWindow', visible=False)
            other.parent = self.root
            self.root.nodes.append(other)
        self.right.disabled = kind == 'disabled'
        self.right.gray = kind == 'gray'
        if kind == 'hidden-caption': self.caption.parent.visible = False
        if kind == 'hidden-button-path': self.right.parent.visible = False
        if kind == 'hidden-first':
            hidden = Window('MessageBoxModalWindow', visible=False)
            hidden.parent = self.root
            self.root.nodes.insert(0, hidden)
        self.right.visible = self.modal.visible
        self.p2.root_window = self.root
        self.stack.enter_context(patch('src.window_text.read_control_text',
            AsyncMock(side_effect=lambda window: window.text)))

        def mutate():
            if not mutation or self.mutated: return
            if mutation.startswith('late-'): return
            self.mutated = True
            if mutation == 'stop': self.p2.questing_status = False
            if mutation == 'loading': self.p2.is_loading.return_value = True
            if mutation == 'battle': self.p2.in_battle.return_value = True
            if mutation == 'source-zone': self.p2.zone = 'Changed/Source'
            if mutation == 'reference-zone': self.q.zone = 'Changed/Reference'
            if mutation == 'source-token': self.p2.area_id += 1
            if mutation == 'session':
                self.session = SimpleNamespace(transition={}, confirmed=False, entry_evidence=False,
                    potion_areas={}, follow_attempts={})
            if mutation == 'quest': self.q.quest_id_value += 1
            if mutation == 'goal': self.q.goal_id.return_value += 1
            if mutation == 'receipt': del self.p2._friend_follow_failure
            if mutation == 'caption': self.caption.text = 'A different one-button confirmation'
            if mutation == 'replacement': self.modal.address += 700
            if mutation == 'cancel': raise asyncio.CancelledError()

        address_reads = 0
        async def button_address():
            nonlocal address_reads
            address_reads += 1
            if mutation and mutation.startswith('late-') and address_reads == 2:
                self.mutated = True
                if mutation == 'late-caption': self.caption.text = 'A different one-button confirmation'
                if mutation == 'late-yesno': self.center.visible = True
                if mutation == 'late-replacement': self.modal.address += 700
            return self.right.address
        self.right.read_base_address = button_address

        class Mouse:
            depth = 0
            async def __aenter__(mouse):
                if self.modal.visible and self.request_times: mutate()
                mouse.depth += 1
                return mouse
            async def __aexit__(mouse, *_): mouse.depth -= 1
            async def click_window(mouse, button, **_):
                self.assertIs(button, self.right)
                self.clicks.append((self.p2.title, button.label))
                self.assertEqual(mouse.depth, 1, 'failure input has one scoped mouse context')
                self.assertIs(get_client_automation_ownership(self.p2)._owner_task, asyncio.current_task())
                if kind != 'persistent': self.close_at = self.clock + (.6 if kind == 'slow-close' else .2)
        self.mouse = Mouse()
        self.p2.mouse_handler = self.mouse
        safety_reads = 0
        async def in_battle():
            nonlocal safety_reads
            if (self.mouse.depth > 0 and getattr(self.p2, '_friend_follow_failure', {}).get('modal') is not None):
                safety_reads += 1
                if safety_reads == 2:
                    if mutation == 'late-safety-caption':
                        self.caption.text = 'A different one-button confirmation'
                    if mutation == 'late-safety-receipt':
                        op = self.p2._friend_follow_failure
                        self.p2._friend_follow_failure = dict(op, token='new-receipt', clicked=False)
            return False
        self.p2.in_battle.side_effect = in_battle

        async def friend(member, **kwargs):
            self.assertIs(member, self.p2)
            self.assertTrue(kwargs.get('strict_identity'))
            if kwargs.get('before_request'): await kwargs['before_request']()
            self.request_times.append(self.clock)
            if kwargs.get('on_request_sent'): kwargs['on_request_sent']()
            if stale: self.stale_at = self.clock + .2
            if arrive_first or retry and len(self.request_times) == 2:
                member.zone, member.area_id = self.q.zone, self.q.area_id
                return
            if kind == 'late': self.show_at = self.clock + (show_delay if show_delay is not None else .3)
            elif kind != 'none': self.modal.visible = self.right.visible = True
            if kind == 'multiple': self.root.nodes[-1].visible = True
            if request_error == 'runtime': raise RuntimeError('request response read failed')
            if request_error == 'timeout': raise asyncio.TimeoutError('request timed out')
            if request_error == 'busy': raise utils.FriendBusyOrInstanceClosed()
            return True

        async def tick(seconds):
            self.clock += seconds
            if self.stale_at is not None and self.clock >= self.stale_at and not self.stale_done:
                self.q.zone = 'Changed/Reference'
                self.stale_done = True
            if self.show_at is not None and self.clock >= self.show_at:
                self.modal.visible = self.right.visible = True
                self.show_at = None
            if self.close_at is not None and self.clock >= self.close_at:
                self.dismissal_owners.append(get_client_automation_ownership(self.p2).owner_label)
                self.modal.visible = self.right.visible = False
                self.close_at = None
            if self.clock >= (90 if retry else stop_at): self.done.set()
            await REAL_SLEEP(0)

        def record(message):
            line = str(message)
            self.events.append((self.clock, line))
            if manual and '[friend_busy_cleanup]' in line and 'decision=modal-detected' in line:
                self.modal.visible = self.right.visible = False
            if regroup_in_backoff and 'decision=backoff-start' in line:
                self.p2.zone, self.p2.area_id = self.q.zone, self.q.area_id
                self.q.position = XYZ(5000,0,0)
            if move_after_arrival and 'friend-arrival-confirmed' in line:
                self.q.position = XYZ(5000,0,0)
            if 'own-friend-instance-unavailable' in line:
                self.retry_states.append((self.clock, dict(self.p2._quest_follower_probe)))
            if 'friend-arrival-confirmed' in line and retry: self.done.set()
        sink = logger.add(record, level='DEBUG')
        self.addCleanup(logger.remove, sink)
        namespace = dict(walker=SimpleNamespace(clients=[self.q, self.p2, self.p3]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=utils.is_visible_by_path, is_friend_teleport_error=utils.is_friend_teleport_error,
            get_window_from_path=utils.get_window_from_path, teleport_to_friend_from_list=friend,
            clients_share_live_area=AsyncMock(side_effect=lambda a,b: a.zone == b.zone and a.area_id == b.area_id),
            dungeon_session_for=lambda _: self.session,
            observe_dungeon_session=AsyncMock(side_effect=lambda _: self.session),
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda:self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError,
                timeout=asyncio.timeout, current_task=asyncio.current_task))
        follow = entry('_follow_quester_session', namespace, members=[self.q, self.p2, self.p3],
            change_party_equipment=AsyncMock(return_value=True), prepare_quester_name=AsyncMock(),
            remove_party_status=Mock(), restart_quest_worker_after_probe=Mock(), update_party_status=Mock())
        task = asyncio.create_task(follow(self.p2, self.q, True))
        waiter = asyncio.create_task(self.done.wait())
        try:
            completed, _ = await asyncio.wait((task, waiter), timeout=2, return_when=asyncio.FIRST_COMPLETED)
            self.assertTrue(completed, 'follow did not reach a bounded decision')
            if task in completed and not task.cancelled(): task.result()
        finally:
            for pending in (task, waiter): pending.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)
        self.assertFalse(get_client_automation_ownership(self.p2).locked)
        self.assertEqual(self.mouse.depth, 0)
        if restart:
            self.done.clear()
            stop_at = self.clock + 10
            task = asyncio.create_task(follow(self.p2, self.q, True))
            waiter = asyncio.create_task(self.done.wait())
            try:
                completed, _ = await asyncio.wait((task, waiter), timeout=2, return_when=asyncio.FIRST_COMPLETED)
                self.assertTrue(completed)
                if task in completed and not task.cancelled(): task.result()
            finally:
                for pending in (task, waiter): pending.cancel()
                await asyncio.gather(task, waiter, return_exceptions=True)
            self.assertFalse(get_client_automation_ownership(self.p2).locked)
            self.assertEqual(self.mouse.depth, 0)

    def event_times(self, decision):
        return [t for t, line in self.events if '[friend_busy_cleanup]' in line and decision in line]

    async def test_A_sent_stale_late_busy_clicked_once_disappears_then_sixty_second_backoff(self):
        await self.scenario(kind='late', stale=True, show_delay=6.3, request_error='none', retry=True)
        self.assertTrue(any('stale-probe-reference' in line for line in self.logs), '\n'.join(self.logs))
        stale_time = next(t for t, line in self.events if 'stale-probe-reference' in line)
        self.assertLess(stale_time, self.event_times('decision=modal-detected')[0])
        self.assertEqual(self.clicks, [('p2', 'rightButton')])
        self.assertEqual(len(self.request_times), 2)
        disappeared = self.event_times('decision=modal-disappeared')[0]
        self.assertGreaterEqual(self.request_times[1], disappeared + 60)
        self.assertEqual(len(self.event_times('decision=click-ok')), 1)
        self.assertTrue(self.event_times('reason=busy-backoff'))
        self.assertFalse(getattr(self.p3, '_friend_follow_failure', None))
        self.assertFalse(self.p2._quest_follower_probe['pending'])

    async def test_B_busy_before_stale_and_C_busy_shortly_after_stale_both_clean(self):
        for delay in (.1, 6.3):
            with self.subTest(delay=delay):
                self.setUp()
                await self.scenario(kind='late', stale=True, show_delay=delay, request_error='none')
                self.assertEqual(self.clicks, [('p2', 'rightButton')])
                self.assertTrue(self.event_times('decision=modal-disappeared'))
                self.stack.close()

    async def test_D_stale_without_modal_has_no_click_or_busy_backoff(self):
        await self.scenario(kind='none', stale=True, request_error='none')
        self.assertTrue(self.stale_done)
        self.assertEqual(self.clicks, [])
        self.assertEqual(len(self.request_times), 1)
        self.assertFalse(self.event_times('decision=backoff-start'))

    async def test_E_stale_unrelated_modal_cannot_be_claimed(self):
        await self.scenario(kind='unrelated', stale=True, request_error='none')
        self.assertEqual(self.clicks, [])
        self.assertFalse(self.event_times('decision=backoff-start'))
        self.assertEqual(len(self.request_times), 1)

    async def test_F_user_dismissal_after_detection_retires_without_late_click(self):
        await self.scenario(manual=True)
        self.assertEqual(self.clicks, [])
        self.assertFalse(self.event_times('decision=click-ok'))
        self.assertTrue(self.event_times('decision=modal-disappeared'))
        self.assertFalse(getattr(self.p2, '_friend_follow_failure', None))

    async def test_G_persistent_clicked_modal_only_polls_and_H_blocks_new_requests(self):
        await self.scenario(kind='persistent', stale=True, request_error='none')
        self.assertEqual(self.clicks, [('p2', 'rightButton')])
        self.assertEqual(len(self.request_times), 1)
        self.assertTrue(self.event_times('decision=modal-still-visible'))
        self.assertTrue(self.event_times('reason=cleanup-active'))
        receipt = getattr(self.p2, '_friend_follow_failure', None)
        self.assertIsNotNone(receipt, 'worker cancellation must not erase sent-input budget')
        self.assertTrue(receipt['clicked'])
        self.assertFalse(self.event_times('decision=backoff-start'))
        self.assertTrue(self.p2._quest_follower_probe['pending'])

    async def test_I_backoff_begins_after_disappearance_and_expiry_allows_one_retry(self):
        await self.scenario(kind='slow-close', retry=True)
        self.assertTrue(self.event_times('decision=modal-disappeared'))
        gone = self.event_times('decision=modal-disappeared')[0]
        backoff = self.event_times('decision=backoff-start')[0]
        self.assertGreaterEqual(backoff, gone)
        self.assertEqual(len(self.request_times), 2)
        self.assertGreaterEqual(self.request_times[1], gone + 60)
        self.assertEqual(self.clicks, [('p2', 'rightButton')])

    async def test_J_preexisting_busy_or_unknown_modal_never_sent_or_claimed(self):
        for caption in ('你的好友现在很忙。', 'An unrelated modal'):
            with self.subTest(caption=caption):
                self.setUp()
                await self.scenario(kind='preexisting', caption=caption)
                self.assertEqual(self.request_times, [])
                self.assertEqual(self.clicks, [])
                self.assertFalse(self.event_times('decision=request-sent'))
                self.stack.close()

    async def test_after_correlation_deadline_late_busy_is_not_clicked(self):
        await self.scenario(kind='late', stale=True, show_delay=31.5, request_error='none', stop_at=40)
        self.assertEqual(self.clicks, [])
        self.assertFalse(self.event_times('decision=modal-detected'))
        self.assertFalse(self.event_times('decision=backoff-start'))

    async def test_actual_request_click_not_lookup_start_emits_receipt(self):
        go, center, close = Window('btnGoToFriend'), Window('centerButton'), Window('btnCharacterClose')
        events = []
        async def find(_parent, name):
            return {'btnGoToFriend':go, 'MessageBoxModalWindow':object(),
                    'centerButton':center, 'btnCharacterClose':close}[name]
        async def click(button, *, on_dispatched=None):
            events.append(('click', button))
            if on_dispatched is not None:
                on_dispatched()
        async def before(): events.append(('baseline', None))
        def sent(): events.append(('sent', None))
        c = SimpleNamespace(title='p2', root_window=object(), mouse_handler=SimpleNamespace(click_window=click))
        with patch.object(utils, '_maybe_get_named_window', find), patch.object(utils.asyncio, 'sleep', AsyncMock()):
            await utils._request_friend_teleport(c, object(), before_request=before, on_request_sent=sent)
        self.assertEqual(events, [('baseline',None), ('click',go), ('sent',None),
                                  ('click',center), ('click',close)])
        events.clear()
        with patch.object(utils, '_maybe_get_named_window', AsyncMock(side_effect=ValueError('No child window named btnGoToFriend'))):
            with self.assertRaises(utils.FriendLookupError):
                await utils._request_friend_teleport(c, object(), before_request=before, on_request_sent=sent)
        self.assertEqual(events, [])

    async def test_busy_result_never_sends_the_sdk_hidden_yes_button_input(self):
        go, center, close = Window('btnGoToFriend'), Window('centerButton', visible=False), Window('btnCharacterClose')
        calls = []
        async def find(_parent, name):
            return {'btnGoToFriend':go, 'MessageBoxModalWindow':self.modal,
                    'centerButton':center, 'btnCharacterClose':close}[name]
        self.modal = Window('MessageBoxModalWindow')
        calls = []
        async def click(button, *, on_dispatched=None):
            calls.append(button)
            if on_dispatched is not None:
                on_dispatched()
        c = SimpleNamespace(title='p2', root_window=object(), mouse_handler=SimpleNamespace(click_window=click))
        sent = []
        with patch.object(utils, '_maybe_get_named_window', find), patch.object(utils.asyncio, 'sleep', AsyncMock()):
            await utils._request_friend_teleport(c, object(), before_request=AsyncMock(), on_request_sent=lambda: sent.append(True))
        self.assertEqual(calls, [go])
        self.assertEqual(sent, [True])

    async def test_worker_recreation_keeps_consumed_click_and_backoff(self):
        for kind in ('persistent', 'busy'):
            with self.subTest(kind=kind):
                self.setUp()
                await self.scenario(kind=kind, restart=True)
                self.assertEqual(self.clicks, [('p2', 'rightButton')])
                self.assertEqual(len(self.request_times), 1)
                self.stack.close()

    async def test_persistent_modal_outlives_correlation_without_reclick_or_request(self):
        await self.scenario(kind='persistent', stop_at=70)
        self.assertEqual(self.clicks, [('p2','rightButton')])
        self.assertEqual(len(self.request_times), 1)
        self.assertTrue(self.p2._friend_follow_failure['clicked'])
        self.assertFalse(self.event_times('decision=backoff-start'))

    async def test_quest_zone_and_session_changes_cannot_revoke_sent_cleanup(self):
        for mutation in ('source-zone', 'reference-zone', 'source-token', 'session', 'quest', 'goal'):
            with self.subTest(mutation=mutation):
                self.setUp()
                await self.scenario(mutation=mutation)
                self.assertTrue(self.mutated)
                self.assertEqual(self.clicks, [('p2','rightButton')])
                self.assertTrue(self.event_times('decision=modal-disappeared'))
                self.stack.close()

    async def test_ambiguous_unsafe_or_replaced_receipt_cannot_click(self):
        for kind, mutation in (('multiple',None), ('yesno',None), ('disabled',None),
                               ('hidden-button-path',None), ('busy','receipt')):
            with self.subTest(kind=kind,mutation=mutation):
                self.setUp()
                await self.scenario(kind=kind,mutation=mutation)
                self.assertEqual(self.clicks, [])
                self.stack.close()

    async def test_last_async_safety_read_cannot_authorize_changed_modal_or_receipt(self):
        for mutation in ('late-safety-caption', 'late-safety-receipt'):
            with self.subTest(mutation=mutation):
                self.setUp()
                await self.scenario(mutation=mutation)
                self.assertEqual(self.clicks, [])
                self.stack.close()

    async def test_installed_sdk_post_dispatch_cancel_keeps_receipt_but_pre_dispatch_failure_does_not(self):
        import wizwalker.mouse_handler as sdk
        for phase in ('pre', 'post', 'post-error'):
            with self.subTest(phase=phase):
                events = []
                c = SimpleNamespace(title='p2', root_window=object(), window_handle=7)
                mouse = sdk.MouseHandler(c)
                c.mouse_handler = mouse
                async def position(x, y, **kwargs):
                    events.append(('position',x,y))
                    if phase == 'pre' or (x,y) == (-100,-100):
                        if phase == 'post-error': raise RuntimeError('post-input reset failed')
                        raise asyncio.CancelledError()
                mouse.set_mouse_position = position
                go = SimpleNamespace(scale_to_client=AsyncMock(return_value=SimpleNamespace(center=lambda:(10,20))))
                async def find(_parent, name):
                    self.assertEqual(name, 'btnGoToFriend')
                    return go
                def dispatch(_handle, message, *_): events.append(('dispatch',message))
                def sent(): events.append(('sent',None))
                error = RuntimeError if phase == 'post-error' else asyncio.CancelledError
                with patch.object(utils, '_maybe_get_named_window',find), \
                     patch.object(sdk, 'user32',SimpleNamespace(SendMessageW=dispatch)), \
                     patch.object(sdk.asyncio, 'sleep',AsyncMock()):
                    with self.assertRaises(error):
                        await utils._request_friend_teleport(c,object(),before_request=AsyncMock(),on_request_sent=sent)
                self.assertIs(mouse.set_mouse_position,position)
                if phase == 'pre':
                    self.assertEqual(events,[('position',10,20)])
                else:
                    self.assertEqual(events,[('position',10,20),('dispatch',0x201),('dispatch',0x202),
                        ('sent',None),('position',-100,-100)])

    async def test_installed_sdk_normal_click_optional_dispatch_callback_is_exactly_once(self):
        import wizwalker.mouse_handler as sdk
        for with_callback in (False, True):
            with self.subTest(with_callback=with_callback):
                events = []
                c = SimpleNamespace(title='p2', window_handle=7)
                mouse = sdk.MouseHandler(c)
                async def position(x, y, **_):
                    events.append(('position', x, y))
                mouse.set_mouse_position = position
                def dispatch(_handle, message, *_):
                    events.append(('dispatch', message))
                callback = (lambda: events.append(('sent', None))) if with_callback else None
                with patch.object(sdk, 'user32', SimpleNamespace(SendMessageW=dispatch)), \
                     patch.object(sdk.asyncio, 'sleep', AsyncMock()):
                    await mouse.click(10, 20, on_dispatched=callback)
                expected = [('position', 10, 20), ('dispatch', 0x201), ('dispatch', 0x202)]
                if with_callback:
                    expected.append(('sent', None))
                expected.append(('position', -100, -100))
                self.assertEqual(events, expected)

    async def test_installed_sdk_click_window_forwards_dispatch_callback_exactly_once(self):
        import wizwalker.mouse_handler as sdk
        events = []
        c = SimpleNamespace(title='p2', window_handle=7)
        mouse = sdk.MouseHandler(c)
        async def position(x, y, **_):
            events.append(('position', x, y))
        mouse.set_mouse_position = position
        def dispatch(_handle, message, *_):
            events.append(('dispatch', message))
        window = SimpleNamespace(scale_to_client=AsyncMock(
            return_value=SimpleNamespace(center=lambda: (10, 20))))
        with patch.object(sdk, 'user32', SimpleNamespace(SendMessageW=dispatch)), \
             patch.object(sdk.asyncio, 'sleep', AsyncMock()):
            await mouse.click_window(window, on_dispatched=lambda: events.append(('sent', None)))
        self.assertEqual(events, [('position', 10, 20), ('dispatch', 0x201), ('dispatch', 0x202),
                                  ('sent', None), ('position', -100, -100)])

    async def test_receipt_and_busy_backoff_block_requests_without_blocking_normal_follow(self):
        for busy in (False,True):
            with self.subTest(busy=busy):
                self.setUp()
                await self.scenario(kind='busy' if busy else 'none', request_error='busy' if busy else 'none',
                    arrive_first=not busy, move_after_arrival=not busy, regroup_in_backoff=busy)
                self.assertEqual(len(self.request_times),1)
                self.assertGreater(self.p2.teleport.await_count,0)
                self.stack.close()

    async def test_successful_arrival_retires_receipt_without_backoff_and_keeps_local_follow(self):
        await self.scenario(kind='none', request_error='none', arrive_first=True, move_after_arrival=True)
        self.assertEqual(self.clicks, [])
        self.assertFalse(getattr(self.p2, '_friend_follow_failure', None))
        self.assertTrue(self.event_times('decision=arrival-confirmed'))
        self.assertFalse(self.event_times('decision=backoff-start'))
        self.assertGreater(self.p2.teleport.await_count, 0)

    async def test_invalidated_verified_busy_is_not_clicked_and_retires_at_bounded_deadline(self):
        await self.scenario(kind='busy', mutation='caption', stop_at=40)
        self.assertEqual(self.clicks, [])
        self.assertTrue(self.event_times('decision=modal-invalidated'))
        self.assertTrue(self.event_times('decision=invalidated-expired'))
        self.assertFalse(getattr(self.p2, '_friend_follow_failure', None))


del Fixture

