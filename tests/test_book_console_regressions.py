"""Focused regressions for quest-card reads and daily/detail console caches."""
import asyncio
import os
import threading
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
import test_mainline_recovery as fixtures
import test_mainline_ui_reads as ui_fixtures
from src.mainline_progress import log_mainline_progress
from src.gui.widgets import ConsoleTextEdit, PyQtSink


class BookCardTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MainlineRecoveryTests.setUp
    initialize = fixtures.MainlineRecoveryTests.initialize
    tick = fixtures.MainlineRecoveryTests.tick
    runtime = fixtures.MainlineRecoveryTests.runtime
    recover = fixtures.MainlineRecoveryTests.recover
    prepare = ui_fixtures.MainlineUIReadTests.prepare
    assert_clean = ui_fixtures.MainlineUIReadTests.assert_clean

    def extra_slot(self, children):
        original = self.client.page_nodes
        slot = fixtures.Window('wndQuestInfo2', self.client, children=children)
        self.client.page_nodes = lambda: original() + [slot]
        return slot

    async def test_explicit_hidden_content_slot_is_excluded_from_complete_page(self):
        recovery = await self.prepare()
        hidden = fixtures.Window('questInfoWindow', self.client)
        hidden.is_visible = AsyncMock(return_value=False)
        self.extra_slot([hidden])
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 42)
        self.assertGreaterEqual(hidden.is_visible.await_count, 4)
        self.assertTrue(any('空槽' in str(message) for message in self.logs))
        self.assert_clean()

    async def test_visible_title_less_slot_is_not_assumed_empty(self):
        await self.prepare()
        self.extra_slot([])
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertNotIn(('click', 'txtGoal'), self.client.events)
        self.assertIn('未发现可见标题字段', self.client._xuanshu_mainline_book_read_error)
        self.assertEqual(self.client._xuanshu_mainline_recovery['phase'], 'cooldown')
        self.assert_clean()

    async def test_visible_empty_title_with_hidden_body_still_fails(self):
        await self.prepare()
        hidden = fixtures.Window('questInfoWindow', self.client)
        hidden.is_visible = AsyncMock(return_value=False)
        self.extra_slot([hidden, fixtures.Window('txtQuestTitle', self.client, text='')])
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertIn('可见标题字段为空', self.client._xuanshu_mainline_book_read_error)
        self.assert_clean()

    async def test_hidden_body_becoming_visible_invalidates_scan(self):
        await self.prepare()
        hidden = fixtures.Window('questInfoWindow', self.client)
        calls = 0
        async def visibility():
            nonlocal calls
            calls += 1
            return calls % 2 == 0
        hidden.is_visible = visibility
        self.extra_slot([hidden])
        await self.recover(times=2)
        self.assertEqual(self.client.tracked, 77)
        self.assertIn('可见状态变化', self.client._xuanshu_mainline_book_read_error)
        self.assert_clean()

    async def test_persistent_read_failure_status_differs_from_readable_unmatched(self):
        recovery = await self.prepare()
        last = dict(self.client._xuanshu_mainline_last_success)
        await log_mainline_progress(self.client)
        self.assertIn('当前主线未匹配', self.client._xuanshu_mainline_progress)
        original = self.client.menu.children
        failures = 0
        async def children():
            nonlocal failures
            if ('all',) in self.client.events:
                failures += 1
                raise RuntimeError('控件文本结构无效：bad')
            return await original()
        self.client.menu.children = children
        await self.recover(times=2)
        self.assertEqual(failures, 5)
        await log_mainline_progress(self.client)
        self.assertIn('任务簿信息读取失败', self.client._xuanshu_mainline_progress)
        self.assertEqual(self.client._xuanshu_mainline_last_success, last)
        failure_logs = [message for message in self.logs if '当前主线：任务簿信息读取失败' in str(message)]
        await log_mainline_progress(self.client)
        self.assertEqual(len(failure_logs), 1)
        state = self.client._xuanshu_mainline_recovery
        self.assertEqual(state['retry_at'], self.clock + 60)
        self.client._xuanshu_mainline_recovery = None
        await log_mainline_progress(self.client)
        self.assertIn('当前主线未匹配', self.client._xuanshu_mainline_progress)
        self.assert_clean()

    async def test_final_outer_attempt_preserves_inner_stage_and_warning(self):
        recovery = await self.prepare()
        self.extra_slot([])
        await self.recover(times=2)
        for _ in range(2):
            self.clock += 61
            await self.recover(times=2)
        warnings = [m for m in self.logs if m.record['level'].name == 'WARNING']
        self.assertEqual(len(warnings), 1)
        for value in ('card-title', 'wndQuestInfo2', '3/3', '5/5', '未发现可见标题字段'):
            self.assertIn(value, str(warnings[0]))
        self.assertEqual(self.client.tracked, 77)
        self.assert_clean()

    async def test_readable_unmatched_cards_do_not_report_read_failure(self):
        await self.prepare()
        self.client.pages = [['木材木材']]
        await self.recover(times=2)
        await log_mainline_progress(self.client)
        self.assertFalse(self.client._xuanshu_mainline_book_read_error)
        self.assertIn('当前主线未匹配', self.client._xuanshu_mainline_progress)
        self.assert_clean()


