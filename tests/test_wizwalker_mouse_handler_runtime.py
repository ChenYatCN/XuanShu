"""The runtime wizwalker file must expose the #77 dispatch callback.

Import the explicit .venv file instead of ``import wizwalker.mouse_handler``:
the repository root also contains a wizwalker package and cwd precedence can
otherwise hide a stale installed copy.
"""
import asyncio
import importlib.util
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".venv" / "Lib" / "site-packages" / "wizwalker" / "mouse_handler.py"


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("xuanshu_runtime_mouse_handler", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TARGET}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeMouseHandlerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_runtime_module()
        cls.MouseHandler = cls.module.MouseHandler

    def handler(self, *, position_error=None, reset_error=None):
        events = []
        handler = object.__new__(self.MouseHandler)
        handler.client = SimpleNamespace(window_handle=1)
        handler.click_lock = None
        handler.click_predelay = 0
        sends = []

        def send(*args):
            sends.append(args)

        async def set_position(x, y):
            events.append(("position", x, y))
            if (x, y) == (-100, -100) and reset_error is not None:
                raise reset_error
            if (x, y) != (-100, -100) and position_error is not None:
                raise position_error

        handler.set_mouse_position = AsyncMock(side_effect=set_position)
        self.module.user32 = SimpleNamespace(
            SendMessageW=Mock(side_effect=send),
            PostMessageW=Mock(side_effect=send),
        )
        return handler, events, sends

    async def test_click_signature_has_keyword_only_on_dispatched(self):
        parameter = inspect.signature(self.MouseHandler.click).parameters["on_dispatched"]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    async def test_click_window_signature_and_forwarding_have_on_dispatched(self):
        parameter = inspect.signature(self.MouseHandler.click_window).parameters["on_dispatched"]
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        handler, events, _ = self.handler()
        seen = []

        async def click(*args, **kwargs):
            seen.append((args, kwargs))

        handler.click = AsyncMock(side_effect=click)
        window = SimpleNamespace(
            scale_to_client=AsyncMock(return_value=SimpleNamespace(center=lambda: (10, 20)))
        )
        await handler.click_window(window, on_dispatched=lambda: None)
        self.assertEqual(seen, [((10, 20), {"on_dispatched": seen[0][1]["on_dispatched"]})])
        self.assertTrue(callable(seen[0][1]["on_dispatched"]))

    async def test_callback_order_is_after_up_before_reset(self):
        handler, events, sends = self.handler()
        callback = lambda: events.append(("callback",))
        window = SimpleNamespace(
            scale_to_client=AsyncMock(return_value=SimpleNamespace(center=lambda: (10, 20)))
        )
        await handler.click_window(window, on_dispatched=callback)
        self.assertEqual([event[0] for event in events],
                         ["position", "callback", "position"])
        self.assertEqual((events[0][1], events[0][2]), (10, 20))
        self.assertEqual((events[2][1], events[2][2]), (-100, -100))
        self.assertEqual([args[1] for args in sends], [0x201, 0x202])

    async def test_pre_dispatch_failure_never_calls_callback(self):
        handler, _, _ = self.handler(position_error=RuntimeError("pre-dispatch"))
        callback = Mock()
        with self.assertRaises(RuntimeError):
            await handler.click(10, 20, on_dispatched=callback)
        callback.assert_not_called()

    async def test_reset_failure_calls_callback_once_and_propagates(self):
        handler, events, _ = self.handler(reset_error=RuntimeError("reset"))
        callback = Mock(side_effect=lambda: events.append(("callback",)))
        with self.assertRaises(RuntimeError):
            await handler.click(10, 20, on_dispatched=callback)
        callback.assert_called_once()
        self.assertEqual([event[0] for event in events], ["position", "callback", "position"])


if __name__ == "__main__":
    unittest.main()
