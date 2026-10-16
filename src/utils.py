import ast
import asyncio
import ctypes
import inspect
import logging
import os
import re
import time
import traceback
import typing
from enum import Enum
from typing import Any, Coroutine, Iterable, List, Optional, Union, get_type_hints

import requests
import wizwalker.errors
import yaml
from loguru import logger
from wizwalker import XYZ, Client, Keycode, Primitive, kernel32
from wizwalker.combat import CombatMember
from wizwalker.extensions.scripting.utils import (
    _click_on_friend,
    _friend_list_entry,
    _maybe_get_named_window,
    _teleport_to_friend,
)
from wizwalker.extensions.wizsprinter.wiz_navigator import toZone
from wizwalker.memory import ObjectType, Window, WindowFlags
from wizwalker.memory.memory_objects.character_registry import DynamicMemoryObject
from wizwalker.utils import (
    get_all_wizard_handles,
    get_pid_from_handle,
    override_wiz_install_location,
)

from src.dance_game_hook import attempt_deactivate_dance_hook
from src.interaction_prompts import (
    matches_text,
    portal_kind,
    resolve_portal_destination,
)
from src.paths import *
from src.sprinty_client import SprintyClient
from src.window_text import read_control_text
from src.interaction_prompts import plain_text
from src.quest_party import pause_dungeon_for_potions, finish_dungeon_potions

# from src.teleport_math import calc_Distance

streamportal_locations = [
    "aeriel",
    "zanadu",
    "outer athanor",
    "inner athanor",
    "sepidious",
    "mandalla",
    "chaos jungle",
    "reverie",
    "nimbus",
    "port aero",
    "husk",
]
nanavator_locations = [
    "karamelle city",
    "sweetzburg",
    "nibbleheim",
    "gutenstadt",
    "black licorice forest",
    "candy corn farm",
    "gobblerton",
]


def get_ui_tree_text(file_path):
    try:
        with open(file_path, "r") as file:
            return file.read()
    except FileNotFoundError:
        return f"UI tree file '{file_path}' not found."
    except Exception as e:
        return f"Error reading UI tree file: {str(e)}"


def get_entity_text(file_path):
    try:
        with open(file_path, "r") as file:
            return file.read()
    except FileNotFoundError:
        return f"Entity file '{file_path}' not found."
    except Exception as e:
        return f"Error reading entity file: {str(e)}"


async def get_window_from_path(root_window: Window, name_path: list[str]) -> Window:
    # FULL CREDIT TO SIROLAF FOR THIS FUNCTION
    async def _recurse_follow_path(window, path):
        if len(path) == 0:
            return window
        for child in await window.children():
            if await child.name() == path[0]:
                found_window = await _recurse_follow_path(child, path[1:])
                if not found_window is False:
                    return found_window

        return False

    return await _recurse_follow_path(root_window, name_path)


async def is_visible_by_path(client: Client, path: list[str]):
    # FULL CREDIT TO SIROLAF FOR THIS FUNCTION
    # checks visibility of a window from the path
    root = client.root_window
    windows = await get_window_from_path(root, path)
    if windows == False:
        return False
    elif await windows.is_visible():
        return True
    else:
        return False


async def close_endorsement_window(client: Client) -> bool:
    """Close only the endorsement panel, using names verified in its GUI asset."""
    windows = await client.root_window.get_windows_with_name("EndorsementWindow")
    for window in windows:
        if not await window.is_visible():
            continue
        for button in await window.get_windows_with_name(
            "CloseEndorsementWindowButton"
        ):
            if await button.is_visible():
                async with client.mouse_handler:
                    await client.mouse_handler.click_window(button)
                return True
    return False




async def close_friend_windows(client: Client) -> bool:
    """Close only friend panels; never use ESC or toggle the friends button."""
    async def visible(window):
        if not await window.is_visible():
            return False
        return all([await parent.is_visible() for parent in await window.get_parents()])

    closed = False
    for name, close_name in (
        ("wndCharacter", "btnCharacterClose"),
        ("NewFriendsListWindow", "btnFriendListClose"),
        ("wndFriendsList", "btnFriendListClose"),
    ):
        for window in await client.root_window.get_windows_with_name(name):
            if not await visible(window):
                continue
            buttons = await window.get_windows_with_name(close_name)
            button = None
            for candidate in buttons:
                if await visible(candidate):
                    button = candidate
                    break
            async with client.mouse_handler:
                if await client.is_loading() or not await visible(window):
                    continue
                if button is not None and await visible(button):
                    await client.mouse_handler.click_window(button)
                else:
                    # The existing teleport utility hides this exact list by
                    # flags. Keep that fallback scoped and preserve other bits.
                    flags = await window.flags()
                    await window.write_flags(WindowFlags(
                        (int(flags) & ~int(WindowFlags.visible)) | int(WindowFlags.disabled)
                    ))
                closed = True
    return closed


async def is_friend_teleport_error(client: Client) -> bool:
    # A Yes/No dungeon confirmation also has rightButton (No).
    # Its centerButton (Yes) distinguishes it from the one-button error.
    return await is_visible_by_path(
        client, friend_is_busy_and_dungeon_reset_path
    ) and not await is_visible_by_path(client, exit_dungeon_path)


async def read_control_checkbox_text(checkbox: Window) -> str:
    return await checkbox.read_wide_string_from_offset(616)


# Teleport to given world through spiral door
async def go_to_new_world(p, destinationWorld, open_window: bool = True, *, select_only=False, stop=None):
    async def stopped():
        return stop is not None and await stop()
    async def window_visible():
        return (await get_spiral_teleport_button(p) is not None if select_only
                else await is_visible_by_path(p, spiral_door_path))
    async def click_button(name):
        if await stopped():
            return False
        if select_only:
            enter = await get_spiral_teleport_button(p)
            parent = await enter.parent() if enter else None
            pane = await parent.get_child_by_name(spiral_door_path[-1]) if parent else None
            button = await pane.get_child_by_name(name) if pane else None
            if button is None or not await button.is_visible() or await stopped():
                return False
            await p.mouse_handler.click_window(button)
        else:
            await p.mouse_handler.click_window_with_name(name)
        return True
    if await stopped():
        return False
    if open_window:
        while portal_kind(
            await get_popup_title(p)
        ) != "world_gate" and not await window_visible():
            if await stopped():
                return False
            await asyncio.sleep(0.1)

        while not await window_visible():
            await asyncio.sleep(0.1)
            if await stopped():
                return False
            await p.send_key(Keycode.X, 0.1)

    while not select_only and await p.is_in_npc_range():
        if await stopped():
            return False
        await p.send_key(Keycode.X, 0.1)
        await asyncio.sleep(0.4)

    while not await window_visible():
        await asyncio.sleep(0.1)
        if await stopped():
            return False
        await p.send_key(Keycode.X, 0.1)

    async with p.mouse_handler:
        # each worldList item (in-file name for a world) correlates to a zoneDoorOptions (in-file name for the buttons in the spiral door)
        worldList = [
            "WizardCity",
            "Krokotopia",
            "Marleybone",
            "MooShu",
            "DragonSpire",
            "Grizzleheim",
            "Celestia",
            "Wysteria",
            "Zafaria",
            "Avalon",
            "Azteca",
            "Khrysalis",
            "Polaris",
            "Arcanum",
            "Mirage",
            "Empyrea",
            "Karamelle",
            "Lemuria",
        ]
        zoneDoorOptions = [
            "wbtnWizardCity",
            "wbtnKrokotopia",
            "wbtnMarleybone",
            "wbtnMooShu",
            "wbtnDragonSpire",
            "wbtnGrizzleheim",
            "wbtnCelestia",
            "wbtnWysteria",
            "wbtnZafaria",
            "wbtnAvalon",
            "wbtnAzteca",
            "wbtnKhrysalis",
            "wbtnPolaris",
            "wbtnArcanum",
            "wbtnMirage",
            "wbtnEmpyrea",
            "wbtnKaramelle",
            "wbtnLemuria",
        ]
        zoneDoorNameList = [
            "Wizard City",
            "Krokotopia",
            "Marleybone",
            "MooShu",
            "DragonSpire",
            "Grizzleheim",
            "Celestia",
            "Wysteria",
            "Zafaria",
            "Avalon",
            "Azteca",
            "Khrysalis",
            "Polaris",
            "Arcanum",
            "Mirage",
            "Empyrea",
            "Karamelle",
            "Lemuria",
        ]
        # user could be on any of the three pages when opening the world door depending on what their active quest is
        # switch all the way to the first page to standardize it
        for i in range(6):
            if await stopped():
                return False
            await click_button("leftButton")
            await asyncio.sleep(0.2)

        if select_only:
            button = await get_spiral_teleport_button(p)
            parent = await button.parent() if button else None
            option_window = [await parent.get_child_by_name(spiral_door_path[-1])] if parent else []
        else:
            option_window = await p.root_window.get_windows_with_name("optionWindow")

        assert len(option_window) == 1, str(option_window)

        for child in await option_window[0].children():
            if await child.name() == "pageCount":
                pageCount = await read_control_text(child) if select_only else await child.maybe_text()
                pageCount = plain_text(pageCount) if select_only else pageCount[8:-9]
                currentPage = pageCount.split("/", 1)[0]
                maxPage = pageCount.split("/", 1)[1]
                break

        # ensure we are on page 1 (and if not click over again)
        while str(currentPage) != "1":
            if await stopped():
                return False
            await click_button("leftButton")
            await asyncio.sleep(0.2)
            for child in await option_window[0].children():
                if await child.name() == "pageCount":
                    pageCount = await read_control_text(child) if select_only else await child.maybe_text()
                    pageCount = plain_text(pageCount) if select_only else pageCount[8:-9]
                    currentPage = pageCount.split("/", 1)[0]

        worldIndex = worldList.index(destinationWorld)
        spiralGateName = zoneDoorNameList[worldIndex]

        isChildFound = False

        for i in range(int(maxPage)):
            if await stopped():
                return False
            for child in await option_window[0].children():
                if await child.name() in ["opt0", "opt1", "opt2", "opt3"]:
                    name = await read_control_text(child) if select_only else await read_control_checkbox_text(child)
                    if name == spiralGateName or matches_text(
                        name, f"WorldNames_{destinationWorld}"
                    ):
                        if await stopped():
                            return False
                        await click_button(zoneDoorOptions[worldIndex])
                        await asyncio.sleep(0.4)
                        if await stopped():
                            return False
                        if select_only:
                            return True
                        await click_button("teleportButton")
                        await p.wait_for_zone_change()

                        # move away from the spiral door so we dont accidentally click on it again after teleporting later
                        # await p.send_key(Keycode.W, 1.5)

                        isChildFound = True
                        break

            # correct world was not found - check the next page
            if not isChildFound:
                previousPage = currentPage
                loopCount = 0
                while currentPage == previousPage and loopCount < 30:
                    if await stopped():
                        return False
                    loopCount += 1
                    await click_button("rightButton")

                    # ensure that wizwalker didn't misclick and that we actually changed pages
                    for child in await option_window[0].children():
                        if await child.name() == "pageCount":
                            pageCount = await read_control_text(child) if select_only else await child.maybe_text()
                            pageCount = plain_text(pageCount) if select_only else pageCount[8:-9]
                            currentPage = pageCount.split("/", 1)[0]


