"""Session-local mount receipt monitoring; never treats player chat as a drop."""
import asyncio
import re
import time
from loguru import logger
from src.drop_logger import get_chat, filter_drops, find_new_stuff
from wizwalker.memory.memory_objects.game_object_template import WizGameObjectTemplate
from wizwalker.memory.memory_objects.enums import ObjectType


async def owned_mounts(client):
    """Read backpack and equipped mounts via the same API as backpack_space."""
    result = {}
    for getter in (client.client_object.try_get_inventory_behavior,
                   client.client_object.try_get_equipment_behavior):
        inventory = await getter()
        if inventory is None:
            continue
        for item in await inventory.item_list():
            base = await item.object_template()
            if base is None:
                continue
            template = WizGameObjectTemplate(client.hook_handler, await base.read_base_address())
            if await template.object_type() != ObjectType.horse:
                continue
            code = await template.display_name()
            name = await client.cache_handler.get_langcode_name(code)
            result[await item.global_id_full()] = (name or code, code, await template.object_name())
    return result


async def has_mount(client, target):
    for name, code, internal in (await owned_mounts(client)).values():
        if target.casefold() in {name.casefold(), code.casefold(), internal.casefold()}:
            return name
    return None


def mount_receipts(text, target=''):
    result = []
    for line in text.splitlines():
        if 'Art_Chat_System.dds' not in line or not re.search(r'<(?:image|item);Mount>', line):
            continue
        for name in filter_drops([line]):
            if not target or name.casefold() == target.casefold():
                result.append(name)
    return result


class MountMonitor:
    def __init__(self, target=''):
        self.target = target
        self.previous = None
        self.baseline = None
        self.next_scan = 0

    async def check(self, client):
        if time.monotonic() >= self.next_scan:
            current = await owned_mounts(client)
            self.next_scan = time.monotonic() + .2
            if self.target:
                for name, code, internal in current.values():
                    if self.target.casefold() in {name.casefold(), code.casefold(), internal.casefold()}:
                        return name
            elif self.baseline is not None:
                for key, values in current.items():
                    if key not in self.baseline:
                        return values[0]
            if self.baseline is None:
                self.baseline = set(current)
        text = await get_chat(client) or ''
        if self.previous is None:
            self.previous = text
            return None
        added = find_new_stuff(self.previous, text)
        self.previous = text
        mounts = mount_receipts(added, self.target)
        return mounts[0] if mounts else None


async def run_until_mount(run, clients, target=''):
    monitors = [(client, MountMonitor(target)) for client in clients]
    for client, monitor in monitors:
        name = await monitor.check(client)
        if name:
            logger.info('{} 已持有坐骑 {}，正常结束脚本组。', client.title, name)
            return
    task = asyncio.create_task(run())
    try:
        while not task.done():
            for client, monitor in monitors:
                name = await monitor.check(client)
                if name:
                    logger.info('{} 获得坐骑 {}，正常停止当前脚本组。', client.title, name)
                    return
            await asyncio.wait({task}, timeout=.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