def raw(level, body):
    return f'2026-10-14 00:00:00 | {level} | app:run:1 - {body}\n'


class ConsoleCacheTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.arg = patch('src.gui.widgets.Q_ARG', lambda _, value: value)
        self.invoke = patch('src.gui.widgets.QMetaObject.invokeMethod', lambda *args: self.posts.append(args[1:]))
        self.arg.start()
        self.invoke.start()
        self.addCleanup(self.arg.stop)
        self.addCleanup(self.invoke.stop)
        self.sink = PyQtSink(None)

    def test_debug_flood_does_not_evict_daily_warning_and_copy_keeps_detail(self):
        self.sink.write(raw('WARNING', 'final failure'))
        for number in range(1001):
            self.sink.write(raw('DEBUG', f'detail {number}'))
        self.assertEqual(len(self.sink.buffer), 1000)
        self.assertEqual(len(self.sink.daily_buffer), 1)
        self.sink.toggle_show_expanded_logs(True)
        self.assertIn('detail 1000', self.posts[-1][-1])
        self.sink.toggle_show_expanded_logs(False)
        self.assertEqual(self.posts[-1][-1], 'WARNING - final failure\n')
        with patch('src.gui.widgets.pyperclip.copy') as copy:
            self.sink.copy()
        self.assertEqual(copy.call_args.args[0], '```\n' + ''.join(line for line, _, _ in self.sink.buffer) + '```')
        self.assertNotIn('final failure', copy.call_args.args[0])

    def test_daily_cache_is_independently_bounded(self):
        for number in range(1002):
            self.sink.write(raw('INFO', f'daily {number}'))
        self.assertEqual(len(self.sink.daily_buffer), 1000)
        self.assertEqual(self.sink.daily_buffer[0][1], 'INFO - daily 2\n')

    def test_empty_modes_and_first_message_replace_placeholder(self):
        self.assertIn('暂无日常日志', self.posts[-1][-1])
        self.sink.toggle_show_expanded_logs(True)
        self.assertEqual(self.posts[-1][-1], '暂无详细日志')
        self.sink.write(raw('DEBUG', 'first'))
        self.assertEqual(self.posts[-1][0], '_set_log')
        self.assertNotIn('暂无', self.posts[-1][-1])
        self.sink.toggle_show_expanded_logs(False)
        self.assertIn('暂无日常日志', self.posts[-1][-1])
        self.sink.write(raw('INFO', 'daily'))
        self.assertEqual(self.posts[-1][-1], 'INFO - daily\n')

    def test_concurrent_append_is_posted_before_mode_snapshot(self):
        self.sink.write(raw('INFO', 'initial'))
        entered, release, toggled = threading.Event(), threading.Event(), threading.Event()
        def post(*args):
            if args[1] == '_append_log':
                entered.set()
                self.assertTrue(release.wait(2))
            self.posts.append(args[1:])
        def toggle():
            toggled.set()
            self.sink.toggle_show_expanded_logs(True)
        with patch('src.gui.widgets.QMetaObject.invokeMethod', post):
            writer = threading.Thread(target=self.sink.write, args=(raw('INFO', 'concurrent'),))
            writer.start()
            self.assertTrue(entered.wait(2))
            changer = threading.Thread(target=toggle)
            changer.start()
            self.assertTrue(toggled.wait(2))
            release.set()
            writer.join(2)
            changer.join(2)
            self.assertFalse(writer.is_alive())
            self.assertFalse(changer.is_alive())
        self.assertEqual([p[0] for p in self.posts[-2:]], ['_append_log', '_set_log'])
        self.assertIn(raw('INFO', 'concurrent'), self.posts[-1][-1])


class ConsoleQtQueueTests(unittest.TestCase):
    def test_real_queued_slots_keep_daily_text_after_worker_flood_and_toggle(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        widget = ConsoleTextEdit()
        sink = PyQtSink(widget)
        app.processEvents()
        self.assertIn('暂无日常日志', widget.toPlainText())
        def write():
            sink.write(raw('WARNING', 'kept warning'))
            for n in range(1001):
                sink.write(raw('DEBUG', f'detail {n}'))
        thread = threading.Thread(target=write)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        sink.toggle_show_expanded_logs(True)
        sink.toggle_show_expanded_logs(False)
        app.processEvents()
        self.assertEqual(widget.toPlainText(), 'WARNING - kept warning')
        sink.write(raw('ERROR', 'new error'))
        app.processEvents()
        self.assertEqual(widget.toPlainText(), 'WARNING - kept warning\nERROR - new error')
        widget.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()
