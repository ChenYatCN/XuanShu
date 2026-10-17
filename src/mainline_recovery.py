"""Quest-book recovery only; navigation and acceptance stay with the quest worker.

QuestLogAllButton, wndQuestInfoN/txtGoal and btnPrevPage are retained from
e9bdd91's quest-book implementation. Paths below the known book root are read
from the live visible hierarchy, rather than guessed from screen coordinates.
"""
import asyncio
import math
import re
from asyncio import sleep
from contextlib import asynccontextmanager
from time import monotonic

from loguru import logger
from src.logging_utils import log_exception
from wizwalker import Keycode, Primitive
from wizwalker.errors import MemoryReadError, MemoryInvalidated

from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.ibao_runtime import complete_before_cancel
from src.interaction_prompts import plain_text
from src.mainline_progress import (expected_mainline_rows, log_mainline_progress,
    log_mainline_match, mainline_consistent, mainline_dungeon_status, match_quest, normalize_name, read_mainline_identity,
    record_mainline_success, title_aliases)
from src.paths import (all_quests_sort_button_path, quest_buttons_parent_path,
    dialog_text_path, decline_quest_path, cancel_multiple_quest_menu_path)
from src.utils import get_window_from_path, is_free, is_visible_by_path
from src.window_text import read_control_text


OWNER = 'mainline-recovery'
FINDER_TITLES = {'任务搜寻', 'quest finder'}
PAGE_READ_ATTEMPTS = 5
FINDER_CLICK_TIMEOUT = 3.0  # Existing book-open wait: 30 reads at .1s.
FINDER_CONFIRM_TIMEOUT = 90.0  # Retain the existing Finder recovery budget.
FINDER_POLL_INTERVAL = .15  # Existing stable-page sampling interval.
FINDER_STABLE_SECONDS = 1.0  # Existing recovery/transition debounce.


def active(client):
    return (getattr(client, 'mainline_finder_enabled', False)
            and getattr(client, 'questing_status', False)
            and getattr(client, '_mainline_recovery_quester', False))


def cancel_mainline_recovery(client):
    """Cancel our UI pass only; never cancel the ordinary quest worker or its movement."""
    if getattr(client, '_xuanshu_mainline_recovery', None):
        client._xuanshu_mainline_progress = f'{client.title} · 主线找回已停止，当前待确认'
    client._xuanshu_mainline_recovery = None
    task = getattr(client, '_xuanshu_mainline_book_task', None)
    if task is not None and not task.done():
        task.cancel()


async def blocked(client, *, own_book=False):
    if (getattr(client, 'refilling_potions', False)
            or getattr(client, 'entity_detect_combat_status', False)
            or getattr(client, 'post_combat_movement_active', False)
            or getattr(client, 'quest_party_probe_pending', False)
            or getattr(client, 'quest_party_battle_rescue_active', False)
            or getattr(client, 'quest_party_quest_worker_restart_requested', False)):
        return True
    owner = getattr(client, 'quest_recovery_owner', None)
    if owner and not (own_book and owner == OWNER):
        return True
    if not await is_free(client) or await is_visible_by_path(client, dialog_text_path):
        return True
    if (await is_visible_by_path(client, decline_quest_path)
            or await is_visible_by_path(client, cancel_multiple_quest_menu_path)):
        return True
    if not own_book and (get_client_automation_ownership(client).locked
                         or await is_visible_by_path(client, quest_buttons_parent_path)):
        return True
    return False


async def guard(client, zone):
    if not active(client):
        raise asyncio.CancelledError
    if mainline_dungeon_status(client) == 'inside':
        raise RuntimeError('已有地牢任务会话，停止主线找回界面操作')
    if await client.zone_name() != zone or await blocked(client, own_book=True):
        raise RuntimeError('找回期间区域或控制状态变化，停止任务界面操作')


async def text(window):
    return plain_text(await read_control_text(window) or '').strip()


@asynccontextmanager
async def ui_read(client, stage, path):
    """Annotate the real failing await; logging never replaces its result."""
    logger.debug('{} 主线找回 UI 读取阶段={}，路径={}', client.title, stage, list(path))
    try:
        yield
    except Exception as exc:
        exc.add_note(f'主线找回 UI 阶段={stage}，路径={list(path)}')
        exc._xuanshu_log_stage = stage
        exc._xuanshu_ui_read_failed = stage in (
            'root-window', 'visibility', 'children', 'child-name',
            'card-control-type', 'card-text', 'card-title', 'card-visibility',
            'page-cards', 'page-counter-type', 'page-counter')
        log_exception(client, f'主线找回 UI 读取失败：阶段={stage}，路径={list(path)}', exc, level='DEBUG')
        raise


