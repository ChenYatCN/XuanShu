import asyncio
from time import time

from wizwalker import ClientHandler, Client, Keycode, PatternFailed, PatternMultipleResults
from wizwalker.memory import MemoryReader, Window
from wizwalker.memory.memory_objects.fish import Fish, FishStatusCode
from loguru import logger
from typing import Union, List






async def patch(client:Client) -> List[tuple[int, bytes]]:
    async def readbytes_writebytes(
        pattern: bytes, write_bytes: bytes, offset: int = 0
    ) -> tuple[int, bytes]:
        add = (
            await reader.pattern_scan(
                pattern,
                return_multiple=False,
                module="WizardGraphicalClient.exe",
            )
            + offset
        )
        old_bytes = await reader.read_bytes(add, len(write_bytes))
        await reader.write_bytes(add, write_bytes)
        return (add, old_bytes)
    
    address_oldbytes = [] 
    reader = MemoryReader(client._pymem)
    
    async def scare_fish_patch():
        # scare fish patch
        num_nops = 5
        write_bytes = b"\x90" * num_nops
        # Newer clients use r15 for the state object and therefore add a 0x41
        # REX prefix before C7.  Accept both layouts.
        pattern = rb"\xE8....\xEB.\x83\xF9\x04\x75.(?:\x41)?\xC7\x87"
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def bobber_submerison_rng_patch():
        # bobber submerison rng patch
        num_nops = 2
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x7D\x37\xC7\x83........\xC7\x83" # 7D 37 C7 83 ?? ?? ?? ?? ?? ?? ?? ?? C7 83
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def fish_notice_bobber_instant_patch():
        # fish notice bobber instant patch
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x82....\xC7\x83........\x8B\x93" # 0F 82 ?? ?? ?? ?? C7 83 ?? ?? ?? ?? ?? ?? ?? ?? 8B 93
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def instant_fish():
        # patch instant fish
        num_nops = 2
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x74\x63\x48\x8B\xCF\xE8....\x0F" #74 63 48 8B CF E8 ?? ?? ?? ?? 0F
        try:
            address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
        except PatternFailed:
            # The 2026 client removed/recompiled this legacy two-byte branch.
            # Verify the replacement layout instead of guessing at a new
            # branch and corrupting the game process.
            current_layout = (
                rb"\x0F\x85....\xF3\x41\x0F\x10\x36"
                rb"\xF3\x41\x0F\x10\x7E\x04"
            )
            await reader.pattern_scan(
                current_layout,
                return_multiple=False,
                module="WizardGraphicalClient.exe",
            )
            return "not_required"
    
    async def instant_fish_2():
        # patch instant fish # 2 
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x82....\xF3\x44\x0F\x10\x0D....\x41\x0F\x2F\xC1" #0F 82 ?? ?? ?? ?? F3 44 0F 10 0D ?? ?? ?? ?? 41 0F 2F C1
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def instant_fish_3():
        # patch instant fish # 3
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x86....\xF3\x41\x0F\x5C\xF2" #0F 86 ?? ?? ?? ?? F3 41 0F 5C F2
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def instant_fish_4():
        # patch instant fish # 4
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x86....\x44\x0F\x2F\x05" #0F 86 ?? ?? ?? ?? 44 0F 2F 05
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def instant_fish_5():
        # patch instant fish # 5
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x84....\x48\x8B\x8B....\x45\x32" #0F 84 ?? ?? ?? ?? 48 8B 8B ?? ?? ?? ?? 45 32 
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def instant_fish_6():
        # patch instant fish # 6
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x84....\xF3\x0F\x10\x70\x6C\x0F\x28\xC6" #0F 84 ?? ?? ?? ?? F3 0F 10 70 6C 0F 28 C6
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def instant_fish_7():
        # patch instant fish # 7
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x86....\xF3\x0F\x10\x8B....\x0F\x28\xC1" #0F 86 ?? ?? ?? ?? F3 0F 10 8B ?? ?? ?? ?? 0F 28 C1
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def instant_fish_8():
        # patch instant fish # 8
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x86....\xF3\x0F\x10\x83....\xF3\x0F\x5C\x83" #0F 86 ?? ?? ?? ?? F3 0F 10 83 ?? ?? ?? ?? F3 0F 5C 83
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def instant_fish_9():
        # patch instant fish # 9
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = rb"\x0F\x87....\xF2\x0F\x10\xB3....\xF2" #0F 87 ?? ?? ?? ?? F2 0F 10 B3 ?? ?? ?? ?? F2
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))
    
    async def skip_bobbing_patch():
        # skipping bobbing animation
        pattern = (
            rb"(?:\x0F\x82....\xF3\x0F\x11\x87|"
            rb"\x0F\x86....\xF3\x0F\x5C\xC1"
            rb"\xF3\x0F\x11\x87\x04\x02\x00\x00)"
        )
        write_bytes = b"\xE9\x79\x05\x00\x00\x90"
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def skip_catch_animation():
        pattern = rb"\x0F\x84....\x48..\x10\x02\x00\x00\xE8....\x84\xC0..\x48..\x78\x02\x00\x00\x00" #0F 84 ?? ?? ?? ?? 48 ?? ?? 10 02 00 00 E8 ?? ?? ?? ?? 84 C0 ?? ?? 48 ?? ?? 78 02 00 00 00
        write_bytes = b"\xE9\x88\x00\x00\x00\x90"
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def skip_struggle():
        num_nops = 6
        write_bytes = b"\x90" * num_nops
        pattern = (
            rb"(?:\x0F\x82....\x44..\xE4\x02\x00\x00"
            rb"\x48..\xC8\x02\x00\x00|"
            rb"\x0F\x82....\x89\xB7\xE4\x02\x00\x00"
            rb"\x48\x8D\x8F\xC8\x02\x00\x00)"
        )
        address_oldbytes.append(await readbytes_writebytes(pattern, write_bytes))

    async def skip_summon_animation():
        # Skip the lure/fish summon animation after casting.
        pattern = (
            rb"\x8B\x54\x24.\x48\x8B\xCF\xE8....\x90\x48\x8B\x5E."
            rb"\x48\x85\xDB\x74\x2E\xBF....\x8B\xC7\xF0\x0F\xC1\x43."
            rb"\x83\xF8.\x75\x1D\x48\x8B\x03\x48\x8B\xCB\xFF\x50."
            rb"\xF0\x0F\xC1\x7B.\x83\xFF.\x75\x0A\x48\x8B\x03\x48\x8B\xCB"
            rb"\xFF\x50.\x90\x48\x8B\x5C\x24.\x48\x8B\x74\x24."
            rb"\x48\x83\xC4.\x5F\xC3"
        )
        address_oldbytes.append(
            await readbytes_writebytes(pattern, b"\x90" * 5, offset=7)
        )

    async def zero_casting_timer():
        # Change the 0x514 millisecond casting delay to zero.
        pattern = (
            rb"\x49\x8D.\xD0\x00\x00\x00\x48\x8B\x01"
            rb"\xBA\x14\x05\x00\x00\xFF\x50\x18"
        )
        address_oldbytes.append(
            await readbytes_writebytes(pattern, b"\x00" * 4, offset=11)
        )

    async def zero_summon_timer():
        # Change the 0x514 millisecond summon delay to zero.
        pattern = (
            rb"\x48\x8D.\xA0\x00\x00\x00\x48\x8B\x01"
            rb"\xBA\x14\x05\x00\x00\xFF\x50\x18"
        )
        address_oldbytes.append(
            await readbytes_writebytes(pattern, b"\x00" * 4, offset=11)
        )

    patches = [
        ("scare_fish", scare_fish_patch),
        ("bobber_submersion_rng", bobber_submerison_rng_patch),
        ("fish_notice_bobber", fish_notice_bobber_instant_patch),
        ("instant_fish_1", instant_fish),
        ("instant_fish_2", instant_fish_2),
        ("instant_fish_3", instant_fish_3),
        ("instant_fish_4", instant_fish_4),
        ("instant_fish_5", instant_fish_5),
        ("instant_fish_6", instant_fish_6),
        ("instant_fish_7", instant_fish_7),
        ("instant_fish_8", instant_fish_8),
        ("instant_fish_9", instant_fish_9),
        ("skip_bobbing", skip_bobbing_patch),
        ("skip_catch_animation", skip_catch_animation),
        ("skip_struggle", skip_struggle),
        ("skip_summon_animation", skip_summon_animation),
        ("zero_casting_timer", zero_casting_timer),
        ("zero_summon_timer", zero_summon_timer),
    ]

    # Fishing patch signatures change independently between game builds. Apply
    # them one at a time so one optional speed-up cannot stop the whole bot.
    skipped_patches = []
    not_required_patches = []
    for patch_name, patch_func in patches:
        try:
            result = await patch_func()
            if result == "not_required":
                not_required_patches.append(patch_name)
        except (PatternFailed, PatternMultipleResults):
            skipped_patches.append(patch_name)

    if skipped_patches:
        logger.warning(
            "Auto fishing skipped incompatible patches "
            f"({len(skipped_patches)}/{len(patches)}): {', '.join(skipped_patches)}. "
            "Restart the game client once before retrying."
        )

    required_patch_count = len(patches) - len(not_required_patches)
    suffix = ""
    if not_required_patches:
        suffix = f"; not required on this game build: {', '.join(not_required_patches)}"
    logger.debug(
        f"Auto fishing patches applied: {len(address_oldbytes)}/{required_patch_count}"
        f"{suffix}"
    )

    return address_oldbytes

