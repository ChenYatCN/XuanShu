import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import HookAlreadyActivated, HookNotReady
from src.dance_game_hook import DanceGameMovesHook, attempt_activate_dance_hook, read_current_dance_game_moves


class DanceHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_signature_and_replay_instructions_stay_paired(self):
        self.assertEqual(DanceGameMovesHook.pattern,
                         rb"\x48\x8B\xD8\x48\x39\x70\x10\x76.\x8B\xC6")
        address = (123456).to_bytes(8, 'little')
        code = await DanceGameMovesHook.bytecode_generator(None, [('dance_game_moves', address)])
        self.assertEqual(code, b'\x48\x8B\xD8\x48\x8B\x00\x48\xA3' + address
                         + b'\x48\x8B\xC3\x48\x39\x70\x10')
        self.assertEqual(DanceGameMovesHook.instruction_length, 7)
        self.assertEqual(DanceGameMovesHook.noops, 2)

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
