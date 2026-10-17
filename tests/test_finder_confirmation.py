"""Finder UI acceptance precedes delayed, stable expected-mainline confirmation."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from wizwalker import Keycode, XYZ
from src.automation_ownership import get_client_automation_ownership
from tests import test_mainline_recovery as fixtures


class FinderConfirmationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MainlineRecoveryTests.setUp
    initialize = fixtures.MainlineRecoveryTests.initialize
    runtime = fixtures.MainlineRecoveryTests.runtime

    async def tick(self, seconds):
        self.clock += seconds
        if getattr(self, 'clicked_at', None) is not None:
            while self.scheduled and self.clock-self.clicked_at >= self.scheduled[0][0]:
                _, effect = self.scheduled.pop(0)
                effect()
        await fixtures.REAL_SLEEP(0)

    async def prepare(self, *, accepted='closed', same_ids=False):
        await self.initialize()
        self.client.title = 'p3'
        self.client.tracked = 999 if same_ids else 77
        self.position = XYZ(77, 100, 0)
        self.client.quest_position.position.side_effect = lambda: self.position
        self.client.pages = [['任务搜寻']]
        self.clicked_at, self.scheduled, self.finder_clicks = None, [], 0
        self.logs = []
        recovery = self.runtime()
        sink = logger.add(lambda message: self.logs.append(str(message)), format='{message}')
        self.addCleanup(logger.remove, sink)
        original_nodes = self.client.page_nodes
        def click_finder():
            self.finder_clicks += 1
            self.clicked_at = self.clock
            if accepted == 'closed':
                self.client.book_open = False
            elif accepted == 'rebuilt':
                self.client.pages = [['Quest Finder accepted']]
                deck = fixtures.Window('DeckConfiguration', self.client, children=[self.client.menu])
                self.client.root_window = fixtures.Window('root', self.client,
                    children=[fixtures.Window('WorldView', self.client, children=[deck])])
        def nodes():
            if self.client.pages[self.client.page] != ['任务搜寻']:
                return original_nodes()
            card = fixtures.Window('wndQuestInfo3', self.client, action=click_finder, children=[
                fixtures.Window('txtQuestTitle', self.client, text='任务搜寻'),
                fixtures.Window('txtQuestFinder', self.client, text='任务搜寻')])
            return [card]
        self.client.page_nodes = nodes
        return recovery

    def target_mainline(self, *, same_ids=False):
        if same_ids:
            self.client.quests[999].name_lang_key.return_value = 'Quest_next'
        else:
            self.client.tracked = 43
        self.position = XYZ(430, 200, 0)

    async def start(self, recovery):
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        return await recovery.maybe_recover_mainline(self.client)

    async def after_click(self, elapsed):
        await self.tick(max(0, self.clicked_at+elapsed-self.clock))

    def assert_owned_cleanup(self):
        self.assertFalse(self.client.book_open)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.mouse_handler.active)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_latest_fixture_ui_accepted_then_questdata_delayed_1point6_seconds(self):
        recovery = await self.prepare()
        self.scheduled = [(1.6, self.target_mainline)]
        self.assertTrue(await self.start(recovery))
        await self.after_click(.7)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        self.assertEqual(self.client._xuanshu_mainline_recovery['attempts'], 1)
        await self.after_click(1.8)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 6)
        self.assertEqual(self.finder_clicks, 1)
        self.assertTrue(any('finder-click-accepted' in line for line in self.logs))
        self.assertTrue(any('finder-mainline-matched' in line for line in self.logs))
        self.assert_owned_cleanup()

    async def test_same_quest_and_goal_ids_allow_delayed_target_and_match_update(self):
        recovery = await self.prepare(same_ids=True)
        self.scheduled = [(1.5, lambda: self.target_mainline(same_ids=True))]
        self.assertTrue(await self.start(recovery))
        await self.after_click(1.6)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client.tracked, 999)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 6)

    async def test_ui_acceptance_alone_never_releases_recovery(self):
        recovery = await self.prepare()
        self.assertTrue(await self.start(recovery))
        for elapsed in (.7, 1.8, 4):
            await self.after_click(elapsed)
            self.assertTrue(await recovery.maybe_recover_mainline(self.client))
            self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)
        self.assertEqual(self.finder_clicks, 1)

    async def test_click_that_changes_nothing_waits_then_fails_not_accepted(self):
        recovery = await self.prepare(accepted='unchanged')
        self.assertTrue(await self.start(recovery))
        self.assertGreaterEqual(self.clock-self.clicked_at, 3)
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assertEqual(self.client._xuanshu_mainline_recovery['attempts'], 1)
        self.assertTrue(any('finder-click-not-accepted' in line for line in self.logs))
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)
        self.assert_owned_cleanup()

    async def test_programmatic_q_cleanup_cannot_be_used_as_click_acceptance(self):
        recovery = await self.prepare(accepted='unchanged')
        self.assertTrue(await self.start(recovery))
        self.assertTrue(any('finder-click-not-accepted' in line for line in self.logs))
        self.assertFalse(any('finder-click-accepted' in line for line in self.logs))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')

    async def test_rebuilt_ui_is_read_from_fresh_root_without_preclick_windows(self):
        recovery = await self.prepare(accepted='rebuilt')
        self.scheduled = [(1.5, self.target_mainline)]
        original = self.client.mouse_handler.click_window
        async def click(window, **kwargs):
            await original(window, **kwargs)
            if window.label == 'wndQuestInfo3':
                window.is_visible = AsyncMock(side_effect=AssertionError('pre-click Window reused'))
                window.children = AsyncMock(side_effect=AssertionError('pre-click Window reused'))
        self.client.mouse_handler.click_window = click
        self.assertTrue(await self.start(recovery))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        await self.after_click(1.6)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assert_owned_cleanup()

    async def test_changed_target_with_stable_wrong_mainline_never_releases(self):
        recovery = await self.prepare()
        self.scheduled = [(1.2, lambda: setattr(self, 'position', XYZ(900, 300, 0)))]
        self.assertTrue(await self.start(recovery))
        await self.after_click(1.3)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)
        self.assertTrue(any('finder-stable-non-target' in line for line in self.logs))

    async def test_transient_unmatched_and_loading_wait_until_expected_match(self):
        recovery = await self.prepare()
        def transient():
            self.client.tracked = 0
            self.position = XYZ(0, 0, 0)
        self.scheduled = [(.9, transient), (1.3, lambda: setattr(self.client, 'loading', True)),
                          (1.8, lambda: setattr(self.client, 'loading', False)), (2.2, self.target_mainline)]
        self.assertTrue(await self.start(recovery))
        for elapsed in (1, 1.5, 1.9, 2.3):
            await self.after_click(elapsed)
            self.assertTrue(await recovery.maybe_recover_mainline(self.client))
            self.assertEqual(self.client._xuanshu_mainline_recovery['attempts'], 1)
        await self.tick(1.01)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 6)

    async def test_confirmation_timeout_is_bounded_and_preserves_three_attempt_budget(self):
        recovery = await self.prepare()
        self.assertTrue(await self.start(recovery))
        await self.after_click(95)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assertTrue(any('finder-confirm-timeout' in line for line in self.logs))
        for _ in range(5):
            await self.tick(61)
            self.assertTrue(await recovery.maybe_recover_mainline(self.client))
            await self.tick(95)
            self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.finder_clicks, 3)
        self.assertEqual(self.client._xuanshu_mainline_recovery['attempts'], 3)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)

    async def test_stable_confirmation_includes_goal_target_and_not_only_quest_id(self):
        recovery = await self.prepare()
        self.scheduled = [(1.2, self.target_mainline)]
        self.assertTrue(await self.start(recovery))
        await self.after_click(1.3)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.client.goal_id.return_value = 8
        await self.tick(1.01)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.position = XYZ(440, 220, 0)
        await self.tick(1.01)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))

    async def test_user_stop_pending_cleans_state_without_next_attempt_or_q(self):
        recovery = await self.prepare()
        self.assertTrue(await self.start(recovery))
        keys_before = list(self.client.events)
        self.client.questing_status = False
        recovery.cancel_mainline_recovery(self.client)
        self.assertFalse(await recovery.maybe_recover_mainline(self.client))
        self.assertIsNone(self.client._xuanshu_mainline_recovery)
        self.assertEqual(self.client.events, keys_before)
        self.assertEqual(self.finder_clicks, 1)
        self.assert_owned_cleanup()

    async def test_other_client_state_is_not_changed_while_confirmation_pending(self):
        recovery = await self.prepare()
        other = fixtures.MainlineClient('p4')
        other._xuanshu_mainline_recovery = {'phase': 'other-client-marker'}
        self.assertTrue(await self.start(recovery))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        self.assertEqual(other._xuanshu_mainline_recovery, {'phase': 'other-client-marker'})
        self.assertFalse(other.events)

    async def test_ui_acceptance_does_not_wait_for_task_transients_to_settle(self):
        recovery = await self.prepare()
        self.scheduled = [(n*.1, lambda n=n: setattr(self.client.goal_id, 'return_value', n+7))
                          for n in range(1, 40)]
        self.assertTrue(await self.start(recovery))
        self.assertLess(self.clock-self.clicked_at, 1)
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)

    async def test_loading_during_final_task_read_prevents_success(self):
        recovery = await self.prepare()
        self.scheduled = [(1.2, self.target_mainline)]
        self.assertTrue(await self.start(recovery))
        await self.after_click(1.3)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        reads = 0
        def position():
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.loading = True
            return self.position
        self.client.quest_position.position.side_effect = position
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertIsNotNone(self.client._xuanshu_mainline_recovery)
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)

    async def test_goal_change_during_last_position_read_invalidates_confirmation(self):
        recovery = await self.prepare()
        self.scheduled = [(1.2, self.target_mainline)]
        self.assertTrue(await self.start(recovery))
        await self.after_click(1.3)
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        await self.tick(1.01)
        reads = 0
        def position():
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.goal_id.return_value = 8
            return self.position
        self.client.quest_position.position.side_effect = position
        self.assertTrue(await recovery.maybe_recover_mainline(self.client))
        self.assertEqual(self.client._xuanshu_mainline_last_success['number'], 5)

    async def test_worker_cancel_during_confirmation_read_clears_state_and_owner(self):
        recovery = await self.prepare()
        self.assertTrue(await self.start(recovery))
        entered = asyncio.Event()
        async def position():
            entered.set()
            await asyncio.Event().wait()
        self.client.quest_position.position.side_effect = position
        events = list(self.client.events)
        worker = asyncio.create_task(recovery.maybe_recover_mainline(self.client))
        try:
            await asyncio.wait_for(entered.wait(), .5)
            self.client.questing_status = False
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
            self.assertIsNone(self.client._xuanshu_mainline_recovery)
            self.assertEqual(self.client.events, events)
            self.assertEqual(self.finder_clicks, 1)
            self.assert_owned_cleanup()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_user_stop_during_click_acceptance_wait_cleans_book_once(self):
        recovery = await self.prepare(accepted='unchanged')
        entered = asyncio.Event()
        original = recovery.sleep
        async def waiting(seconds):
            if self.clicked_at is not None:
                entered.set()
                await asyncio.Event().wait()
            await original(seconds)
        with patch.object(recovery, 'sleep', waiting):
            worker = asyncio.create_task(self.start(recovery))
            try:
                await asyncio.wait_for(entered.wait(), .5)
                self.client.questing_status = False
                recovery.cancel_mainline_recovery(self.client)
                self.assertFalse(await asyncio.wait_for(worker, .5))
                self.assertIsNone(self.client._xuanshu_mainline_recovery)
                self.assertEqual(self.finder_clicks, 1)
                self.assertEqual(sum(event == ('key', Keycode.Q) for event in self.client.events), 2)
                self.assert_owned_cleanup()
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_transient_text_pointer_error_during_rebuild_keeps_click_pending(self):
        recovery = await self.prepare(accepted='unchanged')
        original_nodes = self.client.page_nodes
        def nodes():
            cards = original_nodes()
            if self.clicked_at is not None:
                cards[0].nodes[0].text = 'Finder accepted'
            return cards
        self.client.page_nodes = nodes
        original = recovery.read_control_text
        transient_seen = False
        async def read(window):
            nonlocal transient_seen
            if self.clicked_at is not None and not transient_seen:
                transient_seen = True
                raise RuntimeError('控件文本指针无效')
            return await original(window)
        with patch.object(recovery, 'read_control_text', read):
            self.assertTrue(await self.start(recovery))
        self.assertTrue(transient_seen)
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'finder-confirm')
        self.assertTrue(any('finder-click-accepted' in line for line in self.logs))
        self.assertFalse(any('finder-click-not-accepted' in line for line in self.logs))

    async def test_suppressed_children_error_is_not_evidence_that_book_disappeared(self):
        recovery = await self.prepare(accepted='unchanged')
        world_view = self.client.root_window.nodes[0]
        original = world_view.children
        world_view.read_value_from_offset = AsyncMock(side_effect=lambda offset, _kind:
            0x10000 if offset == 112 else 0x10010)
        async def children():
            if self.clicked_at is not None and self.clock-self.clicked_at < .4:
                return []  # Simulate Window.children suppressing a vector read failure.
            return await original()
        world_view.children = children
        self.assertTrue(await self.start(recovery))
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assertTrue(any('finder-click-not-accepted' in line for line in self.logs))
        self.assertFalse(any('finder-click-accepted' in line for line in self.logs))
        self.assert_owned_cleanup()
