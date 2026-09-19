import asyncio
import unittest
from types import SimpleNamespace

from src.automation_ownership import (
    automation_owner,
    get_client_automation_ownership,
)


class ClientAutomationOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_is_reentrant_for_the_same_task(self):
        client = SimpleNamespace()

        async with automation_owner(client, "outer") as ownership:
            self.assertTrue(ownership.locked)
            self.assertEqual(ownership.owner_label, "outer")
            async with automation_owner(client, "inner"):
                self.assertEqual(ownership.owner_label, "inner")
            self.assertEqual(ownership.owner_label, "outer")

        self.assertFalse(ownership.locked)
        self.assertIsNone(ownership.owner_label)

    async def test_other_task_waits_for_current_owner(self):
        client = SimpleNamespace()
        acquired = asyncio.Event()

        async def contender():
            async with automation_owner(client, "contender"):
                acquired.set()

        async with automation_owner(client, "owner"):
            task = asyncio.create_task(contender())
            done, _ = await asyncio.wait({task}, timeout=0.05)
            self.assertFalse(done)
            self.assertFalse(acquired.is_set())

        await asyncio.wait_for(task, 1)
        self.assertTrue(acquired.is_set())
        self.assertIs(
            get_client_automation_ownership(client),
            get_client_automation_ownership(client),
        )