async def reset_patch(client: Client, address_bytes: List[tuple[int, bytes]]):
    reader = MemoryReader(client._pymem)
    for address, oldbytes in address_bytes:
        await reader.write_bytes(address, oldbytes)

async def window_exists(client, window_name: str, *, check_if_visible=True):
    w = await client.root_window.get_windows_with_name(window_name)
    if check_if_visible:
        return len(w) > 0 and await w[0].is_visible()
    else:
        return len(w) > 0

async def wait_for_window(client, window_name, *, timeout=10, check_if_visible=True):
    start = time()
    while not await window_exists(client, window_name, check_if_visible=check_if_visible):
        if time() - start >= timeout:
            break

async def wait_to_click_window_with_name(client: Client, window_name: str, *, timeout=10, check_if_visible=True):
    await wait_for_window(client, window_name, timeout=timeout, check_if_visible=check_if_visible)
    await asyncio.sleep(0.1)
    async with client.mouse_handler:
        await client.mouse_handler.click_window_with_name(window_name)

async def sell_basket(client: Client):
    await client.send_key(Keycode.V)
    while await window_exists(client, "Trash", check_if_visible=True):
        while not (await window_exists(client, "centerButton")):
            try:
                async with client.mouse_handler:
                    await client.mouse_handler.click_window_with_name("Trash")
            except ValueError:
                await asyncio.sleep(0.1)

        while await window_exists(client, "centerButton"):
            try:
                async with client.mouse_handler:
                    await client.mouse_handler.click_window_with_name("centerButton")
            except ValueError:
                await asyncio.sleep(0.1)

    await client.send_key(Keycode.V)

