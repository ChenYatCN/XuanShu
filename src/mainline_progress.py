"""World-local quest numbering, using quest identity rather than objective text."""
import json
import re
from functools import lru_cache
from pathlib import Path

from loguru import logger
from wizwalker.errors import WizWalkerMemoryError
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


def match_quest(rows, quest_id, code, title, names=None, *, diagnostic=None):
    def result(candidates, method):
        row = candidates[0] if len(candidates) == 1 else None
        if diagnostic is not None:
            reason = ('matched-' + method if row else 'ambiguous-' + method if candidates else
                      'quest-finder-system-card' if method == 'system-card' else 'title-not-in-index')
            diagnostic.update(method=method, reason=reason)
        return row

    if code == 'Quest Finder' or normalize_name(title) in ('questfinder', '任务搜寻'):
        return result([], 'system-card')
    by_id = [r for r in rows if quest_id in r.get('quest_ids', [])]
    if by_id:
        return result(by_id, 'quest-id')
    by_key = [r for r in rows if code and code in r.get('keys', [])]
    if by_key:
        return result(by_key, 'title-key')
    aliases = {normalize_name(title)} - {''}
    if names:
        aliases |= names.by_id.get(code.casefold(), set())
        aliases |= names.aliases(title)
    matches = [r for r in rows if aliases.intersection(
        title_aliases(r['english']) | {normalize_name(s) for s in r.get('chinese', []) + r.get('aliases', [])})]
    # Ambiguous titles must not silently select the wrong quest/world.
    return result(matches, 'title')


async def read_mainline_identity(client):
    """Read the actual tracked identity; the game mainline flag is not an index match."""
    try:
        quest_id = await client.quest_id()
        if not isinstance(quest_id, int) or quest_id < 0:
            return None
        manager = await client.quest_manager()
        owned = await manager.quest_data()
        owned_ids = frozenset(owned)
        quest = owned.get(quest_id)
        if quest is None and quest_id != 0:
            return None
        code = await quest.name_lang_key() if quest is not None else ''
        title = (code if code == 'Quest Finder' else
                 await client.cache_handler.get_langcode_name(code) or code) if code else ''
        goal_id = await client.goal_id() if hasattr(client, 'goal_id') else None
        catalog = installed_catalog(client)
        names = await catalog.get() if catalog else None
        diagnostic = {}
        row = match_quest(quest_rows(), quest_id, code, title, names, diagnostic=diagnostic)
        ready = None
        read_ready = getattr(quest, 'ready_to_turn_in', None)
        if callable(read_ready):
            try:
                ready = await read_ready()
            except (WizWalkerMemoryError, ValueError, AttributeError) as exc:
                logger.trace('主线交付状态暂不可读，不推断退休：{}', exc)
        last = getattr(client, '_xuanshu_mainline_last_success', None) or {}
        verify_retirement = any(previous.get('ready_to_turn_in') and previous['quest_id'] not in owned_ids
            and key not in last.get('retired_mainlines', ())
            for key, previous in last.get('known_mainlines', {}).items())
        owned_verified = False
        if verify_retirement and row is not None:
            try:
                if not await client.is_loading() and not await client.in_battle():
                    fresh = await manager.quest_data()
                    current = fresh.get(quest_id)
                    owned_verified = (frozenset(fresh) == owned_ids and current is not None
                        and await current.name_lang_key() == code and await client.quest_id() == quest_id
                        and await client.goal_id() == goal_id
                        and not await client.is_loading() and not await client.in_battle())
            except (WizWalkerMemoryError, ValueError, AttributeError) as exc:
                logger.trace('主线持有快照暂不稳定，不推断退休：{}', exc)
        return dict(quest_id=quest_id, goal_id=goal_id, code=code, title=title,
                    row=row, owned=owned_ids, names=names,
                    ready_to_turn_in=ready is True, owned_verified=owned_verified,
                    match_method=diagnostic['method'], match_reason=diagnostic['reason'])
    except Exception as exc:
        logger.trace('主线编号读取暂不可用：{}', exc)
        return None


def owned_current(identity):
    return (type(identity.get('quest_id')) is int and identity['quest_id'] > 0
            and identity['quest_id'] in identity.get('owned', ()))


