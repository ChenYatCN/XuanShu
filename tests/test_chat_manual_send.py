import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.chat_translation import ChatTranslationMonitor, _chat_edit, _chat_ready, _manual_chat_owner
from src.automation_ownership import automation_owner
from tests.test_chat_translation import chat_line


class Mouse:
    def __init__(self):
        self.click_window = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def client(title='p1', handle=1):
    return SimpleNamespace(title=title, window_handle=handle, is_running=lambda: True,
        is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
        is_in_dialog=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='World/Area'),
        game_client=SimpleNamespace(player_gid=AsyncMock(return_value=31)),
        mouse_handler=Mouse(), send_hotkey=AsyncMock())


class ManualSendTests(unittest.IsolatedAsyncioTestCase):
    async def run_send(self, first=None, peers=None, draft='', mismatch=False, rows=None, allow_busy=False):
        first = first or client()
        peers = peers or [first]
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        monitor.auto_reply = True
        edit = SimpleNamespace(read_base_address=AsyncMock(return_value=100))
        text = ''
        reads = 0

        async def read(_edit):
            return draft or text

        async def type_text(target, wire):
            nonlocal text
            self.assertIs(target, first)
            self.assertEqual(wire, '/s hello')
            text = 'wrong' if mismatch else wire

        async def logs(peer):
            nonlocal reads
            reads += 1
            if reads <= len(peers):
                return [chat_line('hello')]  # Existing hello is not receipt.
            if rows:
                monitor.observation_counter += 1
                monitor.observed.append((monitor.observation_counter, peer.title,
                                         '附近', 31, 'hello'))
                return [chat_line('hello') + '\n' + rows]
            return [chat_line('hello')]

        with patch('src.chat_translation._chat_edit', AsyncMock(return_value=edit)), \
             patch('src.chat_translation.read_control_text', read), \
             patch('src.chat_translation._type_chat', type_text), \
             patch('src.chat_translation._chat_texts', logs):
            await monitor._manual_send(peers, 'p1', 'hello', verify_seconds=.03, allow_busy=allow_busy)
        return first, events[-1]

    async def test_once_p1_only_and_three_evidence_levels(self):
        first, second = client(), client('p2', 2)
        first, final = await self.run_send(first, [first, second], rows=chat_line('hello'))
        first.send_hotkey.assert_awaited_once()
        second.send_hotkey.assert_not_awaited()
        second.mouse_handler.click_window.assert_not_awaited()
        self.assertTrue(final['invoked'])
        self.assertTrue(final['local_echo'])
        self.assertEqual(final['peer_receipts'], ['p2'])
        self.assertEqual(final['listener_capture'], ['p1', 'p2'])

    async def test_old_hello_not_proof(self):
        _, final = await self.run_send()
        self.assertTrue(final['invoked'])
        self.assertFalse(final['local_echo'])
        self.assertEqual(final['listener_capture'], [])

    async def test_missing_player_gid_uses_character_gid(self):
        first = client()
        first.game_client.player_gid.return_value = 0
        first.client_object = SimpleNamespace(global_id_full=AsyncMock(return_value=31))
        first, final = await self.run_send(first, rows=chat_line('hello'))
        first.send_hotkey.assert_awaited_once()
        self.assertTrue(final['local_echo'])

    async def test_unavailable_gid_allows_manual_send_without_receipt_claim(self):
        first = client()
        first.game_client.player_gid.side_effect = RuntimeError('unavailable')
        first, final = await self.run_send(first, rows=chat_line('hello'))
        first.send_hotkey.assert_awaited_once()
        self.assertTrue(final['invoked'])
        self.assertFalse(final['local_echo'])
        self.assertEqual(final['peer_receipts'], [])
        self.assertEqual(final['listener_capture'], [])

    async def test_missing_zone_still_blocks_send_with_specific_reason(self):
        first = client()
        first.zone_name.return_value = None
        first, final = await self.run_send(first)
        first.send_hotkey.assert_not_awaited()
        self.assertIn('区域读取为空', final['status'])

    async def test_wrong_gid_and_wrong_channel_not_proof(self):
        for row in (chat_line('hello', gid=99), chat_line('hello', icon='Group')):
            _, final = await self.run_send(rows=row)
            self.assertFalse(final['local_echo'])

    async def test_draft_or_mismatch_never_enter(self):
        for kwargs in ({'draft': 'my draft'}, {'mismatch': True}):
            first, final = await self.run_send(**kwargs)
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])

    async def test_busy_offline_loading_dialogue_battle_never_enter(self):
        for attr in ('questing_status', 'is_loading', 'in_battle', 'is_in_dialog', 'is_running'):
            first = client()
            setattr(first, attr, (lambda: False) if attr == 'is_running' else
                    True if attr == 'questing_status' else AsyncMock(return_value=True))
            first, final = await self.run_send(first)
            first.send_hotkey.assert_not_awaited()
            self.assertFalse(final['invoked'])

    async def test_missing_ui_and_missing_p1(self):
        first = client('p2')
        events = []
        monitor = ChatTranslationMonitor(events.append)
        monitor.enabled = True
        await monitor._manual_send([first], 'p1', 'hello', verify_seconds=0)
        first.send_hotkey.assert_not_awaited()
        first.root_window = SimpleNamespace(get_child_by_name=AsyncMock(side_effect=ValueError('missing')))
        with self.assertRaises(ValueError):
            await _chat_edit(first)

    async def test_ownership_prevents_send(self):
        first = client()
        async with automation_owner(first, 'combat'):
            _, final = await self.run_send(first)
        self.assertIn('combat', final['status'])
        first.send_hotkey.assert_not_awaited()

    async def test_duplicate_request_not_queued_and_auto_reply_disabled(self):
        monitor = ChatTranslationMonitor(lambda _event: None)
        monitor.auto_reply = True
        wait = asyncio.Event()
        with patch.object(monitor, '_manual_send', AsyncMock(side_effect=lambda *args: wait.wait())) as send:
            monitor.request_manual_send([], 'p1', 'hello')
            task = monitor.manual_task
            monitor.request_manual_send([], 'p1', 'hello')
            self.assertIs(task, monitor.manual_task)
            self.assertFalse(monitor.auto_reply)
            await monitor.stop()

    async def test_override_allows_battle_and_automatic_quest_send_once(self):
        first = client()
        first.questing_status = True
        first.combat_status = True
        first.in_battle.return_value = True
        first, final = await self.run_send(first, allow_busy=True)
        first.send_hotkey.assert_awaited_once()
        self.assertTrue(final['invoked'])

    async def test_override_keeps_loading_dialogue_draft_and_readback_guards(self):
        for attr in ('is_loading', 'is_in_dialog'):
            first = client()
            getattr(first, attr).return_value = True
            first, final = await self.run_send(first, allow_busy=True)
            self.assertFalse(final['invoked'])
            first.send_hotkey.assert_not_awaited()
        for options in ({'draft': 'existing'}, {'mismatch': True}):
            first, final = await self.run_send(allow_busy=True, **options)
            first.send_hotkey.assert_not_awaited()

    async def test_override_waits_for_combat_click_and_keeps_exclusive_ownership(self):
        first = client()
        acquired = asyncio.Event()
        release = asyncio.Event()
        async def combat():
            async with automation_owner(first, 'combat-click'):
                acquired.set()
                await release.wait()
        task = asyncio.create_task(combat())
        await acquired.wait()
        async def unlock():
            await asyncio.sleep(.01)
            release.set()
        unlock_task = asyncio.create_task(unlock())
        async with _manual_chat_owner(first, True):
            from src.automation_ownership import get_client_automation_ownership
            self.assertEqual(get_client_automation_ownership(first).owner_label, 'manual-nearby-chat')
        await asyncio.gather(task, unlock_task)

