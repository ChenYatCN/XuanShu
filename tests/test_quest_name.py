import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import utils


class QuestNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigil_waits_for_real_title_and_stops_without_empty_comparison(self):
        from src.sigil import Sigil
        client = SimpleNamespace(sigil_status=True)
        sigil = Sigil(client, [client], None)
        with (patch('src.sigil.get_quest_name', new=AsyncMock(side_effect=['', '', 'Talk'])),
              patch('src.sigil.asyncio.sleep', new=AsyncMock()) as sleep):
            self.assertEqual(await sigil.read_quest_name(), 'Talk')
            self.assertEqual(sleep.await_count, 2)
        client.sigil_status = False
        with self.assertRaises(asyncio.CancelledError):
            await sigil.read_quest_name()

    async def test_empty_quest_cannot_match_popup(self):
        with (patch.object(utils, 'get_quest_name', new=AsyncMock(return_value='')),
              patch.object(utils, 'get_window_from_path', new=AsyncMock()) as lookup):
            self.assertFalse(await utils.is_popup_title_relevant(object()))
        lookup.assert_not_awaited()

    async def test_missing_lookup_is_not_cached_when_hud_reappears(self):
        client = SimpleNamespace(title='p1', root_window=object())
        window = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                 maybe_text=AsyncMock(return_value='<center>Talk</center>'))
        with (patch.object(utils, 'is_free', new=AsyncMock(return_value=True)),
              patch.object(utils, 'get_window_from_path', new=AsyncMock(side_effect=[False, None, True, window])) as lookup):
            for _ in range(3):
                self.assertEqual(await utils.get_quest_name(client), '')
            self.assertEqual(await utils.get_quest_name(client), 'Talk')
        self.assertEqual(lookup.await_count, 4)

    async def test_stale_window_and_invalid_text_return_empty(self):
        client = SimpleNamespace(title='p1', root_window=object())
        window = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                 maybe_text=AsyncMock(side_effect=[RuntimeError('stale'), RuntimeError('stale'), None]))
        with (patch.object(utils, 'is_free', new=AsyncMock(return_value=True)),
              patch.object(utils, 'get_window_from_path', new=AsyncMock(return_value=window)),
              patch.object(utils, 'logger') as log):
            for _ in range(3):
                self.assertEqual(await utils.get_quest_name(client), '')
            log.debug.assert_called_once()

    async def test_busy_or_hidden_hud_does_not_wait_or_read_text(self):
        client = SimpleNamespace(title='p1', root_window=object())
        window = SimpleNamespace(is_visible=AsyncMock(return_value=False), maybe_text=AsyncMock())
        with (patch.object(utils, 'is_free', new=AsyncMock(side_effect=[False, True])),
              patch.object(utils, 'get_window_from_path', new=AsyncMock(return_value=window)) as lookup):
            self.assertEqual(await utils.get_quest_name(client), '')
            lookup.assert_not_awaited()
            self.assertEqual(await utils.get_quest_name(client), '')
        window.maybe_text.assert_not_awaited()

    async def test_lookup_failure_is_safe_but_cancellation_propagates(self):
        client = SimpleNamespace(title='p1', root_window=object())
        with (patch.object(utils, 'is_free', new=AsyncMock(return_value=True)),
              patch.object(utils, 'get_window_from_path', new=AsyncMock(side_effect=[RuntimeError('rebuilding'), asyncio.CancelledError()]))):
            self.assertEqual(await utils.get_quest_name(client), '')
            with self.assertRaises(asyncio.CancelledError):
                await utils.get_quest_name(client)