async def checked_children(window):
    """Detect children()'s suppressed vector errors without changing the library."""
    header = getattr(window, 'read_value_from_offset', None)
    if not callable(header):
        return await window.children()
    # Window.children uses the shared-vector header at 112/120 (16-byte slots).
    before = (await header(112, Primitive.int64), await header(120, Primitive.int64))
    size = before[1] - before[0]
    if size < 0 or size % 16 or size // 16 > 1000:
        raise MemoryReadError(f'children vector header invalid: start={before[0]}, end={before[1]}')
    children = await window.children()
    after = (await header(112, Primitive.int64), await header(120, Primitive.int64))
    if before != after or len(children) != size // 16:
        raise MemoryReadError(f'children vector changed or incomplete: '
                              f'before={before}, after={after}, read={len(children)}, expected={size // 16}')
    return children


async def visible_nodes(client):
    root_path = tuple(quest_buttons_parent_path[:-1])
    async with ui_read(client, 'root-window', root_path):
        root = await get_window_from_path(client.root_window, list(root_path))
        if not root or not await root.is_visible():
            raise RuntimeError('Q 任务界面未显示')
    pending, nodes, inactive = [(root, root_path)], [], []
    while pending:
        if len(nodes) + len(pending) > 300:
            raise RuntimeError('任务界面层级超过检查上限')
        window, path = pending.pop(0)
        async with ui_read(client, 'visibility', path):
            if not await window.is_visible():
                if any(re.fullmatch(r'wndQuestInfo\d+', name) for name in path):
                    inactive.append((window, path))
                continue
        nodes.append((window, path))
        async with ui_read(client, 'children', path):
            children = await checked_children(window)
        for child in children:
            async with ui_read(client, 'child-name', path):
                name = await child.name()
                pending.append((child, (*path, name)))
    return nodes, inactive


async def card_field(client, window, path):
    name = path[-1].casefold()
    if not (name.startswith('txt') or any(word in name for word in ('title', 'goal', 'objective', 'reward'))
            or 'quest' in name and name not in ('questinfowindow', 'wndquestinfo')
            and not re.fullmatch(r'wndquestinfo\d+', name)):
        return False
    kind = getattr(window, 'read_type_name', None)
    if callable(kind) and not name.startswith('txt'):
        async with ui_read(client, 'card-control-type', path):
            return await kind() != 'Window'  # Layout panels are not quest-specific data.
    return True


async def hidden_card_fields(client, roots):
    """Hidden content is incomplete, not empty; empty layout shells carry no quest fields."""
    pending, fields, count = list(roots), [], 0
    while pending:
        window, path = pending.pop(0)
        count += 1
        if count + len(pending) > 300:
            raise RuntimeError('任务界面层级超过检查上限')
        if await card_field(client, window, path):
            fields.append(path)
        async with ui_read(client, 'children', path):
            children = await checked_children(window)
        for child in children:
            async with ui_read(client, 'child-name', path):
                pending.append((child, (*path, await child.name())))
    return fields