async def new_portals_cycle(client: Client, location: str):
    if not location:
        raise ValueError(
            "Cannot select a portal without a recognized quest destination"
        )
    zone = await client.zone_name()
    task = await client.quest_id(), await client.goal_id()

    async def current():
        return (getattr(client, 'questing_status', True)
                and not getattr(client, 'refilling_potions', False)
                and not await client.is_loading() and not await client.in_battle()
                and await client.zone_name() == zone
                and (await client.quest_id(), await client.goal_id()) == task)

    async def read_page():
        # A refreshed menu invalidates its old children. Retry reads only,
        # reacquiring from the dynamic root; never replay selection/teleport.
        for attempt in range(3):
            if not await current():
                return None
            try:
                panels = [w for w in await client.root_window.get_windows_with_name('optionWindow')
                          if await w.is_visible()]
                if len(panels) == 1:
                    page, options = None, []
                    for child in await panels[0].children():
                        name = await child.name()
                        if name == 'pageCount':
                            match = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*',
                                                 plain_text(await read_control_text(child)))
                            if match:
                                page = tuple(map(int, match.groups()))
                        elif name in ('opt0', 'opt1', 'opt2', 'opt3') and await child.is_visible():
                            options.append((child, await read_control_text(child)))
                    if page and 1 <= page[0] <= page[1]:
                        return page, options
                reason = 'visible portal page is missing or incomplete'
            except wizwalker.errors.MemoryReadError as exc:
                reason = str(exc)
            except RuntimeError as exc:
                if not str(exc).startswith(('控件文本结构无效：', '控件文本指针无效：')):
                    raise  # Unrelated programming errors are not UI refreshes.
                reason = str(exc)
            logger.debug(f"Client {client.title} - portal page read {attempt + 1}/3 failed: {reason}")
            if attempt < 2:
                await asyncio.sleep(.1)
        return None

    async def read_failed():
        # State changes end the old operation; only persistent read/search
        # failure in this unchanged task is reported as failure to its caller.
        return False if await current() else None

    snapshot = await read_page()
    if snapshot is None:
        return await read_failed()
    for _ in range(snapshot[0][1]):
        if not await current():
            return None
        selected = next((child for child, text in snapshot[1]
                         if resolve_portal_destination(text, [location]) == location), None)
        if selected is not None:
            async with client.mouse_handler:
                snapshot = await read_page()
                if snapshot is None:
                    return await read_failed()
                selected = next((child for child, text in snapshot[1]
                                 if resolve_portal_destination(text, [location]) == location), None)
                if selected is None or not await current():
                    return await read_failed()
                await client.mouse_handler.click_window(selected)
                await asyncio.sleep(.4)
                if not await current():
                    return None
                button = await get_spiral_teleport_button(client)
                if not await current():
                    return None
                if button is None:
                    return await read_failed()
                await client.mouse_handler.click_window(button)
                await client.wait_for_zone_change()
            # The menu is destroyed after travel. Do not continue the outer
            # page loop and dereference its old opt controls.
            return await client.zone_name() != zone

        previous = snapshot[0][0]
        for _ in range(30):  # Preserve the existing finite page-change budget.
            if not await current():
                return None
            async with client.mouse_handler:
                await client.mouse_handler.click_window_with_name('rightButton')
            snapshot = await read_page()
            if snapshot is None:
                return await read_failed()
            if snapshot[0][0] != previous:
                break
        else:
            return False
    return False


async def generate_tfc(client: Client):
    async with client.mouse_handler:
        # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
        for i in range(5):
            try:
                await click_window_by_path(client, close_real_friend_list_button_path)
                await asyncio.sleep(0.1)
            except ValueError:
                await asyncio.sleep(0.1)

        for i in range(2):
            await client.send_key(Keycode.F, 0.1)
            await asyncio.sleep(0.2)

        if await is_visible_by_path(client, enter_true_friend_code_button_path):
            await click_window_by_path(client, enter_true_friend_code_button_path)

        await asyncio.sleep(0.3)

        if await is_visible_by_path(client, generate_true_friend_code_path):
            await click_window_by_path(client, generate_true_friend_code_path)

        await asyncio.sleep(1.0)

        try:
            tfc_window = await get_window_from_path(
                client.root_window, true_friend_code_text_path
            )
            tfc = await tfc_window.maybe_text()
        except:
            print(traceback.print_exc())
            tfc = None

        if await is_visible_by_path(client, exit_generate_true_friend_window):
            await click_window_by_path(client, exit_generate_true_friend_window)

    print(tfc)
    return tfc


# UNFINISHED - requires some way to type in wiz's edit texts
async def accept_tfc(client: Client, tfc: str):
    async with client.mouse_handler:
        for i in range(2):
            await client.send_key(Keycode.F, 0.1)
            await asyncio.sleep(0.2)

        if await is_visible_by_path(client, enter_true_friend_code_button_path):
            await click_window_by_path(client, enter_true_friend_code_button_path)

        await asyncio.sleep(0.3)

        # *** This does not work ***
        for i in range(len(tfc)):
            # convert characters to keycodes, press each one
            await client.send_key(Keycode.W)
            await asyncio.sleep(0.15)

        # if await is_visible_by_path(client, )


async def exit_menus(c: Client, paths):
    for i in paths:
        click_button = await get_window_from_path(c.root_window, i)
        if click_button:
            if await click_button.is_visible():
                async with c.mouse_handler:
                    await c.mouse_handler.click_window(click_button)


async def close_npc_quest_menu(client: Client) -> bool:
    """Dismiss the NPC quest list using its own Exit button."""
    if await client.is_loading() or await client.in_battle():
        return False
    if not await is_visible_by_path(client, cancel_multiple_quest_menu_path):
        return False
    # An active quest dialogue takes priority over the list behind it.
    if await is_visible_by_path(client, advance_dialog_path):
        return False
    await safe_click_window(client, cancel_multiple_quest_menu_path)
    await asyncio.sleep(0.2)
    return True


async def safe_click_window(client: Client, path):
    if await is_visible_by_path(client, path):
        async with client.mouse_handler:
            await click_window_by_path(client, path)


async def click_window_by_path(client: Client, path: list[str], hooks: bool = False):
    # FULL CREDIT TO SIROLAF FOR THIS FUNCTION, notfaj was here :3
    # clicks window from path, must actually exist in the UI tree
    root = client.root_window
    windows = await get_window_from_path(root, path)
    if windows:
        async with client.mouse_handler:
            await client.mouse_handler.click_window(windows)


async def text_from_path(client: Client, path: list[str]) -> str:
    # Returns text from a window via the window path
    window = await get_window_from_path(client.root_window, path)
    return await window.maybe_text()


async def wait_for_loading_screen(client: Client):
    # Wait for a loading screen, then wait until the loading screen has finished.
    logger.debug(f"Client {client.title} - Awaiting loading")
    while not await client.is_loading():
        await asyncio.sleep(0.1)
    while await client.is_loading():
        await asyncio.sleep(0.1)


async def wait_for_zone_change(
    client: Client,
    current_zone: str = None,
    to_zone: str = None,
    loading_only: bool = False,
):
    # Wait for zone to change, allows for waiting in team up forever without any extra checks
    logger.debug(f"Client {client.title} - Awaiting loading")
    if not loading_only:
        # if to_zone is present, wait until we reach the selected zone
        if to_zone is not None:
            while await client.zone_name() != to_zone:
                await asyncio.sleep(0.1)

        # otherwise wait until our zone changes from whatever it was previously
        else:
            if current_zone is None:
                current_zone = await client.zone_name()

            while current_zone == await client.zone_name():
                await asyncio.sleep(0.1)

    # Second loading check incase theres some sort of phantom zone loading screens put us into
    while await client.is_loading():
        await asyncio.sleep(0.1)


async def spiral_door(
    client: Client, open_window: bool = True, cycles: int = 0, opt: int = 0
):
    # optionally open the spiral door window
    if open_window:
        while portal_kind(
            await get_popup_title(client)
        ) != "world_gate" and not await is_visible_by_path(client, spiral_door_path):
            await asyncio.sleep(0.1)

        while not await is_visible_by_path(client, spiral_door_path):
            await asyncio.sleep(0.1)
            await client.send_key(Keycode.X, 0.1)

    # bring menu back to first page
    for i in range(5):
        await client.send_key(Keycode.LEFT_ARROW, 0.1)
        await asyncio.sleep(0.25)

    # navigate menu to proper world
    world_path = spiral_door_path.copy()
    world_path.append(f"opt{opt}")
    await asyncio.sleep(0.5)
    for i in range(cycles):
        if i != 0:
            await client.send_key(Keycode.RIGHT_ARROW, 0.1)
            await asyncio.sleep(0.25)

    await click_window_by_path(client, world_path, True)
    await asyncio.sleep(1)
    current_zone = await client.zone_name()
    await click_window_by_path(client, spiral_door_teleport_path, True)
    await wait_for_zone_change(client, current_zone=current_zone)


async def navigate_to_ravenwood(client: Client):
    # navigates to commons from anywhere in the game
    current_zone = await client.zone_name()

    await client.send_key(Keycode.HOME, 0.1)
    await client.send_key(Keycode.HOME, 0.1)

    await wait_for_zone_change(client, current_zone=current_zone)
    await asyncio.sleep(3)
    use_spiral_door = False
    bartleby_navigation = True
    current_zone = await client.zone_name()
    match current_zone:
        # Handling for dorm room
        case "WizardCity/Interiors/WC_Housing_Dorm_Interior":
            await client.goto(70.15016174316406, 9.419374465942383)
            while not await client.is_loading():
                await client.send_key(Keycode.S, 0.3)
            await wait_for_zone_change(client, current_zone=current_zone)
            await asyncio.sleep(3)
            bartleby_navigation = False

        # Handling for arcanum apartment
        case "Housing_AR_Dormroom/Interior":
            while not await client.is_loading():
                await client.send_key(Keycode.S, 0.3)
            await wait_for_zone_change(client, current_zone=current_zone)
            await asyncio.sleep(3)
            await client.teleport(
                XYZ(x=-19.1153507232666, y=-6312.8994140625, z=-2.00579833984375)
            )
            await client.send_key(Keycode.D, 0.1)
            use_spiral_door = True

        # Any other house in the game
        case _:
            await client.send_key(Keycode.S, 2.5)
            use_spiral_door = True

    # Navigate through spiral door if needed
    if use_spiral_door:
        while not await is_visible_by_path(client, spiral_door_teleport_path):
            await client.send_key(Keycode.X, 0.1)
            await asyncio.sleep(2)
        await spiral_door(client)

    # Navigate through bartleby if needed
    if bartleby_navigation:
        await asyncio.sleep(1)
        current_zone = await client.zone_name()
        await asyncio.sleep(0.25)
        await client.teleport(
            XYZ(x=-15.123456001281738, y=-3244.67529296875, z=244.01925659179688)
        )
        await wait_for_zone_change(client, current_zone=current_zone)


