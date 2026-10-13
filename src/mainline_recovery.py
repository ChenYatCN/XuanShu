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
from wizwalker import Keycode, Primitive
from wizwalker.errors import MemoryReadError, MemoryInvalidated

from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.ibao_runtime import complete_before_cancel
from src.interaction_prompts import plain_text
from src.mainline_progress import (expected_mainline_rows, log_mainline_progress,
    mainline_consistent, mainline_dungeon_status, match_quest, normalize_name, read_mainline_identity,
    record_mainline_success, title_aliases)
from src.paths import (all_quests_sort_button_path, quest_buttons_parent_path,
    dialog_text_path, decline_quest_path, cancel_multiple_quest_menu_path)
from src.utils import get_window_from_path, is_free, is_visible_by_path
from src.window_text import read_control_text


OWNER = 'mainline-recovery'
FINDER_TITLES = {'任务搜寻', 'quest finder'}
PAGE_READ_ATTEMPTS = 5


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
        logger.opt(exception=True).warning('{} 主线找回 UI 读取失败：阶段={}，路径={}',
                                           client.title, stage, list(path))
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
    pending, nodes = [(root, root_path)], []
    while pending:
        if len(nodes) + len(pending) > 300:
            raise RuntimeError('任务界面层级超过检查上限')
        window, path = pending.pop(0)
        async with ui_read(client, 'visibility', path):
            if not await window.is_visible():
                continue
        nodes.append((window, path))
        async with ui_read(client, 'children', path):
            children = await checked_children(window)
        for child in children:
            async with ui_read(client, 'child-name', path):
                name = await child.name()
                pending.append((child, (*path, name)))
    return nodes


async def page(client):
    nodes = await visible_nodes(client)
    cards, signature = [], []
    for window, path in nodes:
        if not re.fullmatch(r'wndQuestInfo\d+', path[-1]):
            continue
        descendants = [(w, p) for w, p in nodes if p[:len(path)] == path]
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
            async with ui_read(client, 'card-title', path):
                raise RuntimeError(f'任务卡片标题不可读：{list(path)}')
        target = next(((w, p) for _, w, p in values if p[-1] == 'txtGoal'), (window, path))
        cards.append((title, target))
        signature.append(tuple(value for value, _, _ in values if value))
    if not cards:
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
    return tuple(signature), cards, buttons[0] if buttons else None


def transient_ui_read(exc):
    if isinstance(exc, (MemoryReadError, MemoryInvalidated)):
        return True
    if isinstance(exc, ValueError):
        return bool(re.fullmatch(r'Array length must be >= 0, not -\d+', str(exc))
                    or re.fullmatch(r'Size was \d+ and the max was \d+', str(exc)))
    return isinstance(exc, RuntimeError) and str(exc).startswith((
        'Q 任务界面未显示', '当前页没有可读取的任务卡片', '任务卡片标题不可读',
        '控件文本结构无效', '控件文本指针无效'))


async def stable_page(client, zone, phase):
    """Return only the latest of two complete, identical fresh-root scans."""
    previous, failure = None, None
    for attempt in range(1, PAGE_READ_ATTEMPTS + 1):
        try:
            async with ui_read(client, 'page-guard', (phase,)):
                await guard(client, zone)
            current = await page(client)
        except (MemoryReadError, MemoryInvalidated, ValueError, RuntimeError) as exc:
            if not transient_ui_read(exc):
                raise
            previous = None  # No object/result from this scan can be used.
            failure = exc
            stage = getattr(exc, '__notes__', [phase])[-1]
            logger.opt(exception=True).warning(
                '{} 主线找回 {} 读取 {}/{} 失败，丢弃整次扫描：{}；{}',
                client.title, phase, attempt, PAGE_READ_ATTEMPTS, stage, exc)
        else:
            if current[0] == previous:
                logger.debug('{} 主线找回 {} 页面完整且稳定：读取 {}/{}',
                             client.title, phase, attempt, PAGE_READ_ATTEMPTS)
                return current
            previous = current[0]
            current = None  # Retain the signature only, never old Window objects.
            logger.debug('{} 主线找回 {} 读取 {}/{}：等待下一次完整扫描确认稳定',
                         client.title, phase, attempt, PAGE_READ_ATTEMPTS)
        if attempt < PAGE_READ_ATTEMPTS:
            await sleep(.15)
    stage = getattr(failure, '__notes__', [phase])[-1]
    raise RuntimeError(f'{phase} 页面读取 {PAGE_READ_ATTEMPTS}/{PAGE_READ_ATTEMPTS} 次后仍未稳定；'
                       f'最后失败阶段={stage}；原因={failure or "完整扫描内容持续变化"}') from failure


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
    matches = [target for title, target in cards if normalize_name(title) in names]
    if len(matches) > 1:
        raise RuntimeError('任务卡片标题重复，无法唯一选择恢复目标')
    return matches[0] if matches else None


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
                signature, cards, left = await stable_page(client, zone, '点击所有任务后')
                expected = expected_mainline_rows(last)
                selected = card_for_row(cards, expected[0], last)
                if selected is None and len(expected) > 1:
                    selected = card_for_row(cards, expected[1])
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
                        await click(client, finders[0], zone)
                        return 'finder'
                    phase = '左翻页查找任务搜寻'
                    await turn_left(client, signature, left, zone)
                    signature, cards, left = await stable_page(client, zone, '翻页后扫描任务搜寻')
                raise RuntimeError('寻找任务搜寻达到有限翻页上限')
        except Exception as exc:
            logger.opt(exception=True).warning(
                '{} 主线找回任务簿流程中止：阶段={}；原因={}；随后按原流程清理 Q。',
                client.title, phase, exc)
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
    rows = expected_mainline_rows(last)
    row = identity['row'] or match_quest(rows, identity['quest_id'], identity['code'], identity['title'], identity['names'])
    if row is not None and any((row['world'], row['number']) == (r['world'], r['number']) for r in rows):
        return dict(identity, row=row)
    return None


