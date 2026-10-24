"""A bounded object probe after an observed, completed current-task approach.

This never supplies ordinary interaction eligibility or dungeon entry proof.
The existing semantic/strict paths always get first refusal.
"""
import asyncio
from copy import copy
import math
import re
import time
from functools import lru_cache

from loguru import logger
from wizwalker import Keycode, HookNotActive
from wizwalker.errors import WizWalkerMemoryError, ExceptionalTimeout
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.interaction_prompts import (plain_text, task_object_prompt, quest_has_action, interaction_kind,
    _INTERACTION_IDS, _prompt_form)
from src.game_text_catalog import TEXT_RECORDS
from src.game_text_snapshot import snapshot_for_client,text_values
from src.paths import (team_up_button_path, team_up_wait_path, team_up_confirm_path,
    dialog_text_path, advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
    dungeon_warning_path)
from src.teleport_math import (_movement_task_snapshot, _same_movement_task, _movement_position,
    _movement_control_changed, _target_approach_context, _current_target_approach,
    _movement_failure_blocks_interaction, _dedicated_collect_objective, calc_Distance)
from src.utils import is_visible_by_path


_READ_ERRORS = (WizWalkerMemoryError, ExceptionalTimeout, HookNotActive, ValueError, AttributeError)
_ACK_SECONDS = 3.0
_LIFETIME = 10.0
_BUSY_FLAGS = ('refilling_potions', 'quest_recovery_owner', 'quest_party_safe_return_active',
    'post_combat_movement_active', 'feeding_pet_status', 'entity_detect_combat_status',
    '_quest_dialogue_update_pending')

# Reuse the existing Talk labels as an exclusion, never as a generic-X synonym.
_DEDICATED_ENGLISH = set()
for _kind in ('talk', 'enter', 'ride', 'teleport', 'climb', 'collect'):
    for _text_id in _INTERACTION_IDS.get(_kind, ()):
        _english, _chinese = TEXT_RECORDS.get(_text_id, ('', ''))
        if ' to ' in _english:
            _DEDICATED_ENGLISH.add(_english.split(' to ', 1)[1].strip())


@lru_cache(maxsize=None)
def _dedicated_forms(snapshot):
    return frozenset(label for kind in ('talk','enter','ride','teleport','climb','collect')
        for text_id in _INTERACTION_IDS.get(kind,()) for value in snapshot.values(text_id)[-1:] if value
        if (label:=re.sub(r'^press(?:or|或)?(?:to)?','',_prompt_form(value))))


def start_navigation(client, target, task, origin, control):
    """Scope optional endpoint observations to this particular quester operation."""
    client._quest_probe_navigation = dict(task=task, target=copy(target), origin=copy(origin),
        control=control, context=_target_approach_context(client, client), active=True, terminal=None)


async def record_endpoint(client, target, task, control, endpoint, kind):
    """Called only after the navigator verifies arrival and no safe closer endpoint."""
    nav = getattr(client, '_quest_probe_navigation', None)
    if not (isinstance(nav, dict) and nav['active'] and _same_movement_task(nav['task'], task)
            and calc_Distance(nav['target'], target) <= 1 and nav['control'] == control):
        return
    try:
        pos = await _movement_position(client)
        if (not all(math.isfinite(v) for v in (*pos, *target, *endpoint, *nav['origin']))
                or calc_Distance(pos, endpoint) > 3
                or calc_Distance(nav['origin'], pos) <= 5
                or calc_Distance(pos, target) >= calc_Distance(nav['origin'], target)-5
                or not client.questing_status or await client.is_loading() or await client.in_battle()
                or await _movement_control_changed(client, control)
                or not _same_movement_task(task, await _movement_task_snapshot(client))
                or nav['context'] != _target_approach_context(client, client)
                or getattr(client, '_quest_probe_navigation', None) is not nav):
            return
        nav['terminal'] = dict(position=copy(pos), endpoint=copy(endpoint), kind=kind,
            observed_at=asyncio.get_running_loop().time())
    except _READ_ERRORS:
        return  # Missing movement evidence cannot authorize a probe.


