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


async def read_mainline_identity(client):
    """Read the actual tracked identity; the game mainline flag is not an index match."""
    try:
        quest_id = await client.quest_id()
        if not isinstance(quest_id, int) or quest_id < 0:
            return None
        manager = await client.quest_manager()
        owned = await manager.quest_data()
        quest = owned.get(quest_id)
        if quest is None and quest_id != 0:
            return None
        code = await quest.name_lang_key() if quest is not None else ''
        title = (code if code == 'Quest Finder' else
                 await client.cache_handler.get_langcode_name(code) or code) if code else ''
        goal_id = await client.goal_id() if hasattr(client, 'goal_id') else None
        catalog = installed_catalog(client)
        names = await catalog.get() if catalog else None
        row = match_quest(quest_rows(), quest_id, code, title, names)
        return dict(quest_id=quest_id, goal_id=goal_id, code=code, title=title,
                    row=row, owned=frozenset(owned), names=names)
    except Exception as exc:
        logger.trace('主线编号读取暂不可用：{}', exc)
        return None


def expected_mainline_rows(last):
    """Recovery alone uses the remembered row and the next number in its table."""
    rows = [last['row']]
    rows.extend(r for r in quest_rows() if r['world'] == last['world']
                and r['number'] == last['number'] + 1)
    return rows


def mainline_consistent(identity, last):
    row = identity['row']
    if row is None:
        return False
    if last is None or any((row['world'], row['number']) == (r['world'], r['number'])
                           for r in expected_mainline_rows(last)):
        return True
    # Still owning the prior quest proves a different chain wasn't a normal
    # continuation. A game's mainline flag cannot erase that recovery evidence.
    if last['quest_id'] in identity['owned']:
        return False
    return not (row['world'] == last['world'] and row['number'] < last['number'])


def record_mainline_success(client, identity):
    row = identity['row']
    client._xuanshu_mainline_last_success = dict(
        world=row['world'], number=row['number'], title=identity['title'],
        quest_id=identity['quest_id'], goal_id=identity['goal_id'], code=identity['code'], row=dict(row))


def mainline_dungeon_status(client):
    """Use recorded entry/exit evidence, including after the group session clears."""
    from src.quest_party import DungeonSession, dungeon_session_for
    current = dungeon_session_for(client)
    settled = getattr(client, '_xuanshu_mainline_dungeon_exit_observation', None)
    if (current is not None and id(client) in current.exited
            and isinstance(settled, dict) and settled.get('settled_session') is current):
        return None
    if current is not None and (current.entry_evidence or current.confirmed):
        client._xuanshu_mainline_dungeon_session = current
    session = getattr(client, '_xuanshu_mainline_dungeon_session', None)
    if not isinstance(session, DungeonSession) or client not in session.members:
        return None
    assigned = (session.quester, *getattr(session.quester, 'quest_party_hitters', ()))
    if (set(map(id, assigned)) != set(map(id, session.members))
            or any(not getattr(member, 'questing_status', False)
                   or (callable(getattr(member, 'is_running', None)) and not member.is_running())
                   for member in session.members)
            or (session.owner is not None and any(
                getattr(member, 'quest_party_status_session', None) is not session.owner
                for member in session.members[1:]))):
        client._xuanshu_mainline_dungeon_session = None
        return None
    member = id(client)
    if (session.confirmed and member in session.exited and member in session.exit_evidence
            and member not in session.potion_areas and not getattr(client, 'refilling_potions', False)):
        return 'exited'
    if current is session and (session.entry_evidence or session.confirmed):
        return 'inside'
    # An aborted pending entry or a changed assignment is not an actual exit.
    client._xuanshu_mainline_dungeon_session = None
    return None


async def log_mainline_progress(client):
    dungeon = mainline_dungeon_status(client)
    identity = await read_mainline_identity(client)
    if dungeon:
        state = '地牢任务进行中' if dungeon == 'inside' else '地牢已退出，主线待确认'
        client._xuanshu_mainline_progress = f'{client.title} · {state}'
        diagnostic = (dungeon, identity['quest_id'] if identity else None)
        if getattr(client, '_xuanshu_mainline_log_state', None) != diagnostic:
            logger.info('{} {} | {}', client.title, state, identity['title'] if identity else '任务状态暂不可读')
            client._xuanshu_mainline_log_state = diagnostic
        if identity is not None:
            client._xuanshu_mainline_id = identity['quest_id']
        return  # Keep the separate external last-success record intact.
    recovery = getattr(client, '_xuanshu_mainline_recovery', None)
    navigating = isinstance(recovery, dict) and recovery.get('phase') == 'navigation'
    read_failed = (isinstance(recovery, dict) and recovery.get('phase') == 'cooldown'
                   and bool(getattr(client, '_xuanshu_mainline_book_read_error', '')))
    if identity is None:
        client._xuanshu_mainline_progress = f'{client.title} · 当前主线暂不可读'
        return  # Never erase the separate last-success record.
    last = getattr(client, '_xuanshu_mainline_last_success', None)
    matched = mainline_consistent(identity, last)
    row = identity['row']
    if matched and recovery and last is not None:
        matched = any((row['world'], row['number']) == (r['world'], r['number'])
                      for r in expected_mainline_rows(last))
    if matched and not recovery:
        record_mainline_success(client, identity)
    if navigating:
        client._xuanshu_mainline_progress = f'{client.title} · 主线接取导航中，待确认'
    elif matched:
        world = row['world']
        count = re.search(r'\((\d+)\)', world)
        total = row.get('total') or (int(count[1]) if count else max(
            r['number'] for r in quest_rows() if r['world'] == world))
        world = world.split('(')[0].strip()
        world = {'wizard city': '魔法城', 'celestia': '天国'}.get(world.casefold(), world)
        client._xuanshu_mainline_progress = f'{client.title} · {world} 主线 {row["number"]}/{total}'
    elif read_failed:
        client._xuanshu_mainline_progress = f'{client.title} · 任务簿信息读取失败，待重试'
    else:
        client._xuanshu_mainline_progress = f'{client.title} · 当前主线未匹配'
    diagnostic = (identity['quest_id'], identity['code'], matched, navigating, read_failed)
    if getattr(client, '_xuanshu_mainline_log_state', None) != diagnostic:
        if matched and not navigating:
            logger.info('{} 当前主线：{} 第 {}/{} 个 | {}', client.title, world, row['number'], total, identity['title'])
        elif read_failed:
            logger.info('{} 当前主线：任务簿信息读取失败，待重试 | {}', client.title, identity['title'])
        else:
            logger.info('{} 当前主线：未匹配或待恢复确认 | {}', client.title, identity['title'])
            logger.debug('{} 主线诊断 Quest ID {}，状态={}', client.title, identity['quest_id'], diagnostic)
        client._xuanshu_mainline_log_state = diagnostic
    client._xuanshu_mainline_id = identity['quest_id']
