"""Role and identity resolution for the configurable auto-quest party."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
import math
import re
import time
from typing import Iterable, Mapping, Sequence, TypeVar


ClientT = TypeVar("ClientT")
FOLLOW_RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0, 30.0)
_DUNGEON_GENERATIONS = count(1)


def _next_dungeon_generation():
    return next(_DUNGEON_GENERATIONS)
FRIEND_ICON_PRESETS = {
    # The selector starts at frame 42 of icon sheet 1. Button7 and Button8
    # therefore map to frames 49 and 50; Button9 is frame 0 on sheet 2.
    "goblin": (1, 49),
    "grass": (1, 50),
    "fish": (2, 0),
}

# Positive naming evidence for these confirmed families only. Full paths and
# family-specific suffixes avoid stripping arbitrary trailing digits/letters.
_DUNGEON_ROOM_SERIES = tuple(re.compile(pattern) for pattern in (
    r'Polaris/Interiors/PL_Z06_KambataUnderwater(?:[0-9]+)?',
    r'Polaris/Interiors/PL_Z02_BabaYagaStatuary(?:[0-9]+)?',
    r'WizardCity/Gauntlets/WC_Triton_Gauntlet1/WC_Triton_Gauntlet_[0-9]+[a-z]?',
    r'Marleybone/Interiors/MB_MuseumStorage_[A-Z]',
))


def same_dungeon_room_series(before, after):
    """A positive room hint; False means unknown, never an exit verdict."""
    return bool(isinstance(before, str) and isinstance(after, str) and any(
        pattern.fullmatch(before) and pattern.fullmatch(after)
        for pattern in _DUNGEON_ROOM_SERIES))


@dataclass(frozen=True)
class QuestParty:
    questers: list[ClientT]
    hitters: list[ClientT]
    idle: list[ClientT]
    hitter_assignments: list[tuple[ClientT, ClientT]]


@dataclass
class DungeonSession:
    """One recorded party entry; room changes do not create new sessions."""
    quester: object
    members: tuple
    source_zones: dict
    owner: object = None
    entry_evidence: bool = False
    confirmed: bool = False
    entry_zones: dict = field(default_factory=dict)
    entry_areas: dict = field(default_factory=dict)
    last_zones: dict = field(default_factory=dict)
    exited: set = field(default_factory=set)
    potion_areas: dict = field(default_factory=dict)
    exit_evidence: dict = field(default_factory=dict)
    room_evidence: dict = field(default_factory=dict)
    transition: dict | None = None
    follow_attempts: dict = field(default_factory=dict)
    entry_wait: dict | None = None
    generation: int = field(default_factory=_next_dungeon_generation)
    phase: str = 'active'
    quester_out: bool = False
    retire_reason: str | None = None
    entry_key: tuple | None = None


def _clear_dungeon_entry(session):
    """Invalidate only this session's entrance records, never another input owner."""
    session.transition = None
    session.entry_wait = None
    session.follow_attempts.clear()
    for member in session.members:
        entry = getattr(member, '_quest_party_entry', None)
        if isinstance(entry, dict) and entry.get('session') is session:
            member._quest_party_entry = None
        approach = getattr(member, '_quest_door_approach', None)
        if isinstance(approach, dict) and approach.get('session') is session:
            approach['ended'] = True


def retire_dungeon_session(session, *, reason='retired'):
    """Retire exactly this session and only state that is proven to belong to it."""
    if not isinstance(session, DungeonSession):
        return False
    already_retired = session.phase == 'retired'
    session.phase = 'retired'
    session.retire_reason = reason
    session.transition = None
    session.entry_wait = None
    session.follow_attempts.clear()
    _clear_dungeon_entry(session)
    for member in session.members:
        if getattr(member, 'quest_party_dungeon', None) is session:
            member.quest_party_dungeon = None
        # Battle rescue/probe/worker state has no session generation yet. 2B
        # consumers must clear it with this expected session after their own
        # fresh validation, not unconditionally here.
    return not already_retired


def mark_dungeon_party_releasing(session, *, reason='quester-out'):
    """Authorize active follower release only after the quester is confirmed out."""
    if (not isinstance(session, DungeonSession) or session.phase == 'retired'
            or not session.quester_out):
        return False
    session.phase = 'party-releasing'
    return True