def object_candidate(objective, title, action, *, client=None, task_client=None):
    # Classification reuses the existing object grammar. No new verb synonyms,
    # task names or map exceptions; unclassified actions remain ineligible.
    text = plain_text(objective)
    first_word = text.split(' ', 1)[0]
    source=task_client or client
    dedicated_goal = (interaction_kind(f'Press X to {first_word}',client=source) in
        ('talk', 'enter', 'ride', 'teleport', 'climb', 'collect')
        or any(re.match(re.escape(prefix)+(r'\b' if prefix.isascii() else ''), text, re.I)
            for prefix in (*text_values('WizardQuestGoals_TalkNPC',source), *_DEDICATED_ENGLISH) if prefix)
        or any(_prompt_form(text).startswith(prefix) for prefix in _dedicated_forms(snapshot_for_client(source))))
    return bool(task_object_prompt(title, action,client=client) and not dedicated_goal
        and not _dedicated_collect_objective(objective,client=source)
        and not any(quest_has_action(objective, kind,client=source) for kind in ('defeat', 'photomance', 'pet_mode'))
        and not re.match(r"^(?:do\s+not\b|don't\b|never\b|不要|禁止)", plain_text(objective), re.I)
        and re.search(r'\bX\b|[Xx][键鍵]|<(?:icon|image);X>|&(?:InputBindings_NPCInteract|Icons_XKey)&',
                      str(action), re.I))


async def _unique_popup(client):
    windows = await client.root_window.get_windows_with_name('NPCRangeWin')
    visible = [w for w in windows if await w.is_visible()]
    if len(visible) != 1:
        return None
    address = await visible[0].read_base_address()
    return address if type(address) is int and address > 0 else None


async def _safe(quester, state):
    client = quester.client
    owner = get_client_automation_ownership(client)
    if (owner is not state['owner'] or owner.locked and owner._owner_task is not asyncio.current_task()
            or getattr(client, 'quest_party_quester', None) is not None
            or not client.questing_status or await client.is_loading() or await client.in_battle()
            or any(getattr(client, flag, False) for flag in _BUSY_FLAGS)
            or await _movement_control_changed(client, state['control'])):
        return False
    for path in (dialog_text_path, advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                 team_up_button_path, team_up_wait_path, team_up_confirm_path, dungeon_warning_path):
        if await is_visible_by_path(client, path):
            return False
    return (state['context'] == _target_approach_context(client, client)
        and getattr(client, '_quest_probe_navigation', None) is state['navigation'])


async def _frame(quester, state):
    """Read task, progress, one ordinary prompt and physical endpoint, then reread."""
    client = quester.client
    if not await _safe(quester, state):
        return None
    task = await _movement_task_snapshot(client)
    progress = await quester._dungeon_quest_snapshot(client)
    title = await quester._quest_interaction_signature(client)
    raw = await quester.read_popup(client)
    popup = await _unique_popup(client)
    position = await _movement_position(client)
    if (task is None or progress is None or title is None or popup is None
            or progress[:2] != task[:2] or not object_candidate(progress[2], title[0], raw,client=client)
            or not _same_movement_task(state['task'], task)
            or calc_Distance(position, state['terminal']['position']) > 3
            or not all(math.isfinite(v) for v in position)
            or await _movement_failure_blocks_interaction(client, state['target'], task, pos=position)
            or not _same_movement_task(task, await _movement_task_snapshot(client))
            or await quester._dungeon_quest_snapshot(client) != progress
            or await quester._quest_interaction_signature(client) != title
            or await quester.read_popup(client) != raw or await _unique_popup(client) != popup
            or not await _safe(quester, state)):
        return None
    # Slow popup/owner reads precede the final task and physical-position reads.
    # Those reads may have crossed a goal update or a late server bounce.
    final_position = await _movement_position(client)
    if (calc_Distance(final_position, state['terminal']['position']) > 3
            or not all(math.isfinite(v) for v in final_position)
            or not await _safe(quester, state)
            or not _same_movement_task(task, await _movement_task_snapshot(client))
            or await quester._dungeon_quest_snapshot(client) != progress
            or getattr(client, '_quest_movement_control', None) != state['control']
            or get_client_automation_ownership(client) is not state['owner']
            or state['owner'].locked and state['owner']._owner_task is not asyncio.current_task()
            or not client.questing_status or any(getattr(client, flag, False) for flag in _BUSY_FLAGS)
            or state['context'] != _target_approach_context(client, client)):
        return None
    return (task[0], task[1], tuple(task[2]), task[3], plain_text(progress[2]), title, raw, popup)