async def page(client, *, include_system=False):
    nodes, inactive = await visible_nodes(client)
    cards, signature, slots = [], [], 0
    for window, path in nodes:
        if not re.fullmatch(r'wndQuestInfo\d+', path[-1]):
            continue
        slots += 1
        descendants = [(w, p) for w, p in nodes if p[:len(path)] == path]
        hidden_content = [(w, p) for w, p in inactive if p[:len(path)] == path]
        async with ui_read(client, 'card-visibility', path):
            if not await window.is_visible():
                raise RuntimeError(f'任务卡片可见状态变化：{list(path)}')
            for w, p in hidden_content:
                if await w.is_visible():
                    raise RuntimeError(f'任务卡片可见状态变化：{list(p)}')
        visible_fields = [p for w, p in descendants if await card_field(client, w, p)]
        values = []
        for w, p in descendants:
            # Container windows do not have a text layout. Read the actual
            # title/goal fields, not an arbitrary string from their storage.
            if not (p[-1].casefold().startswith('txt') or 'title' in p[-1].casefold()):
                continue
            kind = getattr(w, 'read_type_name', None)
            if callable(kind) and not p[-1].casefold().startswith('txt'):
                async with ui_read(client, 'card-control-type', p):
                    if await kind() not in ('ControlText', 'ControlList', 'ControlFreeChat'):
                        continue
            async with ui_read(client, 'card-text', p):
                values.append((await text(w), w, p))
        title = next((value for value, _, p in values if value and p[-1].casefold()
                      in ('txtquesttitle', 'txtquestname', 'txtname')), '')
        if not title:
            title = next((value for value, _, p in values if value and 'title' in p[-1].casefold()), '')
        if not title:
            hidden_fields = await hidden_card_fields(client, hidden_content)
            if not visible_fields and not hidden_fields:
                async with ui_read(client, 'card-visibility', path):
                    if not await window.is_visible():
                        raise RuntimeError(f'任务卡片可见状态变化：{list(path)}')
                    for w, p in hidden_content:
                        if await w.is_visible():
                            raise RuntimeError(f'任务卡片可见状态变化：{list(p)}')
                logger.debug('{} 主线找回跳过空任务槽：{}', client.title, list(path))
                continue  # Empty slots never contribute to the stability signature.
            async with ui_read(client, 'card-title', path):
                title_fields = [(p, value) for value, _, p in values if
                    p[-1].casefold() in ('txtquesttitle', 'txtquestname', 'txtname')
                    or 'title' in p[-1].casefold()]
                reason = '可见标题字段为空' if title_fields else '未发现可见标题字段，卡片尚未加载或结构不完整'
                raise RuntimeError(f'任务卡片标题不可读：{list(path)}；{reason}；'
                                   f'可见字段={[(list(p), value) for value, _, p in values]}；'
                                   f'隐藏任务字段={[list(p) for p in hidden_fields]}')
        async with ui_read(client, 'card-visibility', path):
            if not await window.is_visible():
                raise RuntimeError(f'任务卡片可见状态变化：{list(path)}')
        target = next(((w, p) for _, w, p in values if p[-1] == 'txtGoal'), (window, path))
        if title.casefold() in FINDER_TITLES:
            if include_system:
                cards.append((title, target))  # Finder remains available only to book navigation.
            continue
        if not (any(value for value, _, p in values if p[-1].casefold()
                not in ('txtquesttitle', 'txtquestname', 'txtname') and 'title' not in p[-1].casefold())
                or any(not p[-1].casefold().startswith('txt') and
                    any(word in p[-1].casefold() for word in ('goal', 'objective', 'reward'))
                    for p in visible_fields)):
            async with ui_read(client, 'card-title', path):
                raise RuntimeError(f'任务卡片内容未完整：{list(path)}；标题={title}')
        cards.append((title, target))
        signature.append(tuple(value for value, _, _ in values if value))
    if not slots:
        async with ui_read(client, 'page-cards', ()):
            raise RuntimeError('当前页没有可读取的任务卡片')
    # Page counters are optional: actual card contents alone can prove a page change.
    for window, path in nodes:
        if 'page' in path[-1].casefold() and not path[-1].startswith('btn'):
            kind = getattr(window, 'read_type_name', None)
            if callable(kind) and not path[-1].casefold().startswith('txt'):
                async with ui_read(client, 'page-counter-type', path):
                    if await kind() not in ('ControlText', 'ControlList', 'ControlFreeChat'):
                        continue
            async with ui_read(client, 'page-counter', path):
                value = await text(window)
            if any(c.isdigit() for c in value):
                signature.append((path[-1], value))
    buttons = [(w, p) for w, p in nodes if p[-1] == 'btnPrevPage']
    if len(buttons) > 1:
        raise RuntimeError(f'左翻页控件不唯一：{[list(p) for _, p in buttons]}')
    signature.append(('previous-page-control', bool(buttons)))
    return tuple(signature), cards, buttons[0] if buttons else None


def transient_ui_read(exc):
    if isinstance(exc, (MemoryReadError, MemoryInvalidated)):
        return True
    if isinstance(exc, ValueError):
        return bool(re.fullmatch(r'Array length must be >= 0, not -\d+', str(exc))
                    or re.fullmatch(r'Size was \d+ and the max was \d+', str(exc)))
    return isinstance(exc, RuntimeError) and str(exc).startswith((
        'Q 任务界面未显示', '当前页没有可读取的任务卡片', '任务卡片标题不可读',
        '控件文本结构无效', '控件文本指针无效', '任务卡片可见状态变化', '任务卡片内容未完整'))


