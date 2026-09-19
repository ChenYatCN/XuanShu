"""Isolated ibao-ST sessions on already managed XuanShu clients."""
import asyncio
import time
import types
import math
from copy import deepcopy
from loguru import logger
from wizwalker import XYZ
from wizwalker.errors import HookNotActive
from src import ibao_core
from src.ibao_locations import DEFAULT_LOCATIONS, expand_location_aliases
from src.script_popups import run_with_automation_ui_guard


IBAO_INACTIVITY_TIMEOUT = 180.0


class _TrackedMouseHandler:
    """Forward mouse operations while recording input-producing actions."""

    def __init__(self, mouse_handler, touch):
        self._mouse_handler = mouse_handler
        self._touch = touch

    def __getattr__(self, name):
        return getattr(self._mouse_handler, name)

    async def click_window(self, *args, **kwargs):
        self._touch()
        async with self._mouse_handler:
            return await self._mouse_handler.click_window(*args, **kwargs)

    async def click_window_with_name(self, *args, **kwargs):
        self._touch()
        async with self._mouse_handler:
            return await self._mouse_handler.click_window_with_name(*args, **kwargs)


class _TrackedClient:
    """Transparent client proxy that timestamps ibao keyboard/movement input."""

    def __init__(self, client, touch):
        self._client = client
        self.mouse_handler = _TrackedMouseHandler(client.mouse_handler, touch)
        self._touch = touch

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def send_key(self, *args, **kwargs):
        self._touch()
        return await self._client.send_key(*args, **kwargs)

    async def teleport(self, *args, **kwargs):
        self._touch()
        return await self._client.teleport(*args, **kwargs)


def parse_locations(text):
    locations, names = [], []
    for number, line in enumerate(expand_location_aliases(text).splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) != 3:
            raise ValueError(f'地点配置第 {number} 行格式错误')
        aliases, kind, points = parts
        if kind not in ('Location', 'Dungeon'):
            raise ValueError(f'地点配置第 {number} 行类型错误')
        coords = [XYZ(*map(float, point.split(','))) for point in points.split(':') if point.strip()] or [None]
        if not aliases.strip(': '):
            raise ValueError(f'地点配置第 {number} 行缺少名称')
        if any(p is not None and not all(math.isfinite(v) for v in (p.x, p.y, p.z)) for p in coords):
            raise ValueError(f'地点配置第 {number} 行坐标必须是有限数字')
        for name in aliases.split(':'):
            if name.strip():
                names.append(name.strip())
                locations.append([name.strip(), kind, coords])
    if not names:
        raise ValueError('至少需要一个采集地点')
    return locations, names


def create_session(client, settings):
    scope = dict(vars(ibao_core))
    for name, value in list(scope.items()):
        if isinstance(value, list):
            scope[name] = deepcopy(value)
        elif isinstance(value, types.FunctionType) and value.__module__ == ibao_core.__name__:
            scope[name] = types.FunctionType(value.__code__, scope, name, value.__defaults__, value.__closure__)
            scope[name].__kwdefaults__ = value.__kwdefaults__
    scope['locationList'], scope['baseLocationList'] = parse_locations(settings.get('locations', DEFAULT_LOCATIONS))
    scope['activeClients'] = [ibao_core.clientInfo('', '', client.window_handle, client.title, [], 0, 0)]
    config = {'ENABLE_PAGE_TURNING': bool(settings.get('page_turning', False)),
              'CHARACTER_SWITCH_DELAY': float(settings.get('switch_delay', .3))}
    scope['get_config'] = lambda: dict(config)
    scope['report_collection'] = settings.get('_collection_callback', lambda: None)
    scope['print'] = lambda *args, **kwargs: logger.info('[ibao {}] {}', client.title, ' '.join(map(str, args)))
    return scope


async def run_client(client, settings):
    # Cursor ownership is limited to individual clicks in _TrackedMouseHandler.
    await _run_client_session(client, settings)


async def prepare_restarted_client(client, timeout=60):
    """Dismiss the title, click Play and wait for a loaded character."""
    async with asyncio.timeout(timeout):
        await client.activate_hooks(wait_for_ready=False)
        while True:
            try:
                ready = await ibao_core.is_visible_by_path(client.root_window, ibao_core.playButton)
            except Exception:
                ready = False  # Root window can be unset during initial frames.
            if ready:
                break
            await client.send_key(ibao_core.Keycode.ESC, .1)
            await asyncio.sleep(1)
        async with client.mouse_handler:
            await ibao_core.click_window_until_gone(client, ibao_core.playButton)
        while True:
            try:
                if (not await ibao_core.is_visible_by_path(client.root_window, ibao_core.playButton)
                        and not await client.is_loading() and await client.zone_name()):
                    await client.body.position()
                    return
            except Exception:
                pass  # Character data is transient while entering the world.
            await asyncio.sleep(.5)


