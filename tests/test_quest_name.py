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
