"""Named client groups and isolated lifetimes for scoped shortcut actions."""
import asyncio
import re
from loguru import logger
from src.bot_targeting import resolve_bot_clients


def parse_groups(text):
    groups = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, sep, values = line.partition('=')
        name = name.strip()
        clients = list(dict.fromkeys(s.lower() for s in re.split(r'[,，\s]+', values.strip()) if s))
        if not sep or not name or name in groups or not clients or any(not re.fullmatch(r'p[1-9]\d*', s) for s in clients):
            raise ValueError('每行填写：组名=p1,p2；组名不可重复，客户端须为 p 编号。')
        groups[name] = clients
    return groups


class HotkeyGroups:
    def __init__(self, clients, run):
        self.clients = clients
        self.run = run
        self.groups = {}

    def active(self, action):
        return any(key[0] == action for key in self.groups)

    async def toggle(self, action, titles):
        members = tuple(resolve_bot_clients(self.clients(), None if titles is None else tuple(titles)))
        if not members or (titles is not None and {c.title.casefold() for c in members} != {t.casefold() for t in titles}):
            raise ValueError('分组内有客户端未连接，未执行快捷键。')
        ids = frozenset(id(c) for c in members)
        key = (action, ids)
        if key in self.groups:
            await self.stop(key)
            return
        if titles is None and self.active(action):
            for active_key in list(self.groups):
                if active_key[0] == action:
                    await self.stop(active_key)
            return
        if any(k[0] == action and k[1] & ids for k in self.groups):
            raise ValueError('该功能已有重叠的运行组，请先停止原组。')
        task = asyncio.create_task(self.run(action, members))
        self.groups[key] = (members, task)
        task.add_done_callback(lambda done: self._finished(key, done))
        logger.info('快捷键 {} 已启动：{}', action, ', '.join(c.title for c in members))

    def _finished(self, key, task):
        if self.groups.get(key, (None, None))[1] is task:
            self.groups.pop(key, None)
        if not task.cancelled() and task.exception():
            logger.warning('快捷键分组 {} 已停止：{}', key[0], task.exception())

    async def stop(self, key=None):
        entries = list(self.groups) if key is None else [key]
        tasks = []
        for entry in entries:
            value = self.groups.pop(entry, None)
            if value:
                value[1].cancel()
                tasks.append(value[1])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def remove_missing(self):
        live = {id(c) for c in self.clients() if getattr(c, 'is_running', lambda: True)()}
        for key in list(self.groups):
            if not key[1].issubset(live):
                await self.stop(key)