def clear_dungeon_session(client, expected=None, *, reason='clear'):
    """Clear the current session, except when a stale caller names another one."""
    session = getattr(client, 'quest_party_dungeon', None)
    if not isinstance(session, DungeonSession):
        return False
    if expected is not None and session is not expected:
        return False
    return retire_dungeon_session(session, reason=reason)


def dungeon_session_for(client):
    session = getattr(client, 'quest_party_dungeon', None)
    if not isinstance(session, DungeonSession):
        return None
    if session.phase == 'retired':
        retire_dungeon_session(session, reason='retired-detected')
        return None
    current = (session.quester, *getattr(session.quester, 'quest_party_hitters', ()))
    if (set(map(id, current)) != set(map(id, session.members))
            or (session.owner is not None and any(
                getattr(c, 'quest_party_status_session', None) is not session.owner
                for c in session.members[1:]))
            or any(not getattr(c, 'questing_status', False)
                   or (callable(getattr(c, 'is_running', None)) and not c.is_running())
                   for c in session.members)):
        clear_dungeon_session(client, expected=session, reason='roster-changed')
        return None
    return session


async def dungeon_entry_waiting(client):
    """Keep shared X's loading/countdown phase separate from bounded friend recovery."""
    from loguru import logger
    from src.paths import team_up_wait_path
    from src.utils import is_visible_by_path, clients_share_live_area, observe_party_area, _party_area_token
    session = dungeon_session_for(client)
    wait = session.entry_wait if session else None
    if wait is None:
        return False
    if session.transition is not wait['source']:
        session.entry_wait = None
        return False
    try:
        tokens = []
        members = tuple(m for m in session.members if id(m) in wait['source']['source_tokens'])
        busy_error = None
        for member in members:
            try:
                if (await member.is_loading()
                        or await is_visible_by_path(member, team_up_wait_path)):
                    wait['stable'] = None
                    return True
            except Exception as exc:
                busy_error = exc  # Still check every peer's real loading/countdown.
        if busy_error is not None:
            raise busy_error
        for member in members:
            await observe_party_area(member)
            token = await _party_area_token(member)
            tokens.append(token)
            if (token[0] != wait['source']['source_zone']
                    and all(type(v) is int and v > 0 for v in token[1:3])):
                wait['source'].setdefault('arrivals', {})[id(member)] = token
        if (dungeon_session_for(client) is not session or session.entry_wait is not wait):
            return False
        source = wait['source']
        arrived = all(token[0] != source['source_zone'] for token in tokens)
        if (arrived and len({token[0] for token in tokens}) == 1
                and all([await clients_share_live_area(session.quester, m)
                         for m in members if m is not session.quester])
                and all([not await m.is_loading() and await _party_area_token(m) == token
                         for m, token in zip(members, tokens)])):
            session.entry_wait = None
            for member in session.members:
                entry = getattr(member, '_quest_party_entry', None)
                if isinstance(entry, dict) and entry.get('source') is source:
                    member._quest_party_entry = None
            logger.debug(f"[dungeon_entry] {session.quester.title}: phase=arrived "
                         f"members={[m.title for m in session.members]} same_instance=True")
            return False
        now = time.monotonic()
        evidence = tuple(tokens)
        stable = wait.get('stable')
        if stable is None or stable[0] != evidence:
            wait['stable'] = (evidence, now)
            return True
        if now < wait['deadline'] or now - stable[1] < .75:
            return True
        # The input operation has ended; no old entrance replay is authorized.
        # The existing follower alone may now evaluate actual friend arrival.
        end_dungeon_entry(session, source, 'partial-arrival' if any(
            token[0] != source['source_zone'] for token in tokens) else 'no-arrival')
        logger.debug(f"[dungeon_entry] {session.quester.title}: phase=entry-unconfirmed "
                     f"members={[m.title for m in session.members]} areas={evidence}; "
                     "old entrance replay disabled, release to bounded friend recovery")
        return False
    except Exception as exc:
        if wait.get('read_error') != type(exc).__name__:
            logger.debug(f"[dungeon_entry] {session.quester.title}: phase=wait-unreadable "
                         f"error={type(exc).__name__}: {exc}")
            wait['read_error'] = type(exc).__name__
        if time.monotonic() >= wait['deadline']:
            end_dungeon_entry(session, wait['source'], 'wait-unreadable')
            return False
        return True  # Missing countdown/area evidence cannot permit overlapping input.


