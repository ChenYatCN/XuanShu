"""Crown Shop dismissal must never send a second ESC after the shop closes."""
import asyncio
import unittest
from unittest.mock import patch

from src.ibao_core import crownshopVisibilty, dismiss_crownshop, skipDialogue


class _FakeWindow:
    def __init__(self, state):
        self._state = state

    async def is_visible(self):
        return self._state['visible']


class _FakeRoot:
    def __init__(self, state):
        self._state = state

    async def get_windows_with_name(self, name):
        if name == 'permanentShop' and self._state['present']:
            return [_FakeWindow(self._state)]
        return []


class _FakeClient:
    """Models the real failure: an ESC after the shop is gone opens Settings."""

    def __init__(self, close_after=1, present=True, visible=None):
        self.title = 'p1'
        self._state = {'present': present,
                       'visible': present if visible is None else visible}
        self.root_window = _FakeRoot(self._state)
        self._close_after = close_after
        self.esc_calls = 0
        self.esc_saw_shop = []
        self.settings_open = False
        self.space_seen = asyncio.Event()

    async def send_key(self, key, duration=0):
        name = getattr(key, 'name', str(key))
        if name == 'ESC':
            self.esc_calls += 1
            self.esc_saw_shop.append(self._state['visible'])
            if self._state['visible']:
                if self.esc_calls >= self._close_after:
                    self._state['visible'] = False
            else:
                self.settings_open = True
        elif name == 'SPACEBAR':
            self.space_seen.set()


class CrownShopDismissTests(unittest.IsolatedAsyncioTestCase):
    async def _dismiss_until_gone(self, client):
        while await dismiss_crownshop(client):
            await asyncio.sleep(0)

    async def test_crownshop_dismiss_sends_only_one_escape_when_first_escape_closes_shop(self):
        client = _FakeClient(close_after=1)
        await self._dismiss_until_gone(client)
        self.assertEqual(client.esc_calls, 1)
        self.assertEqual(client.esc_saw_shop, [True])
        self.assertFalse(client.settings_open)

    async def test_crownshop_dismiss_does_not_send_escape_when_already_closed(self):
        client = _FakeClient(present=False)
        self.assertFalse(await dismiss_crownshop(client))
        await self._dismiss_until_gone(client)
        self.assertEqual(client.esc_calls, 0)

    async def test_two_concurrent_crownshop_dismissers_do_not_double_escape(self):
        client = _FakeClient(close_after=1)
        both_observed = asyncio.Barrier(2)

        async def dismisser(side):
            # Both owners observe the popup before either may dismiss it.
            self.assertTrue(await crownshopVisibilty(client), side)
            await both_observed.wait()
            await dismiss_crownshop(client)

        await asyncio.gather(dismisser('skipDialogue'), dismisser('azothFarmer'))
        self.assertEqual(client.esc_calls, 1)
        self.assertFalse(client.settings_open)

    async def test_crownshop_retry_rechecks_visibility_before_second_escape(self):
        client = _FakeClient(close_after=2)
        with patch('src.ibao_core.CROWNSHOP_DISMISS_TIMEOUT', .05), \
             patch('src.ibao_core.CROWNSHOP_DISMISS_POLL', .005):
            # A single call sends exactly one ESC, even though the shop stays up.
            self.assertTrue(await dismiss_crownshop(client))
            self.assertEqual(client.esc_calls, 1)
            self.assertFalse(client.settings_open)
            await self._dismiss_until_gone(client)
        self.assertEqual(client.esc_calls, 2)
        self.assertEqual(client.esc_saw_shop, [True, True])
        self.assertFalse(client.settings_open)

    async def test_new_character_crownshop_cleanup_does_not_open_settings(self):
        client = _FakeClient(close_after=1)
        await self._dismiss_until_gone(client)
        self.assertEqual(client.esc_calls, 1)
        self.assertFalse(client.settings_open)
        self.assertFalse(client._state['visible'])

    async def test_normal_character_without_crownshop_unchanged(self):
        client = _FakeClient(present=False)
        dialogue_reads = 0

        async def in_dialog():
            nonlocal dialogue_reads
            dialogue_reads += 1
            return dialogue_reads == 1

        client.is_in_dialog = in_dialog
        task = asyncio.create_task(skipDialogue(client))
        try:
            await asyncio.wait_for(client.space_seen.wait(), 1)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertGreaterEqual(dialogue_reads, 1)
        self.assertEqual(client.esc_calls, 0)


if __name__ == '__main__':
    unittest.main()
