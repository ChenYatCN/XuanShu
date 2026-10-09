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
from wizwalker.memory.memory_objects.enums import DuelPhase
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
from src.automation_ownership import automation_owner
from src.sprinty_client import SprintyClient
from src.window_text import read_control_text
from src.interaction_prompts import plain_text

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
tamed_demox_locations = [
    "outsiders camp", "graveholm", "mortal plain", "howling lands",
    "black lagoon", "scholomance",
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
async def go_to_new_world(p, destinationWorld, open_window: bool = True):
    if open_window:
        while portal_kind(
            await get_popup_title(p)
        ) != "world_gate" and not await is_visible_by_path(p, spiral_door_path):
            await asyncio.sleep(0.1)

        while not await is_visible_by_path(p, spiral_door_path):
            await asyncio.sleep(0.1)
            await p.send_key(Keycode.X, 0.1)

    while await p.is_in_npc_range():
        await p.send_key(Keycode.X, 0.1)
        await asyncio.sleep(0.4)

    while not await is_visible_by_path(p, spiral_door_path):
        await asyncio.sleep(0.1)
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
            await p.mouse_handler.click_window_with_name("leftButton")
            await asyncio.sleep(0.2)

        option_window = await p.root_window.get_windows_with_name("optionWindow")

        assert len(option_window) == 1, str(option_window)

        for child in await option_window[0].children():
            if await child.name() == "pageCount":
                pageCount = await child.maybe_text()
                pageCount = pageCount[8:-9]
                currentPage = pageCount.split("/", 1)[0]
                maxPage = pageCount.split("/", 1)[1]
                break

        # ensure we are on page 1 (and if not click over again)
        while str(currentPage) != "1":
            await p.mouse_handler.click_window_with_name("leftButton")
            await asyncio.sleep(0.2)
            for child in await option_window[0].children():
                if await child.name() == "pageCount":
                    pageCount = await child.maybe_text()
                    pageCount = pageCount[8:-9]
                    currentPage = pageCount.split("/", 1)[0]

        worldIndex = worldList.index(destinationWorld)
        spiralGateName = zoneDoorNameList[worldIndex]

        isChildFound = False

        for i in range(int(maxPage)):
            for child in await option_window[0].children():
                if await child.name() in ["opt0", "opt1", "opt2", "opt3"]:
                    name = await read_control_checkbox_text(child)
                    if name == spiralGateName or matches_text(
                        name, f"WorldNames_{destinationWorld}"
                    ):
                        await p.mouse_handler.click_window_with_name(
                            zoneDoorOptions[worldIndex]
                        )
                        await asyncio.sleep(0.4)
                        await p.mouse_handler.click_window_with_name("teleportButton")
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
                    loopCount += 1
                    await p.mouse_handler.click_window_with_name("rightButton")

                    # ensure that wizwalker didn't misclick and that we actually changed pages
                    for child in await option_window[0].children():
                        if await child.name() == "pageCount":
                            pageCount = await child.maybe_text()
                            pageCount = pageCount[8:-9]
                            currentPage = pageCount.split("/", 1)[0]


async def new_portals_cycle(client: Client, location: str, *, before_input=None):
    if not location:
        raise ValueError(
            "Cannot select a portal without a recognized quest destination"
        )
    async def ready():
        if (getattr(client, 'questing_status', True) is False
                or getattr(client, 'refilling_potions', False) is True
                or await client.is_loading() or await client.in_battle()):
            raise RuntimeError('客户端已停止、补药、加载或进入战斗')
        if before_input is not None and not await before_input():
            raise RuntimeError('任务或客户端状态已改变，停止目的地选择')
        button = await get_spiral_teleport_button(client)
        if button is None:
            raise RuntimeError('传送菜单已关闭')
        container = await button.parent()
        panels = [p for p in await container.get_windows_with_name('optionWindow')
                  if await p.is_visible()]
        if len(panels) != 1:
            raise RuntimeError('无法唯一确定当前传送菜单')
        children = {await c.name(): c for c in await panels[0].children() if await c.is_visible()}
        page = plain_text(await read_control_text(children['pageCount']))
        match = re.fullmatch(r'(\d+)\s*/\s*(\d+)', page)
        if not match or not 1 <= int(match[1]) <= int(match[2]):
            raise RuntimeError('目的地页码不可读')
        return button, children, (int(match[1]), int(match[2]))

    async def turn(direction, old_page):
        _, children, page = await ready()
        if page != old_page:
            raise RuntimeError('点击前目的地页面发生变化')
        arrow = children.get(direction)
        if arrow is None or await arrow.is_control_grayed():
            raise RuntimeError('目的地翻页按钮不可用')
        # Click once, then wait for actual page advancement, not a fixed delay.
        await ready()
        async with client.mouse_handler:
            await client.mouse_handler.click_window(arrow)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _, _, current = await ready()
            if current != old_page:
                step = 1 if direction == 'rightButton' else -1
                if current != ((old_page[0] - 1 + step) % old_page[1] + 1, old_page[1]):
                    raise RuntimeError('目的地页面未按预期推进')
                return
            await asyncio.sleep(.1)
        raise TimeoutError('目的地翻页未确认，不重复盲点')

    try:
        async with asyncio.timeout(25):
            _, _, first = await ready()
            # Visit every page, so duplicate matching labels cannot be guessed.
            for _ in range(first[0] - 1):
                _, _, page = await ready()
                await turn('leftButton', page)
            matches = []
            for index in range(1, first[1] + 1):
                _, children, page = await ready()
                if page != (index, first[1]):
                    raise RuntimeError('目的地页码已改变')
                for name in ('opt0', 'opt1', 'opt2', 'opt3'):
                    option = children.get(name)
                    if option is None:
                        continue
                    label = await read_control_checkbox_text(option)
                    if resolve_portal_destination(label, [location], exact=True) == location:
                        matches.append((index, name, plain_text(label)))
                if index < first[1]:
                    await turn('rightButton', page)
            if len(matches) != 1:
                raise RuntimeError('目的地不存在或匹配不唯一')
            target_page, option_name, label = matches[0]
            for _ in range(first[1] - target_page):
                _, _, page = await ready()
                await turn('leftButton', page)
            _, children, page = await ready()
            option = children.get(option_name)
            if (page != (target_page, first[1]) or option is None
                    or await option.is_control_grayed()
                    or plain_text(await read_control_checkbox_text(option)) != label):
                raise RuntimeError('目的地不可选或标签发生变化')
            _, children, page = await ready()
            option = children.get(option_name)
            if (page != (target_page, first[1]) or option is None
                    or await option.is_control_grayed()
                    or plain_text(await read_control_checkbox_text(option)) != label):
                raise RuntimeError('选择前目的地发生变化')
            async with client.mouse_handler:
                await client.mouse_handler.click_window(option)
            deadline = time.monotonic() + 3
            while True:
                button, children, page = await ready()
                option = children.get(option_name)
                if (page != (target_page, first[1]) or option is None
                        or plain_text(await read_control_checkbox_text(option)) != label):
                    raise RuntimeError('选中期间目的地发生变化')
                if await option.maybe_checked() and not await button.is_control_grayed():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('未确认目的地选中或 TRAVEL 可用')
                await asyncio.sleep(.1)
            origin = await client.zone_name()
            button, children, page = await ready()
            option = children.get(option_name)
            if (page != (target_page, first[1]) or option is None
                    or not await option.is_visible() or not await option.maybe_checked()
                    or await button.is_control_grayed()
                    or plain_text(await read_control_checkbox_text(option)) != label):
                raise RuntimeError('出发前选中状态已改变')
            async with client.mouse_handler:
                await client.mouse_handler.click_window(button)
            await client.wait_for_zone_change(origin)
            return True
    except Exception as exc:
        logger.warning('{} 目的地 {} 未完成选择/传送：{}', client.title, location, exc)
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


async def close_npc_quest_menu(client: Client, *, select_mainlines=False) -> bool:
    """Use the 4.1.5 Exit rule; indexed selection belongs to explicit recovery."""
    if await client.is_loading() or await client.in_battle():
        return False
    if not await is_visible_by_path(client, cancel_multiple_quest_menu_path):
        return False
    # An active quest dialogue takes priority over the list behind it.
    if await is_visible_by_path(client, advance_dialog_path):
        return False
    if (select_mainlines and getattr(client, 'mainline_finder_enabled', False)
            and (getattr(client, 'questing_status', False) is True
                             or getattr(client, 'auto_dialogue_running', False) is True)):
        # Runtime import keeps utils/questing module initialization acyclic.
        from src.questing import Quester
        if await Quester(client, [client], None)._select_npc_mainline_menu(client):
            return True
    client.npc_mainline_menu_selection = None
    client.npc_mainline_menu_context = None
    client.npc_mainline_menu_read_wait = None
    async with automation_owner(client, 'npc-menu-exit'):
        if (await client.is_loading() or await client.in_battle()
                or await is_visible_by_path(client, advance_dialog_path)):
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
    for modal in await client.root_window.get_windows_with_name('MessageBoxModalWindow'):
        if not await modal.is_visible():
            continue
        captions = await modal.get_windows_with_name('CaptionText')
        text = ' '.join([plain_text(await read_control_text(c) or '')
                         for c in captions if await c.is_visible()]).casefold()
        if not ('地下城已关闭' in text or '地下城已關閉' in text
                or ('instance' in text and 'closed' in text and ('load' in text or 'area' in text))):
            continue
        client._xuanshu_dungeon_closed = True
        logger.error(f'自动任务：{client.title} 区域加载失败，地下城已关闭；停止标记回传重试。')
        if dismiss:
            layout = ['messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout']
            buttons = []
            for name in ('leftButton', 'centerButton', 'rightButton'):
                button = await get_window_from_path(modal, [*layout, name])
                if button and await button.is_visible():
                    buttons.append(button)
            if len(buttons) == 1:
                async with client.mouse_handler:
                    current_text = ' '.join([plain_text(await read_control_text(c) or '')
                                           for c in captions if await c.is_visible()]).casefold()
                    if (not await client.is_loading() and await modal.is_visible()
                            and await buttons[0].is_visible() and current_text == text):
                        await client.mouse_handler.click_window(buttons[0])
        return True
    return False


def potion_dungeon_return_required(client: Client, zone: str) -> bool:
    state = getattr(client, 'quest_dungeon_recovery', None)
    # This promptless instance was confirmed by the user's live failure log.
    # Do not classify every Interiors map as a dungeon (shops/houses also use it).
    return bool(zone) and (
        zone == 'Lemuria/Interiors/LM_Z07_BumblesMind'
        or zone == getattr(client, 'quest_party_group_dungeon_zone', None)
        or isinstance(state, dict) and state.get('zone') == zone
    )


async def potion_zone_id(client: Client):
    """Use the existing ClientZone reader as extra evidence, not a new offset."""
    try:
        zone = await client.client_object.client_zone()
        value = await zone.zone_id() if zone else None
        return value if isinstance(value, int) and value > 0 else None
    except Exception:
        return None


async def potion_quest_snapshot(client: Client):
    # Lazy import: questing already imports utils. Reuse its verified HUD reader.
    from src.questing import Quester
    return await Quester(client, [client], None)._dungeon_quest_snapshot(client)


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


async def prepare_potion_dungeon_return(client: Client, zone: str, require_snapshot: bool = True) -> bool:
    snapshot = await potion_quest_snapshot(client)
    if snapshot is None and require_snapshot:
        logger.error(f'自动任务：{client.title} 原地牢任务状态不可读，取消补药出发。')
        return False
    state = getattr(client, 'quest_dungeon_recovery', None)
    client.potion_return_context = {
        'zone': zone, 'zone_id': await potion_zone_id(client),
        'snapshot': snapshot, 'dungeon_state': dict(state) if isinstance(state, dict) else None,
        'group_zone': getattr(client, 'quest_party_group_dungeon_zone', None),
    }
    # Retain only peers proven present before departure. A peer's observed
    # transition or replaced ClientZone invalidates this evidence on return.
    peers = list(getattr(client, 'quest_party_hitters', []))
    quester = getattr(client, 'quest_party_quester', None)
    if quester is not None:
        peers.append(quester)
    peer_areas = {}
    for peer in peers:
        if not getattr(peer, 'refilling_potions', False) and await clients_share_live_area(client, peer):
            try:
                await observe_party_area(peer)
                peer_areas[id(peer)] = await _party_area_token(peer)
            except Exception:
                pass
    client.potion_return_context['peer_areas'] = peer_areas
    return True


async def _potion_dungeon_room_tp(client: Client, zone: str, *, reenter: bool):
    from src.teleport_math import navmap_tp
    from src.task_lifecycle import gather_owned

    # Keep the refill owner throughout recovery; stop movement on loading,
    # combat, a room transition or the user stopping this client's task.
    movement = asyncio.create_task(navmap_tp(client, reenter=reenter))
    async def watch_room():
        while (getattr(client, 'questing_status', True)
               and not getattr(client, '_xuanshu_dungeon_closed', False)
               and await is_free(client) and await client.zone_name() == zone):
            await asyncio.sleep(.1)
    watcher = asyncio.create_task(watch_room())
    try:
        async with asyncio.timeout(15.0):
            done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
    finally:
        movement.cancel()
        watcher.cancel()
        await gather_owned(movement, watcher, return_exceptions=True)


async def return_to_dungeon_after_potions(client: Client, original_zone: str) -> bool:
    """Resume the instance, then follow the quest arrow through rooms to a peer."""
    context = getattr(client, 'potion_return_context', None)
    if (not isinstance(context, dict) or context.get('zone') != original_zone
            or context.get('snapshot') is None and context.get('zone_id') is None):
        logger.error(f'自动任务：{client.title} 缺少原地牢任务记录，无法可靠确认返回，停止回传。')
        return False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if await closed_dungeon_popup(client, dismiss=True):
            # A previous mark failure can be dismissed once before using the
            # game's valid resume entry; never repeat the mark.
            client._xuanshu_dungeon_closed = False
        if not await client.is_loading() and not await client.in_battle():
            button = await get_window_from_path(client.root_window, dungeon_recall_path)
            if button and await button.is_visible() and not await button.is_control_grayed():
                break
        await asyncio.sleep(0.25)
    else:
        logger.error(f"自动任务：{client.title} 未找到地牢返回按钮，停止本次回传。")
        return False

    logger.info(f"自动任务：{client.title} 正在通过地牢返回按钮返回副本。")
    client._xuanshu_dungeon_closed = False
    departure_zone = await client.zone_name()
    async with client.mouse_handler:
        button = await get_window_from_path(client.root_window, dungeon_recall_path)
        if (await client.is_loading() or await client.in_battle() or not button
                or not await button.is_visible() or await button.is_control_grayed()):
            return False
        await client.mouse_handler.click_window(button)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if await closed_dungeon_popup(client, dismiss=True) or getattr(client, '_xuanshu_dungeon_closed', False):
            return False
        if await client.is_loading() or await client.zone_name() != departure_zone:
            break
        await asyncio.sleep(0.25)
    else:
        logger.error(f"自动任务：{client.title} 点击地牢返回按钮后未观察到区域切换。")
        return False

    deadline = time.monotonic() + 90.0
    stable_reads = 0
    stable_area = None
    last_tp_zone = None
    next_tp_at = 0.0
    peers = [getattr(client, 'quest_party_quester', None),
             *getattr(client, 'quest_party_hitters', [])]
    # Only follow this party's peers that were proved present before departure.
    peers = [peer for peer in peers if peer is not None and peer is not client
             and id(peer) in context.get('peer_areas', {})]
    while time.monotonic() < deadline:
        if getattr(client, 'questing_status', True) is False:
            return False
        if await closed_dungeon_popup(client, dismiss=True) or getattr(client, '_xuanshu_dungeon_closed', False):
            return False
        snapshot = None
        peer = None
        for candidate in peers:
            if (getattr(candidate, 'questing_status', False)
                    and not getattr(candidate, 'refilling_potions', False)
                    and not getattr(candidate, 'in_solo_zone', False)
                    and not await candidate.is_loading()):
                peer = candidate
                break
        target_zone = await peer.zone_name() if peer is not None else original_zone
        current_zone = await client.zone_name()
        area = (current_zone, id(peer))
        if area != stable_area:
            stable_reads = 0
            stable_area = area
        if (not await client.is_loading() and current_zone != departure_zone
                and (await is_free(client) or peer is not None and await client.in_battle())):
            snapshot = await potion_quest_snapshot(client)
            if (snapshot is not None and context.get('snapshot') is not None
                    and snapshot[0] != context['snapshot'][0]):
                logger.error(f'自动任务：{client.title} 返回区域的任务身份与原记录不符，停止恢复任务。')
                return False
            before_id = context.get('zone_id') if isinstance(context, dict) else None
            after_id = await potion_zone_id(client)
            if current_zone == original_zone and before_id is not None and after_id != before_id:
                logger.error(f'自动任务：{client.title} 返回同名区域但 Zone ID 不匹配，停止恢复任务。')
                return False
            if (snapshot is not None and peer is not None and target_zone
                    and target_zone != departure_zone and current_zone != target_zone):
                stable_reads = 0
                if await is_free(client) and time.monotonic() >= next_tp_at:
                    logger.info(f'自动任务：{client.title} 补药返回后位于 {current_zone}，沿任务目标追赶 {peer.title} 所在的 {target_zone}。')
                    try:
                        await _potion_dungeon_room_tp(client, current_zone,
                                                     reenter=last_tp_zone == current_zone)
                    except (TimeoutError, ValueError) as exc:
                        logger.debug(f'自动任务：{client.title} 本轮追赶房间未完成：{exc}')
                    last_tp_zone = current_zone
                    next_tp_at = time.monotonic() + 2.0
                await asyncio.sleep(.5)
                continue
            if (current_zone != target_zone
                    or peer is not None and not await clients_share_live_area(client, peer)):
                snapshot = None
        if snapshot is not None:
            stable_reads += 1
            if stable_reads >= 3:
                if isinstance(context, dict):
                    context['zone'] = current_zone
                    context['zone_id'] = after_id
                    context['returned_snapshot'] = snapshot
                    try:
                        await observe_party_area(client)
                        context['returned_area_token'] = await _party_area_token(client)
                    except Exception:
                        context['returned_area_token'] = None
                    saved = context.get('dungeon_state')
                    if isinstance(saved, dict):
                        client.quest_dungeon_recovery = dict(saved, zone=current_zone,
                            snapshot=snapshot, since=None, active=False, attempted=False)
                    client.quest_party_group_dungeon_zone = (current_zone
                        if context.get('group_zone') is not None else None)
                pending_hitters = {
                    id(h) for h in getattr(client, "quest_party_hitters", [])
                    if getattr(h, "questing_status", False)
                    and not getattr(client, 'in_solo_zone', False)
                }
                client.potion_dungeon_returned = (
                    current_zone, time.monotonic(), pending_hitters,
                ) if pending_hitters or getattr(client, "quest_party_status_session", None) is not None else None
                client.quest_party_battle_sync_state = None
                logger.info(f"自动任务：{client.title} 地牢返回按钮已完成回传，任务状态可读；恢复前仍需确认队伍同副本。")
                return True
        else:
            stable_reads = 0
        await asyncio.sleep(0.5)
    logger.error(f"自动任务：{client.title} 未确认返回原地牢 {original_zone!r}。")
    return False


async def _potion_refill_gold_allowed(client: Client) -> bool:
    try:
        gold = await client.stats.current_gold()
        if not isinstance(gold, int) or gold < 0:
            raise ValueError('金币读数无效')
    except Exception:
        if getattr(client, '_potion_gold_block', None) != 'unreadable':
            logger.warning('自动补药水：{} 金币暂不可读，取消本次补购。', client.title)
        client._potion_gold_block = 'unreadable'
        return False
    if gold < 50000:
        if getattr(client, '_potion_gold_block', None) != 'low':
            logger.info('自动补药水：{} 金币 {} 少于 50000，取消本次补购。', client.title, gold)
        client._potion_gold_block = 'low'
        return False
    client._potion_gold_block = None
    return True


async def buy_potions(client: Client, recall: bool = True, original_zone=None, dungeon_return: bool = False):
    try:
        await asyncio.sleep(1.0)
        max_potions = await client.stats.potion_max()
        purchase_cancelled = False
        # buy potions and close the potions menu, and recall if needed
        for i in range(2):
            original_potion_count = await client.stats.potion_charge()
            current_potion_count = original_potion_count

            # buy potions until our potion count has either increased (we may not have enough gold for all potions) or we are at max potions
            while (
                current_potion_count == original_potion_count
                and current_potion_count < max_potions
            ):
                if not await _potion_refill_gold_allowed(client):
                    purchase_cancelled = True
                    break
                while not await is_visible_by_path(client, potion_shop_base_path):
                    await client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(0.5)

                await click_window_by_path(client, potion_fill_all_path, True)
                await asyncio.sleep(0.25)

                if await _potion_refill_gold_allowed(client):
                    await click_window_by_path(client, potion_buy_path, True)
                else:
                    purchase_cancelled = True
                await asyncio.sleep(0.25)

                while await is_visible_by_path(client, potion_shop_base_path):
                    await click_window_by_path(client, potion_exit_path, True)
                    await asyncio.sleep(0.125)

                if purchase_cancelled:
                    break

                current_potion_count = await client.stats.potion_charge()
                await asyncio.sleep(0.5)

            if purchase_cancelled:
                break
            if i == 0:
                if await client.stats.potion_charge() >= 1.0:
                    original_potion_count = await client.stats.potion_charge()

                    logger.debug(f"Client {client.title} - Using potion")
                    await click_window_by_path(client, potion_usage_path, True)
                    await asyncio.sleep(3.0)

        if purchase_cancelled:
            while await is_visible_by_path(client, potion_shop_base_path):
                await click_window_by_path(client, potion_exit_path, True)
                await asyncio.sleep(0.125)

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"Client {client.title} - 购买药水失败")
        return False

    # Return only after a confirmed recall or validated dungeon-button return.
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
            # Probe before the mark, then once more if the mark did not return.
            # Resume availability is live evidence even for an unclassified map.
            for attempt in range(2):
                use_dungeon_return = dungeon_return or getattr(client, '_xuanshu_dungeon_closed', False)
                if (not use_dungeon_return and hasattr(client, 'root_window')
                        and not await client.is_loading() and not await client.in_battle()):
                    button = await get_window_from_path(client.root_window, dungeon_recall_path)
                    use_dungeon_return = bool(button and await button.is_visible()
                                              and not await button.is_control_grayed())
                if use_dungeon_return:
                    logger.info(f"自动任务：{client.title} 药水补充完成，优先通过地牢返回按钮回传。")
                    returned = await return_to_dungeon_after_potions(client, original_zone)
                    if not returned:
                        client.questing_status = False
                    return returned
                if attempt == 0 and await recall_to_teleport_mark(client, expected_zone=original_zone):
                    return True
            return False

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
    # If we have any missing potions, get potions
    if await client.stats.potion_charge() < await client.stats.potion_max():
        if not await _potion_refill_gold_allowed(client):
            return True  # A skipped purchase is not a travel/instance-return failure.
        original_zone = await client.zone_name()
        dungeon_return = potion_dungeon_return_required(client, original_zone)
        from src.questing import claim_quest_recovery, release_quest_recovery
        if getattr(client, 'refilling_potions', False) or not claim_quest_recovery(client, 'potion_refill'):
            return False
        client.refilling_potions = True
        client.potion_dungeon_returned = None
        client.potion_return_context = None
        client._xuanshu_dungeon_closed = False
        logger.info(f"自动任务：{client.title} 开始补充药水，暂时退出地牢同步。")
        try:
            if not await prepare_potion_dungeon_return(client, original_zone, require_snapshot=dungeon_return):
                client.questing_status = False
                return False
            # No return travel is needed when already in the Commons.
            recall = original_zone != "WizardCity/WC_Hub"
            if recall and not dungeon_return and not await ensure_teleport_mark(client):
                logger.error(f"Client {client.title} - 药水补充失败：原地图标记未确认，取消出发。")
                return False
            if recall and await client.zone_name() != original_zone:
                logger.error(f"Client {client.title} - 放置标记期间地图改变，取消补药。")
                return False
            await asyncio.wait_for(navigate_to_ravenwood(client), timeout=90.0)
            await asyncio.wait_for(navigate_to_commons_from_ravenwood(client), timeout=90.0)
            await asyncio.wait_for(navigate_to_potions(client), timeout=90.0)
            recalled = await asyncio.wait_for(
                buy_potions(client, recall=recall, original_zone=original_zone,
                            dungeon_return=dungeon_return), timeout=180.0,
            )
            if not recalled and dungeon_return:
                client.questing_status = False
            if await is_potion_needed(client, minimum_mana):
                await use_potion(client)
            return recalled
        except asyncio.CancelledError:
            if dungeon_return:
                client.questing_status = False
            raise
        except Exception:
            logger.exception(f"自动任务：{client.title} 补药流程异常。")
            if dungeon_return:
                client.questing_status = False
            return False
        finally:
            client.refilling_potions = False
            release_quest_recovery(client, 'potion_refill')

    return True


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
    for attempt in range(1, attempts + 1):
        if await closed_dungeon_popup(client, dismiss=True) or getattr(client, '_xuanshu_dungeon_closed', False):
            return False
        ready_deadline = time.monotonic() + 30.0
        while await client.is_loading() and time.monotonic() < ready_deadline:
            await asyncio.sleep(.2)
        if await client.is_loading() or await client.in_battle():
            logger.error(f"Client {client.title} - 当前仍在加载或战斗，无法安全回传。")
            return False
        departure_zone = await client.zone_name()
        if expected_zone is not None and departure_zone == expected_zone:
            return True

        if not await wait_for_teleport_mark_timer(client):
            return False

        logger.debug(
            f"Client {client.title} - Recalling to teleport mark "
            f"(attempt {attempt}/{attempts})."
        )
        # The original WizSprinter flow sends the shortcut twice.  Reading the
        # Recall button's grayed state is unreliable immediately after closing
        # the potion shop, while the game still accepts this key sequence.
        await client.send_key(Keycode.PAGE_UP, 0.1)
        await client.send_key(Keycode.PAGE_UP, 0.1)

        travel_started = False
        start_deadline = time.monotonic() + 12.0
        while time.monotonic() < start_deadline:
            if await closed_dungeon_popup(client, dismiss=True) or getattr(client, '_xuanshu_dungeon_closed', False):
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
            await asyncio.sleep(0.25)
        await asyncio.sleep(1.25)

        if await closed_dungeon_popup(client, dismiss=True) or getattr(client, '_xuanshu_dungeon_closed', False):
            return False
        arrival_zone = await client.zone_name()
        if not await client.is_loading() and (expected_zone is None or arrival_zone == expected_zone):
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


