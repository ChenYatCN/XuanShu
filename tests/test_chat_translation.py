import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.chat_translation import ChatTranslationMonitor, ChatLogDelta, parse_chat_log


def chat_line(message, icon='Say', name='Amber', gid=31):
    sender = f'<link;GID:{gid},{name},2>[{name}]</link>' if gid else f'[{name}]'
    return f'<color;FFFFFF><image;Art/Art_Chat_{icon}.dds;24;24;FFFFFFFF> {sender} {message}</color>'


class ChatLogTests(unittest.TestCase):
    def test_party_and_group_both_display_as_team(self):
        for icon in ('Party', 'PARTY', 'Group'):
            with self.subTest(icon=icon):
                self.assertEqual(parse_chat_log(chat_line('team', icon=icon)),
                                 [('队伍', 31, 'Amber', 'team')])

    def test_whisper_and_room_labels_preserve_sender_and_body(self):
        for icon, channel in (('Art_Chat_Text', '私聊'), ('art_CHAT_text', '私聊'),
                              ('chat_balloon_Owner', '房间'), ('CHAT_BALLOON_OWNER', '房间')):
            with self.subTest(icon=icon):
                raw = chat_line('love you', gid=0, name='你').replace('Art_Chat_Say', icon)
                self.assertEqual(parse_chat_log(raw), [(channel, 0, '你', 'love you')])
                raw = chat_line('one &amp; two').replace('Art_Chat_Say', icon)
                self.assertEqual(parse_chat_log(raw), [(channel, 31, 'Amber', 'one & two')])

    def test_unknown_channel_keeps_its_original_label(self):
        raw = chat_line('hello', icon='Unknown')
        self.assertEqual(parse_chat_log(raw), [('Art_Chat_Unknown', 31, 'Amber', 'hello')])

    def test_nearby_group_self_and_system_markup(self):
        raw = '\n'.join([chat_line('hi'), chat_line('team', 'Group'),
                         chat_line('mine', gid=0, name='你'),
                         chat_line('You received gold', 'System'), '[DEBUG] bad'])
        self.assertEqual(parse_chat_log(raw), [('附近', 31, 'Amber', 'hi'),
                         ('队伍', 31, 'Amber', 'team'), ('附近', 0, '你', 'mine')])

    def test_entities_and_emotes_are_preserved(self):
        self.assertEqual(parse_chat_log(chat_line('&lt;b&gt; &amp; <image;Emoticons/smile.dds;24;24>'))[0][3],
                         '<b> & :smile:')

    def test_initial_baseline_append_and_repeated_message(self):
        delta = ChatLogDelta()
        row = chat_line('hi')
        self.assertEqual(delta.update([row]), [])
        self.assertEqual(len(delta.update([row + '\n' + row])), 1)
        self.assertEqual(delta.update([row + '\n' + row]), [])

    def test_scroll_and_transient_empty_do_not_replay(self):
        delta = ChatLogDelta()
        rows = [chat_line(word) for word in ('a', 'b', 'c')]
        delta.update(['\n'.join(rows[:2])])
        self.assertEqual(delta.update(['']), [])
        self.assertEqual(delta.update(['\n'.join(rows)]), [parse_chat_log(rows[2])[0]])
        self.assertEqual(delta.update(['\n'.join(rows[1:])]), [])

    def test_group_mirror_nodes_emit_once_even_when_order_changes(self):
        delta = ChatLogDelta()
        delta.update([''])
        group = chat_line('team', 'Group')
        main = chat_line('hi') + '\n' + group
        self.assertEqual(len(delta.update([main, group])), 2)
        self.assertEqual(delta.update([group, main]), [])

    def test_view_switch_known_history_is_not_replayed(self):
        delta = ChatLogDelta()
        nearby, group = chat_line('hi'), chat_line('team', 'Group')
        delta.update([nearby])
        self.assertEqual(len(delta.update([group])), 1)
        self.assertEqual(delta.update([nearby]), [])
        self.assertEqual(delta.update([group]), [])

    def test_partially_mirrored_group_window_does_not_duplicate_shared_row(self):
        delta = ChatLogDelta()
        delta.update([''])
        shared = chat_line('shared', 'Group')
        first = chat_line('nearby') + '\n' + shared
        second = shared + '\n' + chat_line('new', 'Group')
        self.assertEqual(len(delta.update([first, second])), 3)

    def test_empty_start_captures_first_message(self):
        delta = ChatLogDelta()
        delta.update([''])
        self.assertEqual(len(delta.update([chat_line('hi')])), 1)

    def test_large_history_reset_is_absorbed(self):
        delta = ChatLogDelta()
        delta.update([''])
        self.assertEqual(delta.update(['\n'.join(chat_line(str(i)) for i in range(101))]), [])

    def test_clients_have_independent_baselines(self):
        first, second = ChatLogDelta(), ChatLogDelta()
        first.update([chat_line('old')])
        second.update([''])
        self.assertEqual(len(second.update([chat_line('old')])), 1)

    def test_bounded_seen_history_is_not_filled_by_unchanged_polls(self):
        delta = ChatLogDelta()
        for _ in range(30):
            delta.update([chat_line('hi')])
        self.assertEqual(len(delta.seen), 1)


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


class ChatLogMonitorTests(unittest.IsolatedAsyncioTestCase):
    wait_until = ChatTranslationTests.wait_until
    async def test_log_chat_runs_even_if_private_hook_fails_and_never_replies(self):
        events = []
        client = FakeClient('p1', 101, 11)
        client.hook_handler.activate_chat_hook = AsyncMock(side_effect=RuntimeError('pattern'))
        node = SimpleNamespace(text='')
        client.root_window = SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[node]))
        monitor = ChatTranslationMonitor(events.append)
        monitor.configure(True, auto_reply=True)
        with patch('src.chat_translation.read_control_text', AsyncMock(side_effect=lambda node: node.text)):
            try:
                await monitor.sync([client])
                await self.wait_until(lambda: any('监听中' in e.get('status', '') for e in events))
                node.text = chat_line('nearby') + '\n' + chat_line('team', 'Group')
                await self.wait_until(lambda: len([e for e in events if e['kind'] == 'message']) == 2)
                self.assertEqual({e['channel'] for e in events if e['kind'] == 'message'}, {'附近', '队伍'})
                self.assertEqual(client.chat_owner.sent, [])
                task = monitor.log_tasks[101][1]
            finally:
                await monitor.stop()
        self.assertTrue(task.done())
        self.assertEqual(monitor.log_tasks, {})

    async def test_selection_cancels_old_log_listener_without_duplicate(self):
        monitor = ChatTranslationMonitor(lambda event: None)
        first, second = FakeClient('p1', 101, 11), FakeClient('p2', 102, 22)
        monitor.configure(True, selected_title='p1')
        try:
            await monitor.sync([first, second])
            original = monitor.log_tasks[101][1]
            await monitor.sync([first, second])
            self.assertIs(monitor.log_tasks[101][1], original)
            monitor.configure(True, selected_title='p2')
            await monitor.sync([first, second])
            self.assertTrue(original.done())
            self.assertEqual(set(monitor.log_tasks), {102})
        finally:
            await monitor.stop()


if __name__ == "__main__":
    unittest.main()
