"""Search loaded map regions and verify collecting against the tracked quest."""
import asyncio
import math
import time
from dataclasses import dataclass, field

from loguru import logger
from wizwalker import XYZ, Keycode

from src.collect_matching import collect_names, parse_collect_goal, count_increased, normalize_name
from src.collect_catalog import installed_catalog
from src.interaction_prompts import plain_text
from src.paths import npc_range_path
from src.utils import is_free, is_visible_by_path, get_popup_title
from src.teleport_math import collision_tp, calc_Distance
from src.task_lifecycle import gather_owned
from src.automation_ownership import automation_owner


_SKIP = {'basic positional', 'wisphealth', 'wispmana', 'kt_wisphealth', 'kt_wispmana',
         'wispgold', 'duelcircle', 'player object', 'skeletonkeysigilart', 'basic ambient', 'teleportpad'}


@dataclass
class SearchState:
    key: tuple
    route: list = field(default_factory=list)
    cursor: int = 0
    recent: dict = field(default_factory=dict)
    verified_templates: set = field(default_factory=set)
    last_warning: float = -float('inf')
    anchor: XYZ | None = None


@dataclass
class Candidate:
    entity: object
    xyz: XYZ
    code: str
    display: str
    internal: str
    template_id: int | None
    score: int

    @property
    def key(self):
        return (self.internal, round(self.xyz.x / 10), round(self.xyz.y / 10), round(self.xyz.z / 10))