async def click_window_until_closed(client: Client, path):
    if await is_visible_by_path(client, path):
        async with client.mouse_handler:
            while await is_visible_by_path(client, path):
                await click_window_by_path(client, path)
        return True

    else:
        return False


async def refill_potions(
    client: Client, mark: bool = False, recall: bool = True, original_zone=None,
    dungeon_return: bool = False,
):
    if await client.stats.reference_level() >= 6:
        if not await _potion_refill_gold_allowed(client):
            return True
        starting_zone = await client.zone_name()
        if original_zone is None:
            original_zone = starting_zone

        dungeon_return = dungeon_return or potion_dungeon_return_required(client, original_zone)

        from src.questing import claim_quest_recovery, release_quest_recovery
        if getattr(client, 'refilling_potions', False) or not claim_quest_recovery(client, 'potion_refill'):
            return False
        client.refilling_potions = True
        client.potion_dungeon_returned = None
        client.potion_return_context = None
        client._xuanshu_dungeon_closed = False
        logger.info(f"自动任务：{client.title} 开始补充药水，暂时退出地牢同步。")
        try:
            if not await prepare_potion_dungeon_return(client, original_zone, require_snapshot=dungeon_return):
                client.questing_status = False
                return False
            if recall and original_zone != starting_zone:
                logger.error(f"Client {client.title} - 原地图 {original_zone!r} 与出发地图 {starting_zone!r} 不符，取消补药。")
                return False
            if (recall or mark) and not dungeon_return and starting_zone != "WizardCity/WC_Hub":
                if not await ensure_teleport_mark(client):
                    logger.error(f"Client {client.title} - 药水补充失败：原地图标记未确认，取消出发。")
                    return False

            if recall and await client.zone_name() != original_zone:
                logger.error(f"Client {client.title} - 放置标记期间地图改变，取消补药。")
                return False
            await asyncio.wait_for(navigate_to_ravenwood(client), timeout=90.0)
            await asyncio.wait_for(navigate_to_commons_from_ravenwood(client), timeout=90.0)
            await asyncio.wait_for(navigate_to_potions(client), timeout=90.0)
            result = await asyncio.wait_for(
                buy_potions(client, recall, original_zone=original_zone,
                            dungeon_return=dungeon_return), timeout=180.0,
            )
            if not result and dungeon_return:
                # A failed instance return must not fall through to quest/friend TP.
                client.questing_status = False
                logger.error(f"自动任务：{client.title} 地牢补药返回失败，已停止该客户端自动任务。")
            return result
        except asyncio.CancelledError:
            if dungeon_return:
                client.questing_status = False
            raise
        except Exception:
            logger.exception(f"自动任务：{client.title} 补药流程异常。")
            if dungeon_return:
                client.questing_status = False
            return False
        finally:
            client.refilling_potions = False
            release_quest_recovery(client, 'potion_refill')

    return False


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


