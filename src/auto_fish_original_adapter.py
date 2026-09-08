# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
"""GUI adapter for the repository's untouched standalone fishing script."""

import asyncio
from time import time

from loguru import logger
from wizwalker import Client, Keycode
from wizwalker.memory.memory_objects.fish import FishStatusCode

from src import fish_gaming as original_fishing


async def fish_bot(
    client: Client,
    is_chest: bool,
    school: str = "Any",
    rank: int = 0,
    fish_id: int = 0,
    size_min: float = 0,
    size_max: float = 999,
):
    """Run the original standalone loop against XuanShu' selected client."""
    config = (bool(is_chest), "Any", 0, 0, 0, 999) if is_chest else (
        False, school, rank, fish_id, size_min, size_max
    )

    address_bytes = []
    try:
        logger.debug("Preparing.")
        address_bytes = await original_fishing.patch(client)
        logger.debug("Ready for Fish")

        fishing_manager = await client.game_client.fishing_manager()
        fish_caught = 0
        total = time()
        while client.is_fishing:
            await original_fishing.refresh_pond(client, fishing_manager, config)
            fish_list = await original_fishing.fetch_fish_list(fishing_manager)

            fish_windows = await client.root_window.get_windows_with_name(
                "FishingWindow"
            )
            while len(fish_windows) == 0:
                async with client.mouse_handler:
                    await client.mouse_handler.click_window_with_name(
                        "OpenFishingButton"
                    )
                fish_windows = await client.root_window.get_windows_with_name(
                    "FishingWindow"
                )

            fish_window = fish_windows[0]
            fish_sub_window = await fish_window.get_child_by_name("FishingSubWindow")
            bottomframe = await fish_sub_window.get_child_by_name("BottomFrame")
            icon1 = await bottomframe.get_child_by_name("Icon1")
            async with client.mouse_handler:
                await client.mouse_handler.click_window(icon1)

            is_hooked = False
            basket_full = False
            while not is_hooked and client.is_fishing:
                if await original_fishing.window_exists(
                    client, "MessageBoxModalWindow"
                ):
                    await original_fishing.wait_to_click_window_with_name(
                        client, "rightButton"
                    )
                    await original_fishing.sell_basket(client)
                    basket_full = False
                    break

                fish_list = await original_fishing.fetch_fish_list(fishing_manager)
                statuses = await asyncio.gather(
                    *[fish.status_code() for fish in fish_list]
                )
                for status in statuses:
                    if status == FishStatusCode.unknown2:
                        is_hooked = True
                        break

            if not client.is_fishing:
                break
            if basket_full:
                continue

            await client.send_key(Keycode.SPACEBAR)

            fish_failed = False
            timeout = time()
            while (
                len(
                    await client.root_window.get_windows_with_name(
                        "CaughtFishModalWindow"
                    )
                )
                == 0
            ):
                if not client.is_fishing:
                    break
                if time() - timeout >= 10:
                    fish_failed = True
                    break

            if not client.is_fishing:
                break
            if fish_failed:
                continue

            while (
                len(
                    await client.root_window.get_windows_with_name(
                        "CaughtFishModalWindow"
                    )
                )
                > 0
            ):
                await client.send_key(Keycode.SPACEBAR)
                await asyncio.sleep(0.1)

            fish_caught += 1
            if fish_caught % 100 == 0 and not is_chest:
                await original_fishing.sell_basket(client)

            total_time = round((time() - total) / 60, 2)
            logger.debug(
                "Fish Caught: {}, Number of fish in pool: {}, Time: {} minutes, "
                "Seconds per fish: {}",
                fish_caught,
                len(fish_list) - 1,
                total_time,
                round((total_time / fish_caught) * 60, 2),
            )
    finally:
        if address_bytes:
            await original_fishing.reset_patch(client, address_bytes)
        logger.debug("Closing")