async def stable_page(client, zone, phase, *, include_system=False):
    """Return only the latest of two complete, identical fresh-root scans."""
    previous, failure = None, None
    for attempt in range(1, PAGE_READ_ATTEMPTS + 1):
        try:
            async with ui_read(client, 'page-guard', (phase,)):
                await guard(client, zone)
            current = await page(client, include_system=include_system)
        except (MemoryReadError, MemoryInvalidated, ValueError, RuntimeError) as exc:
            if not transient_ui_read(exc):
                raise
            previous = None  # No object/result from this scan can be used.
            failure = exc
            stage = getattr(exc, '__notes__', [phase])[-1]
            log_exception(client,
                f'主线找回 {phase} 读取 {attempt}/{PAGE_READ_ATTEMPTS} 失败，丢弃整次扫描：{stage}',
                exc, level='DEBUG')
        else:
            if current[0] == previous:
                logger.debug('{} 主线找回 {} 页面完整且稳定：读取 {}/{}',
                             client.title, phase, attempt, PAGE_READ_ATTEMPTS)
                client._xuanshu_mainline_book_read_error = ''
                return current
            previous = current[0]
            current = None  # Retain the signature only, never old Window objects.
            logger.debug('{} 主线找回 {} 读取 {}/{}：等待下一次完整扫描确认稳定',
                         client.title, phase, attempt, PAGE_READ_ATTEMPTS)
        if attempt < PAGE_READ_ATTEMPTS:
            await sleep(.15)
    stage = getattr(failure, '__notes__', [phase])[-1]
    exc = RuntimeError(f'{phase} 页面读取 {PAGE_READ_ATTEMPTS}/{PAGE_READ_ATTEMPTS} 次后仍未稳定；'
                       f'最后失败阶段={stage}；原因={failure or "完整扫描内容持续变化"}')
    exc._xuanshu_log_stage = getattr(failure, '_xuanshu_log_stage', 'page-stability')
    exc._xuanshu_ui_read_failed = True
    raise exc from failure


async def click(client, target, zone, *, use_post=False):
    await guard(client, zone)
    _, path = target
    async with client.mouse_handler:
        await guard(client, zone)
        async with ui_read(client, 'click-window', path):
            window = await get_window_from_path(client.root_window, list(path))
            if not window or not await window.is_visible():
                raise RuntimeError(f'任务控件已失效：{list(path)}')
            if path[-1].startswith(('btn', 'QuestLog')) and await window.is_control_grayed():
                raise RuntimeError(f'任务控件不可用：{list(path)}')
            await complete_before_cancel(asyncio.create_task(
                client.mouse_handler.click_window(window, use_post=use_post)))


async def key(client, code):
    await complete_before_cancel(asyncio.create_task(client.send_key(code, .1)))


async def close_book(client, zone):
    # Only close the book we opened. Never send Q into a new room/dialogue/battle.
    for _ in range(2):
        if not await is_visible_by_path(client, quest_buttons_parent_path):
            return
        if await client.zone_name() != zone or await blocked(client, own_book=True):
            return
        await key(client, Keycode.Q)
        for _ in range(10):
            if not await is_visible_by_path(client, quest_buttons_parent_path):
                return
            await sleep(.1)
    raise RuntimeError('找回任务界面未确认关闭；停止本轮恢复')


async def turn_left(client, signature, button, zone):
    if button is None:
        raise RuntimeError('可见窗口层级中未找到 btnPrevPage 左翻页按钮')
    for attempt in range(3):
        logger.debug('{} 左翻页控件路径：{}，尝试 {}', client.title, list(button[1]), attempt + 1)
        await click(client, button, zone, use_post=attempt > 0)
        for _ in range(10):
            await sleep(.1)
            await guard(client, zone)
            current, _, fresh_button = await stable_page(client, zone, f'左翻页后（点击尝试 {attempt + 1}）')
            if current != signature:
                return
            if fresh_button is not None:
                button = fresh_button
    raise RuntimeError(f'左翻页点击后页码和卡片内容均未变化，可能被上层 UI 遮挡：{list(button[1])}')


def card_for_row(cards, row, last=None):
    names = title_aliases(row['english']) | {normalize_name(t) for t in row.get('chinese', []) + row.get('aliases', [])}
    if last is not None:
        names.add(normalize_name(last['title']))
    matches = [target for title, target in cards if title.casefold() not in FINDER_TITLES
               and normalize_name(title) in names]
    if len(matches) > 1:
        raise RuntimeError('任务卡片标题重复，无法唯一选择恢复目标')
    return matches[0] if matches else None


