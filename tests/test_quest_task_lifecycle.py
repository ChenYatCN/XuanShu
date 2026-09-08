import asyncio
import ast
from pathlib import Path
import unittest
from src.task_lifecycle import gather_owned


class QuestTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_drains_sibling_before_retry(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def mover():
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                stopped.set()
        async def fail():
            await started.wait()
            raise RuntimeError('memory read failed')
        with self.assertRaises(RuntimeError):
            await gather_owned(mover(), fail())
        self.assertTrue(stopped.is_set())

    async def test_stop_drains_nested_workers(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def mover():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        task = asyncio.create_task(gather_owned(gather_owned(mover())))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_queued_quest_loop_cannot_reenable_after_stop(self):
        tree = ast.parse(Path('DeimosCN.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'questing_loop')
        namespace = {'questing_status': False}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'DeimosCN.py', 'exec'), namespace)
        await namespace['questing_loop']()
