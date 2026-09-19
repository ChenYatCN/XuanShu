import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import utils


class NovicePotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_slots_skips_use_and_refill(self):
        client = SimpleNamespace(stats=SimpleNamespace(potion_max=AsyncMock(return_value=0)))
        with patch.object(utils, 'use_potion', new_callable=AsyncMock) as use, \
             patch.object(utils, 'is_potion_needed', new_callable=AsyncMock) as needed, \
             patch.object(utils, 'refill_potions', new_callable=AsyncMock) as refill:
            self.assertTrue(await utils.auto_potions(client, mark=True))
            use.assert_not_awaited()
            needed.assert_not_awaited()
            refill.assert_not_awaited()

    async def test_low_level_skip_and_eligible_failure_propagates(self):
        for level, expected in ((1, True), (6, False)):
            client = SimpleNamespace(stats=SimpleNamespace(
                potion_max=AsyncMock(return_value=1), potion_charge=AsyncMock(return_value=0),
                reference_level=AsyncMock(return_value=level)))
            with patch.object(utils, 'is_potion_needed', new=AsyncMock(return_value=False)), \
                 patch.object(utils, 'refill_potions', new=AsyncMock(return_value=False)) as refill:
                self.assertIs(await utils.auto_potions(client, mark=True), expected)
                self.assertEqual(refill.await_count, int(level >= 6))