async def finder_task_state(client):
    """Fresh QuestData around two position reads; never return a torn frame."""
    try:
        zone = await client.zone_name()
        first = await read_mainline_identity(client)
        target = tuple(await client.quest_position.position())
        current = await read_mainline_identity(client)
        latest = tuple(await client.quest_position.position())
        if (first is None or current is None or await client.zone_name() != zone
                or await client.quest_id() != current['quest_id']
                or await client.goal_id() != current['goal_id']
                or type(current['quest_id']) is not int or current['quest_id'] <= 0
                or type(current['goal_id']) is not int or current['goal_id'] <= 0
                or not all(math.isfinite(v) for v in (*target, *latest))
                or math.sqrt(sum(v*v for v in latest)) <= 1 or math.dist(target, latest) > 1
                or any(first[key] != current[key] for key in ('quest_id', 'goal_id', 'code', 'title'))):
            return None
        return current, (current['quest_id'], current['goal_id'], zone, latest, current['code'], current['title'])
    except (MemoryReadError, MemoryInvalidated, ValueError):
        return None


async def finder_ui_state(client, card_path):
    """Read scalar evidence from a fresh root; retain no Window across the click."""
    root = client.root_window
    async def fresh_path(path):
        window = root
        for name in path:
            found = None
            for child in await checked_children(window):
                if await child.name() == name:
                    found = child
                    break
            if found is None:
                return None
            window = found
        return window
    deck = await fresh_path(quest_buttons_parent_path[:-1])
    book = await fresh_path(quest_buttons_parent_path)
    if not deck or not book or not await deck.is_visible() or not await book.is_visible():
        return ('book-closed',)
    card = await fresh_path(card_path)
    if not card or not await card.is_visible():
        return ('finder-card-gone',)
    addresses = []
    for window in (deck, book, card):
        read_address = getattr(window, 'read_base_address', None)
        addresses.append(await read_address() if callable(read_address) else None)
    pending, fields = [(card, ())], []
    while pending:
        if len(fields) + len(pending) > 300:
            raise RuntimeError('任务界面层级超过检查上限')
        window, path = pending.pop(0)
        visible = await window.is_visible()
        value = await text(window) if visible and path and path[-1].casefold().startswith('txt') else ''
        fields.append((path, visible, value))
        if visible:
            for child in await checked_children(window):
                pending.append((child, (*path, await child.name())))
    return ('open', tuple(addresses), tuple(fields))


async def confirm_finder_click(client, card_path, before_ui, before_task):
    started, previous = monotonic(), None
    while monotonic() - started < FINDER_CLICK_TIMEOUT:
        if not active(client):
            raise asyncio.CancelledError
        try:
            ui = await finder_ui_state(client, card_path)
            observed = ('ui', ui) if ui != before_ui else None
            if observed is None:
                task = await finder_task_state(client)
                if before_task is not None and task is not None and task[1] != before_task[1]:
                    observed = ('questdata', task[1])
            if observed is not None and observed == previous:
                if not active(client):
                    raise asyncio.CancelledError
                logger.debug('[mainline_recovery] client={} phase=finder-confirm state=finder-click-accepted elapsed={:.2f}',
                             client.title, monotonic()-started)
                return dict(before=before_task[1] if before_task else None, accepted_at=monotonic())
            previous = observed
        except (MemoryReadError, MemoryInvalidated, ValueError, RuntimeError) as exc:
            if not transient_ui_read(exc):
                raise
            previous = None  # A stale pointer is transient, not click acceptance.
        await sleep(FINDER_POLL_INTERVAL)
    logger.debug('[mainline_recovery] client={} phase=finder-confirm state=finder-click-not-accepted', client.title)
    raise RuntimeError('finder-click-not-accepted：任务搜寻点击后未观察到可靠状态变化')


