import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.auto_fish_original_adapter import (
    LowFishingEnergy, check_fishing_energy, fish_bot,
)
from src import fish_gaming


class FishingEnergyTests(unittest.IsolatedAsyncioTestCase):
    def client(self, energy=11):
        window = SimpleNamespace()
        window.get_child_by_name = AsyncMock(return_value=window)
        return SimpleNamespace(
            title='p1', is_fishing=True, current_energy=AsyncMock(return_value=energy),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[window])),
            mouse_handler=MagicMock(click_window=AsyncMock()),
            game_client=SimpleNamespace(fishing_manager=AsyncMock()),
        )

    async def test_threshold_and_unknown_energy(self):
        for energy in (0, 9, 10):
            with self.subTest(energy=energy), self.assertRaises(LowFishingEnergy):
                await check_fishing_energy(self.client(energy))
        await check_fishing_energy(self.client(11))
        with self.assertRaises(RuntimeError):
            await check_fishing_energy(self.client(None))

    async def test_low_energy_never_patches_or_casts(self):
        client = self.client(10)
        with patch.object(fish_gaming, 'patch', new_callable=AsyncMock) as prepare:
            await fish_bot(client, False)
        prepare.assert_not_awaited()
        client.mouse_handler.click_window.assert_not_awaited()

    async def test_energy_drops_after_refresh_restores_patch(self):
        client = self.client()
        async def cast(_):
            client.current_energy.return_value = 10
        client.mouse_handler.click_window.side_effect = cast
        with patch.object(fish_gaming, 'patch', AsyncMock(return_value=[(1, b'x')])), \
             patch.object(fish_gaming, 'reset_patch', new_callable=AsyncMock) as reset, \
             patch.object(fish_gaming, 'banish_config', AsyncMock(return_value=[])):
            await asyncio.wait_for(fish_bot(client, False), 1)
        client.mouse_handler.click_window.assert_awaited_once()
        reset.assert_awaited_once_with(client, [(1, b'x')])

    async def test_empty_pond_wait_can_be_cancelled_and_restores_patch(self):
        client = self.client()
        cast = asyncio.Event()
        async def click(_):
            cast.set()
        client.mouse_handler.click_window.side_effect = click
        with patch.object(fish_gaming, 'patch', AsyncMock(return_value=[(1, b'x')])), \
             patch.object(fish_gaming, 'reset_patch', new_callable=AsyncMock) as reset, \
             patch.object(fish_gaming, 'banish_config', AsyncMock(return_value=[])), \
             patch.object(fish_gaming, 'fetch_fish_list', AsyncMock(return_value=[])):
            worker = asyncio.create_task(fish_bot(client, False))
            try:
                await asyncio.wait_for(cast.wait(), 1)
                await asyncio.sleep(.15)
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(worker, 1)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        reset.assert_awaited_once_with(client, [(1, b'x')])
