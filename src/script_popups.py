"""Dismiss selected interruptions only while automation owns the clients."""
import asyncio
import re
import time

from loguru import logger

from src.automation_ownership import automation_owner
from src.interaction_prompts import plain_text
from src.utils import close_endorsement_window, get_window_from_path
from src.window_text import read_control_text
from src.paths import exit_pet_leveled_up_button_path
from src.paths import exit_pet_leveled_up_button_path


def popup_kind(title, caption):
    title, caption = plain_text(title).casefold(), plain_text(caption).casefold()
    # Root GUI_HarassmentTitle and GUI_ConfirmAddCharacterToFriends.
    if title.rstrip('!！。. ') in ('you have been reported', '你已经被举报', '你已被举报', '你已經被舉報'):
        return 'reported'
    if ((caption.startswith('accept ') and caption.endswith('as your friend?'))
            or (caption.startswith('是否接受') and re.search(r'成为你的好友[？?]$|成為你的好友[？?]$', caption))):
        return 'friend_request'
    # The one-button result of adding a friend can also block execution.
    if ((caption.startswith('you are now friends with ') and caption.endswith('!'))
            or (caption.startswith('你和 ') and caption.endswith(('成为了好友！', '成為了好友！')))):
        return 'friend_added'
    group_title = title.rstrip('?？。. ')
    if group_title in ('join a group', '加入一个队伍', '加入一個隊伍'):
        if (re.fullmatch(r'.+ has invited you to join a group\. would you like to join\?', caption)
                or re.fullmatch(
                    r'.+邀请你加入一(?:个|個)队伍[，,]\s*是否同意[？?]', caption
                )
                or re.fullmatch(
                    r'.+邀請你加入一(?:个|個)隊伍[，,]\s*是否同意[？?]', caption
                )):
            return 'group_invite'
    return None


async def modal_kind(window):
    values = []
    # Names verified in Root.wad GUI/MessageBoxWindow.gui.
    for name in ('TitleText', 'CaptionText'):
        parts = []
        for text in await window.get_windows_with_name(name):
            if await text.is_visible():
                parts.append(await read_control_text(text) or '')
        values.append(' '.join(parts))
    return popup_kind(*values)


_BUTTON_LAYOUT = ['messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout']


async def modal_close_button(window, kind):
    # Exact hierarchy from GUI/MessageBoxWindow.gui, scoped to this modal.
    visible = {}
    for name in ('leftButton', 'centerButton', 'rightButton'):
        button = await get_window_from_path(window, [*_BUTTON_LAYOUT, name])
        if button and await button.is_visible():
            visible[name] = button
    if kind == 'friend_request':
        return visible.get('rightButton')
    # Group invitations use leftButton (GG_ACCEPT) and rightButton (GG_CANCEL).
    if kind == 'group_invite' and set(visible) == {'leftButton', 'rightButton'}:
        return visible['rightButton']
    if kind in ('reported', 'friend_added') and len(visible) == 1:
        return next(iter(visible.values()))
    return None


async def close_pet_level_popup(client):
    # Scope the close button to the visible pet-level panel, not a generic X.
    for panel in await client.root_window.get_windows_with_name('PetLevelUpWindow'):
        if not await panel.is_visible():
            continue
        button = await get_window_from_path(panel, exit_pet_leveled_up_button_path[2:])
        if not button or not await button.is_visible():
            continue
        async with client.mouse_handler:
            if (not await client.is_loading() and await panel.is_visible()
                    and await button.is_visible()):
                await client.mouse_handler.click_window(button)
                return True
    return False


async def close_pet_level_popup(client):
    # Scope the close button to the visible pet-level panel, not a generic X.
    for panel in await client.root_window.get_windows_with_name('PetLevelUpWindow'):
        if not await panel.is_visible():
            continue
        button = await get_window_from_path(panel, exit_pet_leveled_up_button_path[2:])
        if not button or not await button.is_visible():
            continue
        async with client.mouse_handler:
            if (not await client.is_loading() and await panel.is_visible()
                    and await button.is_visible()):
                await client.mouse_handler.click_window(button)
                return True
    return False


async def close_automation_popup(client):
    async with automation_owner(client, 'automation-ui-guard'):
        return await _close_automation_popup_owned(client)


async def skip_magic_wheel_tutorial(client):
    if not getattr(client, 'questing_status', False):
        return False
    if await client.zone_name() != 'Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove':
        return False
    # Paths verified against Root.wad GUI/Tutorial.gui, not generic text clicks.
    if time.monotonic() < getattr(client, '_magic_wheel_skip_until', 0):
        for modal in await client.root_window.get_windows_with_name('MessageBoxModalWindow'):
            if not await modal.is_visible():
                continue
            titles = await modal.get_windows_with_name('TitleText')
            values = [plain_text(await read_control_text(t) or '').strip().rstrip('?？ ') for t in titles if await t.is_visible()]
            if not any(t.casefold() in ('确定要跳过新手教程吗', 'are you sure you want to skip the tutorial') for t in values):
                continue
            left = await get_window_from_path(modal, [*_BUTTON_LAYOUT, 'leftButton'])
            right = await get_window_from_path(modal, [*_BUTTON_LAYOUT, 'rightButton'])
            if left and right and await left.is_visible() and await right.is_visible():
                async with client.mouse_handler:
                    await client.mouse_handler.click_window(left)
                client._magic_wheel_skip_until = 0
                return True
    for panel in await client.root_window.get_windows_with_name('TutorialWindow'):
        if not await panel.is_visible():
            continue
        title = await get_window_from_path(panel, ['DialogWindow', 'TitleText'])
        skip = await get_window_from_path(panel, ['DialogWindow', 'SkipButton'])
        if not title or not skip or not await skip.is_visible():
            continue
        if plain_text(await read_control_text(title) or '').strip().casefold() != 'the magic wheel':
            continue
        async with client.mouse_handler:
            await client.mouse_handler.click_window(skip)
        client._magic_wheel_skip_until = time.monotonic() + 15
        return True
    return False


async def _close_automation_popup_owned(client):
    if await client.is_loading():
        return False
    if await skip_magic_wheel_tutorial(client):
        return True
    if await close_endorsement_window(client):
        return True
    if await close_pet_level_popup(client):
        return True
    if await close_pet_level_popup(client):
        return True
    for window in await client.root_window.get_windows_with_name('MessageBoxModalWindow'):
        if not await window.is_visible():
            continue
        kind = await modal_kind(window)
        if kind is None:
            continue
        button = await modal_close_button(window, kind)
        if button is not None:
            async with client.mouse_handler:
                # Recheck after waiting for the mouse: a script may have already
                # replaced this dialog with a purchase or teleport confirmation.
                if (not await client.is_loading() and await window.is_visible()
                        and await button.is_visible() and await modal_kind(window) == kind):
                    current_button = await modal_close_button(window, kind)
                    if current_button is not None:
                        await client.mouse_handler.click_window(current_button)
                        return True
    return False


close_script_popup = close_automation_popup


async def watch_automation_ui(client):
    while True:
        try:
            await close_script_popup(client)
        except Exception as exc:
            # A disappearing window or disconnected client must not stop automation
            # or another client's popup watcher. Cancellation still propagates.
            logger.trace(f'Client {client.title}: 自动化界面守卫暂不可用：{exc}')
        await asyncio.sleep(.5)


watch_script_popups = watch_automation_ui


async def run_with_automation_ui_guard(run, clients):
    watchers = [asyncio.create_task(watch_script_popups(client)) for client in clients]
    try:
        return await run()
    finally:
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)


run_with_script_popups = run_with_automation_ui_guard