def expected_mainline_rows(last, identity=None):
    """Current/next and declared branches, plus proven still-owned matched tasks."""
    rows = [last['row']]
    world_rows = [r for r in quest_rows() if r['world'] == last['world']]
    rows.extend(r for r in world_rows if r['number'] == last['number'] + 1)
    highest = last.get('highest_seen', {}).get(last['world'], last['number'])
    if highest > last['number'] and (last['world'], highest) in last.get('known_mainlines', {}):
        # Backtracking an owned task cannot erase the proven forward anchor.
        # These are candidates only; no earlier task is inferred complete.
        rows.extend(r for r in world_rows if r['number'] in (highest, highest + 1) and r not in rows)

    def alternatives(row):
        note = row.get('note', '')
        if not re.match(r'^interchangeable with\s+', note, re.I):
            return set()
        return {normalize_name(title) for title in re.findall(r'"([^"]+)"', note)}

    anchors = list(rows)
    if last['row'].get('note', '').casefold() == 'returns to previous':
        previous = [r for r in world_rows if r['number'] == last['number'] - 1 and alternatives(r)]
        anchors.extend(previous)
        rows.extend(r for r in previous if r not in rows)
    seen = {r['number'] for r in rows}
    for anchor in anchors:
        names = alternatives(anchor)
        if (not names or sum(normalize_name(r['english']) == normalize_name(anchor['english'])
                             for r in world_rows) != 1):
            continue
        for candidate in world_rows:
            title = normalize_name(candidate['english'])
            if (candidate['number'] not in seen and title in names
                    and normalize_name(anchor['english']) in alternatives(candidate)
                    and sum(normalize_name(r['english']) == title for r in world_rows) == 1):
                rows.append(candidate)
                seen.add(candidate['number'])
    owned = identity.get('owned', ()) if identity is not None else last.get('owned', ())
    for previous in last.get('known_mainlines', {}).values():
        candidate = previous['row']
        if (candidate['world'] == last['world'] and previous['quest_id'] in owned
                and candidate['number'] not in seen):
            rows.append(candidate)
            seen.add(candidate['number'])
    # A live earlier task may also be the only remaining owned continuation.
    # Do not use this to bypass prior-owned for an unrelated, unproven chain.
    candidate = identity.get('row') if identity is not None else None
    if (candidate and owned_current(identity) and last.get('quest_id') not in owned
            and candidate['world'] == last['world'] and candidate['number'] < last['number']
            and candidate['number'] not in seen):
        rows.append(candidate)
    retired = last.get('retired_mainlines', ())
    return [r for r in rows if (r['world'], r['number']) not in retired]


def mainline_reject_reason(identity, last):
    row = identity['row']
    if row is None:
        return identity.get('match_reason') or 'identity-not-in-index'
    key = (row['world'], row['number'])
    if last is not None:
        if key in last.get('retired_mainlines', ()):
            return 'retired-mainline'
        boundary = last.get('completed_boundary', {}).get(row['world'])
        if not owned_current(identity) and boundary is not None and row['number'] <= boundary:
            return 'behind-completed-mainline-boundary'
    if last is None or any((row['world'], row['number']) == (r['world'], r['number'])
                           for r in expected_mainline_rows(last, identity)):
        return None
    # Still owning the prior quest proves a different chain wasn't a normal
    # continuation. A game's mainline flag cannot erase that recovery evidence.
    if last['quest_id'] in identity['owned']:
        return 'prior-mainline-still-owned'
    if row['world'] == last['world'] and row['number'] < last['number']:
        return 'prior-mainline-without-current-ownership'
    if row['world'] != last['world'] and row['world'] in last.get('highest_seen', {}):
        return 'previous-mainline-world'
    return None


def mainline_consistent(identity, last):
    return mainline_reject_reason(identity, last) is None