class CollectSearch:
    SELENOPOLIS_BOOK_STORAGE = 'Krokotopia/KT_Selenopolis/Interiors/KT_Z05I08_Storage_A_Int'
    SELENOPOLIS_BOOK_MARKET = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
    SELENOPOLIS_BOOK_EXIT = XYZ(7553.401, -9213.249, -365.059)
    PROMPT_ALIGN_MAX_STEPS = 40

    def __init__(self, quester, client):
        self.quester = quester
        self.client = client
        self.names = collect_names()
        self.state = None
        self.zone = None
        self.quest_id = None
        self.goal = None
        self.last_text = ''
        self.catalog = None
        self.observed_names = {}
        self.last_popup = ''

    async def snapshot(self):
        self.last_text = plain_text(await self.quester.read_quest_txt(self.client))
        return parse_collect_goal(self.last_text)

    async def read_quest_id(self):
        try:
            value = await self.client.quest_id()
            return value if isinstance(value, int) else None
        except Exception:
            return None

    async def active(self):
        if not self.client.questing_status:
            return False
        if await self.client.is_loading() or await self.client.in_battle():
            return False
        if await self.client.zone_name() != self.zone:
            return False
        if self.quest_id is not None and await self.read_quest_id() != self.quest_id:
            return False
        return True

    async def same_goal(self):
        goal = await self.snapshot()
        return (goal is not None and goal.key == self.goal.key and goal.total == self.goal.total
                and (goal.total is None or goal.current < goal.total))

    async def leave_selenopolis_book_storage(self):
        """This collect stage takes place outside the storage, in the market."""
        key = (self.zone, self.quest_id, self.goal.key, self.goal.total, self.goal.current)
        previous = getattr(self.client, '_selenopolis_book_exit', None)
        if isinstance(previous, dict) and previous.get('key') == key:
            if time.monotonic() < previous['retry_at']:
                return False
        state = None
        try:
            async with asyncio.timeout(30):
                async with automation_owner(self.client, 'selenopolis-books-exit'):
                    if (not await self.active() or not await self.same_goal()
                            or not await is_free(self.client)
                            or getattr(self.client, 'refilling_potions', False)
                            or getattr(self.client, 'quest_recovery_owner', None)):
                        return False
                    state = {'key': key, 'retry_at': time.monotonic() + 30}
                    self.client._selenopolis_book_exit = state
                    logger.info('Client {}: 魔法书在 Marketplace of Ideas 收集，先前往仓库出口切区。',
                                self.client.title)
                    await self.client.teleport(self.SELENOPOLIS_BOOK_EXIT)
                stable_reads = 0
                for _ in range(150):
                    if (not self.client.questing_status
                            or getattr(self.client, 'refilling_potions', False)
                            or getattr(self.client, 'quest_recovery_owner', None)):
                        return False
                    if await self.client.is_loading():
                        stable_reads = 0
                    else:
                        zone = await self.client.zone_name()
                        if zone not in (self.SELENOPOLIS_BOOK_STORAGE, self.SELENOPOLIS_BOOK_MARKET):
                            logger.warning('Client {}: 魔法书出口到达非预期区域 {}，本轮停止采集。',
                                           self.client.title, zone)
                            return False
                        if (not await is_free(self.client)
                                or self.quest_id is not None and await self.read_quest_id() != self.quest_id
                                or not await self.same_goal()):
                            return False
                        stable_reads = stable_reads + 1 if zone == self.SELENOPOLIS_BOOK_MARKET else 0
                        if stable_reads >= 3:
                            self.zone = zone
                            self.client._selenopolis_book_exit = None
                            logger.info('Client {}: 已确认进入市场，继续魔法书采集。', self.client.title)
                            return True
                    await asyncio.sleep(.2)
                raise TimeoutError('仓库出口区域切换未确认')
        except Exception as exc:
            logger.warning('Client {}: 魔法书仓库出口切区未完成，本轮不搜索室内分区：{}',
                           self.client.title, exc)
            return False
        finally:
            if state is not None and getattr(self.client, '_selenopolis_book_exit', None) is state:
                state['retry_at'] = time.monotonic() + 30

    async def loaded_entities(self):
        # Do not equate completion of teleport() with completion of scene loading.
        previous_count = None
        entities = []
        for sample in range(10):
            if not await self.active():
                return []
            await asyncio.sleep(.25)
            entities = await self.client.get_base_entity_list()
            if sample >= 2 and entities and len(entities) == previous_count:
                break
            previous_count = len(entities)
        return entities

    async def candidates(self, entities):
        result = []
        for entity in entities:
            if not await self.active():
                break
            try:
                template = await entity.object_template()
                if template is None:
                    continue
                internal = (await template.object_name()) or ''
                if internal.casefold() in _SKIP:
                    continue
                code = (await template.display_name()) or ''
                display = ''
                if code:
                    try:
                        display = (await self.client.cache_handler.get_langcode_name(code)) or ''
                    except (ValueError, KeyError):
                        pass
                template_id = await entity.template_id_full()
                if not isinstance(template_id, int) or template_id <= 0:
                    template_id = None
                score = self.names.score(self.goal.target, display, code, internal)
                if template_id is not None and template_id in self.state.verified_templates:
                    score = 110
                if len(self.observed_names) < 100:
                    self.observed_names[(code, internal)] = {'code': code, 'display': display, 'internal': internal, 'score': score}
                if score < 85:
                    continue
                xyz = await entity.location()
                if not all(math.isfinite(v) for v in (xyz.x, xyz.y, xyz.z)):
                    continue
                candidate = Candidate(entity, xyz, code, display, internal, template_id, score)
                if self.state.recent.get(candidate.key, 0) <= time.monotonic():
                    result.append(candidate)
            except Exception as exc:
                # Entity pointers can expire as a region streams out. Cancellation
                # must propagate, but one missing entity must not discard the scan.
                logger.trace(f'Collect entity became unavailable: {exc}')
        position = await self.client.body.position()
        result.sort(key=lambda c: (-c.score, calc_Distance(position, c.xyz)))
        return result

    async def prompt_ready(self, candidate):
        if not await self.active() or not await is_visible_by_path(self.client, npc_range_path):
            return False
        if calc_Distance(await self.client.body.position(), candidate.xyz) >= 750:
            return False
        title = await get_popup_title(self.client)
        self.last_popup = title or ''
        # Never let a different nearby NPC/sigil count as the desired item.
        if not title:
            return False
        if self.names.score(self.goal.target, title) >= 85:
            return True
        return bool(candidate.template_id in self.state.verified_templates
                    and candidate.display and self.names.score(candidate.display, title) >= 85)

    async def move_to(self, candidate):
        if await self.prompt_ready(candidate):
            return

        async def stop_condition():
            while await self.active():
                if not await self.same_goal():
                    return
                if await self.prompt_ready(candidate):
                    return
                await asyncio.sleep(.1)

        movement = asyncio.create_task(collision_tp(self.client, candidate.xyz))
        watcher = asyncio.create_task(stop_condition())
        try:
            async with asyncio.timeout(20):
                done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
        finally:
            movement.cancel()
            watcher.cancel()
            await gather_owned(movement, watcher, return_exceptions=True)

    async def align_collect_prompt(self, candidate):
        """Turn only beside a confirmed entity, never interact with another prompt."""
        async with automation_owner(self.client, 'collect-prompt-align'):
            for step in range(self.PROMPT_ALIGN_MAX_STEPS + 1):
                if (not await self.active() or not await self.same_goal()
                        or not await is_free(self.client)
                        or getattr(self.client, 'refilling_potions', False)
                        or getattr(self.client, 'quest_recovery_owner', None)):
                    return False
                try:
                    if not (calc_Distance(await self.client.body.position(), candidate.xyz) < 750
                            and calc_Distance(await candidate.entity.location(), candidate.xyz) <= 100):
                        return False
                except Exception:
                    return False
                if await self.prompt_ready(candidate):
                    return True
                if step == self.PROMPT_ALIGN_MAX_STEPS:
                    break
                if step == 0:
                    logger.debug('Client {}: 已到达采集实体，但交互框不匹配，按 A 转向寻找 {}。',
                                 self.client.title, self.goal.target)
                await self.client.send_key(Keycode.A, .1)
                await asyncio.sleep(.1)
        logger.debug('Client {}: 转向后仍未确认 {} 交互，跳过此候选，不按 X。',
                     self.client.title, self.goal.target)
        return False

    async def collect(self, candidate):
        if not await self.active() or not await self.same_goal():
            return False
        if not await self.quester.is_position_safe(candidate.xyz):
            return False
        before = await self.snapshot()
        before_text = self.last_text
        if before is None:
            return False
        logger.debug(f'Client {self.client.title}: 采集候选 {candidate.internal} / {candidate.display} '
                     f'→ {before.target} (匹配 {candidate.score})')
        try:
            await self.move_to(candidate)
        except TimeoutError:
            return False
        await asyncio.sleep(.3)
        if not await self.active() or not await self.same_goal():
            return False
        for _ in range(8):
            if await self.prompt_ready(candidate):
                break
            if not await self.active():
                return False
            await asyncio.sleep(.25)
        else:
            if not await self.align_collect_prompt(candidate):
                return False
        before = await self.snapshot()
        before_text = self.last_text
        if before is None or before.key != self.goal.key:
            return False
        # A matching popup is required even when the object name was a strong match.
        for attempt in range(3):
            if not await self.active() or not await self.prompt_ready(candidate):
                break
            try:
                # Freshly spawned objects can expose their name before their
                # interaction is ready; validate the live entity again after settling.
                if calc_Distance(await candidate.entity.location(), candidate.xyz) > 100:
                    break
                await asyncio.sleep(.1)
                if not await self.active() or not await self.same_goal() or not await self.prompt_ready(candidate):
                    break
            except (ValueError, OSError):
                return False
            await self.client.send_key(Keycode.X, .1)
            for _ in range(8):
                await asyncio.sleep(.25)
                if not await self.active():
                    return False
                after = await self.snapshot()
                if count_increased(before, after):
                    if candidate.template_id is not None:
                        self.state.verified_templates.add(candidate.template_id)
                    logger.info(f'Client {self.client.title}: {before.target} 采集进度 '
                                f'{before.current}/{before.total} → {after.current}/{after.total}')
                    return True
                if self.last_text and self.last_text != before_text and (after is None or after.key != before.key):
                    # The last pickup may remove the counter and advance the goal.
                    # Do not learn an item ID from a goal change alone.
                    changed_text = self.last_text
                    await asyncio.sleep(.25)
                    await self.snapshot()
                    if await self.active() and self.last_text == changed_text:
                        logger.info(f'Client {self.client.title}: 采集后任务目标已更新。')
                        return True
            if not await is_free(self.client):
                break
        action = '继续搜索'
        logger.debug(f'Client {self.client.title}: {candidate.internal} 交互后未确认进度，{action}。')
        return False

    async def scan(self, entities):
        for candidate in await self.candidates(entities):
            if not await self.active() or not await self.same_goal():
                return False
            success = await self.collect(candidate)
            self.state.recent[candidate.key] = time.monotonic() + (15 if success else 30)
            if success:
                self.state.anchor = None
                return True
        return False

    async def wait_at_anchor(self):
        """Stay at the verified pickup point; re-read entities after every respawn."""
        while await self.active() and await self.same_goal():
            if await is_free(self.client):
                if await self.scan(await self.client.get_base_entity_list()):
                    return True
            # No entity or no confirmed progress is not permission to roam.
            # active/same_goal are rechecked every two seconds, and cancellation propagates.
            await asyncio.sleep(2)
        return False

    async def run(self):
        if not self.client.questing_status:
            return False
        self.zone = await self.client.zone_name()
        self.quest_id = await self.read_quest_id()
        self.goal = await self.snapshot()
        if not self.goal or (self.goal.total is not None and self.goal.current >= self.goal.total):
            return False
        self.catalog = installed_catalog(self.client)
        if self.catalog is not None:
            self.names = await self.catalog.get()
        if (self.zone == self.SELENOPOLIS_BOOK_STORAGE and self.goal.total == 3
                and normalize_name(self.goal.location) == normalize_name('Marketplace of Ideas')
                and self.names.aliases(self.goal.target).intersection(self.names.aliases('魔法书'))):
            if not await self.leave_selenopolis_book_storage():
                return False
        key = (self.zone, self.quest_id, self.goal.key, self.goal.total)
        rotating_key = (key, self.goal.current)
        rotating_goal = self.goal.target.strip() == '旋转旋转'
        attempt = getattr(self.client, 'quest_rotating_realm_attempted', None)
        if rotating_goal and isinstance(attempt, dict) and attempt.get('key') == rotating_key:
            # A Realm change is the action for this objective. Do not restart
            # the ordinary region search on the next quest-worker iteration.
            if attempt['count'] < 2 and time.monotonic() >= attempt['retry_at']:
                if await self.quester.change_realm_for_rotating(self.client):
                    attempt['count'] += 1
                    attempt['retry_at'] = time.monotonic() + 30
                    if attempt['count'] == 2 and await self.same_goal():
                        logger.warning('自动任务：旋转旋转 Realm 切换已达到两次上限，等待任务状态变化。')
                else:
                    attempt['retry_at'] = time.monotonic() + 5
            return False
        previous = getattr(self.client, '_deimos_collect_search', None)
        self.state = previous if isinstance(previous, SearchState) and previous.key == key else SearchState(key)
        self.client._deimos_collect_search = self.state
        if not await self.active() or not await self.same_goal():
            return False
        self.state.anchor = None
        # Search the currently loaded region first, even if navigation data is absent.
        if await self.scan(await self.client.get_base_entity_list()):
            return True
        if not self.state.route:
            try:
                self.state.route = await self.quester.get_zone_chunks()
                origin = await self.client.body.position()
                self.state.route.sort(key=lambda xyz: calc_Distance(origin, xyz))
            except Exception as exc:
                self.warn(f'地图分区读取失败：{exc}')
                return False
        origin = await self.client.body.position()
        for _ in range(len(self.state.route)):
            if not await self.active() or not await self.same_goal():
                return False
            point = self.state.route[self.state.cursor % len(self.state.route)]
            self.state.cursor = (self.state.cursor + 1) % len(self.state.route)
            # Retain the original underground region-loading approach; each
            # region now uses its own navigation height instead of a global Z=0.
            await self.client.teleport(XYZ(point.x, point.y, point.z - 550))
            if await self.scan(await self.loaded_entities()):
                return True
        if await self.active() and await self.same_goal() and await is_free(self.client):
            await self.client.teleport(origin)
        if not rotating_goal and self.catalog is not None and time.monotonic() - self.state.last_warning >= 30:
            try:
                path = await asyncio.to_thread(self.catalog.report_unresolved, self.zone, self.goal,
                                               list(self.observed_names.values()), self.last_popup)
                logger.debug(f'采集未匹配记录已保存：{path}')
            except OSError as exc:
                logger.debug(f'采集未匹配记录无法保存：{exc}')
        if rotating_goal and len(self.state.route) >= 14 and await self.active() and await self.same_goal():
            attempt = {'key': rotating_key, 'count': 0, 'retry_at': time.monotonic() + 5}
            self.client.quest_rotating_realm_attempted = attempt
            if await self.quester.change_realm_for_rotating(self.client):
                attempt['count'] = 1
                attempt['retry_at'] = time.monotonic() + 30
            return False
        self.warn(f'本轮未找到可确认的采集物：{self.goal.target}；'
                  f'已搜索 {len(self.state.route)} 个分区，将等待刷新后重试。')
        await asyncio.sleep(2)
        return False

    def warn(self, message):
        if time.monotonic() - self.state.last_warning >= 30:
            logger.warning(f'Client {self.client.title}: {message}')
            self.state.last_warning = time.monotonic()


async def collect_one(quester, client):
    return await CollectSearch(quester, client).run()
