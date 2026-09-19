import unittest
from types import SimpleNamespace
from src.gui.widgets import PyQtSink


class ConsoleLogFormatTests(unittest.TestCase):
    def test_full_body_retained(self):
        sink = PyQtSink(None)
        for body in ('Client p2 - post-combat A/D movement completed.\n',
                     'target (-12, -20, 3) - failed | retry\n',
                     'failure\nTraceback:\n detail - more\nError: bad-value\n',
                     '掉落：测试坐骑 - 永久\n'):
            raw = '2026-09-18 01:16:46.051 | DEBUG | src.test:run:12 - ' + body
            sink.write(raw)
            self.assertEqual(sink.buffer[-1], (raw, 'DEBUG - ' + body, 'DEBUG'))

    def test_unformatted_body_retained(self):
        sink = PyQtSink(None)
        sink.write('one-two-three-four-five\n')
        self.assertEqual(sink.buffer[-1][1], 'DEBUG - one-two-three-four-five\n')

    def test_custom_format_record(self):
        class Message(str):
            pass
        message = Message('custom format')
        message.record = {'level': SimpleNamespace(name='INFO'), 'message': 'Client p2 - ready'}
        sink = PyQtSink(None)
        sink.write(message)
        self.assertEqual(sink.buffer[-1][1], 'INFO - Client p2 - ready\n')

    def test_ansi(self):
        sink = PyQtSink(None)
        sink.write('\x1b[1;32m2026-09-18 01:16:46 | INFO | app:run:1 - a-b-c\x1b[0m\n')
        self.assertEqual(sink.buffer[-1][1], 'INFO - a-b-c\n')
