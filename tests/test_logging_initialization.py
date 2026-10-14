"""Exercise the actual application run() with real public Loguru sinks, no GUI/backend."""
import ast
import io
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from loguru import logger
from src.logging_utils import daily_format, detailed_format


class LoggingInitializationTests(unittest.TestCase):
    def setUp(self):
        logger.remove()
        self.addCleanup(logger.remove)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stderr = io.StringIO()
        self.gui_outputs = []
        self.starts = []
        self.phase = 0
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        def thread(**kwargs):
            return SimpleNamespace(start=lambda: self.starts.append(kwargs))
        def gui(*args, **kwargs):
            self.phase += 1
            output = []
            self.gui_outputs.append(output)
            logger.add(output.append, format=detailed_format)
            bound = logger.bind(client='p3')
            bound.debug('detail-{}', self.phase)
            bound.info('daily-{}', self.phase)
            logger.complete()
        self.namespace = dict(logger=logger, runtime_data_dir=lambda: self.root,
            sys=SimpleNamespace(stderr=self.stderr), daily_format=daily_format,
            detailed_format=detailed_format, tool_name='test-app',
            generate_timestamp=lambda: str(self.phase + 1), queue=queue,
            threading=SimpleNamespace(Thread=thread), asyncio=Mock(), main=Mock(),
            xuanshu_gui=SimpleNamespace(manage_gui=gui), theme_dict={}, tool_version='test',
            gui_on_top=False, gui_langcode='zh', gui_font='test', gui_font_size=12,
            tool_author='test', settings=Mock())
        exec(compile(ast.Module(body=[run], type_ignores=[]), 'XuanShu.py', 'exec'), self.namespace)
        self.run = self.namespace['run']

    def file(self, phase):
        return (self.root / 'logs' / f'test-app - {phase}.log').read_text(encoding='utf-8')

    def test_empty_handlers_initializes_daily_and_detailed_outputs(self):
        self.run()
        output = self.stderr.getvalue()
        self.assertIn('[信息] [p3] daily-1', output)
        self.assertNotIn('detail-1', output)
        detailed = self.file(1)
        self.assertIn('detail-1', detailed)
        self.assertIn('daily-1', detailed)
        self.assertIn('[p3]', detailed)
        self.assertIn('test_logging_initialization:gui:', detailed)
        self.assertEqual(len(self.gui_outputs[0]), 2)
        self.namespace['main'].assert_not_called()

    def test_windowed_without_stderr_keeps_file_and_gui(self):
        self.namespace['sys'].stderr = None
        self.run()
        self.assertEqual(self.stderr.getvalue(), '')
        self.assertIn('daily-1', self.file(1))
        self.assertIn('detail-1', self.file(1))
        self.assertEqual(len(self.gui_outputs[0]), 2)

    def test_sequential_reinitialization_replaces_sinks_without_duplicates(self):
        old = []
        logger.add(old.append)
        self.run()
        first = self.file(1)
        self.run()
        self.assertEqual(old, [])
        self.assertEqual(self.stderr.getvalue().count('daily-1'), 1)
        self.assertEqual(self.stderr.getvalue().count('daily-2'), 1)
        self.assertEqual(self.file(1), first)
        self.assertEqual(self.file(2).count('daily-2'), 1)
        self.assertEqual(len(self.gui_outputs[0]), 2)
        self.assertEqual(len(self.gui_outputs[1]), 2)
        self.assertTrue(all('daily-2' not in text for text in self.gui_outputs[0]))
        self.assertEqual(len(self.starts), 2)
        self.namespace['main'].assert_not_called()