def log_mainline_match(client, identity, last, matched, reason, *, source='tracked', card=None, expected=None):
    """Separate identity lookup from progression rejection; never infer card Quest IDs."""
    expected = expected if expected is not None else expected_mainline_rows(last, identity) if last else []
    row = identity.get('row')
    world = row['world'] if row else last['world'] if last else None
    highest = (last or {}).get('highest_seen', {}).get(world)
    if matched and row is not None:
        highest = max(highest or 0, row['number'])
    logger.debug('[mainline_match] client={} source={} card={} title={!r} QuestID={} '
        'title_key={} normalized_title={!r} expected_title={} expected_index={} '
        'index_match={} matched_index={} highest_seen={} completed_boundary={} '
        'owned-current={} method={} match={} reason={}',
        client.title, source, card if card is not None else 'unavailable', identity['title'],
        identity.get('quest_id', 'unavailable'), identity.get('code', 'unavailable'),
        normalize_name(identity['title']), [r['english'] for r in expected],
        [(r['world'], r['number']) for r in expected], row is not None,
        (row['world'], row['number']) if row else 'unavailable',
        highest if highest is not None else 'unavailable',
        (last or {}).get('completed_boundary', {}).get(world, 'unconfirmed'), owned_current(identity),
        identity.get('match_method', 'unavailable'), matched, reason)


def record_mainline_success(client, identity):
    """Remember observations separately from the evidence that retires a task."""
    row = identity['row']
    last = getattr(client, '_xuanshu_mainline_last_success', None) or {}
    known = {key: dict(value) for key, value in last.get('known_mainlines', {}).items()}
    if last and not known:  # An existing in-memory anchor is not completion evidence.
        known[(last['world'], last['number'])] = dict(last, ready_to_turn_in=False, missing=0)
    highest = dict(last.get('highest_seen', {}))
    if last:
        highest[last['world']] = max(highest.get(last['world'], 0), last['number'])
    highest[row['world']] = max(highest.get(row['world'], 0), row['number'])
    retired = set(last.get('retired_mainlines', ()))
    boundary = dict(last.get('completed_boundary', {}))
    if 'owned' in identity:
        for key, previous in known.items():
            # Turn-in readiness alone is not completion. Retire only after a
            # previously ready task disappears on successive valid matches.
            if previous['quest_id'] in identity['owned']:
                previous['missing'] = 0
            elif not identity.get('owned_verified'):
                previous['missing'] = 0
            elif previous.get('ready_to_turn_in') and key != (row['world'], row['number']):
                previous['missing'] = min(2, previous.get('missing', 0) + 1)
                if previous['missing'] >= 2:
                    retired.add(key)
                    boundary[key[0]] = max(boundary.get(key[0], 0), key[1])
    current = dict(world=row['world'], number=row['number'], title=identity['title'],
        quest_id=identity['quest_id'], goal_id=identity['goal_id'], code=identity['code'], row=dict(row))
    known[(row['world'], row['number'])] = dict(current,
        ready_to_turn_in=identity.get('ready_to_turn_in') is True, missing=0)
    client._xuanshu_mainline_last_success = dict(
        current, highest_seen=highest, known_mainlines=known, retired_mainlines=frozenset(retired),
        completed_boundary=boundary, owned=identity.get('owned', last.get('owned', frozenset())))


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
    reason = mainline_reject_reason(identity, last)
    matched = reason is None
    row = identity['row']
    if matched and recovery and last is not None:
        matched = any((row['world'], row['number']) == (r['world'], r['number'])
                      for r in expected_mainline_rows(last, identity))
        if not matched:
            reason = 'outside-recovery-expected'
    expected = expected_mainline_rows(last, identity) if last else []
    match_diagnostic = (identity['quest_id'], identity['code'], identity['title'], matched, reason,
                        tuple((r['world'], r['number']) for r in expected), owned_current(identity),
                        tuple((last or {}).get('highest_seen', {}).items()),
                        tuple((last or {}).get('completed_boundary', {}).items()))
    if getattr(client, '_xuanshu_mainline_match_log_state', None) != match_diagnostic:
        log_mainline_match(client, identity, last, matched, reason or 'expected-mainline-matched', expected=expected)
        client._xuanshu_mainline_match_log_state = match_diagnostic
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
        client._xuanshu_mainline_log_state = diagnostic
    client._xuanshu_mainline_id = identity['quest_id']