async def fetch_fish_list(fishing_manager):
    while True:
        try:
            return await fishing_manager.fish_list()
        except RuntimeError:
            await asyncio.sleep(0.1)

async def banish_config(
    fishing_manager,
    is_chest: bool,
    school: str = "Any",
    rank: int = 0,
    fish_id: int = 0,
    size_min: float = 0,
    size_max: float = 999,
):
    """Remove fish that do not match the selected GUI filters."""
    pond_fish = await fetch_fish_list(fishing_manager)

    # Chest-only mode is intentionally a separate, minimal path: inspect the
    # chest flag only, then remove every ordinary fish in one batch. Reading a
    # template, school, rank, ID, or size here only slows down every refresh.
    if is_chest:
        chest_flags = await asyncio.gather(*[fish.is_chest() for fish in pond_fish])
        status_codes = await asyncio.gather(*[fish.status_code() for fish in pond_fish])
        kept_fish = [
            fish
            for fish, fish_is_chest, status in zip(
                pond_fish, chest_flags, status_codes
            )
            if fish_is_chest and status != FishStatusCode.escaped
        ]
        rejected_fish = [
            fish
            for fish, fish_is_chest, status in zip(
                pond_fish, chest_flags, status_codes
            )
            if not fish_is_chest and status != FishStatusCode.escaped
        ]
        if rejected_fish:
            await asyncio.gather(*[
                fish.write_status_code(FishStatusCode.escaped)
                for fish in rejected_fish
            ])
        return kept_fish

    async def is_matching_fish(fish):
        if await fish.is_chest():
            return False

        fish_temp = await fish.template()
        if school.casefold() != "any":
            if (await fish_temp.school_name()).casefold() != school.casefold():
                return False
        if rank and await fish_temp.rank() != rank:
            return False
        if fish_id and await fish.template_id() != fish_id:
            return False

        fish_size = await fish.size()
        return size_min <= fish_size <= size_max

    accepted_flags = await asyncio.gather(
        *[is_matching_fish(fish) for fish in pond_fish]
    )
    kept_fish = [
        fish for fish, accepted in zip(pond_fish, accepted_flags) if accepted
    ]
    rejected_fish = [
        fish for fish, accepted in zip(pond_fish, accepted_flags) if not accepted
    ]
    if rejected_fish:
        await asyncio.gather(*[
            fish.write_status_code(FishStatusCode.escaped)
            for fish in rejected_fish
        ])
    return kept_fish