async def _run_client_session(client, settings):
    scope = create_session(client, settings)
    tracked_client = _TrackedClient(
        client, settings.get('_activity_callback', lambda: None)
    )
    # Reuse exact original UI paths, but bound entry and allow cancellation.
    async with asyncio.timeout(60):
        while not await scope['is_visible_by_path'](tracked_client.root_window, scope['playButton']):
            if await scope['is_visible_by_path'](tracked_client.root_window, scope['logOutConfirm']):
                await scope['click_window_until_gone'](tracked_client, scope['logOutConfirm'])
            elif await scope['is_visible_by_path'](tracked_client.root_window, scope['quitButton']):
                await scope['click_window_until_gone'](tracked_client, scope['quitButton'])
            else:
                await tracked_client.send_key(ibao_core.Keycode.ESC, .1)
            await asyncio.sleep(1)
    await scope['azothFarmer'](tracked_client, 0)


async def progress_signature(client):
    """Observe world/character changes, not repeated input attempts."""
    loading = await client.is_loading()
    if loading:
        return ('loading',)
    zone = await client.zone_name()
    name = await ibao_core.window_from_path(client.root_window, ibao_core.txtName)
    character = await name.maybe_text() if name else None
    cooldown = await ibao_core.window_from_path(client.root_window, ibao_core.petPowerCooldown)
    remaining = await cooldown.maybe_text() if cooldown and await cooldown.is_visible() else None
    return (zone, character, remaining)