def end_dungeon_entry(session, source, reason):
    """End this input round; retain sent intent so it never authorizes another X."""
    from loguru import logger
    if session.transition is not source:
        return
    if session.entry_wait and session.entry_wait['source'] is source:
        session.entry_wait = None
    for member in session.members:
        entry = getattr(member, '_quest_party_entry', None)
        if isinstance(entry, dict) and entry.get('source') is source:
            member._quest_party_entry = None
    if source.get('entry_ended') != reason:
        source['entry_ended'] = reason
        logger.debug(f"[dungeon_entry] {session.quester.title}: phase=ended reason={reason}; "
                     "shared X will not be replayed")


def _log_dungeon_source_capture(client, session, stage, decision, reason, **fields):
    """Bounded scalar diagnostics only; never let logging change a game decision."""
    try:
        from loguru import logger
        def scalar(value):
            if value is None or type(value) in (bool, int, float):
                return value
            if isinstance(value, str):
                return value[:160]
            if isinstance(value, (tuple, list)):
                return tuple(scalar(item) for item in value[:8])
            return f'<{type(value).__name__}>'
        fields = {name: scalar(value) for name, value in fields.items()}
        generation = getattr(session, 'generation', None)
        phase = getattr(session, 'phase', None)
        # A rejected transient session can be recreated each tick. Its new id
        # is not a new failure; accepted credentials retain their generation.
        key = (stage, decision, reason, phase, fields,
               (id(session), generation) if decision == 'accept' else None)
        if getattr(client, '_dungeon_source_capture_log_state', None) == key:
            return
        client._dungeon_source_capture_log_state = key
        payload = dict(client=client.title, generation=generation,
            session=id(session) if session is not None else None, session_phase=phase,
            stage=stage, decision=decision, reason=reason, **fields)
        logger.bind(dungeon_source_capture=payload).debug(
            '[dungeon_source_capture] client={} generation={} session={} session_phase={} '
            'stage={} decision={} reason={} {}', client.title, generation, payload['session'],
            phase, stage, decision, reason, ' '.join(f'{k}={v!r}' for k, v in fields.items()))
    except Exception:
        pass  # Diagnostic storage/formatting must not alter the original return or exception.


def _dungeon_source_capture_error(exc):
    from wizwalker.errors import HookNotActive, WizWalkerMemoryError
    error = ('hook-not-active' if isinstance(exc, HookNotActive) else
             'memory-read-error' if isinstance(exc, WizWalkerMemoryError) else
             'value-error' if isinstance(exc, ValueError) else
             'attribute-error' if isinstance(exc, AttributeError) else
             'timeout-error' if isinstance(exc, TimeoutError) else
             'runtime-error' if isinstance(exc, RuntimeError) else 'read-error')
    return dict(error=error, exception_type=type(exc).__name__)


async def begin_dungeon_entry(quester, entry_clients, *, evidence=False):
    """Record before X, but only live arrival can enable independent movement."""
    session = dungeon_session_for(quester)
    if session is not None:
        return session
    members = (quester, *getattr(quester, 'quest_party_hitters', ()))
    for member in members:
        existing = getattr(member, 'quest_party_dungeon', None)
        if not isinstance(existing, DungeonSession) or existing is session:
            continue
        if existing.phase == 'retired':
            clear_dungeon_session(member, expected=existing, reason='retired-entry')
        elif existing.quester is quester:
            return existing
        else:
            _log_dungeon_source_capture(quester, None, 'create', 'reject', 'session-create-failed',
                check='member-session-conflict', member=getattr(member, 'title', None), existing_session=id(existing))
            return None  # Never create a parallel session over another group's member.
    entry_ids = None
    if len(members) < 2 or set(map(id, members)) != (entry_ids := set(map(id, entry_clients))):
        _log_dungeon_source_capture(quester, None, 'create', 'reject', 'session-create-failed',
            check='entry-roster-mismatch', members=tuple(getattr(c, 'title', None) for c in members),
            entry_count=len(entry_ids) if entry_ids is not None else None)
        return None
    zones = {}
    check = 'member-state'
    try:
        for client in members:
            check, loading = 'member-state', None
            if (not client.questing_status or (loading := await client.is_loading())
                    or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None)):
                _log_dungeon_source_capture(quester, None, 'create', 'reject', 'session-create-failed',
                    check='loading' if loading else 'member-not-ready', member=getattr(client, 'title', None),
                    loading=loading)
                return None
            check = 'zone'
            zones[id(client)] = await client.zone_name()
    except Exception as exc:
        _log_dungeon_source_capture(quester, None, 'create', 'reject', 'session-create-failed',
            check=check + '-unreadable', member=getattr(client, 'title', None),
            **_dungeon_source_capture_error(exc))
        raise  # Creation read errors originally propagate to the caller.
    if not all(zones.values()) or len(set(zones.values())) != 1:
        _log_dungeon_source_capture(quester, None, 'create', 'reject', 'session-create-failed',
            check='zone-invalid' if not all(zones.values()) else 'zone-mismatch',
            member_zones=tuple((getattr(c, 'title', None), zones[id(c)]) for c in members))
        return None
    session = DungeonSession(quester, members, zones,
        owner=getattr(members[1], 'quest_party_status_session', None), entry_evidence=evidence)
    for client in members:
        client.quest_party_dungeon = session
        if session.entry_evidence:
            client._xuanshu_mainline_dungeon_session = session
    return session