async def select_from_book(client, last):
    zone = await client.zone_name()
    async with automation_owner(client, OWNER):
        await guard(client, zone)
        client.quest_recovery_owner = OWNER
        opened = False
        phase = '打开 Q'
        try:
            async with asyncio.timeout(25):
                opened = True
                await key(client, Keycode.Q)
                for _ in range(30):
                    await guard(client, zone)
                    if (await is_visible_by_path(client, quest_buttons_parent_path)
                            and await is_visible_by_path(client, all_quests_sort_button_path)):
                        break
                    await sleep(.1)
                else:
                    raise RuntimeError('Q 任务界面和所有任务按钮未稳定打开')
                phase = '点击所有任务'
                await click(client, (None, tuple(all_quests_sort_button_path)), zone)
                await sleep(.3)
                phase = '扫描当前页'
                signature, cards, left = await stable_page(client, zone, '点击所有任务后', include_system=True)
                expected = expected_mainline_rows(last, await read_mainline_identity(client))
                card_diagnostic = (tuple((title, target[1]) for title, target in cards),
                                   tuple((r['world'], r['number']) for r in expected))
                if getattr(client, '_xuanshu_mainline_card_match_log_state', None) != card_diagnostic:
                    for title, target in cards:
                        system = title.casefold() in FINDER_TITLES
                        matches = [r for i, r in enumerate(expected) if not system and
                                   card_for_row([(title, target)], r, last if i == 0 else None) is not None]
                        log_mainline_match(client, dict(title=title, row=matches[0] if matches else None,
                            match_method='card-title'), last, bool(matches),
                            'quest-finder-system-card' if system else 'expected-card-title-matched' if matches
                            else 'card-title-not-expected', source='quest-book', card=list(target[1]), expected=expected)
                    client._xuanshu_mainline_card_match_log_state = card_diagnostic
                selected = None
                for i, row in enumerate(expected):
                    selected = card_for_row(cards, row, last if i == 0 else None)
                    if selected is not None:
                        break
                if selected is not None:
                    phase = '选择主线追踪'
                    await click(client, selected, zone)
                    return 'restore'
                seen = set()
                for _ in range(32):
                    await guard(client, zone)
                    if signature in seen:
                        raise RuntimeError('左翻页已回到检查过的任务页，未找到任务搜寻')
                    seen.add(signature)
                    finders = [target for title, target in cards if title.casefold() in FINDER_TITLES]
                    if len(finders) > 1:
                        raise RuntimeError('任务搜寻卡片不唯一')
                    if finders:
                        phase = '选择任务搜寻'
                        card_path = finders[0][1]
                        card_path = card_path[:next(i+1 for i, name in enumerate(card_path)
                                                   if re.fullmatch(r'wndQuestInfo\d+', name))]
                        before_ui = await finder_ui_state(client, card_path)
                        before_task = await finder_task_state(client)
                        logger.debug('[mainline_recovery] client={} state=finder-card-found path={}', client.title, list(card_path))
                        await click(client, finders[0], zone)
                        logger.debug('[mainline_recovery] client={} state=finder-click-sent', client.title)
                        confirmation = await confirm_finder_click(client, card_path, before_ui, before_task)
                        state = getattr(client, '_xuanshu_mainline_recovery', None)
                        if isinstance(state, dict):
                            state['finder_click'] = confirmation
                        return 'finder'
                    phase = '左翻页查找任务搜寻'
                    await turn_left(client, signature, left, zone)
                    signature, cards, left = await stable_page(client, zone, '翻页后扫描任务搜寻', include_system=True)
                raise RuntimeError('寻找任务搜寻达到有限翻页上限')
        except Exception as exc:
            exc._xuanshu_log_stage = getattr(exc, '_xuanshu_log_stage', phase)
            log_exception(client, f'主线找回任务簿流程中止：阶段={phase}；按原流程清理 Q', exc, level='DEBUG')
            raise
        finally:
            try:
                if opened:
                    await complete_before_cancel(asyncio.create_task(close_book(client, zone)))
            finally:
                if getattr(client, 'quest_recovery_owner', None) == OWNER:
                    client.quest_recovery_owner = None


def expected_identity(last, identity):
    if identity is None:
        return None
    rows = expected_mainline_rows(last, identity)
    row = identity['row'] or match_quest(rows, identity['quest_id'], identity['code'], identity['title'], identity['names'])
    if (row is not None and mainline_consistent(dict(identity, row=row), last)
            and any((row['world'], row['number']) == (r['world'], r['number']) for r in rows)):
        return dict(identity, row=row)
    return None


def failed(client, state, reason, *, exc=None):
    stage = getattr(exc, '_xuanshu_log_stage', state.get('phase', '未知阶段'))
    client._xuanshu_mainline_book_read_error = reason if getattr(exc, '_xuanshu_ui_read_failed', False) else ''
    state.update(phase='cooldown', retry_at=monotonic() + 60, confirmation=None)
    message = f"主线找回未确认：阶段={stage}；尝试 {state['attempts']}/3 次；原因"
    level = 'WARNING' if state['attempts'] >= 3 else 'DEBUG'
    if exc is not None:
        log_exception(client, message, exc, level=level)
    else:
        logger.bind(client=client.title).log(level, '{}：{}', message, reason)