async def reconcile_combat_state(client: Client, original_locations=None) -> bool:
    """Release stale detection only after a readable, stable ended duel."""
    pending = getattr(client, 'just_entered_combat', None)
    if not (getattr(client, 'entity_detect_combat_status', False)
            or isinstance(pending, (int, float))):
        client._combat_ended_observation = None
        return False

    def protected():
        entered = getattr(client, 'just_entered_combat', None)
        return (getattr(client, '_character_selection_active', False) is True
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'quest_party_target_sync_active', False)
                or getattr(client, 'post_combat_movement_active', False)
                or getattr(client, 'post_combat_cleanup_active', False)
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or isinstance(entered, (int, float)) and time.time() - entered < 7)

    if protected():
        client._combat_ended_observation = None
        return False
    try:
        async with asyncio.timeout(3.0):
            if await client.is_loading():
                client._combat_ended_observation = None
                return False
            zone = await client.zone_name()
            # Client.in_battle() treats read errors as False. Read the phase
            # directly so an unreadable duel cannot authorize state cleanup.
            phase = await client.duel.duel_phase()
    except Exception as exc:
        client._combat_ended_observation = None
        now = time.monotonic()
        if now >= getattr(client, '_combat_reconcile_log_at', 0.0):
            reason = '状态读取超过 3 秒' if isinstance(exc, asyncio.TimeoutError) else str(exc)
            logger.debug('{} 战斗状态暂不可校准，保留保护并稍后重试：{}', client.title, reason)
            client._combat_reconcile_log_at = now + 30.0
        return False
    if not zone or phase != DuelPhase.ended or protected():
        client._combat_ended_observation = None
        return False
    pending = getattr(client, 'just_entered_combat', None)
    now = time.monotonic()
    observation = getattr(client, '_combat_ended_observation', None)
    if (not isinstance(observation, tuple)
            or observation[:2] != (zone, pending)):
        client._combat_ended_observation = (zone, pending, now)
        return False
    if now - observation[2] < 1.5:
        return False

    helped = getattr(client, 'client_being_helped', None)
    if helped is not None:
        helpers = getattr(helped, 'helper_clients', [])
        helpers[:] = [helper for helper in helpers if helper is not client]
    client.entity_detect_combat_status = False
    client.just_entered_combat = None
    client.just_left_combat = False
    client.invincible_combat_timer = False
    client.client_being_helped = None
    client.original_location_before_combat = None
    client._combat_ended_observation = None
    if original_locations is not None:
        original_locations.pop(client.process_id, None)
    logger.info('{} 已确认战斗结束，清理残留入战标记，恢复自动任务。', client.title)
    return True