async def observe_dungeon_session(client):
    from src.utils import clients_share_live_area, observe_party_area, _party_area_token
    session = dungeon_session_for(client)
    if session is None:
        return None
    state = session.transition
    if (state and time.monotonic() < state['deadline'] and id(client) in state['source_tokens']
            and id(client) not in session.potion_areas
            and not getattr(client, 'refilling_potions', False)
            and not await client.is_loading()):
        try:
            await observe_party_area(client)
            token = await _party_area_token(client)
            if (token[0] != state['source_zone']
                    and all(type(v) is int and v > 0 for v in token[1:3])
                    and not await client.is_loading()
                    and await _party_area_token(client) == token):
                state.setdefault('arrivals', {})[id(client)] = token
            elif token[0] == state['source_zone']:
                state.setdefault('arrivals', {}).pop(id(client), None)
        except Exception:
            pass  # Unreadable area evidence never proves arrival.
    if not session.confirmed:
        if not session.entry_evidence:
            return session
        zones, areas = {}, {}
        incomplete = False
        for member in session.members:
            if (await member.is_loading() or await member.in_battle()
                    or getattr(member, 'refilling_potions', False)):
                return session
            zone = await member.zone_name()
            if not zone or zone == session.source_zones[id(member)]:
                if member is session.quester and id(member) in session.entry_zones:
                    clear_dungeon_session(client, expected=session, reason='aborted-entry')
                    return None
                incomplete = True
                continue
            session.entry_zones.setdefault(id(member), zone)
            zones[id(member)] = zone
            await observe_party_area(member)
            try:
                areas[id(member)] = await _party_area_token(member)
            except Exception:
                areas[id(member)] = None  # Live entity evidence is still usable.
        if incomplete or len(set(zones.values())) != 1:
            return session
        for member in session.members[1:]:
            if not await clients_share_live_area(session.quester, member):
                return session
        # Area reads can span frames; never commit a half-entered/stopped group.
        if dungeon_session_for(client) is not session:
            return None
        for member in session.members:
            if (await member.is_loading() or getattr(member, 'refilling_potions', False)
                    or await member.zone_name() != zones[id(member)]):
                return session
            if (areas[id(member)] is not None
                    and await _party_area_token(member) != areas[id(member)]):
                return session
        session.entry_zones = zones
        session.entry_areas = areas
        session.last_zones = dict(zones)
        session.confirmed = True
    if (getattr(client, 'refilling_potions', False) or id(client) in session.potion_areas
            or await client.is_loading()):
        return session
    zone = await client.zone_name()
    if zone == session.source_zones[id(client)] and id(client) not in session.exited:
        from loguru import logger
        logger.debug(f"[dungeon_operation] client={client.title} generation={session.generation} "
                     f"transition={id(state) if state is not None else None} event=zone-change "
                     f"before={session.last_zones[id(client)]} after={zone} evidence=source-zone-exit")
        session.exited.add(id(client))
        session.exit_evidence[id(client)] = ('source-zone', session.last_zones[id(client)], zone)
        if client is session.quester:
            session.quester_out = True
            if session.phase in ('active', 'exiting'):
                session.phase = 'quester-out'
        _clear_dungeon_entry(session)
    elif zone and zone != session.last_zones[id(client)] and id(client) not in session.exited:
        before = session.last_zones[id(client)]
        if same_dungeon_room_series(before, zone):
            session.room_evidence[id(client)] = ('room-series', before, zone)
        else:
            # Unknown naming is not evidence of leaving the recorded dungeon.
            session.room_evidence.pop(id(client), None)
    if id(client) in session.exited:
        session.room_evidence.pop(id(client), None)
    if zone:
        session.last_zones[id(client)] = zone
    if len(session.exited) == len(session.members):
        retire_dungeon_session(session, reason='all-members-out')
        return None
    return session