async def navigate_to_commons_from_ravenwood(client: Client):
    # teleport to ravenwood exit
    current_zone = await client.zone_name()
    await asyncio.sleep(2)
    await client.teleport(
        XYZ(x=-0.7323388457298279, y=-2200.223388671875, z=-155.97055053710938)
    )
    await wait_for_zone_change(client, current_zone=current_zone)
    await asyncio.sleep(2)


async def navigate_to_potions(client: Client):
    hilda = XYZ(-4398.70654296875, 1016.1954345703125, 229.00079345703125)
    # make sure client is not loading
    while await client.is_loading():
        await asyncio.sleep(1)
    # Teleports to Hilda if not already in range
    while not await client.is_in_npc_range():
        await client.teleport(hilda)
        await asyncio.sleep(2)
    # Teleport to hilda brewer


async def closed_dungeon_popup(client: Client, dismiss: bool = False) -> bool:
    """Recognize a terminal instance error, not the retryable file-loading UI."""
    if not hasattr(client, 'root_window'):
        return False
    async def message(modal):
        values = []
        for name in ('TitleText', 'CaptionText'):
            parts = [plain_text(await read_control_text(c) or '')
                     for c in await modal.get_windows_with_name(name) if await c.is_visible()]
            values.append(' '.join(parts).strip().casefold())
        return values
    for modal in await client.root_window.get_windows_with_name('MessageBoxModalWindow'):
        if not await modal.is_visible():
            continue
        title, text = await message(modal)
        if title not in ('信息', '資訊', 'information'):
            continue
        if not (text.rstrip('。.') in ('区域加载失败，地下城已关闭', '區域載入失敗，地下城已關閉')
                or ('instance' in text and 'closed' in text and ('load' in text or 'area' in text))):
            continue
        operation = getattr(client, '_teleport_mark_return', None)
        if isinstance(operation, dict):
            operation['closed'] = True
        logger.warning(f'自动任务：{client.title} 区域加载失败，地下城已关闭；放弃本次旧标记回传。')
        if dismiss:
            layout = ['messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout']
            buttons = []
            for name in ('leftButton', 'centerButton', 'rightButton'):
                button = await get_window_from_path(modal, [*layout, name])
                if button and await button.is_visible():
                    buttons.append(button)
            if len(buttons) == 1:
                async with client.mouse_handler:
                    if (not await client.is_loading() and await modal.is_visible()
                            and await buttons[0].is_visible() and await message(modal) == [title, text]):
                        current = [button for name in ('leftButton', 'centerButton', 'rightButton')
                                   if (button := await get_window_from_path(modal, [*layout, name]))
                                   and await button.is_visible()]
                        if len(current) == 1:
                            await client.mouse_handler.click_window(current[0])
        return True
    return False








async def potion_zone_id(client: Client):
    """Use the existing ClientZone reader as extra evidence, not a new offset."""
    try:
        zone = await client.client_object.client_zone()
        value = await zone.zone_id() if zone else None
        return value if isinstance(value, int) and value > 0 else None
    except Exception:
        return None


async def observe_party_area(client: Client):
    """Invalidate retained area evidence on any observed loading/zone change."""
    try:
        loading = await client.is_loading()
        zone = await client.zone_name()
    except wizwalker.WizWalkerMemoryError:
        loading, zone = True, None
    state = (loading, zone)
    previous = getattr(client, '_party_area_observation', None)
    if previous is not None and state != previous:
        client._party_area_generation = getattr(client, '_party_area_generation', 0) + 1
    client._party_area_observation = state


async def _party_area_token(client: Client):
    area = await client.client_object.client_zone()
    # This address is compared within ONE client, not across processes. It
    # corroborates uninterrupted presence; Zone ID alone is not instance proof.
    return (await client.zone_name(), await area.read_base_address(), await area.zone_id(),
            getattr(client, '_party_area_generation', 0))


async def clients_share_live_area(first: Client, second: Client) -> bool:
    """Retain verified presence only while both clients' area tokens stay valid."""
    try:
        await observe_party_area(first)
        await observe_party_area(second)
        if await first.is_loading() or await second.is_loading():
            return False
        zone = await first.zone_name()
        if not zone or zone != await second.zone_name():
            return False

        async def remember_area():
            try:
                tokens = (await _party_area_token(first), await _party_area_token(second))
                if (await first.is_loading() or await second.is_loading()
                        or await first.zone_name() != zone or await second.zone_name() != zone):
                    return False
                if any(type(token[1]) is not int or token[1] <= 0
                       or type(token[2]) is not int or token[2] <= 0 for token in tokens):
                    return True  # Unreadable addresses/IDs cannot preserve proof.
                for observer, peer, pair in ((first, second, tokens),
                                             (second, first, tokens[::-1])):
                    proofs = getattr(observer, '_party_area_peers', None)
                    if not isinstance(proofs, dict):
                        proofs = observer._party_area_peers = {}
                    proofs[id(peer)] = pair
            except Exception:
                pass  # Live presence remains proof even when it cannot be retained.
            return True

        proofs = getattr(first, '_party_area_peers', {})
        retained = proofs.get(id(second)) if isinstance(proofs, dict) else None
        if retained is not None:
            try:
                if (retained == (await _party_area_token(first), await _party_area_token(second))
                        and not await first.is_loading() and not await second.is_loading()
                        and await first.zone_name() == zone and await second.zone_name() == zone):
                    return True
            except Exception:
                pass
            proofs.pop(id(second), None)
            peer_proofs = getattr(second, '_party_area_peers', {})
            if isinstance(peer_proofs, dict):
                peer_proofs.pop(id(first), None)
        for observer, peer in ((first, second), (second, first)):
            try:
                peer_id = await peer.client_object.global_id_full()
                entities = await SprintyClient(observer).get_base_entity_list()
            except wizwalker.WizWalkerMemoryError:
                continue
            for entity in entities:
                try:
                    if await entity.global_id_full() == peer_id:
                        if (not await first.is_loading() and not await second.is_loading()
                                and await first.zone_name() == zone
                                and await second.zone_name() == zone):
                            return await remember_area()
                        return False
                except wizwalker.WizWalkerMemoryError:
                    continue
        # Combat participants need not appear in the rendered entity tree.
        # Use existing duel readers, not zone-name equality or cached members.
        try:
            if await first.in_battle() and await second.in_battle():
                duel_id = await first.duel.duel_id_full()
                if (type(duel_id) is int and duel_id > 0
                        and duel_id == await second.duel.duel_id_full()):
                    player_ids = (await first.client_object.global_id_full(),
                                  await second.client_object.global_id_full())
                    if (all(type(value) is int and value > 0 for value in player_ids)
                            and player_ids[0] != player_ids[1]):
                        shared_roster = True
                        for observer in (first, second):
                            owners = set()
                            for participant in await observer.duel.participant_list():
                                try:
                                    if await participant.is_player():
                                        owner = await participant.owner_id_full()
                                        if type(owner) is int and owner > 0:
                                            owners.add(owner)
                                except wizwalker.WizWalkerMemoryError:
                                    continue
                            if not set(player_ids).issubset(owners):
                                shared_roster = False
                                break
                        if shared_roster:
                            if (not await first.is_loading() and not await second.is_loading()
                                    and await first.zone_name() == zone
                                    and await second.zone_name() == zone
                                    and await first.in_battle() and await second.in_battle()
                                    and await first.duel.duel_id_full() == duel_id
                                    and await second.duel.duel_id_full() == duel_id):
                                return await remember_area()
                            return False
        except Exception as exc:
            # An unreadable combat roster must not discard independent return
            # evidence. Cancellation still propagates to the caller.
            logger.debug(f'同场战斗证据暂时不可读，继续检查返回证据：{exc}')
        for returned, peer in ((first, second), (second, first)):
            context = getattr(returned, 'potion_return_context', None)
            if not isinstance(context, dict) or not context.get('returned_snapshot'):
                continue
            token = context.get('peer_areas', {}).get(id(peer))
            if (context.get('zone') == zone and token is not None
                    and token == await _party_area_token(peer)
                    and context.get('returned_area_token') == await _party_area_token(returned)
                    and context.get('zone_id') == await potion_zone_id(returned)):
                return not await returned.is_loading() and not await peer.is_loading()
    except Exception as exc:
        logger.debug(f'同副本证据暂时不可读，暂缓坐标同步：{exc}')
    return False














async def buy_potions(client: Client, recall: bool = True, original_zone=None):
    try:
        await asyncio.sleep(1.0)
        max_potions = await client.stats.potion_max()
        # buy potions and close the potions menu, and recall if needed
        for i in range(2):
            original_potion_count = await client.stats.potion_charge()
            current_potion_count = original_potion_count

            # buy potions until our potion count has either increased (we may not have enough gold for all potions) or we are at max potions
            while (
                current_potion_count == original_potion_count
                and current_potion_count < max_potions
            ):
                while not await is_visible_by_path(client, potion_shop_base_path):
                    await client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(0.5)

                await click_window_by_path(client, potion_fill_all_path, True)
                await asyncio.sleep(0.25)

                await click_window_by_path(client, potion_buy_path, True)
                await asyncio.sleep(0.25)

                while await is_visible_by_path(client, potion_shop_base_path):
                    await click_window_by_path(client, potion_exit_path, True)
                    await asyncio.sleep(0.125)

                current_potion_count = await client.stats.potion_charge()
                await asyncio.sleep(0.5)

            if i == 0:
                if await client.stats.potion_charge() >= 1.0:
                    original_potion_count = await client.stats.potion_charge()

                    logger.debug(f"Client {client.title} - Using potion")
                    await click_window_by_path(client, potion_usage_path, True)
                    await asyncio.sleep(3.0)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        from src.logging_utils import log_exception
        log_exception(client, '购买药水失败', exc)
        return False

    # Return only after the recall key has caused a confirmed loading/zone change.
    if recall:
        current_zone = await client.zone_name()

        if original_zone is None:
            logger.error(
                f"Client {client.title} - Cannot safely recall after buying "
                "potions because the original zone was not recorded."
            )
            return False

        # Only recall if we actually left the original zone.
        if original_zone != current_zone:
            return await recall_to_teleport_mark(client, expected_zone=original_zone)

    return True


