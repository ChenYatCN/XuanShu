"""World-local mainline progress from tracked quest identity, never HUD goals."""
import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path

from loguru import logger
from src.collect_matching import normalize_name


@lru_cache(maxsize=1)
def quest_rows():
    try:
        path = Path(__file__).with_name('data') / 'mainline_quests.json'
        return json.loads(path.read_text(encoding='utf-8'))['rows']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning('主线任务索引无法读取，将仅输出未匹配任务：{}', exc)
        return []


def title_aliases(title):
    values = [title, re.split(r'\s*\(', title, maxsplit=1)[0]]
    values.extend(re.findall(r'(?:formerly|previously|old name)\s*:?\s*["“]?([^\)"”]+)', title, re.I))
    return {normalize_name(value) for value in values if value}


def _unique(rows):
    return rows[0] if len(rows) == 1 else None


def match_quest(rows, quest_id, code, title, names=None):
    """ID > language key > unambiguous bilingual title/old-name alias."""
    code = code.casefold() if isinstance(code, str) else ''
    by_id = [row for row in rows if quest_id in row.get('quest_ids', ())]
    if by_id:
        return _unique(by_id)
    by_key = [row for row in rows if code and code in (
        key.casefold() for key in row.get('keys', ()))]
    if by_key:
        return _unique(by_key)
    aliases = {normalize_name(title)} - {''}
    if names:
        aliases |= names.by_id.get(code, set())
        aliases |= names.aliases(title)
    if not aliases:
        return None
    matches = [row for row in rows if aliases.intersection(
        title_aliases(row.get('english', ''))
        | {normalize_name(value) for value in row.get('chinese', ()) + row.get('aliases', ())}
    )]
    return _unique(matches)


def _world_total(row, rows):
    world = row['world']
    total = row.get('total')
    if not isinstance(total, int) or total <= 0:
        count = re.search(r'\((\d+)\)', world)
        total = int(count[1]) if count else max(
            item['number'] for item in rows if item['world'] == world)
    world = world.split('(')[0].strip()
    world = {'wizard city': '魔法城', 'celestia': '天国'}.get(world.casefold(), world)
    return world, total


async def log_mainline_progress(client):
    """One log per tracked Quest ID per client; failures never stop questing."""
    try:
        await _log_mainline_progress(client)
    except Exception as exc:
        client._xuanshu_mainline_id = None
        logger.debug('{} 主线进度日志暂不可用：{}', client.title, exc)


async def _log_mainline_progress(client):
    try:
        quest_id = await client.quest_id()
    except Exception as exc:
        client._xuanshu_mainline_id = None
        logger.debug('{} 主线 Quest ID 暂不可读：{}', client.title, exc)
        return
    if not isinstance(quest_id, int) or quest_id <= 0:
        client._xuanshu_mainline_id = None
        return

    lock = getattr(client, '_xuanshu_mainline_log_lock', None)
    if lock is None:
        lock = asyncio.Lock()
        client._xuanshu_mainline_log_lock = lock
    async with lock:
        if getattr(client, '_xuanshu_mainline_id', None) == quest_id:
            return

        rows = quest_rows()
        quest = None
        try:
            manager = await client.quest_manager()
            quest = (await manager.quest_data()).get(quest_id)
        except Exception as exc:
            logger.debug('{} 主线任务对象暂不可读，使用 Quest ID 回退：{}', client.title, exc)

        mainline = None
        code = ''
        title = ''
        if quest is not None:
            try:
                mainline = await quest.mainline()
            except Exception as exc:
                logger.debug('{} 主线标识暂不可读：{}', client.title, exc)
            try:
                code = await quest.name_lang_key() or ''
            except Exception as exc:
                logger.debug('{} 任务 Language Key 暂不可读：{}', client.title, exc)
            if code:
                try:
                    title = await client.cache_handler.get_langcode_name(code) or ''
                except Exception as exc:
                    logger.debug('{} 任务标题暂不可读，使用 Language Key 回退：{}', client.title, exc)

        if mainline is False:
            client._xuanshu_mainline_id = quest_id
            return
        row = match_quest(rows, quest_id, code, title)

        if row:
            world, total = _world_total(row, rows)
            client._xuanshu_mainline_progress = f'{client.title} · {world} 主线 {row["number"]}/{total}'
            display_title = next(iter(row.get('chinese', ())), '') or title or row['english']
            logger.info('{} 当前主线：{} 第 {}/{} 个 | {}',
                        client.title, world, row['number'], total, display_title)
            client._xuanshu_mainline_id = quest_id
            client._xuanshu_mainline_unmatched_id = None
        else:
            # Retain the last confirmed UI value and retry unresolved identities.
            # Log once while retrying so temporary read failures stay quiet.
            display_title = title or f'Quest ID: {quest_id}'
            if getattr(client, '_xuanshu_mainline_unmatched_id', None) != quest_id:
                logger.info('{} 当前主线：未匹配 | {}', client.title, display_title)
                client._xuanshu_mainline_unmatched_id = quest_id
