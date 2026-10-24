"""Only this request's verified FriendBusy modal may be acknowledged once."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from loguru import logger
from src import utils
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
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


class FriendBusyModalTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp

    async def scenario(self, *, kind='busy', mutation=None, retry=False, caption='你的好友现在很忙。', request_error='busy',
                       gear=False, solo=True, switch_started=None, switch_release=None,
                       switch_release_at=None, join_peers=False):
        # This fixture runs one follower; p3 observes isolation as an unrelated
        # client. Multi-hitter completion is covered by the party probe tests.
        self.q.quest_party_hitters = [self.p2]
        self.client = self.p2
        self.session = None
        self.clicks, self.request_times, self.dismissal_owners = [], [], []
        self.retry_states = []
        self.close_at = self.show_at = None
        self.mutated = False
        self.gear_calls = []
        self.gear_states = []
        self.stack.enter_context(patch.object(
            Quester, 'determine_solo_zone', AsyncMock(return_value=solo)))
        self.stack.enter_context(patch('src.utils.clients_share_live_area', AsyncMock(
            side_effect=lambda a, b: a.zone == b.zone and a.area_id == b.area_id)))
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

        async def friend(member, **kwargs):
            self.assertIs(member, self.p2)
            self.assertTrue(kwargs.get('strict_identity'))
            if kwargs.get('before_request'): await kwargs['before_request']()
            self.request_times.append(self.clock)
            if kwargs.get('on_request_sent'): kwargs['on_request_sent']()
            if retry and len(self.request_times) == 2:
                member.zone, member.area_id = self.q.zone, self.q.area_id
                if join_peers:
                    self.p3.zone, self.p3.area_id = self.q.zone, self.q.area_id
                return
            if kind == 'late': self.show_at = self.clock + .3
            elif kind != 'none': self.modal.visible = self.right.visible = True
            if request_error == 'runtime': raise RuntimeError('request response read failed')
            if request_error == 'timeout': raise asyncio.TimeoutError('request timed out')
            raise utils.FriendBusyOrInstanceClosed()

        async def change_equipment(client, set_number):
            self.gear_calls.append(set_number)
            self.gear_states.append(self.q.quest_party_probe_pending)
            if switch_started is not None:
                switch_started.set()
            if switch_release is not None:
                await switch_release.wait()
            return True

        async def tick(seconds):
            self.clock += seconds
            if switch_release_at is not None and self.clock >= switch_release_at:
                switch_release.set()
            if self.show_at is not None and self.clock >= self.show_at:
                self.modal.visible = self.right.visible = True
                self.show_at = None
            if self.close_at is not None and self.clock >= self.close_at:
                self.dismissal_owners.append(get_client_automation_ownership(self.p2).owner_label)
                self.modal.visible = self.right.visible = False
                self.close_at = None
            if self.clock >= (70 if retry else 10): self.done.set()
            await REAL_SLEEP(0)

        def record(message):
            line = str(message)
            if 'own-friend-instance-unavailable' in line:
                self.retry_states.append((self.clock, dict(self.p2._quest_follower_probe)))
            if 'friend-arrival-confirmed' in line and retry: self.done.set()
        sink = logger.add(record, level='DEBUG')
        self.addCleanup(logger.remove, sink)
        namespace = dict(walker=SimpleNamespace(clients=[self.q, self.p2, self.p3]), questing_status=True,
            gear_switching_in_solo_zones=gear, quest_friend_icons={}, use_potions=False, buy_potions=False,
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
            change_party_equipment=change_equipment, prepare_quester_name=AsyncMock(),
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

    async def test_busy_confirmation_clicks_once_waits_hidden_and_releases_only_own_temp_state(self):
        await self.scenario()
        self.assertEqual(self.clicks, [('p2', 'rightButton')])
        self.assertFalse(self.modal.visible)
        self.assertEqual(self.dismissal_owners, ['friend-follow'])
        self.assertNotIn('friend_failure', self.p2._quest_follower_probe)
        self.assertFalse(getattr(self.p2, '_friend_follow_failure', None))
        self.assertFalse(self.p2._quest_follower_probe['pending'],
                         'a rejected TP is terminal, not arrival')
        self.assertEqual(self.p2._quest_follower_probe['terminal'], 'unavailable')
        self.assertFalse(self.q.quest_party_probe_pending,
                         'the completed cleanup must release quester movement')
        self.assertIsNotNone(getattr(self.p2, '_friend_follow_busy_backoff', None))
        self.assertFalse(hasattr(self.p3, '_friend_follow_failure'))
        self.assertFalse(any('own-visible-transition-modal' in line for line in self.logs))

    async def test_ordinary_friend_busy_releases_quester_movement_after_cleanup(self):
        await self.scenario()
        self.assertTrue(self.p2._quest_follower_probe['terminal'] == 'unavailable')
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(self.q.quest_party_probe_pending)
        gate = Quester(self.q, [self.q], None)
        with patch('src.questing.observe_dungeon_session', AsyncMock(return_value=None)):
            self.assertFalse(await gate._quest_party_probe_blocks_movement())
        self.assertTrue(any('decision=allow reason=friend-probe-terminal' in line
                            for line in self.logs))

    async def test_friend_busy_backoff_blocks_new_request_not_quest_movement(self):
        await self.scenario()
        backoff = getattr(self.p2, '_friend_follow_busy_backoff', None)
        self.assertIsNotNone(backoff)
        self.assertGreater(backoff['busy_until'], self.clock)
        self.assertEqual(len(self.request_times), 1, 'backoff must stop a second request')
        self.assertFalse(self.q.quest_party_probe_pending)
        gate = Quester(self.q, [self.q], None)
        with patch('src.questing.observe_dungeon_session', AsyncMock(return_value=None)):
            self.assertFalse(await gate._quest_party_probe_blocks_movement())

    async def test_cleanup_active_temporarily_blocks_until_modal_disappears(self):
        observed = []
        def record(message):
            if 'friend_busy_cleanup' in str(message) and 'decision=modal-detected' in str(message):
                observed.append(self.q.quest_party_probe_pending)
        sink = logger.add(record, level='DEBUG')
        self.addCleanup(logger.remove, sink)
        await self.scenario(kind='slow-close')
        self.assertTrue(observed)
        self.assertTrue(all(observed), 'cleanup must temporarily own the probe')
        self.assertFalse(self.q.quest_party_probe_pending)

    async def test_friend_request_error_also_releases_quester_movement(self):
        await self.scenario(request_error='runtime')
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertEqual(self.p2._quest_follower_probe['terminal'], 'unavailable')
        self.assertFalse(self.q.quest_party_probe_pending)

    async def test_solo_gear_switch_completes_before_movement_release(self):
        release = asyncio.Event()
        await self.scenario(gear=True, solo=True, switch_release=release,
                            switch_release_at=2)
        self.assertEqual(self.gear_calls, [1])
        self.assertEqual(self.gear_states, [True])
        self.assertTrue(self.q.quest_party_solo_gear_active)
        self.assertTrue(self.q.in_solo_zone)
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertFalse(self.p2._quest_follower_probe['pending'])

    async def test_equipment_switch_incomplete_keeps_probe_pending(self):
        release, started = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(self.scenario(
            gear=True, solo=True, switch_started=started, switch_release=release))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertEqual(self.gear_calls, [1])
            self.assertEqual(self.gear_states, [True])
            self.assertTrue(self.q.quest_party_probe_pending)
            self.assertFalse(getattr(self.q, 'quest_party_solo_gear_active', False))
            self.assertTrue(self.p2._quest_follower_probe['pending'])
        finally:
            release.set()
            await task
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertTrue(self.q.quest_party_solo_gear_active)

    async def test_single_busy_without_solo_confirmation_does_not_switch_gear(self):
        await self.scenario(gear=True, solo=False)
        self.assertEqual(self.gear_calls, [])
        self.assertFalse(getattr(self.q, 'quest_party_solo_gear_active', False))
        self.assertFalse(self.q.in_solo_zone)
        self.assertFalse(self.q.quest_party_probe_pending)

    async def test_hitter_return_restores_first_equipment_set(self):
        release = asyncio.Event()
        await self.scenario(gear=True, solo=True, retry=True,
                            switch_release=release, switch_release_at=2, join_peers=True)
        self.assertEqual(self.gear_calls, [1, 0])
        self.assertFalse(self.q.quest_party_solo_gear_active)
        self.assertFalse(self.q.in_solo_zone)
        self.assertFalse(self.q.quest_party_probe_pending)

    async def test_successful_friend_arrival_still_completes_probe(self):
        await self.scenario(retry=True)
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertGreaterEqual(len(self.request_times), 2)

    async def test_delayed_and_slow_hiding_failure_modal_still_get_one_click(self):
        for kind in ('late', 'slow-close'):
            with self.subTest(kind=kind):
                self.setUp()
                await self.scenario(kind=kind)
                self.assertEqual(self.clicks, [('p2', 'rightButton')])
                self.assertFalse(self.modal.visible)
                self.assertEqual(self.dismissal_owners, ['friend-follow'])
                self.stack.close()

    async def test_verified_language_forms_are_scoped_failure_prompts(self):
        for caption in ('你的好友当前正忙。', 'Your friend is busy right now.', '<string;GUI_00006574>'):
            with self.subTest(caption=caption):
                self.setUp()
                await self.scenario(caption=caption)
                self.assertEqual(self.clicks, [('p2', 'rightButton')])
                self.assertFalse(self.modal.visible)
                self.stack.close()

    async def test_no_modal_unrelated_or_yes_no_modal_never_gets_a_confirmation_click(self):
        for kind in ('none', 'unrelated', 'yesno', 'preexisting', 'hidden-first',
                     'disabled', 'gray', 'hidden-caption', 'hidden-button-path'):
            with self.subTest(kind=kind):
                self.setUp()
                await self.scenario(kind=kind)
                self.assertEqual(self.clicks, [])
                if kind in ('preexisting', 'hidden-first'): self.assertEqual(self.request_times, [])
                self.stack.close()

    async def test_final_input_context_or_modal_replacement_safely_cancels(self):
        for mutation in ('stop', 'battle', 'caption', 'replacement', 'cancel'):
            with self.subTest(mutation=mutation):
                self.setUp()
                await self.scenario(mutation=mutation)
                self.assertTrue(self.mutated)
                self.assertEqual(self.clicks, [])
                self.stack.close()

    async def test_persistent_modal_has_bounded_clicks_and_never_certifies_transition_complete(self):
        await self.scenario(kind='persistent')
        self.assertEqual(self.clicks, [('p2', 'rightButton')] * 3)
        self.assertTrue(self.modal.visible)
        self.assertFalse(self.p2._quest_follower_probe.get('verified', False))
        self.assertEqual(len(self.request_times), 1)
        self.assertFalse(getattr(self.p2, '_friend_follow_failure', None))
        self.assertTrue(self.p2._friend_follow_busy_backoff['clicked'])
        self.assertFalse(any('friend-failure-modal-cleared' in line for line in self.logs))

    async def test_last_address_read_cannot_confirm_a_replaced_or_changed_modal(self):
        for mutation in ('late-caption', 'late-yesno', 'late-replacement'):
            with self.subTest(mutation=mutation):
                self.setUp()
                await self.scenario(mutation=mutation)
                self.assertTrue(self.mutated)
                self.assertEqual(self.clicks, [])
                self.stack.close()

    async def test_request_error_with_verified_busy_modal_uses_the_same_bounded_cleanup(self):
        for request_error in ('runtime', 'timeout'):
            with self.subTest(request_error=request_error):
                self.setUp()
                await self.scenario(request_error=request_error, retry=True)
                self.assertEqual(self.clicks, [('p2', 'rightButton')])
                self.assertFalse(self.modal.visible)
                self.assertEqual(self.dismissal_owners, ['friend-follow'])
                self.assertGreaterEqual(self.request_times[1] - self.request_times[0], 60)
                self.stack.close()

    async def test_sixty_second_backoff_survives_cleanup_and_real_friend_retry_can_arrive(self):
        await self.scenario(retry=True)
        self.assertEqual(len(self.request_times), 2)
        self.assertGreaterEqual(self.request_times[1] - self.request_times[0], 60)
        self.assertEqual(self.clicks, [('p2', 'rightButton')])
        self.assertFalse(self.p2._quest_follower_probe['pending'])

    async def test_unknown_no_modal_uses_existing_retry_until_real_arrival(self):
        await self.scenario(kind='none', request_error='runtime', retry=True)
        self.assertEqual(len(self.request_times), 2)
        self.assertGreater(self.request_times[1], self.request_times[0])
        self.assertEqual(self.clicks, [])
        self.assertFalse(self.p2._quest_follower_probe['pending'])
        self.assertTrue(self.p2._quest_follower_probe['verified'])
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(self.gear_calls, [])


del Fixture