def failed(client, state, reason):
    state.update(phase='cooldown', retry_at=monotonic() + 60, confirmation=None)
    logger.warning('{} 主线找回未确认：{}；本恢复依据已尝试 {}/3 次，限制重试。', client.title, reason, state['attempts'])


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
    # This entry never steals control from potions, combat, dialogue or another owner.
    if await blocked(client):
        if dungeon == 'exited':
            client._xuanshu_mainline_dungeon_exit_observation = None
        state = getattr(client, '_xuanshu_mainline_recovery', None)
        if isinstance(state, dict) and state.get('phase') == 'navigation':
            state['last_poll'] = None
        return False
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
    if isinstance(state, dict) and state.get('phase') == 'navigation':
        now = monotonic()
        if state.get('last_poll') is not None:
            state['elapsed'] += now - state['last_poll']
        state['last_poll'] = now
        expected = expected_identity(last, identity)
        if expected is not None:
            confirmation = (expected['quest_id'], expected['row']['world'], expected['row']['number'])
            if state.get('confirmation') == confirmation:
                record_mainline_success(client, expected)
                client._xuanshu_mainline_recovery = None
                await log_mainline_progress(client)
                logger.info('{} 接取后重新匹配主线成功，恢复正常任务。', client.title)
                return False
            state['confirmation'] = confirmation
            return True  # Confirm the accepted identity twice before new task movement.
        if identity is not None and identity['quest_id'] != state['finder_id']:
            wrong = ('unmatched', identity['quest_id'])
            if state.get('confirmation') == wrong:
                failed(client, state, '接取或切换后的实际任务不是上一条或下一编号主线')
                return True
            state['confirmation'] = wrong
            return True  # Don't start the wrong candidate while its result settles.
        else:
            state['confirmation'] = None
        if state['elapsed'] >= 90:
            failed(client, state, '接取导航未在有限时间内确认恢复')
            return True
        return False  # Crucial: Quest Finder is navigation, not an index-match gate.
    restored = expected_identity(last, identity)
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
    before_target = await client.quest_position.position()
    state.update(phase='book', attempts=state['attempts'] + 1)
    task = asyncio.create_task(select_from_book(client, last))
    client._xuanshu_mainline_book_task = task
    try:
        selected = await task
        if not active(client):
            return False
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
        target = await client.quest_position.position()
        if not active(client):
            cancel_mainline_recovery(client)
            return False
        finder_key = current is not None and current['code'].casefold() == 'quest finder'
        changed = current is not None and current['quest_id'] != identity['quest_id']
        if (not all(math.isfinite(v) for v in target) or math.sqrt(sum(v*v for v in target)) <= 1
                or not (finder_key or changed and math.dist(tuple(target), tuple(before_target)) > 1)):
            raise RuntimeError('点击任务搜寻后没有可确认的导航目标变化')
        state.update(phase='navigation', finder_id=current['quest_id'], elapsed=0,
                     last_poll=monotonic(), confirmation=None)
        logger.info('{} 已选择任务搜寻，交给现有自动任务导航和接取；主线恢复待接取后确认。', client.title)
        return False
    except asyncio.CancelledError:
        client._xuanshu_mainline_recovery = None
        if asyncio.current_task().cancelling():
            raise
        return False  # Disabling recovery cancels only our child UI pass.
    except Exception as exc:
        failed(client, state, str(exc))
        return True
    finally:
        if client._xuanshu_mainline_book_task is task:
            client._xuanshu_mainline_book_task = None
