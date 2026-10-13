"""Whole quest-page scans must survive transient reads without partial choices."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from wizwalker import Keycode
from wizwalker.errors import AddressOutOfRange
from src.automation_ownership import get_client_automation_ownership
import test_mainline_recovery as fixtures


class MainlineUIReadTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MainlineRecoveryTests.setUp
    initialize = fixtures.MainlineRecoveryTests.initialize
    tick = fixtures.MainlineRecoveryTests.tick
    runtime = fixtures.MainlineRecoveryTests.runtime
    recover = fixtures.MainlineRecoveryTests.recover

    async def prepare(self):
        await self.initialize()
        self.client.tracked = 77
        recovery = self.runtime()
        self.logs = []
        sink = logger.add(lambda message: self.logs.append(message), format='{message}')
        self.addCleanup(logger.remove, sink)
        return recovery

    def assert_clean(self):
        self.assertFalse(self.client.book_open)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.mouse_handler.active)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_first_children_failure_reacquires_root_and_selects_previous(self):
        recovery = await self.prepare()
        original = self.client.menu.children
        calls = 0
        async def children():
            nonlocal calls
            if ('all',) in self.client.events:
                calls += 1
                if calls == 1:
                    raise AddressOutOfRange(0)
            return await original()
        self.client.menu.children = children
        root_reads = []
        original_root = recovery.get_window_from_path
        async def root(window, path):
            if path == list(recovery.quest_buttons_parent_path[:-1]):
                root_reads.append(window)
            return await original_root(window, path)
        with patch.object(recovery, 'get_window_from_path', root):
            await self.recover(times=2)
        self.assertEqual(self.client.tracked, 42)
        self.assertGreaterEqual(len(root_reads), 3)
        self.assertTrue(any('children' in str(m) and m.record['exception'] for m in self.logs))
        self.assert_clean()

    async def test_mid_scan_title_failure_discards_previously_matched_card(self):
        recovery = await self.prepare()
        self.client.pages = [['原主线', 'Broken Title']]
        failed = False
        async def read(window):
            nonlocal failed
            if window.label == 'txtQuestTitle' and window.text == 'Broken Title' and not failed:
                failed = True
                self.client.pages = [['任务搜寻']]
                raise ValueError('Array length must be >= 0, not -4098234384')
            return await window.maybe_text()
        with patch.object(recovery, 'read_control_text', read):
            await self.recover(times=2)
        self.assertTrue(failed)
        self.assertEqual(self.client.tracked, 999, 'Never select a card from the failed scan')
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'navigation')
        self.assertTrue(any('txtQuestTitle' in str(m) and m.record['exception'] for m in self.logs))
        self.assert_clean()

    async def test_failed_read_hidden_as_empty_children_is_not_a_complete_scan(self):
        await self.prepare()
        self.client.pages = [['原主线', '下一主线']]
        original = self.client.menu.children
        self.client.menu.read_value_from_offset = AsyncMock(
            side_effect=lambda offset, _kind: 0x10000 if offset == 112 else 0x10000 + 48)
        calls = 0
        async def children():
            nonlocal calls
            if ('all',) in self.client.events:
                calls += 1
                if calls == 1:
                    return []  # Native children() suppresses some vector read errors this way.
            return await original()
        self.client.menu.children = children
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 42)
        self.assertGreaterEqual(calls, 3)
        self.assert_clean()

    async def test_negative_vector_header_does_not_allow_partial_card_selection(self):
        await self.prepare()
        self.client.pages = [['原主线', '下一主线']]
        # The library itself turns a negative shared-vector size into [].
        self.client.menu.read_value_from_offset = AsyncMock(
            side_effect=lambda offset, _kind: 0x10000 if offset == 112 else 0xFFF0)
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assertNotIn(('click', 'txtGoal'), self.client.events)
        self.assert_clean()

    async def test_persistent_children_failure_is_bounded_and_reports_stage_and_attempts(self):
        await self.prepare()
        original = self.client.menu.children
        attempts = 0
        async def children():
            nonlocal attempts
            if ('all',) in self.client.events:
                attempts += 1
                raise ValueError('Array length must be >= 0, not -16')
            return await original()
        self.client.menu.children = children
        await self.recover(times=2)
        self.assertEqual(attempts, 5)
        self.assertEqual(self.client.tracked, 77)
        self.assertNotIn(('click', 'btnPrevPage'), self.client.events)
        self.assertNotIn(('click', 'txtGoal'), self.client.events)
        self.assertTrue(any('5/5' in str(m) and 'children' in str(m) for m in self.logs))
        self.assertEqual(sum(e == ('key', Keycode.Q) for e in self.client.events), 2)
        self.assert_clean()

    async def test_two_complete_matching_scans_required_before_using_page(self):
        await self.prepare()
        self.client.pages = [['下一主线']]
        original = self.client.menu.children
        calls = 0
        async def children():
            nonlocal calls
            if ('all',) in self.client.events:
                calls += 1
                if calls == 2:
                    self.client.pages = [['原主线']]
            return await original()
        self.client.menu.children = children
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 42)
        self.assertGreaterEqual(calls, 3)
        self.assertGreaterEqual(sum(e == ('read_title', '原主线') for e in self.client.events), 2)
        self.assert_clean()

    async def test_transient_failure_after_page_turn_retries_same_page_for_finder(self):
        recovery = await self.prepare()
        self.client.pages = [['Other Mainline'], ['任务搜寻']]
        original = self.client.menu.children
        failed = False
        async def children():
            nonlocal failed
            if self.client.page == 1 and not failed:
                failed = True
                raise AddressOutOfRange(0)
            return await original()
        self.client.menu.children = children
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 999)
        self.assertEqual(sum(e == ('click', 'btnPrevPage') for e in self.client.events), 1)
        self.assertEqual(sum(e == ('key', Keycode.Q) for e in self.client.events), 2)
        for _ in range(3):
            self.assertFalse(await self.recover(times=1))
        self.assertEqual(sum(e == ('key', Keycode.Q) for e in self.client.events), 2)
        self.assert_clean()

    async def test_unrelated_value_error_is_not_retried_as_transient_memory(self):
        await self.prepare()
        self.client.menu.children = AsyncMock(side_effect=ValueError('business rule failure'))
        await self.recover(times=2)
        self.assertEqual(self.client.menu.children.await_count, 1)
        self.assertEqual(self.client.tracked, 77)
        self.assert_clean()

    async def test_title_and_page_container_windows_are_not_read_as_text_controls(self):
        await self.prepare()
        original = self.client.page_nodes
        panel = fixtures.Window('QuestTitlePane', self.client)
        page_panel = fixtures.Window('QuestPagePanel', self.client)
        for window in (panel, page_panel):
            window.read_type_name = AsyncMock(return_value='Window')
            window.maybe_text = AsyncMock(side_effect=RuntimeError('控件文本结构无效：container'))
        def nodes():
            cards = original()
            cards[0].nodes[0].nodes[0].nodes.append(panel)
            return cards
        self.client.page_nodes = nodes
        self.client.menu.nodes.append(page_panel)
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 42)
        panel.maybe_text.assert_not_awaited()
        page_panel.maybe_text.assert_not_awaited()
        self.assert_clean()

    async def test_cancel_during_page_retry_closes_book_and_drains_only_book_task(self):
        recovery = await self.prepare()
        original = self.client.menu.children
        async def children():
            if ('all',) in self.client.events:
                raise AddressOutOfRange(0)
            return await original()
        self.client.menu.children = children
        waiting = asyncio.Event()
        async def wait(seconds):
            if seconds == .15:
                waiting.set()
                await asyncio.Future()
            await self.tick(seconds)
        await self.recover(times=1)
        with patch.object(recovery, 'sleep', wait):
            worker = asyncio.create_task(self.recover(times=1))
            try:
                await asyncio.wait_for(waiting.wait(), 1)
                self.client.mainline_finder_enabled = False
                recovery.cancel_mainline_recovery(self.client)
                self.assertFalse(await asyncio.wait_for(worker, 1))
                self.assertIsNone(self.client._xuanshu_mainline_book_task)
                self.assertTrue(self.client.questing_status)
                self.assert_clean()
                self.assertFalse(await self.recover(times=1))
                self.assertEqual(sum(e == ('key', Keycode.Q) for e in self.client.events), 2)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
