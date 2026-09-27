import asyncio
import unittest
from unittest.mock import AsyncMock

from src.chat_translation import ChatTranslationMonitor


class FakeHookHandler:
    def __init__(self):
        self._base_addrs = {}
        self.receive_started = 0
        self.receive_stopped = 0
        self.send_started = 0
        self.send_stopped = 0

    async def activate_chat_hook(self, *, wait_for_ready):
        assert wait_for_ready is False
        self.receive_started += 1
        self._base_addrs["recv_counter"] = 1

    async def deactivate_chat_hook(self):
        self.receive_stopped += 1
        self._base_addrs.pop("recv_counter")

    async def activate_chat_send_hook(self):
        self.send_started += 1
        self._base_addrs["send_trigger"] = 1

    async def deactivate_chat_send_hook(self):
        self.send_stopped += 1
        self._base_addrs.pop("send_trigger")


class FakeChatOwner:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []

    async def wait_for_message(self, timeout):
        return await asyncio.wait_for(self.incoming.get(), timeout)

    async def send_msg(self, message, *, target_gid):
        self.sent.append((message, target_gid))


class FakeGameClient:
    def __init__(self, gid):
        self.gid = gid

    async def player_gid(self):
        return self.gid


class FakeClient:
    def __init__(self, title, handle, gid):
        self.title = title
        self.window_handle = handle
        self.hook_handler = FakeHookHandler()
        self.chat_owner = FakeChatOwner()
        self.game_client = FakeGameClient(gid)
        self.running = True

    def is_running(self):
        return self.running


class ChatTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("timed out waiting for monitor")

    async def test_multiclient_directed_reply_dedupe_and_cleanup(self):
        events = []
        p1 = FakeClient("p1", 101, 11)
        p2 = FakeClient("p2", 102, 22)
        monitor = ChatTranslationMonitor(events.append)
        monitor.configure(True, auto_reply=True)
        try:
            await monitor.sync([p1, p2])
            await self.wait_until(lambda: p1.hook_handler.send_started and p2.hook_handler.send_started)
            await p1.chat_owner.incoming.put((31, "Hi", 1))
            await p1.chat_owner.incoming.put((31, "Hi", 1))
            await p2.chat_owner.incoming.put((42, "Bonjour", 1))
            await p1.chat_owner.incoming.put((11, "self", 2))
            await p2.chat_owner.incoming.put((42, "Hello", 2))
            await self.wait_until(lambda: len([e for e in events if e["kind"] == "message"]) == 4)
            self.assertEqual(p1.chat_owner.sent, [("Hello", 31)])
            self.assertEqual(p2.chat_owner.sent, [("Hello", 42)])
            self.assertEqual(len([e for e in events if e["kind"] == "reply"]), 2)
        finally:
            await monitor.stop()
        self.assertEqual((p1.hook_handler.receive_stopped, p1.hook_handler.send_stopped), (1, 1))
        self.assertEqual((p2.hook_handler.receive_stopped, p2.hook_handler.send_stopped), (1, 1))

    async def test_first_hello_is_replyable_but_our_hello_echo_is_not(self):
        events = []
        p1 = FakeClient("p1", 101, 11)
        p2 = FakeClient("p2", 102, 22)
        monitor = ChatTranslationMonitor(events.append)
        monitor.configure(True, auto_reply=True)
        try:
            await monitor.sync([p1, p2])
            await self.wait_until(lambda: p1.hook_handler.send_started and p2.hook_handler.send_started)
            await p1.chat_owner.incoming.put((22, "Hello", 1))
            await self.wait_until(lambda: p1.chat_owner.sent == [("Hello", 22)])
            await p2.chat_owner.incoming.put((11, "Hello", 1))
            await self.wait_until(lambda: len([e for e in events if e["kind"] == "message"]) == 2)
            self.assertEqual(p2.chat_owner.sent, [])
        finally:
            await monitor.stop()

    async def test_selection_and_new_client_without_duplicate_hook(self):
        events = []
        p1 = FakeClient("p1", 101, 11)
        p2 = FakeClient("p2", 102, 22)
        monitor = ChatTranslationMonitor(events.append)
        monitor.configure(True, selected_title="p1")
        try:
            await monitor.sync([p1, p2])
            await self.wait_until(lambda: p1.hook_handler.receive_started == 1)
            await monitor.sync([p1, p2])
            self.assertEqual(p1.hook_handler.receive_started, 1)
            self.assertEqual(p2.hook_handler.receive_started, 0)
            monitor.configure(True, auto_reply=False)
            await monitor.sync([p1, p2])
            await self.wait_until(lambda: p2.hook_handler.receive_started == 1)
            p1.running = False
            await monitor.sync([p2])
            self.assertNotIn(101, monitor.tasks)
        finally:
            await monitor.stop()

    async def test_hook_failure_is_reported_without_restarting_immediately(self):
        events = []
        p1 = FakeClient("p1", 101, 11)
        p1.hook_handler.activate_chat_hook = AsyncMock(side_effect=RuntimeError("pattern"))
        monitor = ChatTranslationMonitor(events.append)
        monitor.configure(True)
        try:
            await monitor.sync([p1])
            await self.wait_until(lambda: 101 in monitor.retry_after)
            await monitor.sync([p1])
            self.assertEqual(p1.hook_handler.activate_chat_hook.await_count, 1)
            self.assertTrue(any("失败" in event.get("status", "") for event in events))
        finally:
            await monitor.stop()


if __name__ == "__main__":
    unittest.main()
