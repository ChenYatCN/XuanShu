"""Independent fishing groups with exclusive client ownership."""
import asyncio
from copy import deepcopy
from loguru import logger


class FishingGroups:
    def __init__(self, fish, clients, publish):
        self.fish = fish
        self.clients = clients
        self.publish = publish
        self.groups = {}
        self.workers = {}
        self._run_lock = asyncio.Lock()

    def snapshot(self):
        return [dict(clients=list(key), settings=deepcopy(value[1]))
                for key, value in self.groups.items()]

    def notify(self):
        self.publish(self.snapshot())

    def add(self, titles, settings):
        requested = set(titles or [])
        members = tuple(c for c in self.clients() if c.title in requested)
        if not requested or {c.title for c in members} != requested:
            raise ValueError("请选择仍已注入的钓鱼客户端")
        occupied = {id(c) for group, _ in self.groups.values() for c in group}
        if any(id(c) in occupied for c in members):
            raise ValueError("所选客户端已有钓鱼组，请先停止对应组")
        key = tuple(c.title for c in members)
        self.groups[key] = (members, deepcopy(settings))
        self.notify()

    async def stop(self, titles=None):
        selected = None if titles is None else set(titles)
        keys = [key for key in self.groups if selected is None or selected.intersection(key)]
        tasks = []
        for key in keys:
            self.groups.pop(key, None)
            for task in self.workers.pop(key, []):
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.notify()

    async def _fish(self, client, config):
        client.is_fishing = True
        try:
            await self.fish(client, bool(config.get('fish_chest_only', False)),
                            school=config.get('fish_school', 'Any'),
                            rank=int(config.get('fish_rank', 0)),
                            fish_id=int(config.get('fish_id', 0)),
                            size_min=float(config.get('fish_size_min', 0)),
                            size_max=float(config.get('fish_size_max', 999)))
        finally:
            client.is_fishing = False

    async def run(self):
        # Serialize restart cleanup so old memory patches cannot race new workers.
        async with self._run_lock:
            try:
                while True:
                    # Client titles can change when the launcher reorders clients.
                    # Keep ownership attached to client objects and refresh display keys.
                    renamed = {tuple(c.title for c in members): (key, members, config)
                               for key, (members, config) in self.groups.items()}
                    if any(new != old for new, (old, _, _) in renamed.items()):
                        self.groups = {new: (members, config)
                                       for new, (_, members, config) in renamed.items()}
                        self.workers = {new: self.workers[old]
                                        for new, (old, _, _) in renamed.items() if old in self.workers}
                        self.notify()
                    live = {id(c) for c in self.clients()}
                    for key, (members, config) in list(self.groups.items()):
                        tasks = self.workers.get(key)
                        if any(id(c) not in live for c in members):
                            await self.stop(key)
                            continue
                        if tasks and any(t.done() for t in tasks):
                            for task in tasks:
                                if task.done() and not task.cancelled() and task.exception():
                                    logger.opt(exception=task.exception()).error(
                                        f"钓鱼组 {'+'.join(key)} 已停止")
                            await self.stop(key)
                            continue
                        if tasks is None:
                            self.workers[key] = [asyncio.create_task(self._fish(c, config)) for c in members]
                    await asyncio.sleep(.2)
            finally:
                tasks = [t for group in self.workers.values() for t in group]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self.workers.clear()
