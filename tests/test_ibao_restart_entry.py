"""Recovery must hand a character-select client to azothFarmer, not an in-world one."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import ibao_core
from src.ibao_runtime import create_session, prepare_restarted_client, run_client
from wizwalker.errors import HookAlreadyActivated

_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(_seconds):
    await _REAL_SLEEP(0)


class _PrepareClient:
    def __init__(self, play, loading=False, zone='SomeZone'):
        self.title = 'p1'
        self.window_handle = 4242
        self.process_id = 999
        self.root_window = SimpleNamespace(children=AsyncMock(return_value=[]))
        self.body = SimpleNamespace(position=AsyncMock(return_value=SimpleNamespace(x=0, y=0, z=0)))
        self._play = list(play) if isinstance(play, list) else [play]
        self._loading = list(loading) if isinstance(loading, list) else [loading]
        self._zone = list(zone) if isinstance(zone, list) else [zone]
        self.esc_calls = 0
        self.play_clicks = 0
        self.hooks_activated = 0

    @staticmethod
    def _next(values):
        return values.pop(0) if len(values) > 1 else values[0]

    async def activate_hooks(self, wait_for_ready=False):
        self.hooks_activated += 1

    async def is_loading(self):
        return self._next(self._loading)

    async def zone_name(self):
        return self._next(self._zone)

    async def send_key(self, key, duration=0):
        if getattr(key, 'name', None) == 'ESC':
            self.esc_calls += 1

    def is_running(self):
        return True


class _InWorldClient:
    def __init__(self, play_visible=False):
        self.title = 'p1'
        self.window_handle = 4242
        self.process_id = 999
        self.root_window = SimpleNamespace(children=AsyncMock(return_value=[]))
        self.body = SimpleNamespace(position=AsyncMock(return_value=SimpleNamespace(x=0, y=0, z=0)))
        self.state = {
            'play': play_visible,
            'settings': False,
            'confirm': False,
            'running': True,
            'events': [],
        }

    async def is_loading(self):
        return False

    async def zone_name(self):
        return 'SomeZone'

    def is_running(self):
        return self.state['running']

    async def send_key(self, key, duration=0):
        name = getattr(key, 'name', None) or str(key)
        if name == 'ESC':
            self.state['events'].append('esc')
            self.state['settings'] = True

    async def close(self):
        self.state['running'] = False


class RestartEntryContractTests(unittest.IsolatedAsyncioTestCase):
    def _prepare(self, client):
        play_calls = []

        async def visible(_root, path):
            if path == ibao_core.playButton:
                value = client._next(client._play)
                play_calls.append(value)
                return value
            return False

        async def click(_client, path):
            if path == ibao_core.playButton:
                client.play_clicks += 1

        return visible, click, play_calls

    async def _run_prepare(self, client, timeout=.5):
        visible, click, play_calls = self._prepare(client)
        with patch('src.ibao_core.is_visible_by_path', visible), \
             patch('src.ibao_core.click_window_until_gone', click), \
             patch('src.ibao_runtime.asyncio.sleep', _fast_sleep), \
             patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()):
            await asyncio.wait_for(prepare_restarted_client(client), timeout)
        return play_calls

    def _worker_scope(self, client):
        scope = create_session(client, {})

        async def visible(_root, path):
            if path == scope['playButton']:
                return client.state['play']
            if path == scope['quitButton']:
                return client.state['settings']
            if path == scope['logOutConfirm']:
                return client.state['confirm']
            return False

        async def click(_client, path):
            if path == scope['quitButton']:
                client.state['events'].append('click-quit')
                client.state['settings'] = False
                client.state['confirm'] = True
            elif path == scope['logOutConfirm']:
                client.state['events'].append('click-logout-confirm')
                client.state['confirm'] = False
                client.state['play'] = True

        scope['is_visible_by_path'] = visible
        scope['click_window_until_gone'] = click
        scope['azothFarmer'] = AsyncMock()
        return scope

    async def test_recovery_handoff_returns_at_character_select_before_worker_restart(self):
        client = _PrepareClient(play=[False, True], loading=False, zone=None)
        play_calls = await self._run_prepare(client)

        self.assertTrue(play_calls[-1])
        self.assertEqual(client.esc_calls, 1)
        self.assertEqual(client.play_clicks, 0)
        self.assertEqual(client.hooks_activated, 1)

    async def test_recovery_does_not_enter_world_before_azoth_worker(self):
        client = _PrepareClient(play=True)
        await self._run_prepare(client)

        self.assertEqual(client.esc_calls, 0)
        self.assertEqual(client.play_clicks, 0)
        self.assertEqual(client.body.position.await_count, 0)

    async def test_prepare_already_hooked_still_waits_for_character_select(self):
        client = _PrepareClient(play=[False, True], loading=False, zone=None)
        client.activate_hooks = AsyncMock(side_effect=HookAlreadyActivated('hook'))
        await self._run_prepare(client)

        self.assertEqual(client.esc_calls, 1)
        self.assertEqual(client.play_clicks, 0)

    async def test_restarted_worker_receives_character_select_state(self):
        client = _InWorldClient(play_visible=True)
        scope = self._worker_scope(client)

        with patch('src.ibao_runtime.create_session', return_value=scope), \
             patch('src.ibao_runtime.asyncio.sleep', _fast_sleep), \
             patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()):
            await asyncio.wait_for(run_client(client, {}), 1)

        self.assertEqual(client.state['events'], [])
        scope['azothFarmer'].assert_awaited_once()

    async def test_recovery_character_select_allows_wizard_list_rebuild(self):
        client = _InWorldClient(play_visible=True)
        scope = create_session(client, {})
        self.assertEqual(scope['activeClients'][0].wizLst, [])
        scope['baseLocationList'] = ['The Commons']
        scope['skipDialogue'] = AsyncMock()
        scope['setup'] = AsyncMock()
        scope['get_config'] = lambda: {'ENABLE_PAGE_TURNING': False,
                                       'CHARACTER_SWITCH_DELAY': .3}
        scope['print'] = lambda *args, **kwargs: None

        texts = {
            tuple(scope['txtName']): 'Wizard',
            tuple(scope['txtLevel']): '100',
            tuple(scope['txtLocation']): 'The Commons',
        }

        async def window(_root, path):
            return SimpleNamespace(maybe_text=AsyncMock(return_value=texts.get(tuple(path), '')))

        async def visible(_root, path):
            return path == scope['playButton']

        class _ListBuilt(Exception):
            pass

        async def click(_client, path):
            if path == scope['playButton']:
                raise _ListBuilt

        scope['window_from_path'] = window
        scope['is_visible_by_path'] = visible
        scope['click_window_until_gone'] = click

        with patch('src.ibao_core.asyncio.sleep', _fast_sleep):
            with self.assertRaises(_ListBuilt):
                await scope['azothFarmer'](client, 0)

        self.assertEqual(len(scope['activeClients'][0].wizLst), 1)
        self.assertEqual(scope['activeClients'][0].wizLst[0].Name, 'Wizard')

    async def test_prepare_waits_during_loading_without_escape(self):
        client = _PrepareClient(play=[False, True], loading=[True, False], zone=None)
        await self._run_prepare(client)

        self.assertEqual(client.esc_calls, 0)
        self.assertEqual(client.play_clicks, 0)

    async def test_prepare_title_screen_uses_bounded_escape_until_character_select(self):
        client = _PrepareClient(play=[False, True], loading=False, zone=None)
        await self._run_prepare(client)

        self.assertEqual(client.esc_calls, 1)
        self.assertEqual(client.play_clicks, 0)

    async def test_fresh_normal_ibao_start_still_uses_character_select_entry_flow(self):
        client = _InWorldClient(play_visible=True)
        scope = self._worker_scope(client)

        with patch('src.ibao_runtime.create_session', return_value=scope), \
             patch('src.ibao_runtime.asyncio.sleep', _fast_sleep), \
             patch('src.ibao_runtime.ibao_restart_trace', AsyncMock()):
            await asyncio.wait_for(run_client(client, {}), 1)

        self.assertEqual(client.state['events'], [])
        scope['azothFarmer'].assert_awaited_once()


if __name__ == '__main__':
    unittest.main()

