"""Named client groups and isolated lifetimes for scoped shortcut actions."""
import asyncio
from loguru import logger
from src.bot_targeting import resolve_bot_clients


def client_available(client, clients):
    try:
        return any(c is client for c in clients) and getattr(client, 'is_running', lambda: True)()
    except Exception:
        return False


async def run_client_worker(client, clients, run):
    """Isolate a scoped client's failure/disconnect without cancelling its peers."""
    task = None
    try:
        if not client_available(client, clients()):
            return
        task = asyncio.create_task(run())
        while not task.done():
            if not client_available(client, clients()):
                return
            await asyncio.wait((task,), timeout=0.25)
        return await task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning('快捷键客户端 {} 执行失败：{}', client.title, exc)
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class HotkeyGroups:
    def __init__(self, clients, run):
        self.clients = clients
        self.run = run
        self.groups = {}

    def active(self, action):
        return any(key[0] == action for key in self.groups)

    async def toggle(self, action, titles):
        available = self.clients()
        live = [c for c in available if client_available(c, available)]
        members = list(resolve_bot_clients(live, None if titles is None else tuple(titles)))
        if not members:
            raise ValueError('请选择仍已注入的快捷键作用客户端。')
        if action.startswith('toggle_') and action not in ('toggle_questing', 'toggle_sigil'):
            keys = [(action, frozenset((id(c),))) for c in members]
            if all(key in self.groups for key in keys):
                for key in keys:
                    await self.stop(key)
            else:
                for client, key in zip(members, keys):
                    if key not in self.groups:
                        self._start(action, [client])
            return
        ids = frozenset(id(c) for c in members)
        key = (action, ids)
        matching = next((k for k, (running, _) in self.groups.items()
                         if k[0] == action and frozenset(id(c) for c in running) == ids), None)
        if matching is not None:
            await self.stop(matching)
            return
        if titles is None and self.active(action):
            for active_key in list(self.groups):
                if active_key[0] == action:
                    await self.stop(active_key)
            return
        if any(k[0] == action and {id(c) for c in running} & ids
               for k, (running, _) in self.groups.items()):
            raise ValueError('该功能已有重叠的运行组，请先停止原组。')
        self._start(action, members)

    def _start(self, action, members):
        key = (action, frozenset(id(c) for c in members))
        task = asyncio.create_task(self.run(action, members))
        self.groups[key] = (members, task)
        task.add_done_callback(lambda done: self._finished(key, done))
        logger.info('快捷键 {} 已启动：{}', action, ', '.join(c.title for c in members))

    async def stop_client(self, action, client):
        for key, (members, _) in list(self.groups.items()):
            if key[0] == action and any(c is client for c in members):
                await self.stop(key)

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
        live = self.clients()
        for key, (members, _) in list(self.groups.items()):
            members[:] = [c for c in members if client_available(c, live)]
            if not members:
                await self.stop(key)