def finder_status(client, state, event, status, sample=None):
    snapshot = sample[1] if sample else None
    marker = (event, status, snapshot)
    if state.get('finder_log') == marker:
        return
    state['finder_log'] = marker
    before = state.get('finder_click', {}).get('before')
    elapsed = monotonic() - state.get('finder_click', {}).get('accepted_at', monotonic())
    logger.debug('[mainline_recovery] client={} phase=finder-confirm event={} state={} elapsed={:.2f} '
                 'quest_id_before={} quest_id_now={} goal_id_before={} goal_id_now={} target_changed={} match_state={}',
                 client.title, event, status, elapsed, before[0] if before else None,
                 snapshot[0] if snapshot else None, before[1] if before else None,
                 snapshot[1] if snapshot else None, bool(before and snapshot and before[3] != snapshot[3]), status)


async def confirm_finder_mainline(client, state, last):
    """Pending Finder results never release ordinary quest movement."""
    now = monotonic()
    if state['phase'] == 'finder-confirm' and now >= state['finder_deadline']:
        finder_status(client, state, 'finder-confirm-timeout', 'timeout-no-result')
        failed(client, state, 'timeout-no-result：任务搜寻未在有限时间内确认期望主线')
        return True
    owner = get_client_automation_ownership(client)
    async def unsafe():
        return (await client.is_loading() or await client.in_battle()
                or await blocked(client, own_book=True)
                or owner.locked and owner._owner_task is not asyncio.current_task()
                or await is_visible_by_path(client, quest_buttons_parent_path))
    try:
        sample = None if await unsafe() else await finder_task_state(client)
        if sample is not None and await unsafe():
            sample = None
    except (MemoryReadError, MemoryInvalidated, ValueError) as exc:
        if not transient_ui_read(exc):
            raise
        sample = None
    if not active(client) or getattr(client, '_xuanshu_mainline_recovery', None) is not state:
        cancel_mainline_recovery(client)
        return False
    now = monotonic()
    if state['phase'] == 'finder-confirm' and now >= state['finder_deadline']:
        finder_status(client, state, 'finder-confirm-timeout', 'timeout-no-result')
        failed(client, state, 'timeout-no-result：任务搜寻未在有限时间内确认期望主线')
        return True
    if sample is None:
        state['confirmation'] = None
        finder_status(client, state, 'finder-confirm-pending', 'transient-task')
        return True
    current, snapshot = sample
    if state.get('finder_last_task') != snapshot:
        state['finder_last_task'] = snapshot
        if snapshot != state.get('finder_click', {}).get('before'):
            finder_status(client, state, 'finder-questdata-changed', 'click-accepted-pending', sample)
    expected = expected_identity(last, current)
    confirmation = (snapshot, (expected['row']['world'], expected['row']['number']) if expected else None)
    if state.get('confirmation') != confirmation:
        state.update(confirmation=confirmation, confirmation_since=now)
    elif now-state['confirmation_since'] >= FINDER_STABLE_SECONDS:
        if expected is not None:
            finder_status(client, state, 'finder-mainline-matched', 'matched-mainline', sample)
            record_mainline_success(client, expected)
            client._xuanshu_mainline_recovery = None
            await log_mainline_progress(client)
            return False
        before = state.get('finder_click', {}).get('before')
        if current['row'] is not None and before is not None and snapshot != before:
            finder_status(client, state, 'finder-stable-non-target', 'stable-non-target', sample)
            failed(client, state, '任务搜寻后的稳定任务不是期望主线')
            return True
    finder_status(client, state, 'finder-confirm-pending',
                  'click-accepted-pending' if snapshot == state.get('finder_click', {}).get('before') else 'transient-task', sample)
    return True


