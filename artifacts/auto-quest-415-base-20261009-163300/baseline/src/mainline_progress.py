"""World-local quest numbering, using quest identity rather than objective text."""
import json
import re
from functools import lru_cache
from pathlib import Path

from loguru import logger
from src.collect_catalog import installed_catalog
from src.collect_matching import normalize_name


@lru_cache(maxsize=1)
def quest_rows():
    return json.loads((Path(__file__).with_name('data') / 'mainline_quests.json').read_text(encoding='utf-8'))['rows']


def title_aliases(title):
    # Spreadsheet annotations are not part of the title. Keep explicit old names.
    values = [title, re.split(r'\s*\(', title, maxsplit=1)[0]]
    values.extend(re.findall(r'(?:formerly|previously|old name)\s*:?\s*["“]?([^\)"”]+)', title, re.I))
    return {normalize_name(value) for value in values if value}


def match_quest(rows, quest_id, code, title, names=None):
    by_id = [r for r in rows if quest_id in r.get('quest_ids', [])]
    if by_id:
        return by_id[0] if len(by_id) == 1 else None
    by_key = [r for r in rows if code and code in r.get('keys', [])]
    if by_key:
        return by_key[0] if len(by_key) == 1 else None
    aliases = {normalize_name(title)} - {''}
    if names:
        aliases |= names.by_id.get(code.casefold(), set())
        aliases |= names.aliases(title)
    matches = [r for r in rows if aliases.intersection(
        title_aliases(r['english']) | {normalize_name(s) for s in r.get('chinese', []) + r.get('aliases', [])})]
    # Ambiguous titles must not silently select the wrong quest/world.
    return matches[0] if len(matches) == 1 else None


async def log_mainline_progress(client):
    try:
        quest_id = await client.quest_id()
        if not isinstance(quest_id, int) or quest_id <= 0:
            return
        if getattr(client, '_xuanshu_mainline_id', None) == quest_id:
            return
        manager = await client.quest_manager()
        quest = (await manager.quest_data()).get(quest_id)
        if quest is None:
            return
        if not await quest.mainline():
            client._xuanshu_mainline_id = quest_id
            return
        code = await quest.name_lang_key()
        title = await client.cache_handler.get_langcode_name(code) or code
        catalog = installed_catalog(client)
        names = await catalog.get() if catalog else None
        row = match_quest(quest_rows(), quest_id, code, title, names)
        if row:
            world = row['world']
            count = re.search(r'\((\d+)\)', world)
            total = int(count[1]) if count else max(r['number'] for r in quest_rows() if r['world'] == world)
            world = world.split('(')[0].strip()
            world = {'wizard city': '魔法城', 'celestia': '天国'}.get(world.casefold(), world)
            logger.info('{} 当前主线：{} 第 {}/{} 个 | {}', client.title, world, row['number'], total, title)
        else:
            logger.info('{} 当前主线：未匹配 | {} | Quest ID {}', client.title, title, quest_id)
        client._xuanshu_mainline_id = quest_id
    except Exception as exc:
        # Logging is optional; never prevent the quest worker from continuing.
        logger.trace('主线编号读取暂不可用：{}', exc)