async def offer_probe(quester, target, task):
    """Capture the existing measured approach only when normal paths rejected it."""
    client = quester.client
    nav = getattr(client, '_quest_probe_navigation', None)
    if not isinstance(nav, dict) or not nav.get('active') or nav.get('terminal') is None:
        return False
    approach = _current_target_approach(client, client, target, task, nav['control'])
    if (approach is None or nav['context'] != approach.get('context')
            or not _same_movement_task(nav['task'], task)
            or asyncio.get_running_loop().time()-nav['terminal']['observed_at'] > 5):
        return False
    try:
        if (await quester._interaction_eligibility(client, target, task)).accepted:
            return False
        state = dict(task=task, target=copy(target), context=nav['context'], control=nav['control'],
            navigation=nav, terminal=nav['terminal'], approach=dict(approach),
            owner=get_client_automation_ownership(client), attempts=0, status='prepared', runner=None,
            deadline=time.monotonic()+_LIFETIME)
        first = await _frame(quester, state)
        if first is None:
            return False
        await asyncio.sleep(.1)
        if await _frame(quester, state) != first:
            return False
        retry = await quester._quest_interaction_retry_state(client, target, task_snapshot=task)
        old = retry.get('probe')
        if (old is not None and old.get('attempts', 0)
                and not (old['status'] == 'success' and old['frame'][4] != first[4])):
            return False  # A new navigation/worker cannot replenish an unchanged task's input budget.
        state['frame'] = first
        retry['probe'] = state
        logger.debug(f'[quest_probe] {client.title}: phase=qualified endpoint={nav["terminal"]["kind"]}')
        return True
    except _READ_ERRORS:
        return False


async def _qualify(quester, state):
    if time.monotonic() >= state['deadline']:
        return False
    # The caller's semantic and 5u strict paths keep priority on every attempt.
    if (await quester._interaction_eligibility(quester.client, state['target'], state['task'])).accepted:
        return False
    first = await _frame(quester, state)
    if first != state['frame']:
        return False
    await asyncio.sleep(.1)
    return await _frame(quester, state) == first and time.monotonic() < state['deadline']


async def _wait_ack(quester, state):
    client = quester.client
    until = min(state['deadline'], time.monotonic()+_ACK_SECONDS)
    while time.monotonic() < until:
        if not await _safe(quester, state):
            return 'cancelled'
        task = await _movement_task_snapshot(client)
        progress = await quester._dungeon_quest_snapshot(client)
        if task is None or progress is None or progress[:2] != task[:2] or task[3] != state['task'][3]:
            return 'cancelled'
        before = (state['frame'][0], state['frame'][1], state['frame'][4])
        current = (progress[0], progress[1], plain_text(progress[2]))
        if current != before:
            await asyncio.sleep(.1)
            fresh = await quester._dungeon_quest_snapshot(client)
            final_task = await _movement_task_snapshot(client)
            if (fresh is not None and (fresh[0], fresh[1], plain_text(fresh[2])) == current
                    and _same_movement_task(task, final_task) and await _safe(quester, state)):
                return 'success'
            return 'cancelled'
        # Disappearing/changing popup alone is never quest acknowledgement.
        if not _same_movement_task(state['task'], task) or await _frame(quester, state) != state['frame']:
            return 'cancelled'
        await asyncio.sleep(.2)
    return 'unconfirmed'


async def run_probe(quester, target, task):
    """Return None when there is no probe; otherwise consume this task's finite round."""
    client = quester.client
    retry = getattr(client, '_quest_interaction_retry', None)
    state = retry.get('probe') if isinstance(retry, dict) else None
    if state is None:
        return None
    if not _same_movement_task(state['task'], task) or calc_Distance(state['target'], target) > 1:
        state['status'] = 'cancelled'
        return None
    if state['runner'] is not None:
        return state['status']
    if state['status'] in ('blocked', 'cancelled', 'success'):
        return None  # Retain the probe's spent budget, without suppressing existing handlers.
    state['runner'] = asyncio.current_task()  # Reserve the runner before any awaited ownership/read.
    try:
        async with asyncio.timeout(_LIFETIME):
            while state['attempts'] < 2:
                if not await _qualify(quester, state):
                    state['status'] = 'cancelled'
                    break
                owner = get_client_automation_ownership(client)
                if owner.locked and owner._owner_task is not asyncio.current_task():
                    state['status'] = 'cancelled'
                    break
                async with automation_owner(client, 'quest-probe'):
                    if not await _qualify(quester, state):
                        state['status'] = 'cancelled'
                        break
                    state['attempts'] += 1
                    state['status'] = 'waiting'
                    logger.debug(f'[quest_probe] {client.title}: phase=send attempt={state["attempts"]}/2')
                    await client.send_key(Keycode.X, .1)
                outcome = await _wait_ack(quester, state)
                if outcome != 'unconfirmed':
                    state['status'] = outcome
                    break
            else:
                state['status'] = 'blocked'
    except asyncio.CancelledError:
        state['status'] = 'cancelled'
        raise
    except (asyncio.TimeoutError, *_READ_ERRORS):
        state['status'] = 'cancelled'
    finally:
        if state['status'] == 'waiting':
            state['status'] = 'cancelled'  # Including cancellation after the game received X.
        state['runner'] = None
    logger.debug(f'[quest_probe] {client.title}: phase={state["status"]} attempts={state["attempts"]}')
    return state['status']