async def refresh_pond(
    client,
    fishing_manager,
    is_chest: bool,
    school: str = "Any",
    rank: int = 0,
    fish_id: int = 0,
    size_min: float = 0,
    size_max: float = 999,
):
    # Use the filtered list here. The previous implementation checked the raw
    # pond list, so a pond containing only unwanted fish was never refreshed.
    fish_list = await banish_config(
        fishing_manager, is_chest, school, rank, fish_id, size_min, size_max
    )
    while len(fish_list) == 0:
        previous_fish = await fetch_fish_list(fishing_manager)
        previous_addresses = {fish.base_address for fish in previous_fish}
        fish_windows = await client.root_window.get_windows_with_name("FishingWindow")
        while len(fish_windows) == 0:
            async with client.mouse_handler:
                await client.mouse_handler.click_window_with_name("OpenFishingButton")
            fish_windows = await client.root_window.get_windows_with_name("FishingWindow")
        fish_window: Window = fish_windows[0]

        fish_sub_window = await fish_window.get_child_by_name("FishingSubWindow")
        bottomframe = await fish_sub_window.get_child_by_name("BottomFrame")
        icon2 = await bottomframe.get_child_by_name("Icon2")
        async with client.mouse_handler:
            await client.mouse_handler.click_window(icon2)

        # Wait for a genuinely refreshed pond instead of sleeping a fixed half
        # second and sometimes processing the old escaped objects again.
        saw_empty_pond = False
        while True:
            refreshed_fish = await fetch_fish_list(fishing_manager)
            if not refreshed_fish:
                saw_empty_pond = True
            else:
                refreshed_addresses = {fish.base_address for fish in refreshed_fish}
                statuses = await asyncio.gather(
                    *[fish.status_code() for fish in refreshed_fish]
                )
                if (
                    saw_empty_pond
                    or refreshed_addresses != previous_addresses
                    or any(status != FishStatusCode.escaped for status in statuses)
                ):
                    break
            await asyncio.sleep(0.01)

        fish_list = await banish_config(
            fishing_manager, is_chest, school, rank, fish_id, size_min, size_max
        )

    return fish_list

async def fish_bot(
    client: Client,
    is_chest: bool,
    school: str = "Any",
    rank: int = 0,
    fish_id: int = 0,
    size_min: float = 0,
    size_max: float = 999,
):
    address_bytes = []
    try:
        logger.debug(f'Preparing.')
        address_bytes = await patch(client)
        logger.debug(f"Ready for Fish")

        fishing_manager = await client.game_client.fishing_manager()
        fish_caught = 0
        total = time()
        while client.is_fishing:
            start = time()
            fish_list = await refresh_pond(
                client,
                fishing_manager,
                is_chest,
                school,
                rank,
                fish_id,
                size_min,
                size_max,
            )
            

            # Press Icon 1 (Lure)
            fish_windows = await client.root_window.get_windows_with_name("FishingWindow")

            while len(fish_windows) == 0:
                async with client.mouse_handler:
                    await client.mouse_handler.click_window_with_name("OpenFishingButton")
                fish_windows = await client.root_window.get_windows_with_name("FishingWindow")

            fish_window: Window = fish_windows[0]
            fish_sub_window = await fish_window.get_child_by_name("FishingSubWindow")
            bottomframe = await fish_sub_window.get_child_by_name("BottomFrame")
            icon1 = await bottomframe.get_child_by_name("Icon1")
            async with client.mouse_handler:
                await client.mouse_handler.click_window(icon1)

            # The standalone fishing script observes the live pond here rather
            # than the pre-cast Python objects. Keep that behavior: the game may
            # replace its fish objects while starting the lure sequence.
            is_hooked = False
            cast_cancelled = False
            while not is_hooked:
                if await window_exists(client, "MessageBoxModalWindow"):
                    await wait_to_click_window_with_name(client, "rightButton")
                    await sell_basket(client)
                    cast_cancelled = True
                    break
                live_fish = await fetch_fish_list(fishing_manager)
                statuses = await asyncio.gather(
                    *[fish.status_code() for fish in live_fish]
                )
                for status in statuses:
                    if status == FishStatusCode.unknown2:
                        is_hooked = True
                        break
                if not live_fish or all(
                    status == FishStatusCode.escaped for status in statuses
                ):
                    cast_cancelled = True
                    break
                if not is_hooked:
                    await asyncio.sleep(0.01)

            if cast_cancelled:
                continue
            
            # Invoke
            await client.send_key(Keycode.SPACEBAR)


            # Clear Fish Caught menu
            fish_failed = False
            timeout = time()
            while len(await client.root_window.get_windows_with_name("CaughtFishModalWindow")) == 0:
                if time() - timeout >= 10:
                    fish_failed = True
                    break
                await asyncio.sleep(0.01)
            
            if fish_failed:
                continue

            while len(await client.root_window.get_windows_with_name("CaughtFishModalWindow")) > 0:
                await client.send_key(Keycode.SPACEBAR)
                await asyncio.sleep(0.05)

            fish_caught += 1
            
            # Empty Basket
            if fish_caught % 100 == 0 and not is_chest:
                await sell_basket(client)

            total_time = round((time() - total) / 60, 2)
            logger.debug(f"Fish Caught: {fish_caught}, Number of fish in pool: {len(fish_list) - 1}, Time: {total_time} minutes, Seconds per fish: {round((total_time / fish_caught) * 60, 2)}")

    finally:
        if address_bytes:
            await reset_patch(client, address_bytes)
        logger.debug("Closing")
