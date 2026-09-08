"""Dismiss selected interruptions only while their client group runs a script."""
import asyncio
import re

from loguru import logger

from src.interaction_prompts import plain_text
from src.utils import close_endorsement_window
from src.window_text import read_control_text


def popup_kind(title, caption):
    title, caption = plain_text(title).casefold(), plain_text(caption).casefold()
    # Root GUI_HarassmentTitle and GUI_ConfirmAddCharacterToFriends.
    if title.rstrip('!！。.') in ('you have been reported', '你已经被举报', '你已被举报', '你已經被舉報'):
        return 'reported'
    if ((caption.startswith('accept ') and caption.endswith('as your friend?'))
            or (caption.startswith('是否接受') and re.search(r'成为你的好友[？?]$|成為你的好友[？?]$', caption))):
        return 'friend_request'
    # The one-button result of adding a friend can also block execution.
    if ((caption.startswith('you are now friends with ') and caption.endswith('!'))
            or (caption.startswith('你和 ') and caption.endswith(('成为了好友！', '成為了好友！')))):
        return 'friend_added'
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


async def close_script_popup(client):
    if await client.is_loading():
        return False
    if await close_endorsement_window(client):
        return True
    for window in await client.root_window.get_windows_with_name('MessageBoxModalWindow'):
        if not await window.is_visible():
            continue
        kind = await modal_kind(window)
        if kind is None:
            continue
        # Close an incoming invitation with No; acknowledge one-button notices.
        button_name = 'rightButton' if kind == 'friend_request' else 'centerButton'
        for button in await window.get_windows_with_name(button_name):
            if not await button.is_visible():
                continue
            async with client.mouse_handler:
                # Recheck after waiting for the mouse: a script may have already
                # replaced this dialog with a purchase or teleport confirmation.
                if (not await client.is_loading() and await window.is_visible()
                        and await button.is_visible() and await modal_kind(window) == kind):
                    await client.mouse_handler.click_window(button)
                    return True
    return False


async def watch_script_popups(client):
    while True:
        try:
            await close_script_popup(client)
        except Exception as exc:
            # A disappearing window or disconnected client must not stop scripts
            # or another client's popup watcher. Cancellation still propagates.
            logger.trace(f'Client {client.title}: 脚本弹窗检查暂不可用：{exc}')
        await asyncio.sleep(.5)


async def run_with_script_popups(run, clients):
    watchers = [asyncio.create_task(watch_script_popups(client)) for client in clients]
    try:
        return await run()
    finally:
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
