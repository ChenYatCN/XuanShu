"""Keep typed Chinese only when listening proves the translated draft was sent."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.chat_translation import ChatLogDelta, ChatTranslationMonitor
import tests.test_chat_manual_send as manual
import tests.test_chat_translation as chat
import tests.test_chat_translation_ui as ui


class OriginalEchoTests(unittest.IsolatedAsyncioTestCase):
    settings = ui.ChatTranslationUITests.settings
    make_dialog = ui.ChatTranslationUITests.make_dialog

    async def asyncSetUp(self):
        self.events = []
        self.monitor = ChatTranslationMonitor(self.events.append)
        self.monitor.enabled = True
        self.api = Mock(translate=Mock(return_value='Please wait for me.'))
        self.monitor.translation_api = self.api
        self.p1 = manual.client('p1', 101)
        self.p2 = manual.client('p2', 102)
        self.p2.game_client.player_gid.return_value = 22
        self.monitor.translation_clients = {101: self.p1, 102: self.p2}

    async def asyncTearDown(self):
        await self.monitor.stop()

    async def fill(self, target=None, original='大家等我一下', translated='Please wait for me.', fail=False):
        target = target or self.p1
        self.api.translate.return_value = translated
        edit = SimpleNamespace(read_base_address=AsyncMock(return_value=100))
        with (patch('src.chat_translation._chat_edit', AsyncMock(return_value=edit)),
              patch('src.chat_translation.read_control_text', AsyncMock(return_value='')),
              patch('src.chat_translation._type_chat', AsyncMock()),
              patch('src.chat_translation._chat_readback', AsyncMock(return_value=not fail))):
            if fail:
                # Fail after translation, before typing; avoid a retry timeout.
                with patch('src.chat_translation._type_chat', AsyncMock(side_effect=RuntimeError('fill failed'))):
                    await self.monitor._manual_send([self.p1, self.p2], target.title, original)
            else:
                await self.monitor._manual_send([self.p1, self.p2], target.title, original)
        target.send_hotkey.assert_not_awaited()
        return self.events[-1]

    def echo(self, target=None, text='Please wait for me.', gid=0, name='你', observation=1):
        target = target or self.p1
        self.monitor._publish_message({'kind': 'message', 'source': 'chat_log',
            'handle': target.window_handle, 'title': target.title, 'channel': '附近',
            'sender_gid': gid, 'sender_name': name, 'message': text,
            'observation_id': observation})
        return self.events[-1]

    def messages(self):
        return [e for e in self.events if e['kind'] == 'message']

    async def test_fill_only_retains_original_without_a_sent_message_or_enter(self):
        final = await self.fill(original='  大家等我一下  ')
        self.assertTrue(final['filled'])
        self.assertFalse(final['invoked'])
        self.assertEqual(self.messages(), [])
        event = self.echo()
        self.assertEqual(event.get('source_message'), '  大家等我一下  ')
        self.assertEqual(event['message'], 'Please wait for me.')
        self.api.translate.assert_called_once_with('  大家等我一下  ', target_language='en')
        self.assertTrue(self.monitor.translation_queue.empty())

    async def test_send_association_also_accepts_verified_own_gid(self):
        await self.fill()
        event = self.echo(gid=31, name='Amber')
        self.assertEqual(event.get('source_message'), '大家等我一下')

    async def test_edited_draft_invalidates_original_even_if_old_english_is_sent_later(self):
        await self.fill()
        event = self.echo(text='Please go ahead.')
        self.assertNotIn('source_message', event)
        event = self.echo(observation=2)
        self.assertNotIn('source_message', event)

    async def test_other_or_unknown_sender_does_not_consume_the_pending_original(self):
        await self.fill()
        self.assertNotIn('source_message', self.echo(gid=77, name='Other'))
        self.assertNotIn('source_message', self.echo(gid=0, name='Amber', observation=2))
        event = self.echo(observation=3)
        self.assertEqual(event.get('source_message'), '大家等我一下')

    async def test_multiclient_identical_english_uses_each_original_and_client(self):
        await self.fill(self.p1, '等我一下')
        await self.fill(self.p2, '请等一下')
        first = self.echo(self.p1)
        second = self.echo(self.p2, observation=2)
        self.assertEqual((first.get('source_message'), first.get('title')), ('等我一下', 'p1'))
        self.assertEqual((second.get('source_message'), second.get('title')), ('请等一下', 'p2'))
        self.assertNotEqual(first.get('outgoing_id'), second.get('outgoing_id'))

    async def test_consecutive_fills_with_identical_english_keep_distinct_originals(self):
        await self.fill(original='等我一下')
        first = self.echo()
        await self.fill(original='请等我一下')
        second = self.echo(observation=2)
        self.assertEqual(first.get('source_message'), '等我一下')
        self.assertEqual(second.get('source_message'), '请等我一下')
        self.assertNotEqual(first.get('outgoing_id'), second.get('outgoing_id'))
        # Another genuine send without another fill must not reuse old Chinese.
        self.assertNotIn('source_message', self.echo(observation=3))

    async def test_new_fill_replaces_an_unsent_draft(self):
        await self.fill(original='旧中文', translated='Old draft.')
        await self.fill(original='新中文', translated='New draft.')
        self.assertEqual(self.echo(text='New draft.').get('source_message'), '新中文')
        self.assertNotIn('source_message', self.echo(text='Old draft.', observation=2))

    async def test_plain_english_fill_supersedes_old_chinese_association(self):
        await self.fill(original='旧中文')
        await self.fill(original='Please wait for me.')
        self.assertNotIn('source_message', self.echo())

    async def test_hook_and_chat_log_copies_use_the_same_record_in_either_order(self):
        for hook_first in (True, False):
            await self.fill()
            hook = {'kind': 'message', 'handle': 101, 'title': 'p1',
                    'sender_gid': 31, 'message': 'Please wait for me.',
                    'observation_id': 50 + int(hook_first)}
            if hook_first:
                self.monitor._publish_message(hook)
                first = self.events[-1]
                second = self.echo(observation=10)
            else:
                first = self.echo(observation=11)
                self.monitor._publish_message(hook)
                second = self.events[-1]
            self.assertIsNotNone(first.get('outgoing_id'))
            self.assertEqual(first['outgoing_id'], second.get('outgoing_id'))

    async def test_later_untracked_message_cannot_reuse_the_confirmed_chinese(self):
        await self.fill()
        self.echo()
        with patch('src.chat_translation.time.monotonic', return_value=10**12):
            later = self.echo(self.p2, gid=31, name='Amber', observation=2)
        self.assertNotIn('source_message', later)

    async def test_chat_log_whitespace_canonicalization_preserves_the_typed_chinese(self):
        await self.fill(original='  等我一下  ', translated='Please  wait for me.')
        row = chat.parse_chat_log(chat.chat_line('Please  wait for me.', name='你', gid=0))[0]
        event = self.echo(text=row[3])
        self.assertEqual(event.get('source_message'), '  等我一下  ')

    async def test_real_listener_to_dialog_fill_send_repeat_and_clear(self):
        app, sent, dialog = self.make_dialog()
        def publish(event):
            self.events.append(event)
            dialog.handle_event(event)
        self.monitor.publish = publish
        node = SimpleNamespace(text='')
        self.p1.root_window = SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[node, node]))
        async def wait_until(predicate):
            for _ in range(150):
                if predicate():
                    return
                await asyncio.sleep(.01)
            self.fail('listener did not publish the expected message')
        with patch('src.chat_translation.read_control_text', AsyncMock(side_effect=lambda n: n.text)):
            listener = asyncio.create_task(self.monitor._listen_log(self.p1))
            try:
                await wait_until(lambda: any('监听中' in e.get('status', '') for e in self.events))
                await self.fill()
                self.assertEqual(dialog.messages.toPlainText(), '')
                row = chat.chat_line('Please wait for me.', name='你', gid=0)
                node.text = row
                await wait_until(lambda: len(dialog._history) == 1)
                self.assertEqual(dialog.messages.toPlainText(),
                                 '[p1 · 我]\n原文：大家等我一下\n发送：Please wait for me.')
                await asyncio.sleep(.6)  # Same snapshot polled again, with two mirrored logs.
                self.assertEqual(len(dialog._history), 1)
                await self.fill(original='请等一下')
                node.text = row+'\n'+row
                await wait_until(lambda: len(dialog._history) == 2)
                self.assertIn('原文：请等一下', dialog.messages.toPlainText())
                dialog._clear_button.click()
                self.assertEqual(dialog.messages.toPlainText(), '')
                self.assertEqual(len(dialog._history), 0)
                self.api.translate.assert_has_calls([
                    unittest.mock.call('大家等我一下', target_language='en'),
                    unittest.mock.call('请等一下', target_language='en')])
                self.assertEqual(self.api.translate.call_count, 2)
            finally:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

    async def test_failed_translation_or_fill_does_not_create_a_sent_association(self):
        self.api.translate.side_effect = RuntimeError('translation failed')
        final = await self.fill()
        self.assertFalse(final['filled'])
        self.assertNotIn('source_message', self.echo())
        self.api.translate.side_effect = None
        final = await self.fill(fail=True)
        self.assertFalse(final['filled'])
        self.assertNotIn('source_message', self.echo(observation=2))

    async def test_reused_handle_cannot_inherit_previous_client_original(self):
        await self.fill()
        self.monitor.translation_clients[101] = manual.client('p1', 101)
        self.assertNotIn('source_message', self.echo())

    async def test_missing_gid_uses_only_the_verified_self_label_on_selected_client(self):
        self.p1.game_client.player_gid.return_value = None
        await self.fill()
        self.assertNotIn('source_message', self.echo(self.p2, gid=31, name='Amber'))
        self.assertEqual(self.echo().get('source_message'), '大家等我一下')

    async def test_peer_echo_and_repeated_delivery_share_one_outgoing_record(self):
        await self.fill()
        first = self.echo(self.p2, gid=31, name='Amber')
        own = self.echo(self.p1, observation=2)
        repeat = self.echo(self.p1, observation=2)
        self.assertEqual(first.get('title'), 'p1')
        self.assertIsNotNone(first.get('outgoing_id'))
        self.assertEqual(first['outgoing_id'], own.get('outgoing_id'))
        self.assertEqual(own['outgoing_id'], repeat.get('outgoing_id'))

    async def test_real_log_delta_repeated_poll_and_real_repeated_send(self):
        delta = ChatLogDelta()
        self.assertEqual(delta.update(['']), [])
        await self.fill()
        row = chat.chat_line('Please wait for me.', name='你', gid=0)
        new = delta.update([row, row])
        self.assertEqual(len(new), 1)
        self.echo(observation=1)
        self.assertEqual(delta.update([row, row]), [])
        await self.fill(original='请等我一下')
        self.assertEqual(len(delta.update([row+'\n'+row])), 1)
        second = self.echo(observation=2)
        self.assertEqual(second.get('source_message'), '请等我一下')


class OriginalEchoUITests(unittest.TestCase):
    settings = ui.ChatTranslationUITests.settings
    make_dialog = ui.ChatTranslationUITests.make_dialog

    def message(self, **fields):
        return {'kind': 'message', 'source': 'chat_log', 'title': 'p1',
                'message_id': 1, 'outgoing_id': 1, 'source_message': '大家等我一下',
                'message': 'Please wait for me.', **fields}

    def test_verified_send_displays_exact_original_and_english_once(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event({'kind': 'manual_send', 'filled': True, 'done': True,
                             'source_message': '大家等我一下', 'translation': 'Please wait for me.'})
        self.assertEqual(dialog.messages.toPlainText(), '')
        event = self.message()
        dialog.handle_event(event)
        dialog.handle_event(event)
        dialog.handle_event({**event, 'message_id': 2})  # Another listener's copy.
        self.assertEqual(dialog.messages.toPlainText(),
                         '[p1 · 我]\n原文：大家等我一下\n发送：Please wait for me.')
        self.assertEqual(len(dialog._history), 1)

    def test_clear_removes_both_texts_and_late_events_do_not_resurrect_them(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event(self.message())
        dialog._clear_button.click()
        dialog.handle_event({'kind': 'translation', 'message_id': 1, 'translation': '重新翻译'})
        dialog.handle_event(self.message(message_id=2))
        self.assertEqual(dialog.messages.toPlainText(), '')
        self.assertEqual(len(dialog._history), 0)
        dialog.handle_event(self.message(message_id=3, outgoing_id=2, source_message='继续走'))
        self.assertIn('继续走', dialog.messages.toPlainText())

    def test_genuine_consecutive_sends_and_clients_are_not_text_deduped(self):
        app, sent, dialog = self.make_dialog()
        dialog.handle_event(self.message())
        dialog.handle_event(self.message(message_id=2, outgoing_id=2, source_message='请等一下'))
        dialog.handle_event(self.message(message_id=3, outgoing_id=3, title='p2', source_message='稍等'))
        self.assertEqual(len(dialog._history), 3)
        self.assertEqual(dialog.messages.toPlainText().count('发送：Please wait for me.'), 3)
        self.assertIn('[p2 · 我]', dialog.messages.toPlainText())

    def test_incoming_translation_keeps_existing_display_and_duplicates_use_message_id(self):
        app, sent, dialog = self.make_dialog()
        event = {'kind': 'message', 'message_id': 9, 'title': 'p2', 'source': 'chat_log',
                 'channel': '队伍', 'sender_name': 'Other', 'message': 'hello'}
        dialog.handle_event(event)
        dialog.handle_event(event)
        dialog.handle_event({'kind': 'translation', 'message_id': 9, 'translation': '你好'})
        self.assertEqual(dialog.messages.toPlainText(), '[p2][队伍] [Other]: hello\n  译文：你好')