def dungeon_member_inside(client):
    session = dungeon_session_for(client)
    return bool(session and session.confirmed and id(client) not in session.exited
                and id(client) not in session.potion_areas
                and not getattr(client, 'refilling_potions', False))


def record_dungeon_exit(client, before, after, *, evidence='GUI_InstanceExit'):
    """An actual exit transition, or verified reunion with an exited quester."""
    session = dungeon_session_for(client)
    if (not session or not session.confirmed
            or (before == after and evidence != 'live-rejoin')
            or id(client) in session.potion_areas or getattr(client, 'refilling_potions', False)):
        return
    session.exited.add(id(client))
    if client is session.quester:
        session.quester_out = True
        if session.phase in ('active', 'exiting'):
            session.phase = 'quester-out'
    _clear_dungeon_entry(session)
    session.exit_evidence[id(client)] = (evidence, before, after)
    session.last_zones[id(client)] = after
    session.room_evidence.pop(id(client), None)
    session.potion_areas.pop(id(client), None)
    if len(session.exited) == len(session.members):
        retire_dungeon_session(session, reason='all-members-out')


async def pause_dungeon_for_potions(client):
    from src.utils import potion_zone_id
    session = dungeon_session_for(client)
    if (session and session.confirmed and id(client) not in session.exited
            and id(client) not in session.potion_areas):
        # refilling_potions is already set before these first async reads.
        session.potion_areas[id(client)] = (
            await client.zone_name(), await potion_zone_id(client))


def resume_dungeon_member(client):
    session = dungeon_session_for(client)
    if session is not None:
        session.potion_areas.pop(id(client), None)


async def finish_dungeon_potions(client, recalled=False):
    from src.utils import potion_zone_id
    session = dungeon_session_for(client)
    if not session or id(client) not in session.potion_areas:
        return
    if recalled:
        zone, zone_id = session.potion_areas[id(client)]
        if (type(zone_id) is int and zone_id > 0 and await client.zone_name() == zone
                and await potion_zone_id(client) == zone_id):
            resume_dungeon_member(client)
    if client is session.quester:
        # Even a verified mark return must pass the old area/solo probe before TP.
        client.quest_party_probe_pending = True


async def dungeon_quester_away_for_potions(quester):
    from src.utils import clients_share_live_area
    if getattr(quester, 'refilling_potions', False):
        return True
    session = dungeon_session_for(quester)
    if not session or id(quester) not in session.potion_areas:
        return False
    if await quester.is_loading():
        return True
    zone, _ = session.potion_areas[id(quester)]
    if await quester.zone_name() == zone:
        return False  # Returned candidate; the existing probe still must confirm.
    for member in session.members[1:]:
        if (id(member) not in session.exited and id(member) not in session.potion_areas
                and await clients_share_live_area(quester, member)):
            return False
    return True