async def is_free(client: Client):
    # Returns True if not in combat, loading screen, or in dialogue.
    if getattr(client, '_character_selection_active', False) is True:
        return False
    return not any(
        [
            await client.is_loading(),
            await client.in_battle(),
            await is_visible_by_path(client, advance_dialog_path),
        ]
    )


async def get_quest_name(client: Client):
    # The HUD may be rebuilt between reads. Never retain a missing/stale window
    # while waiting for a different lookup to become visible.
    try:
        if not await is_free(client):
            return ""
        quest_name_window = await get_window_from_path(client.root_window, quest_name_path)
        if quest_name_window is None or isinstance(quest_name_window, bool):
            return ""
        if not await quest_name_window.is_visible():
            return ""
        quest_objective = await quest_name_window.maybe_text()
        if not isinstance(quest_objective, str):
            return ""
        client._quest_name_read_retry_at = 0.0
        return quest_objective.replace("<center>", "").replace("</center>", "")
    except Exception as exc:
        now = time.monotonic()
        retry_at = getattr(client, "_quest_name_read_retry_at", 0.0)
        if not isinstance(retry_at, (int, float)) or now >= retry_at:
            logger.debug(f"Client {client.title} - Quest HUD unavailable; retry next iteration: {exc}")
            client._quest_name_read_retry_at = now + 30.0
        return ""


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
    if not quest_info:
        return False

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


