import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import HookAlreadyActivated, HookNotReady
from src.dance_game_hook import attempt_activate_dance_hook, read_current_dance_game_moves


class DanceHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_failure_is_not_recorded_as_success(self):
        client = SimpleNamespace(title="p2", dance_hook_status=True, hook_handler=AsyncMock())
        client.hook_handler.activate_dance_game_moves_hook.side_effect = RuntimeError("pattern missing")
        with patch("src.dance_game_hook.logger"):
            with self.assertRaisesRegex(RuntimeError, "pattern missing"):
                await attempt_activate_dance_hook(client, sleep_time=0)
        self.assertFalse(client.dance_hook_status)

    async def test_already_active_is_compatible(self):
        client = SimpleNamespace(title="p2", dance_hook_status=False, hook_handler=AsyncMock())
        client.hook_handler.activate_dance_game_moves_hook.side_effect = HookAlreadyActivated("DanceGameMovesHook")
        await attempt_activate_dance_hook(client, sleep_time=0)
        self.assertTrue(client.dance_hook_status)

    async def test_success_marks_active(self):
        client = SimpleNamespace(title="p2", dance_hook_status=False, hook_handler=AsyncMock())
        await attempt_activate_dance_hook(client, sleep_time=0)
        self.assertTrue(client.dance_hook_status)

    async def test_only_valid_moves_are_returned(self):
        handler = SimpleNamespace(_base_addrs={"dance_game_moves": 123}, read_bytes=AsyncMock())
        handler.read_bytes.return_value = b"abcd\0\0\0\0"
        self.assertEqual(await read_current_dance_game_moves(handler), "WDSA")
        for value in (b"\0" * 8, b"invalid\0", b"abcdefgh", b"\xff\0"):
            handler.read_bytes.return_value = value
            with self.assertRaises(HookNotReady):
                await read_current_dance_game_moves(handler)
