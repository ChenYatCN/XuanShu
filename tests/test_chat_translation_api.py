import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from types import SimpleNamespace

import requests

from src.chat_translation_api import (
    ChatTranslationAPI, TranslationError, completion_url, protect_api_key, unprotect_api_key,
)
from src.chat_translation import ChatTranslationMonitor
from src.settings_manager import XuanShuSettings
from tests.test_chat_translation import FakeClient, chat_line


OPTIONS = {'chat_translation_enabled': True,
           'chat_translation_api_url': 'https://api.deepseek.com',
           'chat_translation_model': 'deepseek-flash',
           'chat_translation_api_key_protected': 'encrypted-test-only'}


class TranslationAPITests(unittest.TestCase):
    def make_api(self, options=None):
        with patch('src.chat_translation_api.unprotect_api_key', return_value='test-key-not-real'):
            return ChatTranslationAPI(options or OPTIONS)

    def response(self, status=200, data=None):
        response = Mock(status_code=status)
        response.json.return_value = data if data is not None else {
            'choices': [{'message': {'content': '你好'}}]}
        return response

    def test_base_versioned_and_complete_https_urls(self):
        for value, expected in (
                ('https://api.deepseek.com', 'https://api.deepseek.com/chat/completions'),
                ('https://example.com/v1/', 'https://example.com/v1/chat/completions'),
                ('https://example.com/v1/chat/completions/', 'https://example.com/v1/chat/completions')):
            self.assertEqual(completion_url(value), expected)

    def test_invalid_and_credential_bearing_urls_rejected(self):
        for value in ('http://example.com', 'https://u:p@example.com', 'https://@example.com',
                      'https://example.com?q=1', 'https://example.com?', 'https://example.com#',
                      'https://example.com:bad', 'https://example .com', '', None):
            with self.subTest(value=value), self.assertRaises(TranslationError):
                completion_url(value)

    def test_payload_only_contains_body_and_correct_auth_no_redirect_retry(self):
        response = self.response()
        api = self.make_api()
        with patch('src.chat_translation_api.requests.post', return_value=response) as post:
            self.assertEqual(api.translate('Hello'), '你好')
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args, ('https://api.deepseek.com/chat/completions',))
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer test-key-not-real'})
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], (5, 20))
        self.assertEqual(kwargs['json']['messages'][1], {'role': 'user', 'content': 'Hello'})
        self.assertEqual(kwargs['json']['thinking'], {'type': 'disabled'})
        self.assertIn('Chinese', kwargs['json']['messages'][0]['content'])
        self.assertIn('English', kwargs['json']['messages'][0]['content'])
        self.assertFalse(kwargs['json']['stream'])
        response.close.assert_called_once()

    def test_custom_openai_compatible_model_omits_deepseek_extension(self):
        api = self.make_api({**OPTIONS, 'chat_translation_api_url': 'https://example.com/v1',
                             'chat_translation_model': 'custom-model'})
        with patch('src.chat_translation_api.requests.post', return_value=self.response()) as post:
            api.translate('hello')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'custom-model')
        self.assertNotIn('thinking', post.call_args.kwargs['json'])

    def test_outgoing_target_language_forces_english_without_changing_receive_prompt(self):
        api = self.make_api()
        with patch('src.chat_translation_api.requests.post', return_value=self.response(
                data={'choices': [{'message': {'content': 'Who am I?'}}]})) as post:
            self.assertEqual(api.translate('我是谁', target_language='en'), 'Who am I?')
        prompt = post.call_args.kwargs['json']['messages'][0]['content']
        self.assertIn('into natural English', prompt)
        self.assertNotIn('otherwise translate', prompt)
        self.assertEqual(post.call_args.kwargs['json']['messages'][1]['content'], '我是谁')

    def test_http_errors_timeouts_and_malformed_responses_are_safe_single_attempt(self):
        for status in (301, 401, 429, 500):
            response = self.response(status, {'error': 'secret test-key-not-real'})
            with self.subTest(status=status), patch('src.chat_translation_api.requests.post',
                                                  return_value=response) as post:
                with self.assertRaises(TranslationError) as error:
                    self.make_api().translate('hello')
                self.assertNotIn('test-key-not-real', str(error.exception))
                post.assert_called_once()
                response.json.assert_not_called()
        for error in (requests.Timeout('secret test-key-not-real'),
                      requests.ConnectionError('Authorization: Bearer test-key-not-real')):
            with patch('src.chat_translation_api.requests.post', side_effect=error) as post:
                with self.assertRaises(TranslationError) as safe:
                    self.make_api().translate('hello')
                self.assertNotIn('test-key-not-real', str(safe.exception))
                post.assert_called_once()
        for data in ({}, {'choices': []}, {'choices': [{'message': {'content': ''}}]},
                     {'choices': [{'message': {'content': None}}]}):
            with patch('src.chat_translation_api.requests.post', return_value=self.response(data=data)):
                with self.assertRaises(TranslationError):
                    self.make_api().translate('hello')

    def test_invalid_messages_do_not_request(self):
        with patch('src.chat_translation_api.requests.post') as post:
            for value in ('', 'a' * 4001, None):
                with self.assertRaises(TranslationError):
                    self.make_api().translate(value)
            post.assert_not_called()

    def test_windows_protection_and_settings_round_trip_without_plaintext(self):
        key = 'synthetic-credential-for-local-test-only'
        protected = protect_api_key(key)
        self.assertNotIn(key, protected)
        self.assertEqual(unprotect_api_key(protected), key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = XuanShuSettings(str(path))
            self.assertFalse(settings.get_setting('chat_translation_enabled'))
            settings.set_settings({'chat_translation_api_key_protected': protected})
            self.assertNotIn(key, path.read_text(encoding='utf-8'))
            restored = XuanShuSettings(str(path))
            self.assertEqual(unprotect_api_key(restored.get_setting('chat_translation_api_key_protected')), key)

    def test_bad_credentials_are_rejected_without_echo(self):
        for key in ('', 'bad key', 'x\ny', 'x' * 2049):
            with self.assertRaises(TranslationError):
                protect_api_key(key)
        with self.assertRaises(TranslationError) as error:
            unprotect_api_key('not-a-real-encrypted-secret')
        self.assertNotIn('not-a-real-encrypted-secret', str(error.exception))


class TranslationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, condition):
        for _ in range(100):
            if condition():
                return
            await asyncio.sleep(.01)
        self.fail('worker timeout')

    def monitor(self, api):
        events = []
        monitor = ChatTranslationMonitor(events.append)
        with patch('src.chat_translation.ChatTranslationAPI', return_value=api):
            monitor.configure(True, translation_options=OPTIONS)
        return monitor, events

    def message(self, monitor, text='Hello', title='p1'):
        monitor._publish_message({'kind': 'message', 'title': title, 'message': text})

    async def test_disabled_and_missing_key_preserve_original_without_http(self):
        for options in ({}, {**OPTIONS, 'chat_translation_enabled': False}, OPTIONS):
            events = []
            monitor = ChatTranslationMonitor(events.append)
            with (patch('src.chat_translation_api.unprotect_api_key', side_effect=TranslationError('missing')),
                  patch('src.chat_translation_api.requests.post') as post):
                monitor.configure(True, translation_options=options)
                self.message(monitor)
                await asyncio.sleep(0)
                post.assert_not_called()
                self.assertEqual(len([e for e in events if e['kind'] == 'message']), 1)
                self.assertFalse(any(e['kind'] == 'translation' for e in events))
                await monitor.stop()

    async def test_original_immediate_translation_correlated_and_cached(self):
        api = Mock(translate=Mock(return_value='你好'))
        monitor, events = self.monitor(api)
        self.message(monitor)
        self.message(monitor, title='p2')
        self.message(monitor)
        self.assertEqual(len([e for e in events if e['kind'] == 'message']), 3)
        await monitor.translation_task
        originals = [e for e in events if e['kind'] == 'message']
        translated = [e for e in events if e['kind'] == 'translation']
        self.assertEqual([e['message_id'] for e in originals], [e['message_id'] for e in translated])
        self.assertEqual([e['translation'] for e in translated], ['你好', '你好', '你好'])
        self.assertEqual(api.translate.call_count, 2)  # Cache only within each client.
        self.assertFalse(any(e['kind'] in ('reply', 'manual_send') for e in events))
        await monitor.stop()

    async def test_queue_bounded_originals_preserved_and_close_drops_stale_result(self):
        entered, release = threading.Event(), threading.Event()
        def translate(_text):
            entered.set()
            release.wait(3)
            return '迟到译文'
        api = Mock(translate=Mock(side_effect=translate))
        monitor, events = self.monitor(api)
        try:
            self.message(monitor)
            await self.wait_until(entered.is_set)
            for index in range(40):
                self.message(monitor, str(index))
            self.assertEqual(monitor.translation_queue.qsize(), 32)
            self.assertEqual(len([e for e in events if e['kind'] == 'message']), 41)
            self.assertTrue(any('队列已满' in e.get('status', '') for e in events))
            await monitor.stop()
            self.assertEqual(monitor.translation_queue.qsize(), 0)
        finally:
            release.set()
            await monitor.translation_task
        self.assertFalse(any(e['kind'] == 'translation' for e in events))
        api.translate.assert_called_once()

    async def test_settings_change_discards_old_and_uses_one_worker_for_new_api(self):
        entered, release = threading.Event(), threading.Event()
        def translate(_text):
            entered.set()
            release.wait(3)
            return '旧译文'
        old_api = Mock(translate=Mock(side_effect=translate))
        new_api = Mock(translate=Mock(return_value='新译文'))
        monitor, events = self.monitor(old_api)
        try:
            self.message(monitor)
            await self.wait_until(entered.is_set)
            old_task = monitor.translation_task
            with patch('src.chat_translation.ChatTranslationAPI', return_value=new_api):
                monitor.configure(True, translation_options={**OPTIONS, 'chat_translation_model': 'new'})
            self.message(monitor, 'new')
            self.assertIs(monitor.translation_task, old_task)
        finally:
            release.set()
            await monitor.translation_task
            await monitor.stop()
        self.assertEqual([e['translation'] for e in events if e['kind'] == 'translation'], ['新译文'])
        new_api.translate.assert_called_once_with('new')

    async def test_failure_keeps_original_and_never_retries_or_leaks_key(self):
        api = Mock(translate=Mock(side_effect=RuntimeError('Bearer secret-key')))
        monitor, events = self.monitor(api)
        self.message(monitor)
        await monitor.translation_task
        api.translate.assert_called_once()
        self.assertEqual(len([e for e in events if e['kind'] == 'message']), 1)
        self.assertNotIn('secret-key', json.dumps(events))
        self.assertFalse(any(e['kind'] == 'translation' for e in events))
        await monitor.stop()

    async def test_existing_directed_listener_feeds_translator_without_game_send(self):
        monitor, events = self.monitor(Mock(translate=Mock(return_value='你好')))
        client = FakeClient('p1', 101, 11)
        try:
            await monitor.sync([client])
            await client.chat_owner.incoming.put((42, 'Hello', 1))
            await self.wait_until(lambda: any(e['kind'] == 'translation' for e in events))
            self.assertEqual(client.chat_owner.sent, [])
            self.assertEqual(client.hook_handler.send_started, 0)
        finally:
            await monitor.stop()

    async def test_existing_chatlog_listener_feeds_translator(self):
        node = SimpleNamespace(text='')
        client = FakeClient('p1', 101, 11)
        client.root_window = SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[node]))
        monitor, events = self.monitor(Mock(translate=Mock(return_value='你好')))
        with patch('src.chat_translation.read_control_text', AsyncMock(side_effect=lambda node: node.text)):
            try:
                await monitor.sync([client])
                await self.wait_until(lambda: any('监听中' in e.get('status', '') for e in events))
                node.text = chat_line('Hello')
                await self.wait_until(lambda: any(e['kind'] == 'translation' for e in events))
                self.assertEqual(client.chat_owner.sent, [])
                translated = next(e for e in events if e['kind'] == 'translation')
                self.assertEqual(translated['source'], 'chat_log')
                self.assertEqual(translated['sender_name'], 'Amber')
            finally:
                await monitor.stop()

    def shared_clients(self, monitor):
        first, second = FakeClient('p1', 101, 11), FakeClient('p2', 102, 22)
        monitor.translation_clients = {101: first, 102: second}
        return first, second

    def scene_message(self, monitor, handle, message='hello', gid=31, name='Alex', channel='附近'):
        monitor._publish_message({'kind': 'message', 'source': 'chat_log', 'handle': handle,
            'title': 'p1' if handle == 101 else 'p2', 'channel': channel,
            'sender_gid': gid, 'sender_name': name, 'message': message})

    async def test_translated_outgoing_self_and_peer_echo_preserve_originals_without_api(self):
        api = Mock()
        monitor, events = self.monitor(api)
        first, second = self.shared_clients(monitor)
        monitor.outgoing_translations.append((first, 11, 'Who am I?'))
        self.scene_message(monitor, 101, 'Who am I?', 0, '你')
        self.scene_message(monitor, 102, 'Who am I?', 11)
        self.scene_message(monitor, 101, 'Who am I?', 11)
        self.assertIsNone(monitor.translation_task)
        api.translate.assert_not_called()
        self.assertEqual(len([e for e in events if e['kind'] == 'message']), 3)
        self.assertFalse(any(e['kind'] == 'translation' for e in events))
        await monitor.stop()

    async def test_identical_other_player_and_untracked_self_messages_still_translate(self):
        api = Mock(translate=Mock(return_value='我是谁？'))
        monitor, events = self.monitor(api)
        first, second = self.shared_clients(monitor)
        monitor.outgoing_translations.append((first, 11, 'Who am I?'))
        self.scene_message(monitor, 101, 'Who am I?', 31)
        self.scene_message(monitor, 102, 'Who am I?', 0, '你')
        self.scene_message(monitor, 101, 'untracked', 0, '你')
        await monitor.translation_task
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 3)
        self.assertEqual(api.translate.call_count, 3)
        await monitor.stop()

    async def test_retained_draft_provenance_survives_translation_settings_change(self):
        api = Mock()
        monitor, events = self.monitor(api)
        first, second = self.shared_clients(monitor)
        monitor.outgoing_translations.append((first, None, 'Who am I?'))
        monitor._reset_translation()
        self.scene_message(monitor, 101, 'Who am I?', 0, '你')
        api.translate.assert_not_called()
        self.assertIsNone(monitor.translation_task)
        await monitor.stop()

    async def test_reused_handle_and_unknown_sender_never_assumed_to_be_our_echo(self):
        api = Mock(translate=Mock(return_value='我是谁？'))
        monitor, events = self.monitor(api)
        first, second = self.shared_clients(monitor)
        monitor.outgoing_translations.append((first, 11, 'Who am I?'))
        monitor.translation_clients[101] = FakeClient('p1', 101, 22)
        self.scene_message(monitor, 101, 'Who am I?', 0, '你')
        self.scene_message(monitor, 102, 'Who am I?', 0, 'Alex')
        await monitor.translation_task
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 2)
        self.assertEqual(api.translate.call_count, 2)
        await monitor.stop()

    async def test_external_same_scene_copies_keep_both_originals_translate_once(self):
        api = Mock(translate=Mock(return_value='你好'))
        monitor, events = self.monitor(api)
        self.shared_clients(monitor)
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)):
            self.scene_message(monitor, 101)
            self.scene_message(monitor, 102)
            await monitor.translation_task
        self.assertEqual(len([e for e in events if e['kind'] == 'message']), 2)
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 1)
        api.translate.assert_called_once()
        await monitor.stop()

    async def test_different_or_unverified_scenes_each_get_translation(self):
        for sharing in (False, RuntimeError('unreadable scene')):
            api = Mock(translate=Mock(return_value='你好'))
            monitor, events = self.monitor(api)
            self.shared_clients(monitor)
            helper = AsyncMock(return_value=False) if sharing is False else AsyncMock(side_effect=sharing)
            with patch('src.chat_translation.clients_share_live_area', helper):
                self.scene_message(monitor, 101)
                self.scene_message(monitor, 102)
                await monitor.translation_task
            self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 2)
            self.assertEqual(api.translate.call_count, 2)
            await monitor.stop()

    async def test_own_source_echo_has_translation_peer_same_scene_does_not(self):
        for peer_first in (False, True):
            api = Mock(translate=Mock(return_value='Hello'))
            monitor, events = self.monitor(api)
            self.shared_clients(monitor)
            with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)):
                if peer_first:
                    self.scene_message(monitor, 102, '你好', 11)
                    await asyncio.sleep(.1)
                self.scene_message(monitor, 101, '你好', 0, '你')
                if not peer_first:
                    self.scene_message(monitor, 102, '你好', 11)
                await monitor.translation_task
            translated = [e for e in events if e['kind'] == 'translation']
            self.assertEqual([(e['handle'], e['translation']) for e in translated], [(101, 'Hello')])
            api.translate.assert_called_once_with('你好')
            await monitor.stop()

    async def test_own_message_in_other_scene_also_translates_in_peer_window(self):
        api = Mock(translate=Mock(return_value='Hello'))
        monitor, events = self.monitor(api)
        self.shared_clients(monitor)
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=False)):
            self.scene_message(monitor, 101, '你好', 0, '你', '队伍')
            self.scene_message(monitor, 102, '你好', 11, 'Alex', '队伍')
            await monitor.translation_task
        self.assertEqual([e['handle'] for e in events if e['kind'] == 'translation'], [101, 102])
        self.assertEqual(api.translate.call_count, 2)
        await monitor.stop()

    async def test_single_selected_client_never_suppressed_by_other_clients(self):
        api = Mock(translate=Mock(return_value='Hello'))
        monitor, events = self.monitor(api)
        self.shared_clients(monitor)
        monitor.selected_title = 'p2'
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)) as sharing:
            self.scene_message(monitor, 102, '你好', 11)
            await monitor.translation_task
            sharing.assert_not_awaited()
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 1)
        await monitor.stop()

    async def test_missing_source_echo_falls_back_to_translating_visible_copy(self):
        api = Mock(translate=Mock(return_value='Hello'))
        monitor, events = self.monitor(api)
        self.shared_clients(monitor)
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)):
            self.scene_message(monitor, 102, '你好', 11)
            await monitor.translation_task
        self.assertEqual([e['handle'] for e in events if e['kind'] == 'translation'], [102])
        await monitor.stop()

    async def test_repeated_same_client_and_different_channels_not_dropped(self):
        monitor, events = self.monitor(Mock(translate=Mock(return_value='你好')))
        self.shared_clients(monitor)
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)):
            self.scene_message(monitor, 101)
            self.scene_message(monitor, 101)
            self.scene_message(monitor, 102, channel='队伍')
            await monitor.translation_task
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 3)
        await monitor.stop()

    async def test_unknown_sender_id_never_guessed_from_matching_name(self):
        monitor, events = self.monitor(Mock(translate=Mock(return_value='你好')))
        self.shared_clients(monitor)
        with patch('src.chat_translation.clients_share_live_area', AsyncMock(return_value=True)) as sharing:
            self.scene_message(monitor, 101, gid=0)
            self.scene_message(monitor, 102, gid=0)
            await monitor.translation_task
            sharing.assert_not_awaited()
        self.assertEqual(len([e for e in events if e['kind'] == 'translation']), 2)
        await monitor.stop()