async def maybe_recover_mainline(client):
    """True pauses this iteration; False hands control to the unchanged quest flow."""
    if not active(client):
        cancel_mainline_recovery(client)
        return False
    dungeon = mainline_dungeon_status(client)
    if dungeon == 'inside':
        cancel_mainline_recovery(client)
        client._xuanshu_mainline_dungeon_exit_observation = None
        client._xuanshu_mainline_progress = f'{client.title} · 地牢任务进行中'
        return False  # The existing dungeon task keeps control; no recovery Q.
    state = getattr(client, '_xuanshu_mainline_recovery', None)
    if isinstance(state, dict) and state.get('phase') in ('finder-confirm', 'navigation'):
        if state['phase'] == 'navigation':  # Retire an in-memory old Finder handoff safely.
            state.update(phase='finder-confirm', finder_confirmation=True,
                         finder_click=dict(before=None, accepted_at=monotonic()),
                         finder_deadline=monotonic()+FINDER_CONFIRM_TIMEOUT, confirmation=None)
        try:
            return await confirm_finder_mainline(client, state, client._xuanshu_mainline_last_success)
        except asyncio.CancelledError:
            cancel_mainline_recovery(client)
            raise
    # This entry never steals control from potions, combat, dialogue or another owner.
    if await blocked(client):
        if dungeon == 'exited':
            client._xuanshu_mainline_dungeon_exit_observation = None
        state = getattr(client, '_xuanshu_mainline_recovery', None)
        if isinstance(state, dict) and state.get('phase') == 'navigation':
            state['last_poll'] = None
        return bool(isinstance(state, dict) and state.get('finder_confirmation'))
    last = getattr(client, '_xuanshu_mainline_last_success', None)
    if last is None:
        if not getattr(client, '_mainline_missing_record_logged', False):
            logger.warning('{} 主线找回缺少最后成功记录，不猜测编号和任务标题。', client.title)
            client._mainline_missing_record_logged = True
        return False
    identity = await read_mainline_identity(client)
    if dungeon == 'exited':
        if identity is None or await client.is_loading():
            client._xuanshu_mainline_dungeon_exit_observation = None
            return True
        observed = (identity['quest_id'], identity['goal_id'], identity['code'], await client.zone_name())
        exit_session = getattr(client, '_xuanshu_mainline_dungeon_session', None)
        previous = getattr(client, '_xuanshu_mainline_dungeon_exit_observation', None)
        if (not previous or previous.get('session') is not exit_session
                or previous.get('observed') != observed):
            client._xuanshu_mainline_dungeon_exit_observation = dict(
                session=exit_session, observed=observed, since=monotonic())
            return True
        if monotonic() - previous['since'] < 1:
            return True
        settled_session = getattr(client, '_xuanshu_mainline_dungeon_session', None)
        client._xuanshu_mainline_dungeon_session = None
        client._xuanshu_mainline_dungeon_exit_observation = dict(settled_session=settled_session)
    state = getattr(client, '_xuanshu_mainline_recovery', None)
    restored = expected_identity(last, identity)
    if restored is not None and isinstance(state, dict) and state.get('finder_confirmation'):
        try:
            return await confirm_finder_mainline(client, state, last)
        except asyncio.CancelledError:
            cancel_mainline_recovery(client)
            raise
    if restored is not None or (state is None and identity is not None and mainline_consistent(identity, last)):
        record_mainline_success(client, restored or identity)
        client._xuanshu_mainline_recovery = None
        return False
    if identity is None:
        return True  # A transient failure never changes the last-success record.
    anchor = (last['world'], last['number'], last['quest_id'])
    if not isinstance(state, dict) or state.get('anchor') != anchor:
        state = dict(anchor=anchor, phase='observe', attempts=0, observed=None)
        client._xuanshu_mainline_recovery = state
    if state['phase'] == 'cooldown':
        if state['attempts'] >= 3 or monotonic() < state['retry_at']:
            return True
    observed = (identity['quest_id'], identity['goal_id'], await client.zone_name())
    if state.get('observed') != observed:
        state.update(observed=observed, since=monotonic())
        return True
    if monotonic() - state['since'] < 1:
        return True
    state.update(phase='book', attempts=state['attempts'] + 1)
    task = asyncio.create_task(select_from_book(client, last))
    client._xuanshu_mainline_book_task = task
    try:
        selected = await task
        if not active(client):
            return False
        if selected == 'finder':
            state.update(phase='finder-confirm', finder_confirmation=True, confirmation=None,
                         finder_deadline=state['finder_click']['accepted_at']+FINDER_CONFIRM_TIMEOUT)
            return await confirm_finder_mainline(client, state, last)
        if await is_visible_by_path(client, quest_buttons_parent_path):
            raise RuntimeError('任务界面仍打开，不能交给自动任务移动')
        current = await read_mainline_identity(client)
        if not active(client):
            cancel_mainline_recovery(client)
            return False
        if await client.zone_name() != observed[2]:
            raise RuntimeError('选择任务后区域已变化，停止旧恢复确认')
        if selected == 'restore':
            expected = expected_identity(last, current)
            if expected is None:
                raise RuntimeError('已接卡片选择后未确认主线追踪，停止本轮恢复')
            record_mainline_success(client, expected)
            client._xuanshu_mainline_recovery = None
            await log_mainline_progress(client)
            logger.info('{} 已恢复已接主线追踪：{}', client.title, expected['title'])
            return False
    except asyncio.CancelledError:
        client._xuanshu_mainline_recovery = None
        if asyncio.current_task().cancelling():
            raise
        return False  # Disabling recovery cancels only our child UI pass.
    except Exception as exc:
        failed(client, state, str(exc), exc=exc)
        return True
    finally:
        if client._xuanshu_mainline_book_task is task:
            client._xuanshu_mainline_book_task = None