async def quest_party_members_ready(quester, *, zone=None, _capture_diagnostic=None):
    """Publish a group arrival only from current loading/instance evidence."""
    from src.utils import clients_share_live_area
    session = dungeon_session_for(quester)
    members = (quester, *getattr(quester, 'quest_party_hitters', ()))
    if session and session.confirmed and id(quester) not in session.exited:
        members = tuple(c for c in members if id(c) not in session.exited)
    roster = tuple(map(id, members))
    stage, member = 'zone', quester
    def rejected(reason, **fields):
        if _capture_diagnostic is not None:
            _capture_diagnostic.update(reason=reason, check=stage, member=getattr(member, 'title', None), **fields)
        return False
    try:
        zone = zone or await quester.zone_name()
        if not zone:
            return rejected('zone-invalid', zone_before=zone)
        for member in members:
            stage = 'member-state'
            if (not getattr(member, 'questing_status', False)
                    or (callable(getattr(member, 'is_running', None)) and not member.is_running())
                    or getattr(member, 'refilling_potions', False)
                    or getattr(member, 'quest_recovery_owner', None)
                    or getattr(member, 'quest_party_safe_return_active', False)
                    or getattr(member, 'post_combat_movement_active', False)
                    or getattr(member, 'quest_party_battle_rescue_active', False)):
                return rejected('member-state-changed')
            stage = 'loading'
            if await member.is_loading():
                return rejected('loading-or-battle', loading=True, battle=None)
            stage = 'battle'
            if await member.in_battle():
                return rejected('loading-or-battle', loading=False, battle=True)
            stage = 'zone'
            member_zone = await member.zone_name()
            if member_zone != zone:
                return rejected('zone-changed-during-capture', zone_before=zone, zone_after=member_zone)
            stage = 'live-area'
            if member is not quester and not await clients_share_live_area(quester, member):
                return rejected('member-state-changed')
        current = (quester, *getattr(quester, 'quest_party_hitters', ()))
        if session and session.confirmed and id(quester) not in session.exited:
            current = tuple(c for c in current if id(c) not in session.exited)
        stage = 'roster'
        if tuple(map(id, current)) != roster:
            return rejected('member-state-changed')
        stage = 'session'
        if dungeon_session_for(quester) is not session:
            return rejected('session-replaced')
        # Preserve the original eager list: even a failed earlier member must
        # not skip the later members' existing loading/zone reads.
        checks = []
        for member in members:
            stage = 'loading-recheck'
            loading = await member.is_loading()
            member_zone = None
            if not loading:
                stage = 'zone-recheck'
                member_zone = await member.zone_name()
            checks.append(not loading and member_zone == zone)
            if not checks[-1] and all(checks[:-1]):
                rejected('loading-or-battle' if loading else 'zone-changed-during-capture',
                    loading=loading, zone_before=zone, zone_after=member_zone)
        return all(checks)
    except Exception as exc:
        return rejected('zone-unreadable' if stage.startswith('zone') else 'member-state-changed',
                        **_dungeon_source_capture_error(exc))


async def remember_dungeon_transition(quester, target):
    """Retain this group's source entrance before movement/X can change rooms."""
    from copy import copy
    from wizwalker.errors import WizWalkerMemoryError
    from src.utils import observe_party_area, _party_area_token
    session = dungeon_session_for(quester)
    readiness = {}
    def rejected(stage, reason, **fields):
        _log_dungeon_source_capture(quester, session, stage, 'reject', reason, **fields)
        return False
    if not session:
        return rejected('preflight', 'session-unavailable')
    if session.quester is not quester:
        return rejected('preflight', 'quester-mismatch')
    if id(quester) in session.exited:
        return rejected('preflight', 'member-state-changed', check='quester-exited')
    if session.potion_areas:
        return rejected('preflight', 'member-state-changed', check='potion-areas')
    if not all(math.isfinite(v) for v in (target.x, target.y, target.z)):
        return rejected('preflight', 'target-invalid')
    if not await quest_party_members_ready(quester, _capture_diagnostic=readiness):
        return rejected('preflight', readiness.pop('reason', 'member-state-changed'), **readiness)
    stage, member, zone, task, token = 'zone', quester, None, None, None
    try:
        zone = await quester.zone_name()
        members = tuple(c for c in session.members if id(c) not in session.exited)
        tasks, tokens = {}, {}
        for member in members:
            task, token = None, None
            initial_hitter = member is not quester and not session.confirmed
            try:
                stage = 'observe-area'
                await observe_party_area(member)
                stage = 'task'
                task = await member.quest_id(), await member.goal_id()
                stage = 'area-token'
                token = await _party_area_token(member)
            except (WizWalkerMemoryError, ValueError, AttributeError, RuntimeError, TimeoutError) as exc:
                if initial_hitter:
                    continue  # Optional recovery evidence is not initial entry membership.
                return rejected(stage, {'task': 'task-unreadable', 'area-token': 'area-token-unreadable'}
                    .get(stage, 'area-observation-unreadable'), member=getattr(member, 'title', None), zone_before=zone,
                    task_before=task, token_before=token, **_dungeon_source_capture_error(exc))
            invalid = ('task-invalid' if type(task[0]) is not int
                or task[0] < (1 if member is quester else 0) or type(task[1]) is not int else
                'zone-invalid' if token[0] != zone else
                'area-token-invalid' if any(type(value) is not int or value <= 0 for value in token[1:3]) else None)
            if invalid:
                if initial_hitter:
                    continue
                return rejected('validate-member', invalid, member=getattr(member, 'title', None), task_before=task,
                                zone_before=zone, token_before=token)
            tasks[id(member)], tokens[id(member)] = task, token
        stage = 'session-recheck'
        if dungeon_session_for(quester) is not session:
            return rejected(stage, 'session-replaced', zone_before=zone)
        readiness = {}
        if not await quest_party_members_ready(quester, zone=zone, _capture_diagnostic=readiness):
            return rejected('members-recheck', readiness.pop('reason', 'member-state-changed'), **readiness)
        if tuple(c for c in session.members if id(c) not in session.exited) != members:
            return rejected('members-recheck', 'member-state-changed', check='active-members')
        for member in members:
            if member is not quester and not session.confirmed:
                continue
            stage = 'area-token-recheck'
            token = await _party_area_token(member)
            if token != tokens[id(member)]:
                zone_changed = isinstance(token, (tuple, list)) and token and token[0] != zone
                return rejected(stage, 'zone-changed-during-capture' if zone_changed else
                    'token-changed-during-capture', member=getattr(member, 'title', None), zone_before=zone,
                    token_before=tokens[id(member)], token_after=token)
            stage = 'task-recheck'
            task = await member.quest_id(), await member.goal_id()
            if task != tasks[id(member)]:
                return rejected(stage, 'task-changed-during-capture', member=getattr(member, 'title', None),
                    task_before=tasks[id(member)], task_after=task, zone_before=zone)
        stage = 'commit'
        state = session.transition
        if (state and state['source_zone'] == zone and state['source_tasks'] == tasks
                and state['source_tokens'] == tokens and time.monotonic() < state['deadline']
                and all(abs(a - b) <= 1 for a, b in zip(
                    (state['target'].x, state['target'].y, state['target'].z),
                    (target.x, target.y, target.z)))):
            _log_dungeon_source_capture(quester, session, stage, 'accept', 'stable-source-captured',
                task=tasks[id(quester)], zone=zone, token=tokens[id(quester)])
            return True  # Concurrent workers share the same release/recovery budget.
        session.transition = dict(source_zone=zone, target=copy(target),
            quester_task=tasks[id(quester)], source_tasks=tasks, source_tokens=tokens,
            deadline=time.monotonic() + 30.0, attempts={}, next_attempt_at={}, arrivals={})
        _log_dungeon_source_capture(quester, session, stage, 'accept', 'stable-source-captured',
            task=tasks[id(quester)], zone=zone, token=tokens[id(quester)])
        return True
    except Exception as exc:
        reason = ('task-unreadable' if stage.startswith('task') else
                  'area-token-unreadable' if stage.startswith('area-token') else
                  'zone-unreadable' if stage == 'zone' else
                  'area-observation-unreadable' if stage == 'observe-area' else 'capture-error')
        return rejected(stage, reason, member=getattr(member, 'title', None), zone_before=zone,
                        task_before=task, token_before=token, **_dungeon_source_capture_error(exc))