class IbaoGroups:
    def __init__(self, clients, publish, worker=run_client,
                 ui_guard=run_with_automation_ui_guard, recovery=None,
                 inactivity_timeout=IBAO_INACTIVITY_TIMEOUT, clock=time.monotonic,
                 store=None):
        self.clients, self.publish, self.worker = clients, publish, worker
        self.ui_guard = ui_guard
        self.recovery = recovery
        self.inactivity_timeout = float(inactivity_timeout)
        self.clock = clock
        self.groups = []
        self.results = []
        self.store = store
        saved = store.get_setting('ibao_data') if store else None
        saved = saved if isinstance(saved, dict) else {}
        self.configs = saved.get('configs', {})
        self.results = saved.get('results', [])
        for row in self.results:
            row['archived'] = True
            if row.get('state') in ('采集中', '正在重启'):
                row['state'] = '上次运行中断'
        self._last_publish = self.clock()

    def rows(self):
        rows = []
        for g in self.groups:
            elapsed = max(0, self.clock() - g['started'])
            rows.append({'clients': [c.title for c, _ in g['workers']],
                         'account': g['account'], 'collected': g['collected'],
                         'elapsed': elapsed, 'started_at': g['started_at'],
                         'restarts': g['restarts'],
                         'state': '正在重启' if g['recovering'] else '采集中'})
        return rows + deepcopy(self.results)

    def persist(self):
        if self.store:
            try:
                self.store.set_setting('ibao_data', {'configs': self.configs, 'results': self.rows()})
            except OSError as exc:
                logger.error('生产线记录保存失败：{}', exc)

    def clear_round(self):
        for row in self.results:
            row['archived'] = True
        for group in self.groups:
            row = next(r for r in self.rows() if r['clients'] == [c.title for c, _ in group['workers']])
            row['state'] = '已归档（清空本轮）'
            row['archived'] = True
            self.results.append(row)
            group['collected'] = 0
            group['restarts'] = 0
            group['started'] = self.clock()
            group['started_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.notify()
        self.persist()

    def set_recovery_handler(self, recovery):
        self.recovery = recovery

    async def _guarded_worker(self, client, settings):
        return await self.ui_guard(
            lambda: self.worker(client, settings), [client]
        )

    @staticmethod
    def _replace_group_client(group, old_client, new_client, supervisor):
        group['workers'][:] = [
            (new_client if client is old_client else client, task)
            for client, task in group['workers']
        ]
        if not group['workers']:
            group['workers'].append((new_client, supervisor))

    async def _run_worker(self, group, client, settings):
        """Run one client and replace it in-place after an inactivity restart."""
        supervisor = asyncio.current_task()
        current_client = client
        while True:
            current_client.is_ibao = True
            last_activity = self.clock()
            last_signature = None

            def touch():
                nonlocal last_activity
                last_activity = self.clock()

            runtime_settings = deepcopy(settings)
            # Input attempts are not evidence of progress.
            runtime_settings['_activity_callback'] = lambda: None
            runtime_settings['_progress_callback'] = touch
            def collected():
                group['collected'] += 1
                group['consecutive_restarts'] = 0
                touch()
                self.notify()
                self.persist()
            runtime_settings['_collection_callback'] = collected
            worker_task = asyncio.create_task(
                self._guarded_worker(current_client, runtime_settings)
            )
            group['_worker_task'] = worker_task
            try:
                while not worker_task.done():
                    if not getattr(current_client, 'is_running', lambda: True)():
                        raise HookNotActive('Client')
                    if self.worker is run_client:
                        try:
                            async with asyncio.timeout(2):
                                signature = await progress_signature(current_client)
                            if signature != last_signature:
                                touch()
                                last_signature = signature
                        except HookNotActive:
                            raise
                        except Exception:
                            pass
                    remaining = self.inactivity_timeout - (
                        self.clock() - last_activity
                    )
                    if remaining <= 0:
                        break
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(worker_task), timeout=min(remaining, 2)
                        )
                    except Exception:
                        if worker_task.done():
                            break
                        continue

                if worker_task.done():
                    if worker_task.cancelled():
                        return await worker_task
                    error = worker_task.exception()
                    if isinstance(error, HookNotActive) or not getattr(current_client, 'is_running', lambda: True)():
                        if error is None:
                            raise HookNotActive('Client')
                        raise error
                    if error is None or self.recovery is None:
                        return await worker_task
                    logger.warning('ibao {} 采集异常，等待无操作超时后恢复: {}', current_client.title, error)
                    await asyncio.sleep(max(0, self.inactivity_timeout - (self.clock() - last_activity)))

                logger.warning(
                    'ibao {} 超过 {} 秒无采集、角色、地图或冷却进展，准备恢复',
                    current_client.title,
                    round(self.inactivity_timeout),
                )
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
                if self.recovery is None:
                    raise RuntimeError('未配置 ibao 客户端自动重启接口')
                if group['consecutive_restarts'] >= 3:
                    raise RuntimeError('连续 3 次重启后仍无收集，已暂停；请检查地点、宠物能力和零食后手动启动')
                group['consecutive_restarts'] += 1
                group['restarts'] += 1

                group['recovering'] = True
                self.notify()
                old_client = current_client
                try:
                    current_client = await self.recovery(
                        old_client, deepcopy(settings)
                    )
                finally:
                    group['recovering'] = False
                if current_client is None:
                    raise RuntimeError('ibao 客户端自动重启没有返回新客户端')

                old_client.is_ibao = False
                self._replace_group_client(
                    group, old_client, current_client, supervisor
                )
                self.notify()
                logger.info(
                    'ibao {} 已重新连接，继续执行原采集配置',
                    current_client.title,
                )
            finally:
                if not worker_task.done():
                    worker_task.cancel()
                    await asyncio.gather(worker_task, return_exceptions=True)
                group.pop('_worker_task', None)

    def notify(self):
        self.publish(self.rows())

    def add(self, titles, settings):
        from src.bot_targeting import resolve_bot_clients
        members = resolve_bot_clients(self.clients(), tuple(titles or []))
        if (not members or {c.title for c in members} != set(titles)
                or any(not getattr(c, 'is_running', lambda: True)() for c in members)):
            raise ValueError('请选择仍已注入的客户端')
        occupied = {id(c) for g in self.groups for c, task in g['workers']}
        if any(id(c) in occupied for c in members):
            raise ValueError('所选客户端已经在采集，请先停止')
        parse_locations(settings.get('locations', DEFAULT_LOCATIONS))
        delay = float(settings.get('switch_delay', .3))
        if not .1 <= delay <= 10:
            raise ValueError('切换间隔应为 0.1 至 10 秒')
        for client in members:
            account = getattr(client, 'account_nick', None)
            if account:
                self.configs[account] = deepcopy(settings)
            group = {
                'settings': deepcopy(settings),
                'workers': [],
                'recovering': False,
                'collected': 0,
                'account': account or '',
                'started': self.clock(),
                'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'consecutive_restarts': 0, 'restarts': 0,
            }
            self.groups.append(group)
            client.is_ibao = True
            task = asyncio.create_task(
                self._run_worker(group, client, deepcopy(settings))
            )
            group['workers'].append((client, task))
            task.add_done_callback(lambda done, g=group, c=client: self._finished(g, c, done))
        self.notify()
        self.persist()

    def _finished(self, group, client, task):
        row = next(r for r in self.rows() if r['started_at'] == group['started_at'] and r['clients'] == [c.title for c, _ in group['workers']])
        row['state'] = ('异常停止：' + str(task.exception())) if not task.cancelled() and task.exception() else '已停止'
        self.results.append(row)
        for current_client, _ in group['workers']:
            current_client.is_ibao = False
        client.is_ibao = False
        if not task.cancelled() and task.exception():
            logger.warning('ibao {} 已停止：{}', client.title, task.exception())
        group['workers'][:] = [(c, t) for c, t in group['workers'] if t is not task]
        if not group['workers'] and group in self.groups:
            self.groups.remove(group)
        self.notify()
        self.persist()

    async def stop(self, titles=None):
        tasks = [task for g in self.groups for c, task in g['workers']
                 if titles is None or c.title in titles]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.notify()

    async def remove_missing(self):
        if self.groups and self.clock() - self._last_publish >= 5:
            self._last_publish = self.clock()
            self.notify()
            self.persist()
        live = {id(c) for c in self.clients()}
        tasks = [
            task
            for group in self.groups
            if not group.get('recovering', False)
            for client, task in group['workers']
            if id(client) not in live or not getattr(client, 'is_running', lambda: True)()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