async def spiral_door_with_quest(client: Client):
    async with asyncio.timeout(15):
        button = await get_spiral_teleport_button(client)
        if button is None:
            return
        logger.debug(f"Client {client.title}: 世界选择界面已识别，点击进入世界。")
        while button is not None:
            async with client.mouse_handler:
                await client.mouse_handler.click_window(button)
            await asyncio.sleep(0.25)
            if await client.is_loading():
                break
            button = await get_spiral_teleport_button(client)

    while await client.is_loading():
        await asyncio.sleep(0.1)


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


async def _cycle_friends_list(
    client, right_button, friends_list, icon, icon_list, name, current_page
):

    name = " ".join(plain_text(name).split()).casefold() if name is not None else None
    list_text = await friends_list.maybe_text() or ""
    candidates = []
    for idx, friend_entry in enumerate(_friend_list_entry.finditer(list_text)):
        friend_icon = int(friend_entry.group("icon_index"))
        friend_icon_list = int(friend_entry.group("icon_list"))
        friend_name = " ".join(plain_text(friend_entry.group("name")).split()).casefold()
        if icon is not None and icon_list is not None:
            if friend_icon != icon or friend_icon_list != icon_list:
                continue
        elif not name:
            raise RuntimeError("Invalid args")
        if name and not (friend_name == name or (
                " " not in name and friend_name.split(maxsplit=1)[0] == name)):
            continue
        candidates.append((friend_entry, idx))

    if len(candidates) > 1:
        raise ValueError(f"好友匹配不唯一：{name or f'图标 {icon_list}:{icon}'}；请使用完整角色名或唯一好友图标")
    match, idx = candidates[0] if candidates else (None, 0)
    if match is not None:
        if name and name != " ".join(plain_text(match.group("name")).split()).casefold():
            logger.debug("好友首名唯一匹配：{} -> {}", name, match.group("name"))
        target_page = (idx // 10) + 1

        if target_page != current_page:
            if target_page < current_page:
                raise ValueError("目标好友在前一页；未确认翻页方向，停止本次传送")
            for _ in range(target_page - current_page):
                await client.mouse_handler.click_window(right_button)

    return match, idx


# TODO: add error if friend is busy message pops up
async def teleport_to_friend_from_list(
    client, *, icon_list: int = None, icon_index: int = None, name: str = None
):
    """
    Teleport to a friend from the client's friend list

    Args:
        client: Client to teleport
        icon_list: Icon list the icon is from (1 or 2) or None
        icon_index: Index of the icon or None
        name: Name of the player or None
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
        raise ValueError("No friends online")

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
    )

    if friend is None:
        raise ValueError(
            f"Could not find friend with icon {icon_index} icon list {icon_list} and/or name {name}"
        )

    await _click_on_friend(client, friends_list_window, friend_index)

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
    await _teleport_to_friend(client, character_window)

    # close friends window
    await friends_window.write_flags(WindowFlags(2147483648))


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


async def collect_wisps(client: Client, nothing_but_safe_entities=True, *, limit=None):
    # Collects all the wisps in the current area, only works within the entity draw distance.
    if limit is not None and limit <= 0:
        return
    sprinter = SprintyClient(client)
    entities = []
    resource_readers = {}
    for name, current, maximum in (
            ('WispHealth', client.stats.current_hitpoints, client.stats.max_hitpoints),
            ('WispMana', client.stats.current_mana, client.stats.max_mana)):
        if await current() < await maximum():
            found = await sprinter.get_base_entities_with_vague_name(name)
            entities.extend(found)
            resource_readers.update({id(entity): (current, maximum) for entity in found})
    if limit is None:
        entities += await sprinter.get_base_entities_with_vague_name('WispGold')
    if not entities:
        return

    if nothing_but_safe_entities:
        safe_entities = await sprinter.find_safe_entities_from(entities)
    else:
        safe_entities = entities

    zone = await client.zone_name()
    total_collected = 0
    for entity in safe_entities:
        readers = resource_readers.get(id(entity))
        if readers is not None and await readers[0]() >= await readers[1]():
            continue  # A previous pickup can fill this resource.
        wisp_xyz = await entity.location()
        if not await is_free(client) or await client.zone_name() != zone:
            return
        await client.teleport(wisp_xyz)
        total_collected += 1
        if limit is not None and total_collected >= limit:
            return
        await asyncio.sleep(0.1)


async def collect_wisps_with_limit(client: Client, limit=3):
    return await collect_wisps(client, limit=limit)


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
        ):
            if attempt < max_retries:
                logger.debug(
                    f"Task {task_coro} encountered a memory error, retrying ({attempt + 1}/{max_retries})..."
                )
                await asyncio.sleep(1)
            else:
                logger.error(
                    f"Task {task_coro} exceeded max retries ({max_retries}), giving up."
                )


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