async def capture_dungeon_return(client):
    """Attach task/area context to the existing pre-combat coordinate caches."""
    from src.teleport_math import _movement_task_snapshot
    from src.utils import observe_party_area, _party_area_token
    session = dungeon_session_for(client)
    if not dungeon_member_inside(client):
        client._quest_party_safe_return = None
        return
    tokens = {}
    try:
        for member in (client, session.quester):
            await observe_party_area(member)
            token = await _party_area_token(member)
            if any(type(value) is not int or value <= 0 for value in token[1:3]):
                break
            tokens[id(member)] = token
    except Exception:
        tokens = {}
    client._quest_party_safe_return = (
        session, await client.zone_name(), await _movement_task_snapshot(session.quester), tokens)


async def dungeon_return_is_current(client):
    """Keep ordinary safe returns, but discard obsolete dungeon progress."""
    from src.teleport_math import _movement_task_snapshot, calc_Distance
    from src.utils import observe_party_area, _party_area_token
    saved = getattr(client, '_quest_party_safe_return', None)
    if saved is None:
        return not dungeon_member_inside(client)
    session, zone, task, tokens = saved
    try:
        await observe_dungeon_session(client)
        if (dungeon_session_for(client) is not session or not dungeon_member_inside(client)
                or await client.is_loading() or await client.zone_name() != zone
                or task is None or type(task[0]) is not int or task[0] <= 0
                or type(task[1]) is not int):
            return False
        for member in (client, session.quester):
            await observe_party_area(member)
            if (id(member) not in tokens or await member.is_loading()
                    or await _party_area_token(member) != tokens[id(member)]):
                return False
        current = await _movement_task_snapshot(session.quester)
        return (current is not None and current[:2] == task[:2] and current[3] == task[3]
                and all(math.isfinite(v) for v in (current[2].x, current[2].y, current[2].z))
                and calc_Distance(current[2], task[2]) <= 1.0)
    except Exception:
        return False