async def to_world(clients, destinationWorld):
    world_hub_zones = [
        "WizardCity/WC_Hub",
        "Krokotopia/KT_Hub",
        "Marleybone/MB_Hub",
        "MooShu/MS_Hub",
        "DragonSpire/DS_Hub_Cathedral",
        "Grizzleheim/GH_MainHub",
        "Celestia/CL_Hub",
        "Wysteria/PA_Hub",
        "Zafaria/ZF_Z00_Hub",
        "Avalon/AV_Z00_Hub",
        "Azteca/AZ_Z00_Zocalo",
        "Khrysalis/KR_Z00_Hub",
        "Polaris/PL_Z00_Walruskberg",
        "Mirage/MR_Z00_Hub",
        "Empyrea/EM_Z00_Aeriel_HUB",
        "Karamelle/KM_Z00_HUB",
        "Lemuria/LM_Z00_Hub",
    ]
    world_list = [
        "WizardCity",
        "Krokotopia",
        "Marleybone",
        "MooShu",
        "DragonSpire",
        "Grizzleheim",
        "Celestia",
        "Wysteria",
        "Zafaria",
        "Avalon",
        "Azteca",
        "Khrysalis",
        "Polaris",
        "Mirage",
        "Empyrea",
        "Karamelle",
        "Lemuria",
    ]

    world_index = world_list.index(destinationWorld)
    destinationZone = world_hub_zones[world_index]

    zoneChanged = await toZone(clients, destinationZone)

    if zoneChanged == 0:
        logger.debug("Reached destination world: " + destinationWorld)
    else:
        logger.error(
            "Failed to go to zone.  It may be spelled incorrectly, or may not be supported."
        )


async def use_potion(client: Client):
    # Uses a potion if we have one
    if await client.stats.potion_charge() >= 1.0:
        logger.debug(f"Client {client.title} - Using potion")
        await click_window_by_path(client, potion_usage_path, True)


async def is_potion_needed(client: Client, minimum_mana: int = 16):
    # Get client stats for mana/hp
    mana = await client.stats.current_mana()
    max_mana = await client.stats.max_mana()
    health = await client.stats.current_hitpoints()
    max_health = await client.stats.max_hitpoints()
    client_level = await client.stats.reference_level()
    if minimum_mana > await client.stats.reference_level():
        minimum_mana = client_level
    combined_minimum_mana = int(0.23 * max_mana) + minimum_mana

    if max_health == 0:
        return False

    if mana < combined_minimum_mana or float(health) / float(max_health) < 0.55:
        return True
    else:
        return False


async def auto_potions_force_buy(
    client: Client, mark: bool = False, minimum_mana: int = 16
):
    was_refilling = getattr(client, 'refilling_potions', False)
    client.refilling_potions = True
    recalled = False
    try:
        # If we have any missing potions, get potions
        if await client.stats.potion_charge() < await client.stats.potion_max():
            await pause_dungeon_for_potions(client)
            original_zone = await client.zone_name()
            # do not recall from potion buy if we were already in the commons - wait for zone change will fail
            if original_zone == "WizardCity/WC_Hub":
                recall = False
            else:
                recall = True
                if not await ensure_teleport_mark(client):
                    logger.error(f"Client {client.title} - 药水补充失败：原地图标记未确认，取消出发。")
                    return False
            if recall and await client.zone_name() != original_zone:
                logger.error(f"Client {client.title} - 放置标记期间地图改变，取消补药。")
                return False
            # Navigate to ravenwood
            await navigate_to_ravenwood(client)
            # Navigate to commons
            await navigate_to_commons_from_ravenwood(client)
            # Navigate to hilda brewer
            await navigate_to_potions(client)
            # Buy potions
            recalled = await buy_potions(client, recall=recall, original_zone=original_zone)

            if await is_potion_needed(client, minimum_mana):
                await use_potion(client)

            return recalled

        return True
    finally:
        client.refilling_potions = was_refilling
        if not was_refilling:
            await finish_dungeon_potions(client, recalled=recalled is True)


async def is_control_grayed(button):
    return await button.read_value_from_offset(688, "bool")


async def change_equipment_set(client: Client, set_number: int):
    async with client.mouse_handler:
        # Press B until backpack opens
        while not await is_visible_by_path(client, backpack_is_visible_path):
            await client.send_key(Keycode.B, 0.1)

        # Click open equipment page button.  Corrects for failed clicks
        while await is_visible_by_path(client, backpack_title_path):
            while not await is_visible_by_path(
                client, equipment_set_manager_title_path
            ):
                await client.mouse_handler.click_window_with_name("EquipmentManager")

        # Click specific set
        individual_equipment_set = individual_equipment_set_parent_path.copy()
        individual_equipment_set.append("equippedIcon" + str(set_number))
        for i in range(8):
            await click_window_by_path(client, individual_equipment_set)

        # Click equipment set button.  Corrects for failed clicks
        while await is_visible_by_path(
            client, backpack_title_path
        ) or await is_visible_by_path(client, equipment_set_manager_title_path):
            await client.send_key(Keycode.B, 0.1)


class FriendBusyOrInstanceClosed(Exception):
    def __init__(
        self,
        msg="Friend was busy / has teleports disabled, or you attempted to enter an area that is no longer accessible",
        *args,
        **kwargs,
    ):
        super().__init__(msg, *args, **kwargs)


class LoadingScreenNotFound(Exception):
    def __init__(
        self,
        msg="The client never entered a loading screen and safe_wait_for_zone_change timed out",
        *args,
        **kwargs,
    ):
        super().__init__(msg, *args, **kwargs)


async def safe_wait_for_zone_change(
    self: Client,
    name: Optional[str] = None,
    *,
    sleep_time: Optional[float] = 0.5,
    timeout=10.0,
    handle_hooks_if_needed=True,
):
    # you should generally provide a zone name via the parameter to prevent a race condition
    if name is None:
        name = await self.zone_name()

    start_time = time.time()
    client_was_in_loading = False
    while await self.zone_name() == name:
        # check so we know if the client ever actually entered a loading screen
        if await self.is_loading():
            client_was_in_loading = True

        if await is_visible_by_path(self, friend_is_busy_and_dungeon_reset_path):
            async with self.mouse_handler:
                await click_window_by_path(self, friend_is_busy_and_dungeon_reset_path)

            raise FriendBusyOrInstanceClosed

        if timeout is not None:
            # X seconds have passed
            if time.time() > start_time + timeout and not client_was_in_loading:
                if await self.is_loading():
                    client_was_in_loading = True
                # if after X seconds we have not entered a loading screen and have not seen a friend is busy popup, we're in the same zone
                else:
                    raise LoadingScreenNotFound

        await asyncio.sleep(sleep_time)


async def teleport_mark_is_available(client: Client) -> bool:
    """Return True only when the Recall button exists and is enabled."""
    try:
        recall_window = await get_window_from_path(
            client.root_window, teleport_mark_recall_path
        )
        if not recall_window:
            logger.debug(f"Client {client.title} - Recall 按钮路径未找到。")
            return False
        grayed = await recall_window.is_control_grayed()
        if grayed:
            logger.debug(f"Client {client.title} - Recall 按钮存在，但读取为灰色。")
        return not grayed
    except Exception as exc:
        logger.warning(f"Client {client.title} - 无法读取 Recall 按钮：{exc}")
        return False


