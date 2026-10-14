import unittest
from types import SimpleNamespace
from unittest.mock import patch
from loguru import logger
from src.logging_utils import log_blocked, log_recovered, log_exception, log_toggle, daily_format, detailed_format
import asyncio
from unittest.mock import Mock


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.sink = logger.add(lambda message: self.records.append(message.record))
        self.addCleanup(logger.remove, self.sink)
        self.now = 0
        self.timer = patch('src.logging_utils.monotonic', lambda: self.now)
        self.timer.start(); self.addCleanup(self.timer.stop)
        self.a, self.b = SimpleNamespace(title='p1'), SimpleNamespace(title='p2')
        self.task = (12, 34, (0, 0, 0), 'zone')

    def warnings(self):
        return [r for r in self.records if r['level'].name == 'WARNING']

    def test_first_warning_and_exact_30_second_summary(self):
        log_blocked(self.a, self.task, '补走受阻', delay=10)
        self.assertEqual(len(self.warnings()), 1)
        self.now = 29
        log_blocked(self.a, self.task, '补走受阻')
        self.assertEqual(len(self.warnings()), 1)
        self.now = 30
        log_blocked(self.a, self.task, '补走受阻')
        self.assertIn('重复 2 次', self.warnings()[-1]['message'])

    def test_clients_tasks_and_reasons_are_independent(self):
        log_blocked(self.a, self.task, 'A')
        log_blocked(self.b, self.task, 'A')
        log_blocked(self.a, self.task, 'B')
        log_blocked(self.a, (12, 35, None, 'zone'), 'B')
        self.assertEqual([r['extra']['client'] for r in self.warnings()], ['p1','p2','p1','p1'])

    def test_recovery_once_then_new_block_is_immediate(self):
        log_blocked(self.a, self.task, 'A')
        log_recovered(self.a, '已确认状态推进')
        log_recovered(self.a, '已确认状态推进')
        self.assertEqual(sum(r['level'].name == 'INFO' for r in self.records), 1)
        log_blocked(self.a, self.task, 'A')
        self.assertEqual(len(self.warnings()), 2)

    def test_unreadable_snapshot_and_xyz_jitter_do_not_reset_summary(self):
        log_blocked(self.a, self.task, 'A')
        log_blocked(self.a, (12,34,(.2,0,0),'zone'), 'A')
        log_blocked(self.a, None, 'A')
        self.assertEqual(len(self.warnings()), 1)

    def test_same_exception_one_trace_distinct_objects_two_traces(self):
        for _ in range(2):
            try:
                raise ValueError('same reason')
            except ValueError as exc:
                log_exception(self.a, 'inner', exc, level='DEBUG')
                log_exception(self.a, 'outer', exc)
        traces = [r for r in self.records if r['exception']]
        self.assertEqual(len(traces), 2)
        self.assertTrue(all(r['level'].name == 'DEBUG' for r in traces))
        self.assertTrue(all(r['exception'].traceback for r in traces))

    def test_daily_sink_omits_trace_and_internal_header(self):
        messages = []
        sink = logger.add(messages.append, format=daily_format, level='INFO')
        try:
            log_toggle([self.a,self.b], False, '界面按钮')
            try:
                raise RuntimeError('bad')
            except RuntimeError as exc:
                log_exception(self.a, '任务失败', exc)
        finally:
            logger.remove(sink)
        self.assertIn('[信息] [p1,p2] 自动任务已停止，来源：界面按钮', str(messages[0]))
        self.assertNotIn('Traceback', ''.join(messages))
        self.assertNotIn('logging_utils:', ''.join(messages))

    def test_gui_default_hides_debug_and_expanded_keeps_trace(self):
        from src.gui.widgets import PyQtSink
        sink = PyQtSink(None)
        with patch('src.gui.widgets.QMetaObject.invokeMethod') as invoke:
            handle = logger.add(sink.write, format=detailed_format)
            try:
                log_toggle([self.a], True, '界面按钮')
                log_exception(self.a, '读取失败', ValueError('bad'), level='DEBUG')
            finally:
                logger.remove(handle)
            self.assertEqual(invoke.call_count, 1)
            self.assertIn('[p1]', sink.buffer[0][1])
            self.assertNotIn('logging_utils', sink.buffer[0][1])
            self.assertEqual(sink.buffer[-1][2], 'DEBUG')
            self.assertIn('ValueError: bad', sink.buffer[-1][0])
            self.assertIn('[p1]', sink.buffer[-1][0])
            sink.toggle_show_expanded_logs(True)
            self.assertEqual(invoke.call_count, 2)
            sink.write('2026-10-14 00:00:00 | DEBUG | app:run:1 - detail\n')
            self.assertEqual(invoke.call_count, 3)
            sink.toggle_show_expanded_logs(False)
            self.assertEqual(invoke.call_count, 4)

    def test_mainline_final_failure_keeps_stage_reason_attempts(self):
        from src.mainline_recovery import failed
        for attempts in (1, 3):
            exc = ValueError('卡片不可用')
            exc._xuanshu_log_stage = 'card-read'
            state = dict(phase='book', attempts=attempts, confirmation='old')
            with patch('src.mainline_recovery.monotonic', return_value=10):
                failed(self.a, state, str(exc), exc=exc)
            self.assertEqual(state, dict(phase='cooldown', attempts=attempts,
                confirmation=None, retry_at=70))
        self.assertEqual(len(self.warnings()), 1)
        final = self.warnings()[0]
        self.assertEqual(final['extra']['client'], 'p1')
        for text in ('card-read', '卡片不可用', '3/3'):
            self.assertIn(text, final['message'])

    def test_toggle_source_is_transported_to_tasks_without_game_reads(self):
        from tests.test_scoped_runtime import load_function
        old, new = SimpleNamespace(done=lambda: False), SimpleNamespace()
        namespace = dict(hotkey_groups=SimpleNamespace(groups={
            ('toggle_questing', 1): ([self.a], old)}), questing_task=None,
            logger=Mock(), listener=object(), hotkey_status=True,
            questing_status=False, walker=SimpleNamespace(clients=[self.a]))
        log = load_function('_log_quest_toggle', namespace)
        namespace['_log_quest_toggle'] = log
        log('toggle_begin', {'source': 'ui'})
        self.assertEqual(old._logging_stop_source, '界面按钮')
        new.done = lambda: False
        namespace['hotkey_groups'].groups[('toggle_questing', 2)] = ([self.b], new)
        log('toggle_end', {'source': 'ui'})
        self.assertEqual(new._logging_start_source, '界面按钮')


class NestedExceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_repeated_errors_are_summarized_without_changing_retry_delays(self):
        import wizwalker
        from tests.test_scoped_runtime import load_function
        from unittest.mock import AsyncMock
        client = SimpleNamespace(title='p1', questing_status=True)
        errors = [RuntimeError('same cause'), RuntimeError('same cause')]
        async def worker(_client):
            if errors:
                raise errors.pop(0)
            client.questing_status = False
        sleep = AsyncMock()
        api = SimpleNamespace(sleep=sleep, create_task=asyncio.create_task,
            CancelledError=asyncio.CancelledError, current_task=asyncio.current_task)
        run = load_function('async_questing', dict(Client=object, asyncio=api,
            walker=SimpleNamespace(clients=[client]), members=[client],
            questing_status=True, run_questing_worker=worker, wizwalker=wizwalker, logger=Mock()))
        records = []
        handle = logger.add(lambda message: records.append(message.record))
        try:
            with patch('src.logging_utils.monotonic', return_value=0):
                await run(client)
        finally:
            logger.remove(handle)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [3.0, 2.0, 2.0])
        self.assertIsNone(client.quest_party_quest_worker_task)
        self.assertEqual(sum(r['level'].name == 'WARNING' for r in records), 1)
        self.assertEqual(sum(bool(r['exception']) for r in records), 2)

    async def test_ui_inner_and_outer_keep_one_trace_and_propagate_same_error(self):
        from src.mainline_recovery import ui_read, failed
        records = []
        handle = logger.add(lambda message: records.append(message.record))
        client = SimpleNamespace(title='p1')
        exc = RuntimeError('control invalid')
        try:
            try:
                async with ui_read(client, 'children', ('root', 'window')):
                    raise exc
            except RuntimeError as caught:
                self.assertIs(caught, exc)
                failed(client, dict(phase='book', attempts=3), str(caught), exc=caught)
        finally:
            logger.remove(handle)
        traces = [r for r in records if r['exception']]
        self.assertEqual(len(traces), 1)
        import traceback
        self.assertIs(traces[0]['exception'].value, exc)
        self.assertIn('test_ui_inner_and_outer_keep_one_trace_and_propagate_same_error',
            [frame.name for frame in traceback.extract_tb(traces[0]['exception'].traceback)])
        self.assertIn('root', traces[0]['message'])
        self.assertIn('阶段=children', next(r['message'] for r in records if r['level'].name == 'WARNING'))