def friend_follow_retry_delay(failure_count: int) -> float:
    """Return bounded backoff for a consecutive friend-teleport failure."""
    index = max(0, min(int(failure_count) - 1, len(FOLLOW_RETRY_DELAYS) - 1))
    return FOLLOW_RETRY_DELAYS[index]


def _clean(value) -> str:
    return str(value or "").strip().casefold()


def stable_client_identity(client) -> str:
    """Return the best persistent identity available for a hooked client."""
    nickname = _clean(getattr(client, "account_nick", None))
    if nickname:
        return f"account:{nickname}"
    gid = getattr(client, "player_gid", None)
    if gid:
        return f"gid:{gid}"
    return f"title:{_clean(getattr(client, 'title', ''))}"


def client_identity_aliases(client) -> set[str]:
    """Accept new persistent IDs and legacy p-title settings during migration."""
    title = _clean(getattr(client, "title", ""))
    nickname = _clean(getattr(client, "account_nick", None))
    gid = getattr(client, "player_gid", None)
    aliases = {title, f"title:{title}"} if title else set()
    if nickname:
        aliases.update({nickname, f"account:{nickname}"})
    if gid:
        aliases.update({str(gid).casefold(), f"gid:{gid}".casefold()})
    return aliases


def resolve_quester_friend_icon(
    client, configured_icons: Mapping[str, object] | None
) -> tuple[int, int] | None:
    """Resolve an optional friend-list icon fallback for a questing client."""
    normalized = {
        _clean(identity): value
        for identity, value in (configured_icons or {}).items()
        if _clean(identity)
    }
    raw = next(
        (
            normalized[alias]
            for alias in client_identity_aliases(client)
            if alias in normalized
        ),
        None,
    )
    if not isinstance(raw, Mapping):
        return None
    try:
        icon_list = int(raw.get("icon_list"))
        icon_index = int(raw.get("icon_index"))
    except (TypeError, ValueError):
        return None
    if icon_list not in (1, 2) or icon_index < 0:
        return None
    return icon_list, icon_index


def _normalized(values: Iterable[str] | None) -> set[str]:
    return {_clean(value) for value in (values or []) if _clean(value)}


def _find_selected(clients: list[ClientT], configured: set[str]) -> list[ClientT]:
    return [
        client
        for client in clients
        if configured.intersection(client_identity_aliases(client))
    ]


def resolve_quest_party(
    clients: Sequence[ClientT],
    *,
    enabled: bool,
    quester_titles: Iterable[str] | None,
    hitter_titles: Iterable[str] | None,
    assignment_mode: str = "auto",
    manual_assignments: Mapping[str, str] | None = None,
) -> QuestParty:
    """Resolve persistent role selections against currently injected clients.

    Old p-title settings remain valid. New settings prefer a GID, then the saved
    account nickname, so reconnecting in another order does not swap roles.
    """
    live_clients = list(clients)
    if not enabled:
        return QuestParty(live_clients, [], [], [])

    quester_keys = _normalized(quester_titles)
    hitter_keys = _normalized(hitter_titles) - quester_keys
    questers = _find_selected(live_clients, quester_keys)
    hitters = [
        client
        for client in _find_selected(live_clients, hitter_keys)
        if client not in questers
    ]
    selected_ids = {id(client) for client in questers + hitters}
    idle = [client for client in live_clients if id(client) not in selected_ids]

    assignments: list[tuple[ClientT, ClientT]] = []
    normalized_manual = {
        _clean(hitter_id): _clean(quester_id)
        for hitter_id, quester_id in (manual_assignments or {}).items()
        if _clean(hitter_id) and _clean(quester_id)
    }
    for index, hitter in enumerate(hitters):
        quester = None
        if assignment_mode == "manual":
            target_keys = {
                normalized_manual[alias]
                for alias in client_identity_aliases(hitter)
                if alias in normalized_manual
            }
            quester = next(
                (
                    candidate
                    for candidate in questers
                    if target_keys.intersection(client_identity_aliases(candidate))
                ),
                None,
            )
        if quester is None and questers:
            quester = questers[index % len(questers)]
        if quester is not None:
            assignments.append((hitter, quester))

    return QuestParty(questers, hitters, idle, assignments)