async def wait_for_teleport_mark_timer(client: Client, timeout: float = 90.0):
    """Wait for the mark/recall cooldown without spamming the hotkey."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation = getattr(client, '_teleport_mark_return', None)
        if isinstance(operation, dict):
            if (operation['closed'] or await closed_dungeon_popup(client, dismiss=True)
                    or (operation['questing'] and not client.questing_status)
                    or await client.in_battle()):
                return False
        try:
            timer_window = await get_window_from_path(
                client.root_window, teleport_mark_recall_timer_path
            )
            if not timer_window:
                return True
            timer_text = (await timer_window.maybe_text()) or ""
            match = re.search(r"-?\d+", timer_text)
            if not match or int(match.group()) <= 0:
                return True
            remaining = int(match.group())
            logger.debug(
                f"Client {client.title} - Waiting {remaining}s for teleport "
                "mark cooldown."
            )
            await asyncio.sleep(max(0.25, min(float(remaining - 1), 5.0)))
        except (TypeError, ValueError):
            return True
        except Exception:
            await asyncio.sleep(0.25)

    logger.warning(
        f"Client {client.title} - Timed out waiting for teleport mark cooldown."
    )
    return False


async def ensure_teleport_mark(client: Client, attempts: int = 2) -> bool:
    """Place a mark and verify the game accepted it before leaving the area."""
    mark_zone = await client.zone_name()
    if mark_zone == "WizardCity/WC_Hub":
        return False

    loading_deadline = time.monotonic() + 30.0
    while await client.is_loading() and time.monotonic() < loading_deadline:
        await asyncio.sleep(0.2)
    if await client.is_loading() or await client.in_battle():
        logger.warning(
            f"Client {client.title} - Cannot place a teleport mark while busy."
        )
        return False

    if not await wait_for_teleport_mark_timer(client):
        return False

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            logger.debug(
                f"Client {client.title} - Moving backward for 3 seconds "
                "before the second teleport mark attempt."
            )
            await client.send_key(Keycode.S, 3.0)
            await asyncio.sleep(0.75)
            if (
                await client.is_loading()
                or await client.in_battle()
                or await client.zone_name() != mark_zone
            ):
                logger.error(
                    f"Client {client.title} - 地图在第二次放置标记前发生变化，"
                    "取消补药。"
                )
                return False

        mana_before = await client.stats.current_mana()
        mark_before = await teleport_mark_is_available(client)
        logger.debug(
            f"Client {client.title} - Placing teleport mark "
            f"(attempt {attempt}/{attempts})."
        )
        await client.send_key(Keycode.PAGE_DOWN, 0.2)

        # UI and mana may update on different frames.  An unavailable->available
        # Recall button proves a new mark.  If a previous mark existed, spent
        # mana confirms that it was replaced rather than the key being lost.
        verify_deadline = time.monotonic() + 2.5
        while time.monotonic() < verify_deadline:
            await asyncio.sleep(0.25)
            mark_after = await teleport_mark_is_available(client)
            mana_after = await client.stats.current_mana()
            if mark_after and (not mark_before or mana_after < mana_before):
                logger.debug(f"Client {client.title} - Teleport mark confirmed.")
                await asyncio.sleep(0.75)
                return True

        mark_after = await teleport_mark_is_available(client)
        if mark_before and not mark_after:
            logger.debug(
                f"Client {client.title} - Previous teleport mark was cleared; "
                "waiting before setting the new mark."
            )
        else:
            logger.warning(
                f"Client {client.title} - The game did not confirm the mark "
                f"input ({attempt}/{attempts})."
            )
        await asyncio.sleep(1.0)

    return False


async def recall_to_teleport_mark(
    client: Client, expected_zone: Optional[str] = None, attempts: int = 3
) -> bool:
    """Use the original double-PAGE_UP recall and verify the zone transition."""
    previous_operation = getattr(client, '_teleport_mark_return', None)
    operation = {'closed': False, 'questing': bool(getattr(client, 'questing_status', False))}
    client._teleport_mark_return = operation

    async def abandoned():
        # A UI watcher may already have dismissed the error. Keep its failure
        # only for this operation, so disappearing UI cannot imply success.
        if operation['closed'] or await closed_dungeon_popup(client, dismiss=True):
            return True
        return ((operation['questing'] and not client.questing_status)
                or await client.in_battle())

    try:
        for attempt in range(1, attempts + 1):
            if await abandoned():
                return False
            ready_deadline = time.monotonic() + 30.0
            while await client.is_loading() and time.monotonic() < ready_deadline:
                if await abandoned():
                    return False
                await asyncio.sleep(.2)
            if await client.is_loading() or await client.in_battle():
                logger.error(f"Client {client.title} - 当前仍在加载或战斗，无法安全回传。")
                return False
            if await abandoned():
                return False
            departure_zone = await client.zone_name()
            if expected_zone is not None and departure_zone == expected_zone:
                return True

            if not await wait_for_teleport_mark_timer(client):
                return False

            if await abandoned() or await client.is_loading():
                return False

            logger.debug(
                f"Client {client.title} - Recalling to teleport mark "
                f"(attempt {attempt}/{attempts})."
            )
            # The original WizSprinter flow sends the shortcut twice.  Reading the
            # Recall button's grayed state is unreliable immediately after closing
            # the potion shop, while the game still accepts this key sequence.
            await client.send_key(Keycode.PAGE_UP, 0.1)
            if await abandoned():
                return False
            if not await client.is_loading():
                await client.send_key(Keycode.PAGE_UP, 0.1)

            travel_started = False
            start_deadline = time.monotonic() + 12.0
            while time.monotonic() < start_deadline:
                if await abandoned():
                    return False
                if await client.is_loading() or await client.zone_name() != departure_zone:
                    travel_started = True
                    break
                await asyncio.sleep(0.2)

            if not travel_started:
                logger.warning(
                    f"Client {client.title} - Recall input was not accepted "
                    f"({attempt}/{attempts})."
                )
                await asyncio.sleep(1.0)
                continue

            loading_deadline = time.monotonic() + 45.0
            while await client.is_loading() and time.monotonic() < loading_deadline:
                if await abandoned():
                    return False
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.25)
            if await abandoned():
                return False

            arrival_zone = await client.zone_name()
            if not await client.is_loading() and (expected_zone is None or arrival_zone == expected_zone):
                if await abandoned():
                    return False
                logger.debug(f"Client {client.title} - Return to teleport mark confirmed.")
                return True

            logger.warning(
                f"Client {client.title} - Recall arrived in {arrival_zone!r}, "
                f"expected {expected_zone!r}."
            )

        logger.error(
            f"Client {client.title} - Failed to return to teleport mark after "
            f"{attempts} attempts."
        )
        return False
    finally:
        if getattr(client, '_teleport_mark_return', None) is operation:
            if previous_operation is None:
                del client._teleport_mark_return
            else:
                client._teleport_mark_return = previous_operation


async def click_window_until_closed(client: Client, path):
    if await is_visible_by_path(client, path):
        async with client.mouse_handler:
            while await is_visible_by_path(client, path):
                await click_window_by_path(client, path)
        return True

    else:
        return False


async def refill_potions(
    client: Client, mark: bool = False, recall: bool = True, original_zone=None
):
    was_refilling = getattr(client, 'refilling_potions', False)
    client.refilling_potions = True  # Before any departure/mark await.
    result = False
    try:
        await pause_dungeon_for_potions(client)
        if await client.stats.reference_level() >= 6:
            starting_zone = await client.zone_name()
            if original_zone is None:
                original_zone = starting_zone

            if recall and original_zone != starting_zone:
                logger.error(f"Client {client.title} - 原地图 {original_zone!r} 与出发地图 {starting_zone!r} 不符，取消补药。")
                return False
            if (recall or mark) and starting_zone != "WizardCity/WC_Hub":
                if not await ensure_teleport_mark(client):
                    logger.error(f"Client {client.title} - 药水补充失败：原地图标记未确认，取消出发。")
                    return False

            if recall and await client.zone_name() != original_zone:
                logger.error(f"Client {client.title} - 放置标记期间地图改变，取消补药。")
                return False
            # Navigate to ravenwood
            await navigate_to_ravenwood(client)
            # Navigate to commons from ravenwood
            await navigate_to_commons_from_ravenwood(client)
            # Navigate to hilda brewer
            await navigate_to_potions(client)
            # Buy potions
            result = await buy_potions(client, recall, original_zone=original_zone)
            return result

        return False
    finally:
        client.refilling_potions = was_refilling
        if not was_refilling:
            await finish_dungeon_potions(client, recalled=recall and result is True)


async def refill_potions_if_needed(
    p: Client, mark: bool = False, recall: bool = True, original_zone=None
):
    if await p.stats.potion_charge() < 1.0 and await p.stats.reference_level() >= 6:
        return await refill_potions(p, mark, recall, original_zone)
    return True


async def auto_potions(
    client: Client, mark: bool = False, minimum_mana: int = 16, buy: bool = True
):
    # No unlocked potion slots is a normal state, not a refill failure.
    if await client.stats.potion_max() <= 0:
        return True
    if await is_potion_needed(client, minimum_mana):
        await use_potion(client)
    # If we have less than 1 potion left, get potions
    if buy:
        return await refill_potions_if_needed(client, mark=mark)
    return True


async def wait_for_window_by_path(
    client: Client, path: list[str], hooks: bool = False, click: bool = True
):
    while not await is_visible_by_path(client, path):
        await asyncio.sleep(0.1)
    if click or hooks:
        await click_window_by_path(client, path, hooks)


# From peechez's dance game bot
async def maybe_find_window_by_name(parent, name):
    for child in await parent.children():
        if await child.name() == name:
            return child
    return None


# From peechez's dance game bot
async def wait_and_return_window_by_path(parent, *path):
    window = parent
    for name in path:
        while (maybe_window := await maybe_find_window_by_name(window, name)) is None:
            pass
        window = maybe_window
    return window


async def post_keys(client, keys):
    user32_dance = ctypes.windll.user32

    for key in keys:
        user32_dance.PostMessageW(client.window_handle, 0x100, ord(key), 0)
        user32_dance.PostMessageW(client.window_handle, 0x101, ord(key), 0)


def refresh_character_memory(client: Client):
    """Drop addresses owned by the previous character/world, not live hooks."""
    client._world_view_window = None
    client._character_registry_addr = None
    client._quest_client_manager_addr = None


async def logout_and_in(client: Client):
    # Improved version of Major's logging out and in function
    intentional = getattr(client, '_intentional_character_switch', False)
    client._intentional_character_switch = True
    try:
        await client.send_key(Keycode.ESC, 0.1)
        await wait_for_window_by_path(client, quit_button_path, True)
        await asyncio.sleep(0.25)
        if await is_visible_by_path(client, dungeon_warning_path):
            await client.send_key(Keycode.ENTER, 0.1)
        await wait_for_window_by_path(client, play_button_path, True)
        refresh_character_memory(client)
        # TODO: Find a better solution to waiting for load in screen to end
        await asyncio.sleep(4)
        if await client.is_loading():
            await wait_for_loading_screen(client)
    finally:
        refresh_character_memory(client)
        client._intentional_character_switch = intentional




async def is_free(client: Client):
    # Returns True if not in combat, loading screen, or in dialogue.
    if (getattr(client, 'questing_status', False) is not True
            and getattr(client, '_character_selection_active', False) is True):
        return False
    return not any(
        [
            await client.is_loading(),
            await client.in_battle(),
            await is_visible_by_path(client, advance_dialog_path),
        ]
    )


async def get_quest_name(client: Client):
    while not await is_free(client):
        await asyncio.sleep(0.1)
    quest_name_window = await get_window_from_path(client.root_window, quest_name_path)
    while not await is_visible_by_path(client, quest_name_path):
        await asyncio.sleep(0.1)
    quest_objective = await quest_name_window.maybe_text()
    quest_objective = quest_objective.replace("<center>", "")
    quest_objective = quest_objective.replace("</center>", "")
    return quest_objective


# quest_number - 0-3
# opens book, selects quest, and then closes book
async def select_quest_from_questbook(
    client: Client, quest_book_sort: list[str], quest_number: int
):

    while not await is_visible_by_path(client, quest_book_sort):
        await client.send_key(Keycode.Q)
        await asyncio.sleep(0.5)

    if await is_visible_by_path(client, quest_book_sort):
        await click_window_by_path(client, quest_book_sort)

    await asyncio.sleep(0.5)

    quest_number_path = quest_buttons_parent_path[:]
    quest_number_path.append("wndQuestInfo" + str(quest_number))
    quest_number_path.append("questInfoWindow")
    quest_number_path.append("wndQuestInfo")
    quest_number_path.append("txtGoal")
    print(quest_number_path)

    for i in range(5):
        if await is_visible_by_path(client, quest_number_path):
            await click_window_by_path(client, quest_number_path)
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.5)

    while await is_visible_by_path(client, quest_book_sort):
        await client.send_key(Keycode.Q)
        await asyncio.sleep(0.5)


async def get_popup_title(client: Client) -> Optional[str]:
    try:
        if not await is_visible_by_path(client, popup_title_path):
            return None
        popup_window = await get_window_from_path(client.root_window, popup_title_path)
        if popup_window is None:
            return None
        popup_str = await read_control_text(popup_window)
        return popup_str.replace("<center>", "").replace("</center>", "")
    except (wizwalker.errors.MemoryReadError, ValueError, UnicodeError):
        # The popup can be replaced between visibility and text reads.
        return None


async def is_popup_title_relevant(client: Client, quest_info: str = None) -> bool:
    if not quest_info:
        quest_info = await get_quest_name(client)

    popup_text = await get_window_from_path(client.root_window, popup_title_path)
    maybe_collect_item = await popup_text.maybe_text()
    if maybe_collect_item.lower() in str(quest_info).lower():
        return True
    return False


async def get_spiral_teleport_button(client: Client):
    # Multiple anonymous modal roots can coexist. Follow every matching branch
    # instead of accepting the first (possibly hidden) path match.
    async def follow(window, names):
        if not names:
            return window if await window.is_visible() else None
        for child in await window.children():
            if await child.name() == names[0] and await child.is_visible():
                found = await follow(child, names[1:])
                if found is not None:
                    return found
        return None

    for path in (spiral_door_teleport_path, spiral_door_teleport_path[1:]):
        found = await follow(client.root_window, path)
        if found is not None:
            return found
    return None


async def is_spiral_door_open(client: Client) -> bool:
    return await get_spiral_teleport_button(client) is not None


async def get_spiral_selected_world(client: Client):
    """Resolve selection from the live option pane, not mere dialog visibility.

    UniverseMap.gui places the movable check mark over the selected wbtn icon,
    inside optionWindow. All positions below come from the actual controls.
    """
    button = await get_spiral_teleport_button(client)
    if button is None:
        return None, None
    parent = await button.parent()
    panel = await parent.get_child_by_name(spiral_door_path[-1]) if parent else None
    if panel is None or not await panel.is_visible():
        return None, button
    children = await panel.children()
    marks = [w for w in children if await w.name() == spiral_door_selected_path[-1]
             and await w.is_visible()]
    if len(marks) != 1:
        return None, button
    rect = await marks[0].window_rectangle()
    x, y = (rect.x1 + rect.x2) / 2, (rect.y1 + rect.y2) / 2
    selected = []
    for window in children:
        name = await window.name()
        if not name.startswith('wbtn') or not await window.is_visible():
            continue
        icon = await window.window_rectangle()
        if icon.x1 <= x < icon.x2 and icon.y1 <= y < icon.y2:
            selected.append(name[4:])
    return (selected[0] if len(selected) == 1 else None), button


async def spiral_door_with_quest(client: Client):
    from src.automation_ownership import automation_owner
    from src.logging_utils import log_blocked, log_exception
    from src.teleport_math import (_movement_task_snapshot, _movement_body,
                                   _movement_control_changed, _movement_stop_reason, _same_movement_task)
    task = await _movement_task_snapshot(client)
    if task is None or type(task[0]) is not int or task[0] <= 0 or type(task[1]) is not int:
        log_blocked(client, task, '世界门任务信息暂不可读，停止本轮恢复')
        return False
    _, control = await _movement_body(client)
    state = getattr(client, '_quest_world_gate_recovery', None)
    if not isinstance(state, dict) or state['task'] != task[:2]:
        state = dict(task=task[:2], fallback_used=False, blocked='')
        client._quest_world_gate_recovery = state
    async def stopped():
        if hasattr(client, 'is_running') and not client.is_running():
            return 'world-gate client disconnected'
        reason = await _movement_stop_reason(client, task[2], task[3], task,
                                             interaction=False, control=control)
        if not reason and any(getattr(client, flag, False) for flag in (
                'refilling_potions', 'quest_recovery_owner', 'post_combat_movement_active',
                'feeding_pet_status', 'entity_detect_combat_status', 'quest_party_safe_return_active')):
            reason = 'world-gate control unavailable'
        if reason:
            logger.debug(f"Client {client.title}: 世界门旧恢复停止：{reason}")
        return reason

    def blocked(reason):
        state['blocked'] = reason
        client._quest_internal_block_reason = reason
        log_blocked(client, task, reason)
        return False

    button_read_failed = False

    async def read_enter_button():
        nonlocal button_read_failed
        try:
            button = await get_spiral_teleport_button(client)
            if button is None or not await button.is_visible():
                return 'UNKNOWN', None
            grayed = await button.is_control_grayed()
            if grayed is False:
                return 'AVAILABLE', button
            if grayed is True:
                return 'DISABLED', button
        except Exception as exc:
            # A disappearing/unreadable control is not evidence of a gray button.
            if not button_read_failed:
                log_exception(client, '世界门进入按钮暂不可读，有限等待重读', exc, level='DEBUG')
                button_read_failed = True
        return 'UNKNOWN', None

    async def enter(world, *, explicit_selection=False):
        async with client.mouse_handler:
            if await stopped():
                return False
            status, button = await read_enter_button()
            if status != 'AVAILABLE':
                return blocked(f'世界门点击前进入按钮状态变为 {status}，停止本轮恢复')
            if explicit_selection:
                selected, _ = await get_spiral_selected_world(client)
                if selected != world:
                    raise RuntimeError(f'未能确认明确选择魔法城：selected={selected}, expected={world}')
            logger.debug(f"Client {client.title}: 世界门进入按钮可用，目标验证={world}，明确选世界={explicit_selection}")
            if await stopped():
                return False
            await client.mouse_handler.click_window(button)
        while True:
            # Once enter was clicked, loading and the expected world transition
            # are allowed. A different task/control or unexpected zone aborts.
            if (not getattr(client, 'questing_status', True)
                    or (hasattr(client, 'is_running') and not client.is_running())
                    or await _movement_control_changed(client, control)
                    or await client.in_battle()
                    or any(getattr(client, flag, False) for flag in (
                        'refilling_potions', 'quest_recovery_owner', 'post_combat_movement_active',
                        'feeding_pet_status', 'entity_detect_combat_status', 'quest_party_safe_return_active'))
                    or (await client.quest_id(), await client.goal_id()) != task[:2]):
                logger.debug(f"Client {client.title}: 世界门进入等待期间任务或控制状态变化，停止旧恢复")
                return False
            zone = await client.zone_name()
            if world is not None and zone != task[3] and zone.split('/', 1)[0] != world:
                logger.debug(f"Client {client.title}: 世界门区域已变化为 {zone}，停止旧恢复")
                return False
            if (zone == task[3] and not await client.is_loading()
                    and not _same_movement_task(task, await _movement_task_snapshot(client))):
                logger.debug(f"Client {client.title}: 世界门等待期间目标已变化或不可读，停止旧恢复")
                return False
            if (isinstance(zone, str) and zone and zone != task[3]
                    and not await client.is_loading()
                    and (world is None or zone.split('/', 1)[0] == world)):
                fresh = await _movement_task_snapshot(client)
                if fresh is None:
                    log_blocked(client, task, f'区域已变化为 {zone}，任务信息暂不可读，等待重新读取')
                    return False
                if fresh[:2] != task[:2]:
                    return False
                if await client.is_loading() or await client.zone_name() != zone:
                    await asyncio.sleep(.1)
                    continue
                # Fresh task/zone reads may yield to a different controller.
                if (not getattr(client, 'questing_status', True)
                        or (hasattr(client, 'is_running') and not client.is_running())
                        or await _movement_control_changed(client, control)
                        or await client.in_battle()
                        or any(getattr(client, flag, False) for flag in (
                            'refilling_potions', 'quest_recovery_owner', 'post_combat_movement_active',
                            'feeding_pet_status', 'entity_detect_combat_status', 'quest_party_safe_return_active'))
                        or (await client.quest_id(), await client.goal_id()) != task[:2]):
                    return False
                client._quest_internal_block_reason = ''
                logger.debug(f"Client {client.title}: 已确认到达 {zone}，已重新读取任务 {fresh[:2]}；交回原自动任务")
                return True
            await asyncio.sleep(.1)

    async with automation_owner(client, 'world-gate'):
        if await stopped():
            return False
        # Inspect the real enter control before optional goal/selection metadata.
        # UNKNOWN may settle, but only a visible, explicitly gray control can
        # spend the persistent one-time WizardCity fallback budget.
        try:
            async with asyncio.timeout(15):
                while True:
                    if await stopped():
                        return False
                    status, _ = await read_enter_button()
                    if status != 'UNKNOWN':
                        break
                    await asyncio.sleep(.1)
                if status == 'AVAILABLE':
                    state['blocked'] = ''
                    client._quest_internal_block_reason = ''
                    expected = None
                    try:
                        quest = (await (await client.quest_manager()).quest_data()).get(task[0])
                        goal = (await quest.goal_data()).get(task[1]) if quest else None
                        destination = await goal.goal_destination_zone() if goal else None
                        expected = destination.split('/', 1)[0] if isinstance(destination, str) and destination else None
                    except Exception as exc:
                        # The goal is optional for entering; a real zone change
                        # remains required even when destination reads fail.
                        log_exception(client, '世界门目标世界暂不可读，按实际切区确认进入', exc, level='DEBUG')
                    return await enter(expected)
        except Exception as exc:
            log_exception(client, '世界门按钮读取或进入未确认', exc, level='DEBUG')
            return blocked(f'世界门按钮读取或进入未确认：{type(exc).__name__}: {exc}')
        if await stopped():
            return False  # Never start a fallback using an old task/area.
        status, _ = await read_enter_button()
        if status != 'DISABLED':
            return False
        if state['fallback_used']:
            return blocked('世界门进入按钮仍灰色；同一任务魔法城回退已使用 1/1 次')
        state['fallback_used'] = True  # Persistent on the client, including cancellation/restarts.
        logger.bind(client=client.title).info('世界门进入按钮明确灰色；执行魔法城回退 1/1 次')
        try:
            async with asyncio.timeout(15):
                if not await go_to_new_world(client, 'WizardCity', open_window=False,
                                              select_only=True, stop=stopped):
                    if await stopped():
                        return False
                    raise RuntimeError('未能明确选择魔法城')
                return await enter('WizardCity', explicit_selection=True)
        except Exception as exc:
            if await stopped():
                return False
            log_exception(client, '魔法城回退未确认', exc, level='DEBUG')
            return blocked(f'魔法城回退失败（本任务已使用 1/1 次）：{type(exc).__name__}: {exc}')


async def sync_camera(client: Client, xyz: XYZ = None, yaw: float = None):
    # Teleports the freecam to a specified position, yaw, etc.
    if not xyz:
        xyz = await client.body.position()

    if not yaw:
        yaw = await client.body.yaw()

    xyz.z += 200

    camera = await client.game_client.free_camera_controller()
    await camera.write_position(xyz)
    await camera.write_yaw(yaw)


async def _cycle_to_online_friends(client, friends_list):
    """Select the online-friends list without assuming an English client."""
    list_label = await _maybe_get_named_window(friends_list, "lblFriendsList")
    right_button = await _maybe_get_named_window(friends_list, "btnListTypeRight")
    online_labels = {
        "online friends",
        "在线好友",
        "在線好友",
        "在线朋友",
        "在線朋友",
    }

    async def _get_text():
        current_text = await list_label.maybe_text()
        if current_text is None:
            raise ValueError("Friend's list has no label")
        return current_text.replace("<center>", "").replace("</center>", "").strip()

    # There are only a few list types.  The fixed bound prevents a translated
    # or changed label from leaving the follower stuck in an endless loop.
    for _ in range(8):
        current_page = await _get_text()
        if (
            matches_text(current_page, "GUI_00000510", "GUI_FriendsOnline")
            or current_page.casefold() in online_labels
        ):
            return
        await client.mouse_handler.click_window(right_button)
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if await _get_text() != current_page:
                break
            await asyncio.sleep(0.1)

    raise ValueError("Could not find the online friends list")


class FriendLookupError(ValueError):
    """A lookup/UI failure is distinct from an explicit instance rejection."""
    def __init__(self, reason, message='', *, candidate_count=None, match_method='name'):
        self.reason = reason
        self.candidate_count = candidate_count
        self.match_method = match_method
        super().__init__(f'{reason}: {message}')


async def _cycle_friends_list(
    client, right_button, friends_list, icon, icon_list, name, current_page, *, strict_identity=None
):
    automatic = (getattr(client, 'questing_status', False) is True
                 if strict_identity is None else strict_identity)
    name = " ".join(plain_text(name).split()).casefold() if name is not None else None
    if automatic and not name:
        raise FriendLookupError('quester-name-unavailable')
    list_text = await friends_list.maybe_text() or ''
    candidates = []
    for idx, friend_entry in enumerate(_friend_list_entry.finditer(list_text)):
        friend_name = " ".join(plain_text(friend_entry.group('name')).split()).casefold()
        # Automatic followers keep the complete reference identity. Icons are
        # cosmetic and cannot override a name or authorize a name-less lookup.
        if not automatic and icon is not None and icon_list is not None:
            if (int(friend_entry.group('icon_index')) != icon
                    or int(friend_entry.group('icon_list')) != icon_list):
                continue
        elif not name:
            raise RuntimeError('Invalid args')
        if name and not (friend_name == name or (not automatic and
                ' ' not in name and friend_name.split(maxsplit=1)[0] == name)):
            continue
        candidates.append((friend_entry, idx))
    client._friend_lookup_diagnostic = dict(candidate_count=len(candidates),
        match_method='name' if name else 'icon')
    if len(candidates) > 1:
        raise FriendLookupError('ambiguous-friend-match', '好友匹配不唯一；请使用完整角色名',
                                candidate_count=len(candidates))
    match, idx = candidates[0] if candidates else (None, 0)
    if match is not None:
        target_page = (idx // 10) + 1
        if target_page < current_page:
            raise FriendLookupError('friend-ui-not-ready', '目标好友在前一页；未确认翻页方向')
        for _ in range(target_page - current_page):
            await client.mouse_handler.click_window(right_button)
    return match, idx


# TODO: add error if friend is busy message pops up
async def _request_friend_teleport(client, character_window):
    """Request only: the follower still must prove loading ended and the same live instance."""
    try:
        await _teleport_to_friend(client, character_window)
    except ValueError as exc:
        # The installed helper already clicked GoToFriend before these lookups.
        # It also already waited finitely for the modal. Never repeat the friend
        # selection merely because a late/disappearing confirmation was absent.
        if str(exc) == 'No child window named btnGoToFriend':
            raise FriendLookupError('friend-entry-found-but-tp-unavailable') from exc
        if str(exc) not in {f'No child window named {name}' for name in (
                'MessageBoxModalWindow', 'centerButton', 'btnCharacterClose')}:
            raise
        logger.debug(f"[friend_request] {client.title}: confirmation pending/disappeared: {exc}; "
                     "request not retried, await actual instance arrival")
        return False
    return True  # Confirmation input only, never an arrival result.


async def teleport_to_friend_from_list(
    client, *, icon_list: int = None, icon_index: int = None, name: str = None,
    strict_identity: bool = False
):
    """
    Teleport to a friend from the client's friend list

    Args:
        client: Client to teleport
        icon_list: Icon list the icon is from (1 or 2) or None
        icon_index: Index of the icon or None
        name: Name of the player or None
        strict_identity: Require the complete reference name for automatic followers
    """
    if (
        icon_list is None
        and icon_index is not None
        or icon_list is not None
        and icon_index is None
    ):
        raise ValueError("Icon list and icon index must both be defined or not defined")

    if all(i is None for i in (icon_list, icon_index, name)):
        raise ValueError("Must specify icon_list and icon_index or name or all")

    automatic = getattr(client, 'questing_status', False) is True
    client._friend_lookup_diagnostic = dict(candidate_count=None, match_method='name' if name else 'icon')
    if strict_identity and not (name and plain_text(name).strip()):
        raise FriendLookupError('quester-name-unavailable')
    stage = 'friend-ui-not-ready'
    try:
        try:
            friends_window = await _maybe_get_named_window(
                client.root_window, "NewFriendsListWindow"
            )
        except ValueError:
            # friend's list isn't open so open it
            friend_button = await _maybe_get_named_window(client.root_window, "btnFriends")
            await client.mouse_handler.click_window(friend_button)

            friends_window = await _maybe_get_named_window(
                client.root_window, "NewFriendsListWindow"
            )
        else:
            if not await friends_window.is_visible():
                # friend's list isn't open so open it
                friend_button = await _maybe_get_named_window(
                    client.root_window, "btnFriends"
                )
                await client.mouse_handler.click_window(friend_button)

        await _cycle_to_online_friends(client, friends_window)

        friends_list_window = await _maybe_get_named_window(friends_window, "listFriends")
        friends_list_text = await friends_list_window.maybe_text()

        # no friends online
        if not friends_list_text:
            raise FriendLookupError('quester-not-found', candidate_count=0)

        right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
        page_number = await _maybe_get_named_window(friends_window, "PageNumber")

        page_number_text = await page_number.maybe_text()

        current_page, _ = map(
            int,
            page_number_text.replace("<center>", "")
            .replace("</center>", "")
            .replace(" ", "")
            .split("/"),
        )

        friend, friend_index = await _cycle_friends_list(
            client,
            right_button,
            friends_list_window,
            icon_index,
            icon_list,
            name,
            current_page,
            strict_identity=strict_identity,
        )

        if friend is None:
            raise FriendLookupError('quester-not-found', candidate_count=0)

        if strict_identity:
            # A re-sort or task stop while reading/paging must not click a
            # different row. This reuses the parsed list's actual row index.
            current = list(_friend_list_entry.finditer(await friends_list_window.maybe_text() or ''))
            if (friend_index >= len(current) or current[friend_index].group(0) != friend.group(0)
                    or not client.questing_status or await client.is_loading() or await client.in_battle()):
                raise FriendLookupError('friend-ui-not-ready', '好友列表或客户端状态已变化')
        stage = 'friend-tp-click-failed'
        await _click_on_friend(client, friends_list_window, friend_index)

        stage = 'friend-ui-not-ready'
        if automatic and not strict_identity:
            character_window = await _maybe_get_named_window(client.root_window, 'wndCharacter')
        else:
            # Other UI panels retain hidden wndCharacter children. Only the live
            # profile opened from this friend list may receive the teleport click.
            character_window = None
            for attempt in range(5):
                visible_profiles = []
                for window in await client.root_window.get_windows_with_name("wndCharacter"):
                    if (await window.is_visible()
                            and all([await parent.is_visible() for parent in await window.get_parents()])):
                        visible_profiles.append(window)
                if len(visible_profiles) > 1:
                    raise ValueError("当前有多个可见好友详情窗口，停止本次传送")
                if visible_profiles:
                    character_window = visible_profiles[0]
                    break
                if attempt < 4:
                    await asyncio.sleep(0.4)
            if character_window is None:
                raise ValueError("好友详情窗口尚未显示，停止本次传送")
            if (await client.is_loading() or not await character_window.is_visible()
                    or not all([await parent.is_visible() for parent in await character_window.get_parents()])):
                raise ValueError("好友详情窗口已关闭或客户端正在加载，停止本次传送")
        stage = 'friend-tp-click-failed'
        if strict_identity:
            if not client.questing_status or await client.is_loading() or await client.in_battle():
                raise FriendLookupError('friend-ui-not-ready', '客户端状态已变化')
            stage = 'friend-entry-found-but-tp-unavailable'
            go = await _maybe_get_named_window(character_window, 'btnGoToFriend')
            if (not await go.is_visible() or await go.is_control_grayed()
                    or WindowFlags.disabled in await go.flags()):
                raise FriendLookupError(stage)
            stage = 'friend-tp-click-failed'
            await _request_friend_teleport(client, character_window)
        elif automatic:
            await _request_friend_teleport(client, character_window)
        else:
            await _teleport_to_friend(client, character_window)

        # close friends window
        if not (getattr(client, 'questing_status', False) is True and await client.is_loading()):
            await friends_window.write_flags(WindowFlags(2147483648))


    except asyncio.CancelledError:
        raise
    except (ValueError, AttributeError, wizwalker.WizWalkerMemoryError) as exc:
        diagnostic = client._friend_lookup_diagnostic
        reason = exc.reason if isinstance(exc, FriendLookupError) else stage
        if isinstance(exc, FriendLookupError) and exc.candidate_count is not None:
            diagnostic['candidate_count'] = exc.candidate_count
        diagnostic['reason'] = reason
        count = diagnostic.get('candidate_count')
        logger.debug(f"[friend_lookup] follower={client.title} quester_name_available={bool(name)} "
            f"candidate_count={count if count is not None else 'unavailable'} match_method={diagnostic.get('match_method')} "
            f"decision=retry reason={reason}")
        if isinstance(exc, FriendLookupError):
            raise
        raise FriendLookupError(reason, str(exc), candidate_count=diagnostic.get('candidate_count'),
                                match_method=diagnostic.get('match_method')) from exc


# returns True if all provided friends are in the list, and False if any single friend is not
async def check_for_multiple_friends_in_list(client: Client, friend_names: list[str]):

    async with client.mouse_handler:

        # if some form of friend list or friend popup is already open, close it
        # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
        for i in range(5):
            try:
                await click_window_by_path(client, close_real_friend_list_button_path)
                await asyncio.sleep(0.1)
            except ValueError:
                await asyncio.sleep(0.1)

        # try:
        # 	friends_window = await _maybe_get_named_window(client.root_window, "NewFriendsListWindow")
        # except:

        friend_button = await _maybe_get_named_window(client.root_window, "btnFriends")
        await client.mouse_handler.click_window(friend_button)
        await asyncio.sleep(0.4)
        friends_window = await _maybe_get_named_window(
            client.root_window, "NewFriendsListWindow"
        )

        await _cycle_to_online_friends(client, friends_window)

        friends_list_window = await _maybe_get_named_window(
            friends_window, "listFriends"
        )

        right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
        page_number = await _maybe_get_named_window(friends_window, "PageNumber")

        page_number_text = await page_number.maybe_text()

        current_page, _ = map(
            int,
            page_number_text.replace("<center>", "")
            .replace("</center>", "")
            .replace(" ", "")
            .split("/"),
        )

        for friend_name in friend_names:
            friend, friend_index = await _cycle_friends_list(
                client,
                right_button,
                friends_list_window,
                None,
                None,
                friend_name,
                current_page,
            )

            if friend is None:
                return False

        # Pray that we don't mis-press, because we cannot detect the friends list and cannot accurately click it
        for i in range(2):
            await client.send_key(Keycode.F, 0.1)

        # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
        for i in range(3):
            try:
                await click_window_by_path(client, close_real_friend_list_button_path)
                await asyncio.sleep(0.1)
            except ValueError:
                await asyncio.sleep(0.1)

    return True


async def check_for_friend_in_list(client: Client, friend_name: str):
    async with client.mouse_handler:
        # if some form of friend list or friend popup is already open, close it
        # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
        for i in range(5):
            try:
                await click_window_by_path(client, close_real_friend_list_button_path)
                await asyncio.sleep(0.1)
            except ValueError:
                await asyncio.sleep(0.1)

        # try:
        # 	friends_window = await _maybe_get_named_window(client.root_window, "NewFriendsListWindow")
        # except:

        friend_button = await _maybe_get_named_window(client.root_window, "btnFriends")
        await client.mouse_handler.click_window(friend_button)
        await asyncio.sleep(0.4)
        friends_window = await _maybe_get_named_window(
            client.root_window, "NewFriendsListWindow"
        )

        await _cycle_to_online_friends(client, friends_window)

        friends_list_window = await _maybe_get_named_window(
            friends_window, "listFriends"
        )

        right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
        page_number = await _maybe_get_named_window(friends_window, "PageNumber")

        page_number_text = await page_number.maybe_text()

        current_page, _ = map(
            int,
            page_number_text.replace("<center>", "")
            .replace("</center>", "")
            .replace(" ", "")
            .split("/"),
        )

        friend, friend_index = await _cycle_friends_list(
            client,
            right_button,
            friends_list_window,
            None,
            None,
            friend_name,
            current_page,
        )

        # Pray that we don't mis-press, because we cannot detect the friends list and cannot accurately click it
        for i in range(2):
            await client.send_key(Keycode.F, 0.1)

        # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
        for i in range(3):
            try:
                await click_window_by_path(client, close_real_friend_list_button_path)
                await asyncio.sleep(0.1)
            except ValueError:
                await asyncio.sleep(0.1)

    if friend is None:
        return False
    else:
        return True


# requires that the character screen is already open
async def set_wizard_name_from_character_screen(client: Client):
    option_window = await client.root_window.get_windows_with_name("TitleScroll")
    assert len(option_window) == 1, str(option_window)

    # for child in await option_window[0].children():
    children = await option_window[0].children()
    wizard_name = await children[0].maybe_text()
    wizard_name = wizard_name[8:-9]

    client.wizard_name = wizard_name


# requires that the character screen is already open
async def return_wizard_energy_from_character_screen(client: Client):
    energy_txt_window = await get_window_from_path(
        client.root_window, energy_amount_path
    )

    energy_txt = await energy_txt_window.maybe_text()
    current_energy = energy_txt[8:]
    total_energy = energy_txt[8:]
    current_energy = current_energy.split("/", 1)[0]
    total_energy = total_energy.split("/", 1)[1]
    current_energy = int(current_energy)
    total_energy = int(total_energy)

    return current_energy, total_energy


async def get_friend_popup_wizard_name(client: Client):
    option_window = await client.root_window.get_windows_with_name("lblCharacterName")

    if len(option_window) > 0:
        try:
            assert len(option_window) == 1, str(option_window)
        except:
            await asyncio.sleep(0.1)

        wizard_name = await option_window[0].maybe_text()
        wizard_name = wizard_name[8:-9]

        return wizard_name
    else:
        return ""


async def collect_wisps(client: Client, nothing_but_safe_entities=True):
    # Collects all the wisps in the current area, only works within the entity draw distance.
    entities = []
    entities = await SprintyClient(client).get_base_entities_with_vague_name(
        "WispHealth"
    )
    entities += await SprintyClient(client).get_base_entities_with_vague_name(
        "WispMana"
    )
    entities += await SprintyClient(client).get_base_entities_with_vague_name(
        "WispGold"
    )

    if nothing_but_safe_entities:
        safe_entities = await SprintyClient(client).find_safe_entities_from(entities)
    else:
        safe_entities = entities

    if safe_entities:
        for entity in safe_entities:
            wisp_xyz = await entity.location()
            await client.teleport(wisp_xyz)
            await asyncio.sleep(0.1)


async def collect_wisps_with_limit(client: Client, limit=3):
    # Collects all the wisps in the current area, only works within the entity draw distance.
    entities = []
    entities = await SprintyClient(client).get_base_entities_with_vague_name(
        "WispHealth"
    )
    entities += await SprintyClient(client).get_base_entities_with_vague_name(
        "WispMana"
    )

    total_collected = 0

    for entity in entities:
        wisp_xyz = await entity.location()
        await client.teleport(wisp_xyz)
        total_collected += 1

        if total_collected == limit:
            break

        await asyncio.sleep(0.1)


async def pid_to_client(clients: List[Client], pid: int) -> Client:
    for client in clients:
        if client.process_id == pid:
            return client

    if clients:
        return clients[0]
    else:
        return None


async def wait_for_visible_by_path(
    client: Client, path: List[str], wait_for_not: bool = False, interval: float = 0.25
):
    if wait_for_not:
        while await is_visible_by_path(client, path):
            await asyncio.sleep(interval)

    else:
        while not await is_visible_by_path(client, path):
            await asyncio.sleep(interval)


async def try_task_coro(
    coro: Coroutine, clients: List[Client], deactive_mouseless: bool = False
):
    from src.logging_utils import log_exception
    task_coro = coro
    max_retries = 10
    for attempt in range(max_retries + 1):
        try:
            await task_coro()
            return

        except asyncio.CancelledError:
            for p in clients:
                p.feeding_pet_status = False
            await asyncio.gather(*[attempt_deactivate_dance_hook(p) for p in clients])
            return

        except (
            wizwalker.errors.MemoryInvalidated,
            wizwalker.errors.ExceptionalTimeout,
        ) as exc:
            log_exception(','.join(p.title for p in clients),
                f'任务流程内存异常，尝试 {attempt + 1}/{max_retries + 1}', exc, level='DEBUG')
            if attempt < max_retries:
                logger.debug(
                    f"Task {task_coro} encountered a memory error, retrying ({attempt + 1}/{max_retries})..."
                )
                await asyncio.sleep(1)
            else:
                log_exception(','.join(p.title for p in clients),
                    f'任务流程重试 {max_retries} 次后最终停止', exc, level='ERROR')


def index_with_str(input_str, desired_str: str) -> int:
    for i, s in enumerate(input_str):
        if desired_str in s.lower():
            return i

    return None


def read_webpage(url) -> Union[List, None]:
    # return a list of lines from a hosted file
    try:
        response = requests.get(url, allow_redirects=True)
        page_text = response.text
        line_list = page_text.splitlines()

    except:
        return []

    else:
        return line_list


def assign_pet_level(destinationLevel):
    pet_world_tracks = ["btnTrack0", "btnTrack1", "btnTrack2", "btnTrack3", "btnTrack4"]
    pet_world_list = ["WizardCity", "Krokotopia", "Marleybone", "Mooshu", "Dragonspyre"]

    pet_world_index = pet_world_list.index(destinationLevel)
    selected_track = pet_world_tracks[pet_world_index]

    if selected_track is not None:
        for index, track in enumerate(wizard_city_dance_game_path):
            if track in pet_world_tracks:
                wizard_city_dance_game_path[index] = selected_track


def required_params(signature: inspect.Signature) -> int:
    """Counts the number of params required for a function, based off of its function signature."""
    req_params = 0

    for param in signature.parameters.values():
        if param.default is inspect.Parameter.empty:
            req_params += 1

    return req_params


async def conditional_await(func, args: dict = {}) -> Any:
    """Awaits any function that returns something if async, runs normally if sync."""
    if inspect.iscoroutinefunction(func):
        return await func(**args)

    else:
        return func(**args)


# To track seen objects and avoid circular references
seen_objects = {}


async def class_snapshot(
    instance,
    recurse: bool = True,
    current_depth: int = 0,
    max_depth: int = 25,
    types_blacklist: tuple = (inspect._empty, Window, wizwalker.memory.DynamicWindow),
    edge_cases: dict = {},
) -> dict:
    """Recursively calls every function in a class, async or not. Assembles a dict containing the outputs for these, referenced by function name. Only does functions that have no arguments."""
    snapshot_data = {}

    # Limit recursion depth to prevent stack overflow
    if (
        current_depth >= max_depth
    ):  # If we have reached or exceeded max depth, return an empty dict
        return snapshot_data

    # Avoid recursion on already processed objects (handles circular references)
    if id(instance) in seen_objects:
        return {}

    seen_objects[id(instance)] = True  # Mark this object as processed

    current_depth += 1  # Increment the current depth

    valid_types = (int, float, bool, str, Enum, type(None))  # Valid built-in types
    iter_types = (
        list,
        dict,
        set,
        tuple,
    )  # Types we can iterate through in a useful manner

    def _is_valid_type(obj, types=valid_types):
        # Returns True if the object supplied's type is apart of the list of supplied valid types
        return isinstance(obj, types)

    def _is_return_type_blacklisted(func, types: tuple = types_blacklist):
        return_type = typing.get_type_hints(func).get("return")
        if isinstance(return_type, typing._GenericAlias):
            return_type = return_type.__args__[0]

        # Check if return_type is a class before using issubclass
        if isinstance(return_type, type):
            return issubclass(return_type, types)
        return False

    for name, func in inspect.getmembers(instance, predicate=inspect.ismethod):
        signature = inspect.signature(func)
        if name in edge_cases:
            edge_case_args = edge_cases[name]
            is_func_compat = True
        else:
            edge_case_args = {}
            is_func_compat = (
                not name.startswith("__")
                and not len(signature.parameters)
                and not _is_return_type_blacklisted(func)
            )

        if (
            is_func_compat
        ):  # Skip built-in methods and only consider functions without arguments
            try:
                output = await conditional_await(func, edge_case_args)

            except (
                Exception
            ) as e:  # Some functions will inevitably error and we want to continue regardless.
                logging.error(f"Error calling {name}: {e}")
                snapshot_data[name] = None
                continue

            if isinstance(output, Enum):  # Use only the value of the enum
                output = output.value

            if _is_valid_type(
                output
            ):  # If this is just normal data, we can use the output
                snapshot_data[name] = output

            elif _is_valid_type(
                output, iter_types
            ):  # If the output is iterable, check everything inside it
                if isinstance(
                    output, dict
                ):  # Dict handling, checks both the keys and values
                    output_dict = {}
                    for o_k, o_v in output.items():
                        snapshot_k = o_k
                        snapshot_v = o_v

                        if not _is_valid_type(
                            o_k
                        ):  # If this isn't a built-in type, recurse
                            snapshot_k = await class_snapshot(
                                o_k,
                                recurse,
                                current_depth,
                                max_depth,
                                types_blacklist,
                                edge_cases,
                            )

                        if not _is_valid_type(o_v):
                            snapshot_v = await class_snapshot(
                                o_v,
                                recurse,
                                current_depth,
                                max_depth,
                                types_blacklist,
                                edge_cases,
                            )

                        output_dict[snapshot_k] = snapshot_v

                    snapshot_data[name] = output_dict
                    continue

                else:  # Iterable output handling
                    output_iterable = []
                    for o in output:
                        if _is_valid_type(o):
                            output_iterable.append(o)
                        else:
                            o_snapshot = await class_snapshot(
                                o,
                                recurse,
                                current_depth,
                                max_depth,
                                types_blacklist,
                                edge_cases,
                            )
                            output_iterable.append(o_snapshot)

                    snapshot_data[name] = type(output)(output_iterable)

            else:
                snapshot_data[name] = await class_snapshot(
                    output,
                    recurse,
                    current_depth,
                    max_depth,
                    types_blacklist,
                    edge_cases,
                )

    return snapshot_data


async def class_snapshot_iterable(
    instances: Iterable,
    recurse: bool = True,
    current_depth: int = 0,
    max_depth: int = 25,
    types_blacklist=(inspect._empty, Window, wizwalker.memory.DynamicWindow),
    edge_cases: dict = {},
):
    snapshots = []
    for inst in instances:
        snapshots.append(
            await class_snapshot(
                inst, recurse, current_depth, max_depth, types_blacklist, edge_cases
            )
        )

    return snapshots


def override_wiz_install_using_handle(max_size=100):
    """
    This function allows you to automatically override your wiz install location, provided that wizard101 is open.
    """
    path = ctypes.create_unicode_buffer(max_size)
    pid = get_pid_from_handle(get_all_wizard_handles()[0])
    handle = kernel32.OpenProcess(
        0x410, 0, pid
    )  # PROCESS_QUERY_INFORMATION and PROCESS_VM_READ
    ctypes.windll.psapi.GetModuleFileNameExW(handle, None, ctypes.byref(path), max_size)
    kernel32.CloseHandle(handle)
    install_location = path.value.replace("\\Bin\\WizardGraphicalClient.exe", "")
    override_wiz_install_location(install_location)
