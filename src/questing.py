from src.task_lifecycle import gather_owned
import asyncio
import time
import traceback
import math

from loguru import logger

import re
from src.auto_pet import auto_pet
from src.interaction_prompts import (
    is_dungeon_entry_prompt, interaction_kind, quest_has_action, quest_interaction_matches,
    split_quest_location, collect_object_name, portal_kind, resolve_portal_destination,
    plain_text,
)
from src.teleport_math import *
from wizwalker import XYZ, Orient, Keycode, MemoryReadError, Client, Rectangle, HookAlreadyActivated, HookNotActive
from wizwalker.file_readers.wad import Wad
from wizwalker.memory import DynamicClientObject
from wizwalker.memory.memory_objects.enums import WindowFlags
from wizwalker.extensions.scripting import teleport_to_friend_from_list
from src.sprinty_client import SprintyClient
from src.utils import *
from src.paths import *
from src.collecting import collect_one
from src.script_popups import close_automation_popup
from src.window_text import read_control_text
from src.automation_ownership import automation_owner
from thefuzz import fuzz


def claim_quest_recovery(client: Client, owner: str) -> bool:
    """One quest recovery may control a client at a time on the asyncio loop."""
    if (getattr(client, 'refilling_potions', False)
            or isinstance(getattr(client, "quest_recovery_owner", None), str)):
        return False
    client.quest_recovery_owner = owner
    return True


def release_quest_recovery(client: Client, owner: str) -> None:
    if getattr(client, "quest_recovery_owner", None) == owner:
        client.quest_recovery_owner = None


class Quester():
    GUMMY_WORMS_PHOTO_ZONE = 'Karamelle/Interiors/KM_Z04_BonBon'
    GUMMY_WORMS_PHOTO_POSITION = XYZ(1258.114, 2119.140, 2.000)
    GUMMY_WORMS_PHOTO_ORIENTATION = Orient(0.000, 0.000, 1.590)
    GIANT_VAT_PHOTO_POSITION = XYZ(48.393, 187.929, 2.000)
    GIANT_VAT_PHOTO_ORIENTATIONS = (
        Orient(0.000, 0.000, 3.855), Orient(0.000, 0.000, 3.146),
    )
    DUNGEON_NO_PROGRESS_SECONDS = 180.0
    MAINLINE_FINDER_STABLE_READS = 3
    MAINLINE_FINDER_RETRY_SECONDS = 60.0
    MAINLINE_FINDER_MAX_PAGES = 32
    NIGHTMARE_ZONE = "Empyrea/Interiors/EM_Z15_NightmareKrok"
    PRIVATE_WING_ZONE = "Empyrea/Interiors/EM_Z07_PrivateWing"
    PRIVATE_WING_EXIT = XYZ(20.521, 11529.802, 2.052)
    PRIVATE_WING_STABLE_SECONDS = 1.5
    GOBBLERTON_ZONE = "Karamelle/KM_Z10_Gobblerton"
    GOBBLERTON_EXIT = XYZ(874.868, 5917.971, 800.977)
    LEMURIA_DUNGEON_ZONE = 'Lemuria/Interiors/LM_Z05_I01_Dungeon'
    LEMURIA_DUNGEON_EXIT = XYZ(-1818.267, -6764.240, 2.001)
    SACRED_YARN_ZONE = 'MooShu/Interiors/MS_CAT_SacredYarnTemple'
    SACRED_YARN_EXIT = XYZ(6.900, 2767.551, -2.704)
    TAMARIN_HOUSE_ZONE = 'Lemuria/Interiors/LM_Z07_TamarinHouse'
    TAMARIN_HOUSE_EXIT = XYZ(-1371.641, 1631.124, 1.000)
    TAMARIN_HOUSE_ARRIVAL = XYZ(9.903, -424.032, 1.000)
    BUMBLES_MIND_ZONE = 'Lemuria/Interiors/LM_Z07_BumblesMind'
    BUMBLES_MIND_BATTLE = XYZ(2.390, -259.991, -1124.726)
    DUELING_TENT_ZONE = 'Novus/Interiors/NV_Z01_DuelingTent'
    DUELING_TENT_EXIT = XYZ(16.146, -1194.045, -4.171)
    BUMBLES_PET_ZONE = 'Lemuria/LM_Z07_Heap'
    BUMBLES_PET_POSITION = XYZ(14017.3837890625, -563.506591796875, -2051.80712890625)
    LEMURIA_DUNGEON_ROOMS = (
        LEMURIA_DUNGEON_ZONE,
        'Lemuria/Interiors/LM_Z05_I02_Cave',
    )
    LEMURIA_NAVIGATION_HUB = 'Lemuria/LM_Z00_Hub'
    # Only the public main maps; never infer END safety from a world prefix.
    LEMURIA_NAVIGATION_AREAS = frozenset({
        'Lemuria/LM_Z00_Hub', 'Lemuria/LM_Z01_WildLands',
        'Lemuria/LM_Z02_UrsaiVillage', 'Lemuria/LM_Z03_NightForest',
        'Lemuria/LM_Z04_Badlands', 'Lemuria/LM_Z05_Mandoria',
        'Lemuria/LM_Z06_SkyCity', 'Lemuria/LM_Z07_Heap',
    })
    LEMURIA_NAVIGATION_CONFIRM_SECONDS = 45.0
    LEMURIA_NAVIGATION_FALLBACK_SECONDS = 300.0
    LEMURIA_NAVIGATION_COOLDOWN_SECONDS = 60.0
    # Stay below the existing 120-second stationary-task watchdog.
    NIGHTMARE_NO_PROGRESS_SECONDS = 60.0
    NIGHTMARE_POINTS = (
        XYZ(762.465, 4769.614, 152.062),
        XYZ(-1471.999, 3207.999, 87.999),
        XYZ(-1637.558, 1386.909, 120.300),
        XYZ(-117.739, -198.734, 84.204),
        XYZ(1545.715, 1442.725, 88.246),
        XYZ(1890.277, 2671.811, 165.886),
        XYZ(-180.542, 4028.970, 83.271),
        XYZ(-912.558, 2125.669, 82.704),
        XYZ(-633.743, 1097.940, 79.690),
        XYZ(359.133, 1289.478, 79.696),
        XYZ(595.140, 3042.207, 79.694),
        XYZ(134.281, 2532.948, 79.698),
    )
    NIGHTMARE_EXIT = XYZ(16.766, 6608.310, 618.730)
    TRIGGER_NEAR_DISTANCE = 350.0
    TRIGGER_AWAY_DISTANCE = 900.0
    TRIGGER_MAX_ATTEMPTS = 2
    TRIGGER_STABLE_OBSERVATIONS = 3

    def __init__(self, client: Client, clients: list[Client], leader_pid: int):
        self.client = client
        self.clients = clients
        self.leader_pid = leader_pid
        self.current_leader_client = client
        self.current_leader_pid = leader_pid
        self.d_location = None
        self._krok_exit_watch = {}
        self._npc_retry_exhausted = {}
        self._npc_retry_debug_at = {}
        self._trigger_reentry = {}
        self._mainline_finder_observations = {}
        self._mainline_finder_retry_at = {}

    async def _confirm_dungeon_entry(self, client: Client, previous_zone: str) -> None:
        """Arm recovery only after an entry prompt caused a real zone change."""
        current_zone = await client.zone_name()
        if current_zone and previous_zone and current_zone != previous_zone:
            client.quest_dungeon_recovery = {
                "zone": current_zone, "snapshot": None, "since": None,
                "active": False, "attempted": False, "waiting_logged": False,
            }

    async def _dungeon_quest_snapshot(self, client: Client):
        """Use the same live quest identifiers and HUD goal as NPC retry."""
        try:
            quest_id = await client.quest_id()
            goal_id = await client.goal_id()
            goal_text = await self.read_quest_txt(client)
        except Exception as exc:
            logger.debug(f"Client {client.title} - cannot read dungeon quest progress: {exc}")
            return None
        if not isinstance(quest_id, int) or quest_id <= 0 or not goal_text:
            return None
        return quest_id, goal_id, goal_text

    async def _dungeon_recovery_blocked(self, client: Client) -> bool:
        if (not client.questing_status
                or getattr(client, 'bumbles_pet_pending', False) is True
                or isinstance(getattr(client, "quest_recovery_owner", None), str)
                or getattr(client, "quest_party_probe_pending", False)
                or getattr(client, "quest_party_battle_rescue_active", False)
                or getattr(client, "quest_party_quest_worker_restart_requested", False)
                or getattr(client, "post_combat_movement_active", False)):
            return True
        if not await is_free_leader_questing(client):
            return True
        if (await is_spiral_door_open(client)
                or await is_visible_by_path(client, exit_dungeon_path)
                or await is_visible_by_path(client, dungeon_warning_path)
                or await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                or await is_visible_by_path(client, npc_range_path)
                or await is_visible_by_path(client, missing_area_path)
                or await is_visible_by_path(client, all_quests_sort_button_path)):
            return True
        return False

    async def _refresh_dungeon_quest(self, client: Client) -> bool:
        """Select the first quest card via the existing questbook UI tree."""
        opened = False
        try:
            await client.send_key(Keycode.Q)
            opened = True
            deadline = time.monotonic() + 3.0
            first_card = [*quest_buttons_parent_path, "wndQuestInfo0", "questInfoWindow", "wndQuestInfo", "txtGoal"]
            while time.monotonic() < deadline:
                if (await is_visible_by_path(client, all_quests_sort_button_path)
                        and await is_visible_by_path(client, first_card)):
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning(f"Client {client.title} - task menu or first quest card did not appear.")
                return False
            await asyncio.sleep(0.3)
            if await self._dungeon_recovery_blocked_for_open_menu(client):
                return False
            await click_window_by_path(client, first_card)
            await asyncio.sleep(0.5)
            return True
        finally:
            if opened and await is_visible_by_path(client, all_quests_sort_button_path):
                await client.send_key(Keycode.Q)

    async def _dungeon_recovery_blocked_for_open_menu(self, client: Client) -> bool:
        """The questbook itself is expected here, but combat/transition is not."""
        return (not client.questing_status
                or getattr(client, "quest_party_probe_pending", False)
                or getattr(client, "quest_party_battle_rescue_active", False)
                or await client.is_loading() or await client.in_battle()
                or await is_visible_by_path(client, advance_dialog_path)
                or await is_visible_by_path(client, exit_dungeon_path))

    def _note_dungeon_recovery_wait(self, client: Client) -> None:
        state = getattr(client, "quest_dungeon_recovery", None)
        if (isinstance(state, dict) and state.get("since") is not None
                and not state.get("waiting_logged")
                and time.monotonic() - state["since"] >= self.DUNGEON_NO_PROGRESS_SECONDS):
            logger.info("自动任务：无进展恢复等待当前关键状态结束。")
            state["waiting_logged"] = True

    async def _maybe_refresh_stalled_dungeon_quest(self, client: Client) -> bool:
        """Return true when this iteration was consumed by questbook recovery."""
        state = getattr(client, "quest_dungeon_recovery", None)
        if not isinstance(state, dict) or state.get("active"):
            return False
        zone = await client.zone_name()
        if not zone or zone != state["zone"]:
            if zone and (zone == getattr(client, "quest_party_group_dungeon_zone", None)
                         or zone in self.LEMURIA_DUNGEON_ROOMS):
                # The existing party probe confirmed this next dungeon room.
                state.update(zone=zone, snapshot=None, since=None,
                             attempted=False, waiting_logged=False)
            else:
                # A different room is not proof that we are still in the instance.
                client.quest_dungeon_recovery = None
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None:
            state["since"] = None
            state["snapshot"] = None
            return False
        now = time.monotonic()
        if snapshot != state["snapshot"] or state["since"] is None:
            state.update(snapshot=snapshot, since=now, attempted=False, waiting_logged=False)
            return False
        if now - state["since"] < self.DUNGEON_NO_PROGRESS_SECONDS:
            return False
        if await self._dungeon_recovery_blocked(client):
            self._note_dungeon_recovery_wait(client)
            return False
        # Re-read after the priority checks, before taking over the UI.
        current_snapshot = await self._dungeon_quest_snapshot(client)
        if current_snapshot is None or current_snapshot != snapshot:
            state.update(snapshot=current_snapshot, since=now if current_snapshot else None,
                         attempted=False, waiting_logged=False)
            return False
        if not claim_quest_recovery(client, "dungeon_quest"):
            return False
        state["active"] = True
        logger.info("自动任务：地牢内连续 3 分钟无任务进展，尝试重新选择当前任务。")
        try:
            refreshed = await self._refresh_dungeon_quest(client)
            if refreshed:
                logger.info("自动任务：任务追踪已刷新，继续任务传送。")
            return True
        except Exception as exc:
            logger.warning(f"Client {client.title} - dungeon quest refresh failed: {exc}")
            return True
        finally:
            state.update(since=time.monotonic(), active=False, attempted=True,
                         waiting_logged=False)
            try:
                state["snapshot"] = await self._dungeon_quest_snapshot(client)
            finally:
                release_quest_recovery(client, "dungeon_quest")

    async def _lemuria_navigation_sample(self, client: Client):
        """Tri-state navigation evidence, never mistake a stale XYZ for proof."""
        identity = await self._mainline_identity(client)
        if (identity is None or identity[3] is None or identity[4] is not True
                or identity[3]['world'].split('(')[0].strip().casefold() != 'lemuria'):
            return None
        try:
            quest = (await (await client.quest_manager()).quest_data()).get(identity[0])
            goal_id = await client.goal_id()
            goal = (await quest.goal_data()).get(goal_id)
            if (goal is None or await quest.permit_quest_helper() is not True
                    or await goal.no_quest_helper() is not False
                    or await quest.pet_only_quest() is not False
                    or await goal.pet_only_quest() is not False):
                return None
            goal_type = await goal.goal_type()
            kind = getattr(goal_type, 'value', None)
            destination = await goal.goal_destination_zone()
            code = await goal.name_lang_key()
            text = plain_text(await self.read_quest_txt(client))
            # Require an ordinary, rendered navigation stage. Photo/minigame,
            # defeat/collect and unknown/no-helper goals belong to their normal
            # handlers even when the arrow is intentionally absent.
            if (not destination or not code or not text
                    or quest_has_action(text, 'photomance')
                    or not ((kind == 4 and quest_has_action(text, 'talk'))
                            or (kind == 5 and quest_has_action(text, 'explore'))
                            or (kind == 8 and quest_has_action(text, 'use')))):
                return None
            if (await client.quest_id() != identity[0] or await client.goal_id() != goal_id):
                return None
        except Exception:
            return None
        target = None
        try:
            value = await client.quest_position.position()
            if (all(math.isfinite(v) for v in (value.x, value.y, value.z))
                    and calc_Distance(value, XYZ(0, 0, 0)) > 1):
                target = value
        except Exception:
            pass
        mode = 'fallback' if target is None else 'unknown'
        try:
            hud = await get_window_from_path(client.root_window, quest_helper_hud_path)
            arrow = await get_window_from_path(client.root_window, quest_helper_arrow_path)
            distance = await get_window_from_path(client.root_window, quest_helper_distance_path)
            if hud is not None and arrow is not None and distance is not None:
                # A hidden whole HUD can be a deliberate user setting, not a
                # broken target; do not send END to repair that setting.
                if not await hud.is_visible():
                    return None
                arrow_visible = await arrow.is_visible()
                distance_text = plain_text(await distance.maybe_text())
                has_distance = bool(re.search(r'\d', distance_text))
                if arrow_visible and has_distance and target is not None:
                    mode = 'valid'
                elif not arrow_visible and not has_distance:
                    mode = 'missing'
                elif target is None:
                    mode = 'fallback'
            # Missing controls are unknown UI evidence, not proof of loss.
        except Exception:
            pass
        if mode == 'missing' and target is not None:
            try:
                if calc_Distance(await client.body.position(), target) < 150:
                    return None  # ordinary arrival/interaction, not lost guidance
            except Exception:
                return None
        return {'key': (identity[0], goal_id, code, destination, text),
                'mode': mode, 'target': target}

    async def _lemuria_navigation_blocked(self, client: Client, zone: str, owner=None):
        if (zone not in self.LEMURIA_NAVIGATION_AREAS
                or getattr(client, 'bumbles_pet_pending', False) is True
                or getattr(client, 'in_solo_zone', False)
                or potion_dungeon_return_required(client, zone)
                or getattr(client, 'refilling_potions', False)
                or isinstance(getattr(client, 'quest_dialogue_settle', None), dict)):
            return True
        return await self._trigger_reentry_blocked(client, owner)

    async def _maybe_recover_lemuria_navigation(self, client: Client) -> bool:
        owner = 'lemuria_navigation'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        state = getattr(client, 'quest_lemuria_navigation_recovery', None)
        zone = await client.zone_name()
        if (not getattr(client, 'questing_status', False) or not zone.startswith('Lemuria/')
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                or zone not in self.LEMURIA_NAVIGATION_AREAS
                or getattr(client, 'in_solo_zone', False)
                or potion_dungeon_return_required(client, zone)):
            client.quest_lemuria_navigation_recovery = None
            client.lemuria_navigation_pending = False
            return False
        if await self._lemuria_navigation_blocked(client, zone):
            if isinstance(state, dict):
                state['since'] = None
            client.lemuria_navigation_pending = bool(isinstance(state, dict) and state.get('holding'))
            return bool(isinstance(state, dict) and state.get('holding'))
        sample = await self._lemuria_navigation_sample(client)
        now = time.monotonic()
        if sample is None or sample['mode'] == 'unknown':
            if isinstance(state, dict) and state.get('holding'):
                try:
                    if (await client.quest_id(), await client.goal_id()) == state['key'][:2]:
                        return True  # unreadable guidance is not restored guidance
                except Exception:
                    return True
            client.quest_lemuria_navigation_recovery = None
            client.lemuria_navigation_pending = False
            return False  # unknown mainline/stage belongs to existing recovery
        if sample['mode'] == 'valid':
            if isinstance(state, dict) and state.get('holding'):
                if state['key'] == sample['key']:
                    if state.get('restored_since') is None:
                        state['restored_since'] = now
                        return True
                    if now - state['restored_since'] < 1.5:
                        return True
                logger.info('自动任务：任务助手导航已恢复，继续任务传送。')
                for member in getattr(client, 'quest_mainline_sync_members', [client]):
                    member.quest_mainline_sync_state = None
                client.quest_party_quest_worker_zone = zone
                client.quest_party_probe_pending = bool(getattr(client, 'quest_party_hitters', []))
            client.quest_lemuria_navigation_recovery = None
            client.lemuria_navigation_pending = False
            return False
        if not isinstance(state, dict) or state['key'] != sample['key'] or state['zone'] != zone:
            holding = bool(isinstance(state, dict) and state.get('holding')
                           and state['key'] == sample['key'])
            state = {'key': sample['key'], 'zone': zone, 'mode': sample['mode'],
                     'since': now, 'holding': holding}
            client.quest_lemuria_navigation_recovery = state
        elif state['since'] is None or state['mode'] != sample['mode']:
            state.update(since=now, mode=sample['mode'])
        state['restored_since'] = None
        client.lemuria_navigation_pending = True
        counts = getattr(client, '_lemuria_navigation_attempts', None)
        if not isinstance(counts, dict):
            client._lemuria_navigation_attempts = counts = {}
        quest_id = sample['key'][0]
        if counts.get(quest_id, 0) >= 2:
            state['holding'] = True
            return True
        if now < getattr(client, '_lemuria_navigation_retry_at', 0):
            return state['holding']
        threshold = (self.LEMURIA_NAVIGATION_CONFIRM_SECONDS if sample['mode'] == 'missing'
                     else self.LEMURIA_NAVIGATION_FALLBACK_SECONDS)
        if not state['holding'] and now - state['since'] < threshold:
            return False
        # Reserve the existing lock, then re-read all evidence before END.
        if not claim_quest_recovery(client, owner):
            return state['holding']
        try:
            if await self._lemuria_navigation_blocked(client, zone, owner):
                return state['holding']
            current = await self._lemuria_navigation_sample(client)
            if (current is None or current['key'] != sample['key']
                    or current['mode'] not in ('missing', 'fallback')
                    or await client.zone_name() != zone):
                return False
            state['holding'] = True
            logger.info('自动任务：Lemuria 当前主线正常，但任务助手导航丢失。')
            logger.debug('{} Lemuria 导航恢复：QuestID={}，GoalID={}，阶段={!r}，证据={}',
                         client.title, sample['key'][0], sample['key'][1],
                         sample['key'][4], sample['mode'])
            completed = await self._run_lemuria_navigation_recovery(client, state)
            if completed:
                state['holding'] = False
                client.lemuria_navigation_pending = False
                for member in getattr(client, 'quest_mainline_sync_members', [client]):
                    member.quest_mainline_sync_state = None
                arrival = await client.zone_name()
                client.quest_party_quest_worker_zone = arrival
                client.quest_party_probe_pending = bool(getattr(client, 'quest_party_hitters', []))
            elif state['holding']:
                logger.warning('Lemuria 任务助手导航仍未恢复。')
            return True  # next iteration must pass the original party sync gate
        except Exception as exc:
            state['holding'] = True
            logger.warning('Lemuria 任务助手导航仍未恢复。原因：{}', exc)
            return True
        finally:
            client._lemuria_navigation_retry_at = time.monotonic() + self.LEMURIA_NAVIGATION_COOLDOWN_SECONDS
            state['since'] = None
            self._krok_exit_watch.pop(id(client), None)
            self._trigger_reentry.pop(id(client), None)
            self._npc_retry_exhausted.pop(id(client), None)
            self._mainline_finder_observations.pop(id(client), None)
            dungeon = getattr(client, 'quest_dungeon_recovery', None)
            if isinstance(dungeon, dict):
                dungeon.update(since=time.monotonic(), waiting_logged=False)
            release_quest_recovery(client, owner)

    async def _run_lemuria_navigation_recovery(self, client, state):
        async def same_stage():
            try:
                quest_id, goal_id = await client.quest_id(), await client.goal_id()
                if not isinstance(quest_id, int) or quest_id <= 0 or not isinstance(goal_id, int):
                    return False
                if (quest_id, goal_id) != state['key'][:2]:
                    state['holding'] = False
                    client.lemuria_navigation_pending = False
                    return None
            except Exception:
                return False  # transient unreadability during END/loading
            sample = await self._lemuria_navigation_sample(client)
            if sample is not None and sample['key'] != state['key']:
                state['holding'] = False
                client.lemuria_navigation_pending = False
                return None
            return sample if sample is not None else False

        async with asyncio.timeout(90):
            sample = await same_stage()
            if not sample or sample['mode'] == 'valid':
                return bool(sample)  # never send END after navigation recovered
            if (await self._lemuria_navigation_blocked(client, state['zone'], 'lemuria_navigation')
                    or await client.zone_name() != state['zone']):
                return False
            if (await client.is_loading() or await client.in_battle()
                    or not await is_free_leader_questing(client)):
                return False
            logger.info('自动任务：准备按 END 返回主城，刷新任务助手。')
            quest_id = state['key'][0]
            client._lemuria_navigation_attempts[quest_id] = client._lemuria_navigation_attempts.get(quest_id, 0) + 1
            await client.send_key(Keycode.END, .1)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if not client.questing_status or await client.in_battle():
                    return False
                if not await client.is_loading() and await same_stage() is None:
                    return False
                if await client.is_loading() or await client.zone_name() != state['zone']:
                    break
                await asyncio.sleep(.2)
            else:
                return False
            # A different zone alone is not a successful return or navigation.
            deadline = time.monotonic() + 35
            stable_since = None
            while time.monotonic() < deadline:
                if not client.questing_status or await client.in_battle():
                    return False
                if not await client.is_loading() and await same_stage() is None:
                    return False
                if (await client.zone_name() != self.LEMURIA_NAVIGATION_HUB
                        or await client.is_loading() or not await is_free_leader_questing(client)):
                    stable_since = None
                elif stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 1.5:
                    break
                await asyncio.sleep(.2)
            else:
                return False
            logger.info('自动任务：已返回主城，正在检查任务助手导航。')
            deadline = time.monotonic() + 30
            stable_since = None
            while time.monotonic() < deadline:
                if (not client.questing_status or await client.in_battle()
                        or await client.zone_name() != self.LEMURIA_NAVIGATION_HUB):
                    return False
                sample = await same_stage()
                if sample is None:
                    return False
                if (sample and sample['mode'] == 'valid' and not await client.is_loading()
                        and await is_free_leader_questing(client)):
                    if stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 1.5:
                        logger.info('自动任务：任务助手导航已恢复，继续任务传送。')
                        return True
                else:
                    stable_since = None
                await asyncio.sleep(.2)
            return False

    async def _nightmare_can_act(self, client: Client) -> bool:
        return (client.questing_status
                and await client.zone_name() == self.NIGHTMARE_ZONE
                and not await client.is_loading()
                and not await client.in_battle()
                and not client.entity_detect_combat_status)

    async def _run_nightmare_recovery(self, client: Client) -> bool:
        for index, point in enumerate(self.NIGHTMARE_POINTS, 1):
            logger.info(f"NightmareKrok Recovery：{index}/12")
            for attempt in range(2):
                if not await self._nightmare_can_act(client):
                    logger.warning("自动任务：NightmareKrok 交互中断，客户端状态或区域已变化。")
                    return False
                try:
                    await asyncio.wait_for(client.teleport(point), timeout=8.0)
                    await asyncio.sleep(0.4)
                    if not await self._nightmare_can_act(client):
                        raise RuntimeError("区域或客户端状态已变化")
                    if calc_Distance(await client.body.position(), point) > 350:
                        raise RuntimeError("未到达交互坐标")
                    await asyncio.wait_for(client.send_key(Keycode.X, 0.1), timeout=3.0)
                except Exception as exc:
                    if attempt == 1:
                        logger.warning(f"自动任务：NightmareKrok 第 {index}/12 点交互失败：{exc}")
                        return False
                    continue

                # A successful X is never pressed again just because the quest
                # objective changes or the interaction takes time to settle.
                deadline = time.monotonic() + 30.0
                stable_since = None
                while time.monotonic() < deadline:
                    if not await self._nightmare_can_act(client):
                        logger.warning(f"自动任务：NightmareKrok 第 {index}/12 点状态变化，停止恢复。")
                        return False
                    if await is_free_leader_questing(client):
                        if stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= 1.5:
                            break
                    else:
                        stable_since = None
                    await asyncio.sleep(0.2)
                else:
                    logger.warning(f"自动任务：NightmareKrok 第 {index}/12 点交互等待超时。")
                    return False
                break

        logger.info("自动任务：NightmareKrok 交互完成，前往区域切换点。")
        if not await self._nightmare_can_act(client):
            return False
        try:
            await asyncio.wait_for(client.teleport(self.NIGHTMARE_EXIT), timeout=8.0)
        except Exception:
            if (await client.zone_name() == self.NIGHTMARE_ZONE
                    and not await client.is_loading()
                    and calc_Distance(await client.body.position(), self.NIGHTMARE_EXIT) > 350):
                raise
        logger.info("自动任务：NightmareKrok 正在等待区域切换。")
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if not client.questing_status:
                return False
            if (await client.zone_name() != self.NIGHTMARE_ZONE
                    or await client.is_loading()):
                break
            await asyncio.sleep(0.2)
        else:
            logger.warning("自动任务：NightmareKrok 最终传送后区域切换未开始，停止本次恢复。")
            return False

        stable_since = None
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if not client.questing_status:
                return False
            zone = await client.zone_name()
            if (zone and zone != self.NIGHTMARE_ZONE
                    and not await client.is_loading()
                    and await is_free_leader_questing(client)):
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 1.5:
                    if getattr(client, "quest_party_hitters", []):
                        client.quest_party_quest_worker_zone = zone
                        client.quest_party_probe_pending = True
                    await self._dungeon_quest_snapshot(client)
                    try:
                        await asyncio.wait_for(client.quest_position.position(), timeout=5.0)
                    except Exception as exc:
                        logger.warning(f"自动任务：NightmareKrok 新区域任务目标暂不可读：{exc}")
                        return False
                    logger.info("自动任务：NightmareKrok 区域切换完成，继续任务传送。")
                    return True
            else:
                stable_since = None
            await asyncio.sleep(0.2)
        logger.warning("自动任务：NightmareKrok 新区域未稳定，停止本次恢复。")
        return False

    async def _maybe_recover_nightmare(self, client: Client) -> bool:
        if await client.zone_name() != self.NIGHTMARE_ZONE:
            client.quest_nightmare_recovery = None
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None:
            client.quest_nightmare_recovery = None
            return False
        state = getattr(client, "quest_nightmare_recovery", None)
        now = time.monotonic()
        if state is None or state["snapshot"] != snapshot:
            client.quest_nightmare_recovery = {
                "snapshot": snapshot, "since": now, "attempted": False
            }
            return False
        if (state["attempted"] or now - state["since"] < self.NIGHTMARE_NO_PROGRESS_SECONDS
                or await self._trigger_reentry_blocked(client)):
            return False
        if not claim_quest_recovery(client, "nightmare_krok"):
            return False
        state["attempted"] = True
        logger.info("自动任务：NightmareKrok 任务无进展，开始特殊交互恢复。")
        try:
            completed = await self._run_nightmare_recovery(client)
            if completed or await client.zone_name() != self.NIGHTMARE_ZONE:
                client.quest_nightmare_recovery = None
        except Exception as exc:
            logger.warning(f"自动任务：NightmareKrok 特殊恢复失败：{exc}")
        finally:
            try:
                dungeon = getattr(client, "quest_dungeon_recovery", None)
                if isinstance(dungeon, dict):
                    dungeon.update(snapshot=await self._dungeon_quest_snapshot(client),
                                   since=time.monotonic(), waiting_logged=False)
            finally:
                release_quest_recovery(client, "nightmare_krok")
        return True

    async def _trigger_reentry_blocked(self, client: Client, owner: str = None) -> bool:
        current_owner = getattr(client, "quest_recovery_owner", None)
        if (not client.questing_status
                or getattr(client, 'bumbles_pet_pending', False) is True
                or isinstance(current_owner, str) and current_owner != owner
                or getattr(client, "quest_party_probe_pending", False)
                or getattr(client, "quest_party_battle_rescue_active", False)
                or getattr(client, "quest_party_quest_worker_restart_requested", False)
                or getattr(client, "post_combat_movement_active", False)
                or getattr(client, "mainline_chain_retry_active", False)
                or self._krok_exit_watch.get(id(client))
                or isinstance(getattr(client, "quest_dungeon_recovery", None), dict)
                and client.quest_dungeon_recovery.get("active")):
            return True
        if not await is_free_leader_questing(client):
            return True
        return (await is_spiral_door_open(client)
                or await is_visible_by_path(client, npc_range_path)
                or await is_visible_by_path(client, exit_dungeon_path)
                or await is_visible_by_path(client, dungeon_warning_path)
                or await is_visible_by_path(client, decline_quest_path)
                or await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                or await is_visible_by_path(client, missing_area_path)
                or await is_visible_by_path(client, all_quests_sort_button_path))

    async def _maybe_reenter_quest_trigger(self, client: Client, target: XYZ) -> bool:
        """Re-enter a missed interaction/exploration trigger; never loop TP forever."""
        tent_watch = self._krok_exit_watch.get(id(client), {})
        if (await client.zone_name() == self.DUELING_TENT_ZONE
                and (tent_watch.get('attempts', 0) > 0
                     or isinstance(getattr(client, '_xuanshu_dueling_tent_failed', None), dict)
                     or getattr(client, 'quest_recovery_owner', None) == 'dueling_tent')):
            return False  # A blocked TP belongs to this map's dedicated recovery.
        key_id = id(client)
        if await self._trigger_reentry_blocked(client):
            state = self._trigger_reentry.get(key_id)
            if state is not None:
                state["seen"] = 0
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or not (
            quest_has_action(snapshot[2], "talk") or quest_has_action(snapshot[2], "use")
            or quest_has_action(snapshot[2], "explore")
        ):
            self._trigger_reentry.pop(key_id, None)
            return False
        if (not all(math.isfinite(value) for value in (target.x, target.y, target.z))
                or calc_Distance(target, XYZ(0.0, 0.0, 0.0)) <= 1.0):
            self._trigger_reentry.pop(key_id, None)
            return False
        zone = await client.zone_name()
        position = await client.body.position()
        key = (zone, snapshot, round(target.x), round(target.y), round(target.z))
        state = self._trigger_reentry.get(key_id)
        if state is None or state["key"] != key:
            state = {"key": key, "seen": 0, "attempts": 0}
            self._trigger_reentry[key_id] = state
        if (calc_Distance(position, target) > self.TRIGGER_NEAR_DISTANCE
                or abs(position.z - target.z) > 150.0):
            state["seen"] = 0
            return False
        if state["attempts"] >= self.TRIGGER_MAX_ATTEMPTS:
            if await self.quest_interaction_ready(client, target):
                return False
            if not state.get("exhausted_logged"):
                logger.warning(f"Client {client.title} - 近距离任务点两次重新进场仍无进展；"
                               f"暂停该任务点 TP，等待交互或任务状态变化：{snapshot}")
                state["exhausted_logged"] = True
            # Keep the worker alive to read progression/interaction. Returning
            # False here previously fell straight back into collision_tp.
            await asyncio.sleep(0.5)
            return True
        state["seen"] += 1
        if state["seen"] < self.TRIGGER_STABLE_OBSERVATIONS:
            return False
        # Re-read the task after the safety checks, before taking control.
        if (await self._trigger_reentry_blocked(client)
                or await client.zone_name() != zone
                or await self._dungeon_quest_snapshot(client) != snapshot
                or calc_Distance(await client.quest_position.position(), target) > 50.0):
            state["seen"] = 0
            return False
        if not claim_quest_recovery(client, "trigger_reentry"):
            return False
        attempt = state["attempts"]
        state["attempts"] += 1
        state["seen"] = 0
        logger.info(
            "自动任务：已到达任务点但交互未触发，尝试重新进入任务触发范围。"
            if attempt == 0 else
            "自动任务：普通重新进场无效，尝试第二级任务点刷新。"
        )
        try:
            await self._reenter_quest_trigger(client, zone, target, snapshot, attempt)
        except Exception as exc:
            logger.warning(f"Client {client.title} - trigger re-entry failed: {exc}")
        finally:
            try:
                dungeon = getattr(client, "quest_dungeon_recovery", None)
                if isinstance(dungeon, dict) and dungeon.get("zone") == zone:
                    dungeon.update(snapshot=await self._dungeon_quest_snapshot(client),
                                   since=time.monotonic(), waiting_logged=False)
            finally:
                release_quest_recovery(client, "trigger_reentry")
        return True

    async def _reenter_quest_trigger(
        self, client: Client, zone: str, target: XYZ, snapshot, attempt: int
    ) -> None:
        position = await client.body.position()
        dx, dy = position.x - target.x, position.y - target.y
        length = math.hypot(dx, dy)
        if length < 20.0:
            yaw = await client.body.yaw()
            dx, dy = math.cos(yaw), math.sin(yaw)
            length = 1.0
        dx, dy = dx / length, dy / length
        if attempt:
            dx, dy = -dy, dx
        away_x = target.x + dx * self.TRIGGER_AWAY_DISTANCE
        away_y = target.y + dy * self.TRIGGER_AWAY_DISTANCE
        await client.goto(away_x, away_y)
        if (await client.zone_name() != zone
                or await self._dungeon_quest_snapshot(client) != snapshot
                or await self._trigger_reentry_blocked(client, "trigger_reentry")):
            return
        away = await client.body.position()
        if math.hypot(away.x - target.x, away.y - target.y) < 750.0:
            # A wall or another obstacle prevented a real exit from range.
            await client.goto(target.x, target.y)
            return
        await asyncio.sleep(0.5)
        if (await client.zone_name() != zone
                or await self._dungeon_quest_snapshot(client) != snapshot
                or await self._trigger_reentry_blocked(client, "trigger_reentry")):
            return
        await client.goto(target.x, target.y)
        await asyncio.sleep(0.5)
        if await client.zone_name() != zone or not client.questing_status:
            return
        if await self._dungeon_quest_snapshot(client) != snapshot:
            self._trigger_reentry.pop(id(client), None)
            logger.info("自动任务：任务交互已重新触发，继续任务流程。")
        elif await self.quest_interaction_ready(client, target):
            # The normal NPC handler owns the visible interaction from here.
            # Do not start another re-entry for the same unchanged quest.
            self._trigger_reentry[id(client)]["attempts"] = self.TRIGGER_MAX_ATTEMPTS
            logger.info("自动任务：任务交互已重新触发，继续任务流程。")

    async def read_quest_txt(self, client: Client) -> str:
        try:
            quest_name = await get_window_from_path(client.root_window, quest_name_path)
            quest = await quest_name.maybe_text()
        except Exception:
            quest = ""
        return quest

    async def read_spiral_door_title(self, client: Client) -> str:
        try:
            title_text_path = await get_window_from_path(client.root_window, spiral_door_title_path)
            # UniverseMap.gui contains a hidden streamTitle whose default is
            # Streamportal even at ordinary world gates. Only a displayed title
            # identifies a special portal; the tracked quest selects the world.
            if not title_text_path or not await title_text_path.is_visible():
                return ""
            title = await title_text_path.maybe_text()
        except Exception:
            title = ""

        return title

    async def read_popup(self, p: Client) -> str:
        try:
            popup_text_path = await get_window_from_path(p.root_window, popup_msgtext_path)
            txtmsg = await popup_text_path.maybe_text()
        except Exception:
            txtmsg = ""
        return txtmsg

    async def detected_interact_from_popup(self, p: Client) -> bool:
        txtmsg = await self.read_popup(p)

        return bool(txtmsg) and interaction_kind(txtmsg) not in {"talk", "open", "collect"}

    async def is_position_safe(self, position: XYZ, safe_distance: float = 1000) -> bool:
        sp = SprintyClient(self.client)

        for mob in await sp.get_mobs():
            mob_pos = await mob.location()
            if math.dist(mob_pos, position) < safe_distance:
                return False

        return True

    async def get_zone_chunks(self) -> list[XYZ]:
        wad = await self.load_wad(await self.client.zone_name())
        nav_data = await wad.get_file("zone.nav")
        vertices, _ = parse_nav_data(nav_data)

        full = calc_chunks(vertices)
        return full

    async def get_collect_quest_object_name(self) -> str:
        # '<center>Collect Cog in Triton Avenue (0 of 3)</center>'
        # -> 'Cog'
        s = await self.read_quest_txt(self.client)
        return collect_object_name(s)

    # TODO: Does this need a client?
    async def get_quest_zone_name(self, c: Client) -> str:
        # <center>Collect Cog in Triton Avenue (0 of 3)</center>
        # Collect Cog in Triton Avenue (0 of 3)
        # -> 'Triton Avenue'

        query = await self.read_quest_txt(c)

        return split_quest_location(query)[1]

    async def get_truncated_quest_objectives(self, p: Client) -> str:
        quest_objective = await get_quest_name(p)
        if '(' in quest_objective:
            quest_objective = quest_objective.split('(', 1)
            quest_objective = quest_objective[0]

        return quest_objective

    async def load_wad(self, path: str):
        return Wad.from_game_data(path.replace("/", "-"))

    async def followers_in_correct_zone(self) -> bool:
        if getattr(self.current_leader_client, "refilling_potions", False) is True:
            return True
        zone = await self.current_leader_client.zone_name()
        for c in self.clients:
            if getattr(c, "refilling_potions", False) is True or getattr(c, "questing_status", True) is False:
                continue
            if await c.zone_name() != zone:
                return False
        return True

    async def determine_solo_zone(self) -> bool:
        sprinter = SprintyClient(self.current_leader_client)
        entities = await sprinter.get_base_entity_list()

        player_count = 0
        for entity in entities:
            entity_name = await entity.object_name()
            if entity_name == 'Player Object':
                player_count += 1
                if player_count > 1:
                    return False
        return True

    async def get_follower_clients(self) -> list[Client]:
        follower_clients = []
        for c in self.clients:
            if (c.process_id != self.current_leader_client.process_id
                    and getattr(c, "refilling_potions", False) is not True
                    and getattr(c, "questing_status", True) is not False):
                follower_clients.append(c)

        return follower_clients

    # get a list of clients that are questing alongside the leader, including the leader (not boosting)
    async def get_questing_clients(self) -> list[Client]:
        questing_clients = [self.current_leader_client]
        quest_objective_leader = await self.get_truncated_quest_objectives(self.current_leader_client)

        for c in await self.get_follower_clients():
            quest_objective_follower = await self.get_truncated_quest_objectives(c)
            if quest_objective_follower == quest_objective_leader:
                questing_clients.append(c)

        return questing_clients

    # get a dict of client quests for clients that are on the same quest as the leader, including the leader
    async def get_client_quests(self, questing_clients: list[Client]) -> dict[Client, str]:
        client_quests = {}

        for c in questing_clients:
            quest_objective = await self.get_truncated_quest_objectives(c)
            client_quests[c] = quest_objective

        return client_quests

    async def zone_recorrect_hub(self):
        if any(getattr(p, 'bumbles_pet_pending', False) is True for p in self.clients):
            return
        if any(isinstance(getattr(p, 'quest_lemuria_navigation_recovery', None), dict)
               and p.quest_lemuria_navigation_recovery.get('holding')
               or getattr(p, 'quest_recovery_owner', None) in ('lemuria_navigation', 'dueling_tent')
               for p in self.clients):
            return
        if await self.followers_in_correct_zone():
            return
        for p in self.clients:
            if await self._maybe_handle_bumbles_mind(p):
                return
            if getattr(p, 'quest_recovery_owner', None) == 'tamarin_house':
                return
            if await self._maybe_handle_tamarin_house(p):
                return
        for p in self.clients:
            if getattr(p, "refilling_potions", False) is True or getattr(p, "questing_status", True) is False:
                continue
            await p.send_key(Keycode.END)
            await p.send_key(Keycode.END)
            await asyncio.sleep(3)
            # use is_loading instead of wait_for_change, as one client could already be in the hub
            while await p.is_loading():
                await asyncio.sleep(0.1)

        await asyncio.sleep(2)

    async def friend_teleport(self, maybe_solo_zone: bool):
        if any(getattr(c, 'bumbles_pet_pending', False) is True for c in self.clients):
            return [], None
        if any(getattr(c, 'quest_recovery_owner', None) == 'dueling_tent' for c in self.clients):
            return [], None
        if getattr(self.current_leader_client, "refilling_potions", False) is True:
            return [], None
        clients_in_solo_zone = []
        solo_zone = None
        leader_in_solo_zone = False
        was_loading = False

        for c in self.clients:
            if getattr(c, "refilling_potions", False) is True or getattr(c, "questing_status", True) is False:
                continue
            if c.process_id != self.current_leader_pid:
                c_zone = await c.zone_name()
                leader_zone = await self.current_leader_client.zone_name()
                if c_zone != leader_zone or maybe_solo_zone:
                    if await is_free(c) and not c.entity_detect_combat_status:
                        # teleport to leader
                        await c.send_key(Keycode.F, 0.1)
                        ##############################################
                        async with c.mouse_handler:
                            await teleport_to_friend_from_list(c, name=self.current_leader_client.wizard_name)  # icon_list=1, icon_index=self.current_leader_client.questing_friend_teleport_icon)
                        if c_zone != leader_zone:
                            async with c.mouse_handler:
                                try:
                                    await safe_wait_for_zone_change(c, handle_hooks_if_needed=False)
                                except FriendBusyOrInstanceClosed:
                                    leader_in_solo_zone = True
                                    solo_zone = await self.current_leader_client.zone_name()

                                    # if leader is in solo zone, others may be too - meaning the user is likely trying to quest multiple clients at the same time.  Keep track of these questing clients
                                    for p in self.clients:
                                        if await p.zone_name() == await self.current_leader_client.zone_name():
                                            clients_in_solo_zone.append(p)

                                    break
                                except LoadingScreenNotFound:
                                    print(traceback.print_exc())
                                    while True:
                                        await asyncio.sleep(1.0)

                                was_loading = True

                        else:
                            await asyncio.sleep(6.0)
                            if await is_visible_by_path(c, friend_is_busy_and_dungeon_reset_path):
                                async with c.mouse_handler:
                                    while await is_visible_by_path(c, friend_is_busy_and_dungeon_reset_path):
                                        leader_in_solo_zone = True
                                        await click_window_by_path(c, friend_is_busy_and_dungeon_reset_path)

                                solo_zone = await self.current_leader_client.zone_name()

                                # if leader is in solo zone, others may be too - meaning the user is likely trying to quest multiple clients at the same time.  Keep track of these questing clients
                                for p in self.clients:
                                    if await p.zone_name() == await self.current_leader_client.zone_name():
                                        clients_in_solo_zone.append(p)

                                break

        if not leader_in_solo_zone:
            maybe_solo_zone = False

        # leaving a loading screen is not equivalent to being ready to move - give clients time to truly load into a zone
        if was_loading:
            await asyncio.sleep(3.0)

        return clients_in_solo_zone, solo_zone

    # handle zone recorrection in follow leader mode using friend TP.  Also quest all questing clients individually when they are in a solo zone

    # maybe_solo_zone - tells the function that we think we may be in a solo zone, and that it should check and account for that even if we are technically in the same zone as the leader
    async def zone_recorrect_friend_tp(self, maybe_solo_zone: bool, gear_switching_in_solo_zones=False):
        # for solo zone questing support across multiple clients
        async def solo_zone_questing_loop(clients_in_solo: List[Client], zone: str):
            async def solo_zone_questing(solo_cl: Client):
                questing = Quester(solo_cl, self.clients, None)
                while solo_cl.questing_status and await solo_cl.zone_name() == zone:
                    await asyncio.sleep(1.0)

                    if solo_cl in self.clients and solo_cl.questing_status:
                        await questing.auto_quest_solo(auto_pet_disabled=True)

            await gather_owned(*[solo_zone_questing(cl) for cl in clients_in_solo])

        if len(self.clients) > 1:
            # if clients are not in same zone as leader, teleport there, or quest leader individually
            while not await self.followers_in_correct_zone() or maybe_solo_zone:
                clients_in_solo_zone, solo_zone = await self.friend_teleport(maybe_solo_zone)
                initial_clients_in_solo_zone = clients_in_solo_zone.copy()

                # if client(s) in solo zone, switch to non-leader auto questing and quest each valid client on their own until their zone changes
                # deck_switching = True
                if len(clients_in_solo_zone) > 0:
                    for solo_client in clients_in_solo_zone:
                        logger.debug('Client ' + solo_client.title + ' is in solo zone - questing alone')

                        solo_client.in_solo_zone = True

                    if gear_switching_in_solo_zones:
                        logger.debug('Switching to second equipment set on all clients.')
                        await gather_owned(*[change_equipment_set(c, 1,) for c in clients_in_solo_zone])

                    # loop until we have confirmed that we are no longer in a solo zone
                    while len(clients_in_solo_zone) > 0 and solo_zone is not None:
                        await solo_zone_questing_loop(clients_in_solo=clients_in_solo_zone, zone=solo_zone)

                        logger.debug('Clients may have left the solo zone - attempting to teleport to leader.')
                        clients_in_solo_zone, solo_zone = await self.friend_teleport(maybe_solo_zone=True)

                    # we have confirmed that we are out of the solo zone(s) - carry on questing
                    maybe_solo_zone = await self.determine_solo_zone()
                    if maybe_solo_zone:
                        logger.debug('Some clients appear to still be in the solo zone.')
                    else:
                        logger.debug('Clients all appear to have left the solo zone.')

                    for solo in initial_clients_in_solo_zone:
                        solo.in_solo_zone = False

                    if gear_switching_in_solo_zones:
                        logger.debug('Switching back to first equipment set on all clients.')
                        await gather_owned(*[change_equipment_set(c, 0,) for c in initial_clients_in_solo_zone])
                else:
                    maybe_solo_zone = await self.determine_solo_zone()

        return maybe_solo_zone

    async def heal_and_handle_potions(self):
        await gather_owned(*[self.collect_wisps(p) for p in self.clients])
        await gather_owned(*[self.guarantee_use_potion(p) for p in self.clients])

        clients_needing_potions = []
        for p in self.clients:
            if await p.stats.potion_charge() < 1.0 and await p.stats.reference_level() >= 6:
                clients_needing_potions.append(p)

        if clients_needing_potions:
            results = await gather_owned(*[refill_potions(c) for c in clients_needing_potions])
            for client, result in zip(clients_needing_potions, results):
                if result is False:
                    client.questing_status = False
            # Keep failed clients out of every subsequent legacy group action.
            # Rebind the local roster; do not modify the shared application roster.
            self.clients = [c for c in self.clients if getattr(c, "questing_status", True) is not False]
            if getattr(self.current_leader_client, "questing_status", True) is False and self.clients:
                self.current_leader_client = self.clients[0]
            return all(result is not False for result in results)
        return True

    async def collect_wisps(self, p: Client):
        if await is_free(p):
            if await is_potion_needed(p) and await p.stats.current_mana() > 1 and await p.stats.current_hitpoints() > 1:
                await collect_wisps(p)

    async def guarantee_use_potion(self, p: Client):
        if await is_free(p):
            if await is_potion_needed(p, minimum_mana=16):
                original_potion_count = await p.stats.potion_charge()
                while await p.stats.potion_charge() == original_potion_count and original_potion_count >= 1:
                    logger.debug(f'Client {p.title} - Using potion')
                    await click_window_by_path(p, potion_usage_path, True)
                    await asyncio.sleep(.6)

    # if followers in different zone, try X presses (for X zone changes that require delayed presses between clients)
    async def X_press_zone_recorrect(self):
        for c in self.clients:
            if c.process_id != self.current_leader_pid:
                await c.send_key(Keycode.X, 0.1)
                await asyncio.sleep(2.5)

        await asyncio.sleep(2)
        for c in self.clients:
            while await c.is_loading():
                await asyncio.sleep(0.1)

    # async def enter_dungeon(self):
        # Handles entering dungeons
        # await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])

        # await asyncio.sleep(1.5)

        # for c in self.clients:
        #     if await is_visible_by_path(c, dungeon_warning_path):
        #         await c.send_key(Keycode.ENTER, 0.1)

        # await gather_owned(*[c.wait_for_zone_change() for c in self.clients])

    async def find_quest_zone_area_name(self, client: Client, door_locations: list) -> Optional[str]:
        location = await self.get_quest_zone_name(client)
        self.d_location = resolve_portal_destination(location, door_locations)
        return self.d_location

    async def new_world_doors(self, client: Client) -> bool:
        kind = portal_kind(await self.read_spiral_door_title(client))
        if kind in {"streamportal", "nanavator"}:
            choices = streamportal_locations if kind == "streamportal" else nanavator_locations
            location = await self.find_quest_zone_area_name(client, choices)
            if location is None:
                logger.warning(f"Client {client.title}: 无法确定特殊传送门的任务目的地，跳过传送。")
                return True
            await new_portals_cycle(client, location)
            return True

        return False

    async def handle_spiral_navigation(self):
        if await self.new_world_doors(self.current_leader_client):
            if self.d_location is None:
                return
            for c in self.clients:
                if c.process_id != self.current_leader_pid:
                    if await is_visible_by_path(c, spiral_door_teleport_path):
                        await new_portals_cycle(c, self.d_location)
        else:
            # Handles spiral door navigation
            await spiral_door_with_quest(self.current_leader_client)

            await asyncio.sleep(1)
            leader_world = (await self.current_leader_client.zone_name()).split('/', 1)[0]

            # Follower clients use separate spiral door navigation than leader (since they may not have the same quest)
            for c in self.clients:
                if c.process_id != self.current_leader_pid:
                    if await is_visible_by_path(c, spiral_door_teleport_path):
                        await go_to_new_world(c, leader_world)

    async def auto_tfc_friend_all_wizards(self):
        for code_generator in self.clients:
            for code_redeemer in self.clients:
                if code_generator.process_id != code_redeemer.process_id:
                    friend_already_in_list = await check_for_friend_in_list(code_generator, code_redeemer.wizard_name)

                    if not friend_already_in_list:
                        tfc = await generate_tfc(code_generator)
                        await accept_tfc(code_redeemer, tfc)

    # This is horribly inconsistent due to wizwalker reading incorrect values
    async def auto_friend_all_wizards(self):
        # await self.current_leader_client.send_key(Keycode.END, 0.1)
        # await asyncio.sleep(4)
        # while await self.current_leader_client.is_loading():
        #    await asyncio.sleep(.1)

        # move leader forward so they are not overlapped with other arriving clients
        await self.current_leader_client.send_key(Keycode.W, .5)

        await asyncio.sleep(2)
        await gather_owned(*[navigate_to_ravenwood(p) for p in self.clients])
        # await toZone(self.clients, 'WizardCity/WC_Ravenwood')  # await self.current_leader_client.zone_name())

        # send all to a common realm (Centaur)
        for c in self.clients:
            while not await is_visible_by_path(c, check_spellbook_open_path):
                await c.send_key(Keycode.ESC, 0.1)

            async with c.mouse_handler:
                for i in range(3):
                    await c.mouse_handler.click_window_with_name('RealmsButton')
                    await asyncio.sleep(.1)

                for i in range(3):
                    await c.mouse_handler.click_window_with_name('btnRealm' + str(6))
                    await asyncio.sleep(.1)

                for i in range(3):
                    if not await c.is_loading():
                        try:
                            await c.mouse_handler.click_window_with_name('btnGoToRealm')
                        except ValueError:
                            await asyncio.sleep(.1)
                        await asyncio.sleep(.5)

                await asyncio.sleep(2.0)
                while await c.is_loading():
                    await asyncio.sleep(.1)

                if await is_visible_by_path(c, close_spellbook_path):
                    await click_window_by_path(c, close_spellbook_path)

        for requester in self.clients:
            requester_original_position = await requester.body.position()
            # teleport to a wacky spot in Ravenwood so that we dont accidentally click the wrong player
            # yaw moves our camera really close (because we're colliding with a wall), reducing the chance of mis-clicking
            await requester.teleport(XYZ(x=-1884.0, y=-2328.0, z=0.0), yaw=0.7693982720375061)
            await asyncio.sleep(.3)
            await requester.send_key(Keycode.W, 0.1)
            for acceptor in self.clients:
                if requester.process_id != acceptor.process_id:
                    friend_already_in_list = await check_for_friend_in_list(requester, acceptor.wizard_name)

                    if not friend_already_in_list:
                        original_acceptor_location = await acceptor.body.position()
                        # teleport the clients that are friending each other to the same location
                        await acceptor.teleport(await requester.body.position(), yaw=await requester.body.yaw())
                        await asyncio.sleep(10)

                        # Click a few times initially and pray it works, as wiz sometimes lies about the add friend window being visible when it isn't

                        async with requester.mouse_handler:
                            rect: Rectangle = requester.window_rectangle
                            width = (rect.x2 - rect.x1)
                            height = (rect.y2 - rect.y1)
                            # Width and Height are always off by a set number - unsure if this interferes with clicking
                            # width = abs(width + 16)
                            # height = height - 39
                            width = abs(width)
                            center_x = width / 2
                            center_y = height / 2

                            for i in range(5):
                                await requester.mouse_handler.click(x=int(center_x), y=int(center_y), sleep_duration=0.3)
                                await asyncio.sleep(.2)

                            # friend_title = await get_friend_popup_wizard_name(requester)

                            # continually click until the correct friend popup window appears
                            # this code may never run, as reading the friend popup window is inaccurate
                            # while friend_title != acceptor.wizard_name:
                            #     await requester.send_key(Keycode.D, 0.3)
                            #     await requester.mouse_handler.click(x=int(center_x), y=int(center_y), sleep_duration=0.3)
                            #     friend_title = await get_friend_popup_wizard_name(requester)

                            # Click add friend
                            for i in range(2):
                                if await is_visible_by_path(requester, add_remove_friend_path):
                                    await click_window_by_path(requester, add_remove_friend_path)

                            # Confirm sending the request
                            for i in range(2):
                                if await is_visible_by_path(requester, confirm_send_friend_request):
                                    await asyncio.sleep(.2)
                                    await click_window_by_path(requester, confirm_send_friend_request)

                            # Wait for accept friend popup to appear
                            for i in range(2):
                                while not await is_visible_by_path(acceptor, confirm_accept_friend_request):
                                    await asyncio.sleep(.1)

                            async with acceptor.mouse_handler:
                                # Accept friend request
                                for i in range(2):
                                    if await is_visible_by_path(acceptor, confirm_accept_friend_request):
                                        await asyncio.sleep(.4)
                                        await click_window_by_path(acceptor, confirm_accept_friend_request)
                            # Close friend window on requestor
                            # This fails consistently, even when the friends list is actually open.  Detecting whether the friends list is open is also horrifically inconsistent so just brute force it
                                for i in range(5):
                                    try:
                                        await click_window_by_path(requester, close_real_friend_list_button_path)
                                        await asyncio.sleep(.1)
                                    except ValueError:
                                        await asyncio.sleep(.1)

                        await acceptor.teleport(original_acceptor_location)
                        await acceptor.send_key(Keycode.W, 0.1)
                        await asyncio.sleep(1)

            await requester.teleport(requester_original_position)

    async def num_clients_in_same_area(self) -> int:
        sprinter = SprintyClient(self.current_leader_client)
        entities = await sprinter.get_base_entity_list()

        player_count = 0
        for entity in entities:
            entity_gid = await entity.global_id_full()
            for c in self.clients:
                client_gid = await c.client_object.global_id_full()
                if client_gid == entity_gid:
                    player_count += 1
                    break
            if player_count == len(self.clients):
                break

        return player_count

    async def determine_new_leader_and_followers(self, client_quests: dict, questing_clients: list[Client],
                                                    follower_clients: list[Client]) -> tuple[list[Client], dict[Client, str]]:
            original_length = len(client_quests)
            if len(client_quests) > 0:
                for c in questing_clients:
                    if c in client_quests:
                        if await self.get_truncated_quest_objectives(c) != client_quests.get(c):
                            client_quests.pop(c)

                # if all clients have moved on from their previous quest
                if len(client_quests) == 0:
                    # pass leader back to original leader client
                    if self.current_leader_client.title != self.client.title:
                        logger.debug('Clients caught up - resetting leader to client ' + self.client.title)
                        self.current_leader_client = self.client
                        self.current_leader_pid = self.client.process_id
                        follower_clients = await self.get_follower_clients()

                    client_quests = await self.get_client_quests(questing_clients=questing_clients)

                elif len(client_quests) < original_length:
                    # pass leader to next client in dict
                    logger.debug('client(s) fell behind - new leader ' + list(client_quests)[0].title + ' assigned')
                    self.current_leader_client = list(client_quests)[0]
                    self.current_leader_pid = self.current_leader_client.process_id
                    follower_clients = await self.get_follower_clients()
            else:
                client_quests = await self.get_client_quests(questing_clients=questing_clients)

            return follower_clients, client_quests

    async def correct_dungeon_desync(self, follower_clients):
        # attempt to correct the desync by simply teleporting all clients to the leader
        async def teleport_to_friend_from_list_wizard_leader_name_mouse_handler(client):
            async with client.mouse_handler:
                await teleport_to_friend_from_list(client, name=self.current_leader_client.wizard_name)

        await gather_owned(*[teleport_to_friend_from_list_wizard_leader_name_mouse_handler(c) for c in follower_clients])

        await asyncio.sleep(1.5)

        # this may fail - some zones cannot be teleported to even if they have public sigils - detect whether this zone is locked off to teleports
        teleport_banned_zone = False
        for c in self.clients:
                while await is_visible_by_path(c, friend_is_busy_and_dungeon_reset_path):
                    async with c.mouse_handler:
                        await click_window_by_path(c, friend_is_busy_and_dungeon_reset_path)
                    teleport_banned_zone = True

        await asyncio.sleep(.5)

        # if we cannot teleport, send all to hub, then teleport
        # teleporting can never fail in the hub (due to a locked zone at least), so this is a surefire way get all clients in the same realm / area
        if teleport_banned_zone:
            logger.debug('Friend teleport correction for dungeon desync failed - sending all clients to hub and retrying teleport')
            await self.zone_recorrect_hub()
            await gather_owned(*[teleport_to_friend_from_list_wizard_leader_name_mouse_handler(c) for c in follower_clients])

            await asyncio.sleep(5.0)
            for c in self.clients:
                while await c.is_loading():
                    await asyncio.sleep(.1)
        else:
            await asyncio.sleep(3.0)
            for c in self.clients:
                while await c.is_loading():
                    await asyncio.sleep(.1)


    async def auto_collect_rewrite(self, client: Client):
        return await collect_one(self, client)

    async def _rotating_realm_menu(self, client: Client) -> None:
        realm_tab = ['WorldView', 'DeckConfiguration', 'SettingPage', 'TabWindow', 'RealmsButton']
        realm_panel = ['WorldView', 'DeckConfiguration', 'SettingPage', 'RealmOptions', 'wndRealmPanel']
        if await is_visible_by_path(client, realm_panel):
            return
        for _ in range(3):
            if await is_visible_by_path(client, realm_tab):
                break
            await client.send_key(Keycode.ESC, .1)
            await asyncio.sleep(.25)
        else:
            raise RuntimeError('Realm 设置菜单未打开')
        await click_window_by_path(client, realm_tab)
        for _ in range(10):
            if await is_visible_by_path(client, realm_panel):
                return
            await asyncio.sleep(.2)
        raise RuntimeError('Realm 选项页面未打开')

    async def _rotating_realm_choices(self, client: Client):
        options = ['WorldView', 'DeckConfiguration', 'SettingPage', 'RealmOptions']
        panel_path = options + ['wndRealmPanel']
        right_path = options + ['btnRealmRight']
        for _ in range(4):
            panel = await get_window_from_path(client.root_window, panel_path)
            if panel and await panel.is_visible():
                buttons = {}
                for window in await panel.children():
                    name = await window.name()
                    if re.fullmatch(r'btnRealm\d+', name) and await window.is_visible():
                        buttons[name] = window
                selected = [name for name, window in buttons.items() if await window.maybe_checked()]
                available = {name for name, window in buttons.items()
                             if WindowFlags.disabled not in await window.flags()}
                if len(selected) == 1 and available - set(selected):
                    current = selected[0]
                    preferred = ['btnRealm1', 'btnRealm0']
                    return current, list(dict.fromkeys(
                        name for name in preferred + sorted(available) if name in available and name != current
                    ))
            right = await get_window_from_path(client.root_window, right_path)
            if not right or not await right.is_visible() or WindowFlags.disabled in await right.flags():
                break
            await click_window_by_path(client, right_path)
            await asyncio.sleep(.25)
        raise RuntimeError('Realm 页面无法确认当前 Realm 或可用备用 Realm')

    async def _rotating_zone_id(self, client: Client):
        zone = await client.client_object.client_zone()
        return await zone.zone_id() if zone else None

    async def _perform_rotating_realm_change(self, client: Client) -> None:
        options = ['WorldView', 'DeckConfiguration', 'SettingPage', 'RealmOptions']
        try:
            await self._rotating_realm_menu(client)
            current, choices = await self._rotating_realm_choices(client)
            if current == 'btnRealm1':
                logger.info('自动任务：当前已处于首选 Realm，改用备用 Realm。')
            destination = None
            for name in choices:
                await click_window_by_path(client, options + ['wndRealmPanel', name])
                go = await get_window_from_path(client.root_window, options + ['btnGoToRealm'])
                if go and await go.is_visible() and WindowFlags.disabled not in await go.flags():
                    destination = name
                    break
            if destination is None:
                raise RuntimeError('没有可切换的 Realm')
            before_zone = await client.zone_name()
            before_id = await self._rotating_zone_id(client)
            logger.info('自动任务：正在切换 Realm。')
            await click_window_by_path(client, options + ['btnGoToRealm'])
            loading_seen = False
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if not client.questing_status:
                    raise RuntimeError('自动任务已停止')
                loading_seen = loading_seen or await client.is_loading()
                zone_id = await self._rotating_zone_id(client)
                if loading_seen or before_id is not None and zone_id is not None and zone_id != before_id:
                    break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Realm Change 未开始')
            stable_since = None
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                if not client.questing_status:
                    raise RuntimeError('自动任务已停止')
                if (await client.zone_name() == before_zone and not await client.is_loading()
                        and await is_free_leader_questing(client)):
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= 1.5:
                        break
                else:
                    stable_since = None
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Realm Change 后客户端未稳定')
            # Selection is checked only after the transfer has completed; a
            # selected button immediately after clicking it is not proof.
            await self._rotating_realm_menu(client)
            confirmed, _ = await self._rotating_realm_choices(client)
            if confirmed != destination or confirmed == current:
                raise RuntimeError('Realm 页面未确认切换到不同 Realm')
            if not loading_seen and await self._rotating_zone_id(client) == before_id:
                raise RuntimeError('未观察到 Loading 或区域实例变化')
        finally:
            if await is_visible_by_path(client, close_spellbook_path):
                await click_window_by_path(client, close_spellbook_path)

    async def change_realm_for_rotating(self, client: Client) -> bool:
        if (not client.questing_status or getattr(client, 'quest_party_probe_pending', False)
                or not await is_free_leader_questing(client)):
            return False
        if not claim_quest_recovery(client, 'rotating_realm'):
            return False
        logger.info('自动任务：旋转旋转本轮未找到，准备通过切换 Realm 推进任务。')
        try:
            before = await self._dungeon_quest_snapshot(client)
            await self._perform_rotating_realm_change(client)
            logger.info('自动任务：Realm 切换完成，重新检查旋转旋转任务进度。')
            after = before
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and client.questing_status:
                after = await self._dungeon_quest_snapshot(client)
                if after != before:
                    break
                await asyncio.sleep(.25)
            if before == after:
                logger.warning('自动任务：Realm 已切换，但旋转旋转任务进度尚未变化。')
            hitters = list(getattr(client, 'quest_party_hitters', []))
            if hitters:
                logger.info('自动任务：任务客户端 Realm 已切换，正在同步打手客户端。')
                pending = {id(hitter) for hitter in hitters}
                client.quest_party_realm_unsynced = set(pending)
                sync = {'pending': pending, 'done': set(), 'failed': {}}
                client.quest_rotating_realm_sync = sync
                deadline = time.monotonic() + 45.0
                while time.monotonic() < deadline and client.questing_status:
                    if sync['failed'] or sync['done'] == pending:
                        break
                    await asyncio.sleep(.25)
                if sync['done'] != pending:
                    raise RuntimeError(f"打手 Realm 同步未完成：{sync['failed'] or '等待超时'}")
                logger.info('自动任务：Realm 与打手同步完成，继续自动任务。')
            else:
                logger.info('自动任务：Realm 切换完成，继续自动任务。')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'自动任务：旋转旋转 Realm 切换失败：{exc}')
        finally:
            client.quest_rotating_realm_sync = None
            release_quest_recovery(client, 'rotating_realm')
        return True

    async def dungeon_recall(self, p: Client) -> Optional[bool]:
        original_zone = await p.zone_name()
        dungeon_recalled = await click_window_until_closed(p, dungeon_recall_path)

        if dungeon_recalled:
            while original_zone == await p.zone_name():
                await asyncio.sleep(1.0)
                dungeon_full = await click_window_until_closed(p, friend_is_busy_and_dungeon_reset_path)
                if dungeon_full:
                    await asyncio.sleep(1.0)
                    return False

            return True

    async def open_character_screen(self, p: Client):
        while not await is_visible_by_path(p, close_spellbook_path):
            await p.send_key(Keycode.C, 0.1)
            await asyncio.sleep(.3)

    async def close_character_screen(self, p: Client):
        while await is_visible_by_path(p, close_spellbook_path):
            await p.send_key(Keycode.C, 0.1)
            await asyncio.sleep(.3)

    async def handle_dungeon_recall(self, follower_clients):
        # don't recall on secondary clients unless we've already successfully recalled on the primary
        dungeon_recalled = await self.dungeon_recall(self.current_leader_client)
        if dungeon_recalled:
            await gather_owned(*[self.dungeon_recall(p) for p in follower_clients])

    async def auto_pet_questing(self, questing_clients: list[Client], ignore_pet_level_up, play_dance_game):
        auto_pet_on = False
        for p in self.clients:
            if p.auto_pet_status:
                auto_pet_on = True
                break

        # train pet on all clients if any questing client levels up
        if auto_pet_on:
            any_client_leveled_up = False
            for c in questing_clients:
                char_level = await c.stats.reference_level()
                if char_level > c.character_level:
                    any_client_leveled_up = True
                    break

            if any_client_leveled_up:
                logger.debug('One or more questing clients leveled up - training pets on all questing clients.')
                await gather_owned(*[auto_pet(c, ignore_pet_level_up, play_dance_game, questing=True) for c in self.clients])

    async def handle_zone_correction(self, maybe_solo_zone: bool, questing_friend_tp: bool, gear_switching_in_solo_zones: bool):
        # If zone changed, try to determine if we are in a solo zone
        if len(self.clients) > 1:
            maybe_solo_zone = await self.determine_solo_zone()

        # if followers in wrong zone, first attempt to click X - this may send them into the next zone if they are near an interactible door
        # don't do this when friend tp is active (as x press correction for some reason appears to be inconsistent)
        if not await self.followers_in_correct_zone() or maybe_solo_zone:
            logger.debug('Clients may be in wrong zone - attempting to correct with X press')
            await self.X_press_zone_recorrect()

            # paths to exit out of when they pop up near the end of the loop
            end_of_loop_paths = (exit_recipe_shop_path, exit_equipment_shop_path, cancel_multiple_quest_menu_path, cancel_spell_vendor, exit_snack_shop_path, exit_reagent_shop_path, exit_tc_vendor, exit_minigame_sigil, exit_wysteria_tournament, exit_dungeon_path, exit_zafaria_class_picture_button, exit_pet_leveled_up_button_path, avalon_badge_exit_button_path, potion_exit_path)
            await gather_owned(*[exit_menus(c, end_of_loop_paths) for c in self.clients])

        if not await self.followers_in_correct_zone() or maybe_solo_zone:
            if not questing_friend_tp:
                # if we still aren't in correct zone, send all to hub and retry
                await self.zone_recorrect_hub()
            else:
                # if still in the wrong zone, try friend teleport
                maybe_solo_zone = await self.zone_recorrect_friend_tp(maybe_solo_zone, gear_switching_in_solo_zones)

                await asyncio.sleep(2.0)

    async def handle_dungeon_entry(self, questing_friend_tp: bool, follower_clients: list[Client], entry_zone: str):
        # await self.enter_dungeon()
        await gather_owned(*[c.wait_for_zone_change() for c in self.clients])

        await asyncio.sleep(1.0)
        await self._confirm_dungeon_entry(self.current_leader_client, entry_zone)

        # check for instance desync, and attempt to fix if it has happened
        if questing_friend_tp:
            num_visible_clients = await self.num_clients_in_same_area()

            if num_visible_clients == len(self.clients):
                pass

            elif num_visible_clients > 1:
                # teleport all, this clearly isn't a solo zone
                logger.debug('One or more clients was separated from the group - teleporting all to leader')
                await self.correct_dungeon_desync(follower_clients)

    async def handle_npc_talking_quests(
        self, talking_client: Client, present_clients: list[Client]
    ) -> bool:
        if not claim_quest_recovery(talking_client, "npc_dialogue"):
            self._log_npc_retry_wait(talking_client, f"recovery owned by {talking_client.quest_recovery_owner}")
            return False
        try:
            async with automation_owner(talking_client, 'npc-dialogue-transaction'):
                updated = await self._handle_npc_talking_quests(talking_client, present_clients)
            if updated:
                self._npc_retry_exhausted.pop(id(talking_client), None)
                self._npc_retry_debug_at.pop(id(talking_client), None)
        except asyncio.CancelledError:
            self._npc_retry_exhausted.pop(id(talking_client), None)
            self._npc_retry_debug_at.pop(id(talking_client), None)
            raise
        except Exception as exc:
            self._npc_retry_exhausted.pop(id(talking_client), None)
            self._log_npc_retry_wait(talking_client, f"NPC state unavailable: {exc}")
            updated = False
        finally:
            release_quest_recovery(talking_client, "npc_dialogue")
            talking_client.quest_dialogue_settle = {'snapshot': None, 'since': None}
        if not updated:
            # Run only after releasing our owner; otherwise recovery blocks itself.
            await self._maybe_refresh_stalled_dungeon_quest(talking_client)
        return updated

    def _log_npc_retry_wait(self, client: Client, reason: str) -> None:
        now = time.monotonic()
        previous = self._npc_retry_debug_at.get(id(client))
        if previous is None or previous[0] != reason or now >= previous[1]:
            logger.debug(f"Client {client.title} - NPC interaction deferred: {reason}")
            self._npc_retry_debug_at[id(client)] = (reason, now + 30.0)

    async def _handle_npc_talking_quests(
        self, talking_client: Client, present_clients: list[Client]
    ) -> bool:
        """Wait for NPC dialogue and confirm the tracked quest actually advances."""

        async def read_quest_state():
            quest_text = await self.read_quest_txt(talking_client)
            try:
                quest_xyz = await talking_client.quest_position.position()
            except Exception:
                quest_xyz = None
            try:
                quest_id = await talking_client.quest_id()
            except Exception:
                quest_id = None
            try:
                goal_id = await talking_client.goal_id()
            except Exception:
                goal_id = None
            return quest_text, quest_xyz, quest_id, goal_id

        def quest_state_changed(before, after) -> bool:
            before_text, before_xyz, before_id, before_goal = before
            after_text, after_xyz, after_id, after_goal = after
            if not after_text or not isinstance(after_id, int) or after_id < 0:
                return False
            if before_id is not None and before_id != after_id:
                return True
            if before_goal is not None and after_goal is not None and before_goal != after_goal:
                return True
            if before_text and after_text and after_text != before_text:
                return True
            if before_xyz is not None and after_xyz is not None:
                return calc_Distance(before_xyz, after_xyz) > 1.0
            return False

        initial_state = await read_quest_state()
        retry_key = id(talking_client)
        if (not initial_state[0] or not isinstance(initial_state[2], int)
                or initial_state[2] <= 0):
            self._log_npc_retry_wait(talking_client, "quest snapshot unreadable")
            return False
        zone = await talking_client.zone_name()
        if not talking_client.questing_status or not await is_free_leader_questing(talking_client):
            # A new dialogue/transition ends the old failed interaction round.
            self._npc_retry_exhausted.pop(retry_key, None)
            self._log_npc_retry_wait(talking_client, "dialogue, combat or transition active")
            return False
        exhausted_state = self._npc_retry_exhausted.get(retry_key)
        if exhausted_state is not None:
            unchanged = (zone == exhausted_state['zone']
                         and not quest_state_changed(exhausted_state['snapshot'], initial_state))
            if unchanged and time.monotonic() < exhausted_state['retry_at']:
                self._log_npc_retry_wait(talking_client, "retry cooldown; waiting for quest progress")
                return False
            self._npc_retry_exhausted.pop(retry_key, None)
            self._npc_retry_debug_at.pop(retry_key, None)
        mainline_turn_in = await self._mainline_turn_in_snapshot(
            talking_client, initial_state[2]
        )
        after_talking_paths = (
            exit_zafaria_class_picture_button,
            exit_pet_leveled_up_button_path,
            avalon_badge_exit_button_path,
        )

        # One normal interaction, followed by at most three position/view retries.
        for attempt in range(1, 5):
            if attempt == 1:
                logger.debug(f"Client {talking_client.title} - Talking to NPC: sending X.")
                await gather_owned(
                    *[p.send_key(Keycode.X, 0.1) for p in present_clients]
                )
            else:
                logger.warning(
                    f"Client {talking_client.title} - Quest did not update after "
                    f"dialogue; adjusting position and view before retry "
                    f"({attempt - 1}/3)."
                )
                if not getattr(talking_client, "questing_status", False) or not await is_free_leader_questing(talking_client):
                    return False
                await talking_client.send_key(Keycode.A, 0.3)
                try:
                    camera = await talking_client.game_client.selected_camera_controller()
                    if camera is not None:
                        orientation = await camera.orientation()
                        await camera.update_orientation(
                            Orient(orientation.pitch, orientation.roll, orientation.yaw + 0.12)
                        )
                except Exception as exc:
                    logger.debug(f"Client {talking_client.title} - Could not adjust view for NPC retry: {exc}")
                await asyncio.sleep(0.3)
                if quest_state_changed(initial_state, await read_quest_state()):
                    await self._continue_mainline_chain(talking_client, mainline_turn_in)
                    return True
                if (not getattr(talking_client, "questing_status", False)
                        or not await is_free_leader_questing(talking_client)):
                    return False
                try:
                    # quest_interaction_ready reads the HUD objective; do not
                    # wait forever if it disappeared during the dialogue.
                    async with asyncio.timeout(2.0):
                        ready = await self.quest_interaction_ready(
                            talking_client, await talking_client.quest_position.position()
                        )
                except Exception as exc:
                    logger.debug(f"Client {talking_client.title} - NPC retry target unavailable: {exc}")
                    ready = False
                if not ready or interaction_kind(await self.read_popup(talking_client)) != "talk":
                    continue
                await talking_client.send_key(Keycode.X, 0.15)

            # A missed X press previously left this loop waiting forever.  Give
            # the game a few seconds to display dialogue, then retry the input.
            appear_deadline = time.monotonic() + 4.0
            while (
                await is_free_leader_questing(talking_client)
                and time.monotonic() < appear_deadline
            ):
                if quest_state_changed(initial_state, await read_quest_state()):
                    await asyncio.sleep(1.0)
                    await gather_owned(
                        *[exit_menus(c, after_talking_paths) for c in present_clients]
                    )
                    await self._continue_mainline_chain(talking_client, mainline_turn_in)
                    return True
                await asyncio.sleep(0.15)

            if await is_free_leader_questing(talking_client):
                continue

            logger.info(
                f"Client {talking_client.title} - Detected dialogue; waiting "
                "for it and the quest update to finish."
            )

            # Dialogue may briefly close and immediately open another page or
            # reward window.  Require a quiet period before allowing movement.
            quiet_since = None
            dialogue_deadline = time.monotonic() + 60.0
            while time.monotonic() < dialogue_deadline:
                dialogue_active = await self._advance_npc_dialogue(talking_client)
                await close_npc_quest_menu(talking_client)
                if not dialogue_active and await is_free_leader_questing(talking_client):
                    if quiet_since is None:
                        quiet_since = time.monotonic()
                    elif time.monotonic() - quiet_since >= 3.0:
                        break
                else:
                    quiet_since = None
                await asyncio.sleep(0.15)

            await gather_owned(
                *[exit_menus(c, after_talking_paths) for c in present_clients]
            )

            # Leave extra time for the server to publish the new objective.
            await asyncio.sleep(1.25)
            if quest_state_changed(initial_state, await read_quest_state()):
                logger.debug(
                    f"Client {talking_client.title} - Quest update confirmed."
                )
                await self._continue_mainline_chain(talking_client, mainline_turn_in)
                return True

            await asyncio.sleep(1.25)
            if quest_state_changed(initial_state, await read_quest_state()):
                logger.debug(
                    f"Client {talking_client.title} - Delayed quest update confirmed."
                )
                await self._continue_mainline_chain(talking_client, mainline_turn_in)
                return True

        final_state = await read_quest_state()
        if quest_state_changed(initial_state, final_state):
            await self._continue_mainline_chain(talking_client, mainline_turn_in)
            return True
        self._npc_retry_exhausted[retry_key] = {
            'snapshot': final_state, 'zone': zone, 'retry_at': time.monotonic() + 30.0,
        }
        logger.error(
            f"Client {talking_client.title} - NPC interaction made no quest progress "
            "after 3 position/view retries; releasing recovery and cooling down "
            "for 30 seconds before another bounded attempt."
        )
        return False

    async def _mainline_identity(self, client: Client):
        """Return a tracked quest only when its ID and quest data are readable."""
        from src.mainline_progress import match_quest, quest_rows

        rows = quest_rows()
        if not rows:
            return None
        try:
            quest_id = await client.quest_id()
            if quest_id == 0:
                return 0, '', '', None, None
            if not isinstance(quest_id, int) or quest_id < 0:
                return None
            quest = (await (await client.quest_manager()).quest_data()).get(quest_id)
            if quest is None:
                return None
            code = await quest.name_lang_key() or ''
            mainline = await quest.mainline()
            title = ''
            if code and code != 'Quest Finder':
                try:
                    title = await client.cache_handler.get_langcode_name(code) or ''
                except Exception:
                    pass
            row = match_quest(rows, quest_id, code, title)
            diagnostic = (quest_id, code, title, bool(row), mainline)
            if getattr(client, '_mainline_identity_debug', None) != diagnostic:
                logger.debug('{} 主线匹配：Quest ID {}，Language Key {!r}，标题 {!r}，游戏标志 {} -> {}',
                             client.title, quest_id, code, title, mainline,
                             f"{row['world']} 第 {row['number']}" if row else '未匹配')
                client._mainline_identity_debug = diagnostic
            return quest_id, code, title, row, mainline
        except Exception as exc:
            logger.debug('{} 主线身份暂不可读：{}', client.title, exc)
            return None

    async def _mainline_finder_blocked(self, client: Client) -> bool:
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'bumbles_pet_pending', False) is True
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'mainline_chain_retry_active', False)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                or getattr(client, 'entity_detect_combat_status', False)
                or getattr(client, 'post_combat_movement_active', False)
                or self._krok_exit_watch.get(id(client))
                or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                and client.quest_dungeon_recovery.get('active')):
            return True
        if (await client.is_loading() or await client.in_battle()
                or getattr(client, 'entity_detect_combat_status', False)
                or not await is_free_leader_questing(client)):
            return True
        return (await is_spiral_door_open(client)
                or await is_visible_by_path(client, decline_quest_path)
                or await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                or await is_visible_by_path(client, quest_buttons_parent_path)
                or await is_visible_by_path(client, exit_dungeon_path)
                or await is_visible_by_path(client, dungeon_warning_path))

    async def _advance_npc_dialogue(self, client: Client) -> bool:
        """One shared, serialized dialogue/offer step for task and dialogue workers."""
        owner = getattr(client, 'quest_recovery_owner', None)
        if isinstance(owner, str) and owner != 'npc_dialogue':
            return False
        async with automation_owner(client, 'npc-dialogue-step'):
            if await client.is_loading() or await client.in_battle():
                return False
            button = await get_window_from_path(client.root_window, advance_dialog_path)
            if not button or not await button.is_visible():
                if getattr(client, '_npc_dialogue_debug', None) is not None:
                    logger.debug('{} NPC 对话/邀请按钮已关闭，等待任务数据稳定。', client.title)
                    client._npc_dialogue_debug = None
                return False
            caption = plain_text(await self._window_text(button)).casefold()
            invitation = (caption in ('接受', 'accept')
                          or await is_visible_by_path(client, decline_quest_path))
            diagnostic = (caption, invitation)
            if getattr(client, '_npc_dialogue_debug', None) != diagnostic:
                logger.debug('{} NPC 状态：按钮 {!r}，任务邀请 {}', client.title, caption, invitation)
                client._npc_dialogue_debug = diagnostic
            now = time.monotonic()
            state = getattr(client, 'quest_invitation_state', None)
            if invitation:
                if not isinstance(state, dict) or state.get('closed'):
                    state = {'before_id': await client.quest_id(), 'attempts': 0,
                             'next_at': 0.0, 'failed': False}
                    client.quest_invitation_state = state
                if now < state['next_at'] or state['failed']:
                    return True
                if state['attempts'] >= 3:
                    state['failed'] = True
                    logger.debug('{} 任务邀请接受重试耗尽，关闭邀请后交由现有主线恢复。', client.title)
                    await client.send_key(Keycode.ESC, 0.1)
                    return True
                # Re-read the actual UI while holding the same input owner.
                if await button.is_visible() and not await button.is_control_grayed():
                    await self._click_ui_window(client, button)
                    state['attempts'] += 1
                    state['next_at'] = now + 1.5
                    logger.debug('{} 任务邀请：点击接受 {}/3，接取前 Quest ID {}',
                                 client.title, state['attempts'], state['before_id'])
                    client.quest_dialogue_settle = {'snapshot': None, 'since': None}
            else:
                next_at = getattr(client, '_npc_dialogue_next_at', 0.0)
                if not isinstance(next_at, (int, float)) or now >= next_at:
                    await client.send_key(Keycode.SPACEBAR)
                    client._npc_dialogue_next_at = now + .25
            return True

    async def _quest_dialogue_blocks_movement(self, client: Client) -> bool:
        """Wait for a quiet, stable tracked quest after dialogue/transition."""
        navigation = getattr(client, 'quest_lemuria_navigation_recovery', None)
        if (isinstance(navigation, dict)
                and isinstance(getattr(client, 'quest_dialogue_settle', None), dict)):
            navigation['since'] = None
        if await self._advance_npc_dialogue(client):
            if isinstance(navigation, dict):
                navigation['since'] = None
            client.quest_dialogue_settle = {'snapshot': None, 'since': None}
            return True
        if not await is_free_leader_questing(client):
            if isinstance(navigation, dict):
                navigation['since'] = None
            client.quest_dialogue_settle = {'snapshot': None, 'since': None}
            return True
        state = getattr(client, 'quest_dialogue_settle', None)
        if not isinstance(state, dict):
            return False
        try:
            snapshot = (await client.quest_id(), await client.goal_id(), await client.zone_name())
        except Exception:
            state.update(snapshot=None, since=None)
            return True
        now = time.monotonic()
        if state['snapshot'] != snapshot or state['since'] is None:
            state.update(snapshot=snapshot, since=now)
            return True
        if now - state['since'] < 3.0:
            return True
        invitation = getattr(client, 'quest_invitation_state', None)
        if isinstance(invitation, dict) and not invitation.get('closed'):
            identity = await self._mainline_identity(client)
            accepted = bool(identity and identity[0] != invitation['before_id']
                            and identity[3] is not None)
            logger.debug('{} 任务接取稳定：Quest ID {} -> {}，下一主线确认 {}',
                         client.title, invitation['before_id'], snapshot[0], accepted)
            invitation.update(closed=True, confirmed=accepted)
            if not accepted and not invitation.get('npc_retry_done'):
                invitation['npc_retry_done'] = True
                previous = getattr(client, 'mainline_last_turn_in_snapshot', None)
                if previous is not None:
                    await self._continue_mainline_chain(client, previous)
                    state.update(snapshot=None, since=None)
                    return True
        client.quest_dialogue_settle = None
        return False

    async def _mainline_sync_blocks_movement(self, client: Client) -> bool:
        """Check the live questers before any ordinary group quest movement."""
        members = list(getattr(client, 'quest_mainline_sync_members', []))
        if len(members) < 2:
            return False
        now = time.monotonic()
        group_log = members[0]
        identities = {}
        waiting = []
        for member in members:
            navigation = getattr(member, 'quest_lemuria_navigation_recovery', None)
            if isinstance(navigation, dict) and navigation.get('holding'):
                member.quest_mainline_sync_state = None
                waiting.append(f'{member.title} 等待任务助手导航恢复')
                continue
            if not getattr(member, 'questing_status', False):
                waiting.append(f'{member.title} 等待任务客户端恢复')
                continue
            if (await self._quest_dialogue_blocks_movement(member)
                    or getattr(member, 'mainline_chain_retry_active', False)
                    or isinstance(getattr(member, 'quest_recovery_owner', None), str)
                    or await is_visible_by_path(member, decline_quest_path)
                    or await is_visible_by_path(member, cancel_multiple_quest_menu_path)
                    or await is_visible_by_path(member, quest_buttons_parent_path)):
                member.quest_mainline_sync_state = None
                waiting.append(f'{member.title} 对话或切区中')
                continue
            try:
                snapshot = (await member.quest_id(), await member.zone_name())
            except Exception:
                snapshot = None
            state = getattr(member, 'quest_mainline_sync_state', None)
            if not isinstance(state, dict) or state['snapshot'] != snapshot:
                member.quest_mainline_sync_state = {'snapshot': snapshot, 'since': now, 'reads': 1}
                waiting.append(f'{member.title} 等待任务稳定')
                continue
            state['reads'] += 1
            if snapshot is None or state['reads'] < 2 or now - state['since'] < 3.0:
                waiting.append(f'{member.title} 等待任务稳定')
                continue
            identity = await self._mainline_identity(member)
            if (identity is None or identity[0] != snapshot[0]
                    or identity[3] is None):
                waiting.append(f'{member.title} 等待接取')
                continue
            identities[id(member)] = identity

        quest_ids = {identity[0] for identity in identities.values()}
        positions = {(identity[3]['world'], identity[3]['number'])
                     for identity in identities.values()}
        blocked = bool(waiting or len(quest_ids) != 1 or len(positions) != 1)
        if not blocked:
            if getattr(group_log, 'quest_mainline_sync_log', None) != ('ready', next(iter(quest_ids))):
                row = next(iter(identities.values()))[3]
                titles = '/'.join(member.title for member in members)
                logger.info('主线同步完成：{} -> {} 第 {} 个 | {}',
                            titles, row['world'], row['number'], row.get('english', ''))
            group_log.quest_mainline_sync_log = ('ready', next(iter(quest_ids)))
            group_log.quest_mainline_sync_warning = None
            return False

        status = tuple(waiting) + tuple(
            f'{member.title} Quest ID {identities[id(member)][0]}'
            for member in members if id(member) in identities
        )
        if getattr(group_log, 'quest_mainline_sync_log', None) != ('waiting', status):
            logger.info('自动任务：主线任务已更新，等待分组客户端同步。')
            logger.info('主线同步：{}', ' | '.join(status))
            group_log.quest_mainline_sync_log = ('waiting', status)
        abnormal = [item.split(' ', 1)[0] for item in waiting if item.endswith('等待接取')]
        if len(quest_ids) > 1:
            newest = max(identity[3]['number'] for identity in identities.values())
            abnormal.extend(member.title for member in members
                            if id(member) in identities
                            and identities[id(member)][3]['number'] < newest)
        warning = tuple(dict.fromkeys(abnormal))
        if warning and getattr(group_log, 'quest_mainline_sync_warning', None) != warning:
            logger.warning('主线同步：{} 未接到当前主线，暂停任务传送。', '/'.join(warning))
            group_log.quest_mainline_sync_warning = warning
        if len(identities) == len(members) and len(quest_ids) > 1:
            rows = [identity[3] for identity in identities.values()]
            if len({row['world'] for row in rows}) == 1:
                target = max(identities.values(), key=lambda item: item[3]['number'])
                target_count = sum(item[0] == target[0] for item in identities.values())
                current = identities.get(id(client))
                snapshot = getattr(client, 'mainline_last_turn_in_snapshot', None)
                if (target_count >= max(1, len(members) - 1)
                        and current and current[3]['number'] < target[3]['number']
                        and isinstance(snapshot, tuple) and snapshot[0] == current[0]
                        and now >= getattr(client, 'mainline_sync_npc_retry_at', 0)):
                    client.mainline_sync_npc_retry_at = now + 60.0
                    logger.info('主线同步：正在为 {} 尝试原 NPC 续接。', client.title)
                    await self._continue_mainline_chain(client, snapshot, expected_id=target[0])
                    if await client.quest_id() != target[0]:
                        await self._maybe_recover_mainline(client, expected_id=target[0])
                    return True
                if (target_count >= max(1, len(members) - 1)
                        and current and current[3]['number'] < target[3]['number']
                        and snapshot is None):
                    await self._maybe_recover_mainline(client, expected_id=target[0])
                    return True
        # The client's own worker runs its existing NPC continuation first.
        # Only its existing finder can recover a stable, unmatched quest.
        if client in members and id(client) not in identities and not any(
                item.startswith(f'{client.title} 对话') or item.startswith(f'{client.title} 等待任务稳定')
                for item in waiting):
            await self._maybe_recover_mainline(client)
        return True

    async def _maybe_recover_mainline(self, client: Client, expected_id=None) -> bool:
        """Pause a confirmed side quest, then run one bounded Quest Finder pass."""
        if await is_visible_by_path(client, advance_dialog_path):
            await self._advance_npc_dialogue(client)
            self._mainline_finder_observations.pop(id(client), None)
            return True
        if not getattr(client, 'mainline_finder_enabled', False):
            self._mainline_finder_observations.pop(id(client), None)
            self._mainline_finder_retry_at.pop(id(client), None)
            client.mainline_finder_offer_guard = False
            return False
        # Instance objectives can legitimately be absent from the mainline index.
        # Give the existing quest-card recovery the iteration before Finder can
        # pause movement (including its stability/retry waiting iterations).
        dungeon_state = getattr(client, 'quest_dungeon_recovery', None)
        dungeon_zone = getattr(client, 'quest_party_group_dungeon_zone', None)
        current_zone = await client.zone_name()
        if (current_zone in self.LEMURIA_DUNGEON_ROOMS
                and (not isinstance(dungeon_state, dict) or dungeon_state.get('zone') != current_zone)):
            client.quest_dungeon_recovery = dungeon_state = {
                'zone': current_zone, 'snapshot': None, 'since': None,
                'active': False, 'attempted': False, 'waiting_logged': False,
            }
        if isinstance(dungeon_state, dict) or isinstance(dungeon_zone, str):
            zone = await client.zone_name()
            if zone and (zone == dungeon_zone or isinstance(dungeon_state, dict)
                         and zone == dungeon_state.get('zone')):
                self._mainline_finder_observations.pop(id(client), None)
                self._mainline_finder_retry_at.pop(id(client), None)
                client.mainline_finder_offer_guard = False
                if not isinstance(dungeon_state, dict):
                    client.quest_dungeon_recovery = {
                        'zone': zone, 'snapshot': None, 'since': None,
                        'active': False, 'attempted': False, 'waiting_logged': False,
                    }
                return await self._maybe_refresh_stalled_dungeon_quest(client)
        identity = await self._mainline_identity(client)
        key = id(client)
        if identity is None:
            observed = self._mainline_finder_observations.get(key)
            if observed is not None:
                observed['count'] = 0
                observed['since'] = time.monotonic()
                return True
            return True
        if identity[3] is not None and (expected_id is None or identity[0] == expected_id):
            self._mainline_finder_observations.pop(key, None)
            self._mainline_finder_retry_at.pop(key, None)
            client.mainline_finder_offer_guard = False
            return False

        # Mainline-only mode requires the configured index, not merely the
        # game's broad mainline flag. Wait for stable reads before recovery.
        try:
            zone = await client.zone_name()
        except Exception:
            zone = None
        if not zone:
            return True
        snapshot = (*identity[:3], zone)
        now = time.monotonic()
        observed = self._mainline_finder_observations.get(key)
        if observed is None or observed['snapshot'] != snapshot:
            observed = {'snapshot': snapshot, 'count': 0, 'since': now}
            self._mainline_finder_observations[key] = observed
        if await self._mainline_finder_blocked(client):
            observed['count'] = 0
            observed['since'] = now
            return True
        observed['count'] += 1
        stable_seconds = 3.0
        if (observed['count'] < self.MAINLINE_FINDER_STABLE_READS
                or now - observed['since'] < stable_seconds):
            return True
        if now < self._mainline_finder_retry_at.get(key, 0):
            return True
        if not claim_quest_recovery(client, 'mainline_finder'):
            return True

        logger.info('自动任务：当前追踪任务不属于主线，开始寻找主线任务。')
        recovered = False
        try:
            recovered = await (self._run_mainline_finder(client)
                               if expected_id is None else
                               self._run_mainline_finder(client, expected_id=expected_id))
            if recovered:
                self._mainline_finder_observations.pop(key, None)
                self._mainline_finder_retry_at.pop(key, None)
                logger.info('自动任务：已确认主线任务并恢复追踪，恢复正常自动任务。')
            else:
                self._mainline_finder_retry_at[key] = time.monotonic() + self.MAINLINE_FINDER_RETRY_SECONDS
                logger.warning('自动任务：本轮未能找到可确认的主线任务，退出主线恢复流程。')
                logger.info('自动任务：暂停非主线传送，60 秒后重试；手动切回主线可立即恢复。')
        except Exception as exc:
            self._mainline_finder_retry_at[key] = time.monotonic() + self.MAINLINE_FINDER_RETRY_SECONDS
            logger.warning('自动任务：主线找回失败，暂停非主线传送：{}', exc)
            logger.info('自动任务：60 秒后重试；手动切回主线可立即恢复。')
        finally:
            # A dialogue can outlive the recovery coroutine.  Keep the normal
            # dialogue worker from accepting that leftover offer on its next tick.
            client.mainline_finder_offer_guard = (
                not recovered and getattr(client, 'mainline_finder_enabled', False)
            )
            try:
                try:
                    if await is_visible_by_path(client, cancel_multiple_quest_menu_path):
                        await close_npc_quest_menu(client)
                except Exception as exc:
                    logger.debug('主线找回清理 NPC 菜单失败：{}', exc)
            finally:
                release_quest_recovery(client, 'mainline_finder')
        return True

    @staticmethod
    async def _visible_window_nodes(window, path):
        """Inspect only a known UI subtree; never infer a click from pixels."""
        pending = [(window, tuple(path))]
        result = []
        while pending:
            if len(result) + len(pending) > 300:
                raise RuntimeError('UI 子控件过多，无法安全定位')
            current, current_path = pending.pop(0)
            if not await current.is_visible():
                continue
            result.append((current, current_path))
            for child in await current.children():
                pending.append((child, (*current_path, await child.name())))
        return result

    @staticmethod
    async def _window_text(window) -> str:
        try:
            return plain_text(await read_control_text(window))
        except Exception:
            try:
                return plain_text(await window.maybe_text())
            except Exception:
                return ''

    async def _questbook_page(self, client: Client, mainlines=None):
        menu = await get_window_from_path(client.root_window, quest_buttons_parent_path)
        if not menu or not await menu.is_visible():
            raise RuntimeError('Q 任务菜单未显示')
        nodes = await self._visible_window_nodes(menu, quest_buttons_parent_path)
        cards = [(window, path) for window, path in nodes
                 if re.fullmatch(r'wndQuestInfo\d+', path[-1])]
        if not cards:
            raise RuntimeError('当前任务页没有可读取的任务卡片')

        signature = []
        finder = []
        for card, card_path in cards:
            descendants = [(window, path) for window, path in nodes
                           if path[:len(card_path)] == card_path]
            texts = [(await self._window_text(window), window, path)
                     for window, path in descendants]
            signature.append(tuple(text for text, _, _ in texts if text))
            if mainlines is not None:
                title = next((text for text, _, path in texts
                              if ('title' in path[-1].casefold()
                                  or path[-1].casefold() in ('txtquestname', 'txtname')) and text), '')
                if title:
                    target = next(((window, path) for _, window, path in texts
                                   if path[-1] == 'txtGoal'), (card, card_path))
                    mainlines.append((title, target))
            if any(text.casefold() in ('任务搜寻', 'quest finder')
                   for text, _, _ in texts):
                target = next(((window, path) for _, window, path in texts
                               if path[-1] == 'txtGoal'), (card, card_path))
                finder.append(target)
        if len(finder) > 1:
            raise RuntimeError('当前页出现多个“任务搜寻”卡片')

        candidates = [(window, path) for window, path in nodes
                      if re.search(r'right|next|forward', path[-1], re.I)
                      and not any(path[:len(card_path)] == card_path
                                  for _, card_path in cards)]
        if len(candidates) > 1:
            # Widget-relative geometry only disambiguates named candidates;
            # it never substitutes a fixed screen position for a UI identity.
            try:
                card_x = max((await card.scale_to_client()).center()[0]
                             for card, _ in cards)
                candidates = [(window, path) for window, path in candidates
                              if (await window.scale_to_client()).center()[0] > card_x]
            except Exception:
                pass
        next_page = candidates[0] if len(candidates) == 1 else None
        if next_page is None and not finder:
            direct = [path[-1] for _, path in nodes
                      if len(path) == len(quest_buttons_parent_path) + 1]
            logger.debug('任务菜单可见一级控件：{}', direct)
        return tuple(signature), finder[0] if finder else None, next_page

    @staticmethod
    async def _click_ui_window(client: Client, window) -> None:
        async with client.mouse_handler:
            await client.mouse_handler.click_window(window)

    async def _close_questbook(self, client: Client) -> None:
        if not await is_visible_by_path(client, quest_buttons_parent_path):
            return
        await client.send_key(Keycode.Q)
        deadline = time.monotonic() + 2.5
        while await is_visible_by_path(client, quest_buttons_parent_path):
            if time.monotonic() >= deadline:
                raise RuntimeError('任务菜单未成功关闭，暂停任务传送')
            await asyncio.sleep(.1)

    async def _restore_owned_mainline(self, client: Client, expected_id=None) -> bool:
        """Scan existing quest cards before searching for an unaccepted quest."""
        from src.mainline_progress import match_quest, normalize_name, quest_rows

        rows = quest_rows()
        owned = []
        quests = await (await client.quest_manager()).quest_data()
        for quest_id, quest in quests.items():
            if not isinstance(quest_id, int) or quest_id <= 0:
                continue
            try:
                code = await quest.name_lang_key() or ''
            except Exception as exc:
                logger.debug('已接任务 {} 的 Language Key 暂不可读：{}', quest_id, exc)
                continue
            if code.casefold() in ('quest finder', '任务搜寻', '任务搜索'):
                continue
            title = ''
            if code:
                try:
                    title = await client.cache_handler.get_langcode_name(code) or ''
                except Exception as exc:
                    # One special/untranslated quest must not abort the whole book.
                    logger.debug('已接任务 {} 标题解析失败，继续使用 Quest ID / Language Key：{}', quest_id, exc)
            row = match_quest(rows, quest_id, code, title or '')
            if row is not None:
                owned.append((quest_id, title or '', row))

        zone = await client.zone_name()
        world = zone.split('/', 1)[0].casefold()
        eligible = [item for item in owned if (
            expected_id is not None and item[0] == expected_id
            or expected_id is None
            and item[2]['world'].split('(', 1)[0].strip().casefold() == world)]
        if len({item[0] for item in eligible}) > 1:
            raise RuntimeError('存在多条可匹配的当前世界主线，无法唯一恢复追踪')
        try:
            if not await is_visible_by_path(client, quest_buttons_parent_path):
                await client.send_key(Keycode.Q)
            deadline = time.monotonic() + 4.0
            while not await is_visible_by_path(client, quest_buttons_parent_path):
                if time.monotonic() >= deadline:
                    raise RuntimeError('检查已接主线时任务菜单未稳定打开')
                await asyncio.sleep(.1)
            logger.info('自动任务：检查各页已接任务，优先恢复现有主线追踪。')
            seen = set()
            load_deadline = time.monotonic() + 4.0
            for page_number in range(1, self.MAINLINE_FINDER_MAX_PAGES + 1):
                while True:
                    cards = []
                    try:
                        signature, _, next_page = await self._questbook_page(client, mainlines=cards)
                        break
                    except RuntimeError as exc:
                        if (page_number != 1 or time.monotonic() >= load_deadline
                                or str(exc) not in ('Q 任务菜单未显示', '当前任务页没有可读取的任务卡片')):
                            raise
                        await asyncio.sleep(.1)
                if signature in seen:
                    break
                seen.add(signature)
                logger.debug('{} Q 菜单已检查第 {} 页（从打开时当前页开始），卡片数 {}',
                             client.title, page_number, len(cards))
                selected = None
                for title, _target in cards:
                    row = match_quest(rows, None, '', title)
                    matches = [item for item in owned
                               if normalize_name(item[1]) == normalize_name(title)
                               or row is not None and item[2] == row]
                    if row is not None and len(matches) != 1:
                        raise RuntimeError('任务列表中存在主线，但 Quest ID 暂无法唯一确认；暂不执行任务搜寻')
                    if len(matches) == 1:
                        quest_id, _, matched = matches[0]
                        if (expected_id is not None and quest_id == expected_id
                                or expected_id is None
                                and matched['world'].split('(', 1)[0].strip().casefold() == world):
                            selected = (quest_id, title, _target)
                            break
                if selected is not None:
                    quest_id, title, target = selected
                    logger.debug('{} Q 菜单第 {} 个检查页命中主线：{}，Quest ID {}，停止翻页',
                                 client.title, page_number, title, quest_id)
                    await self._click_ui_window(client, target[0])
                    await self._close_questbook(client)
                    stable_since = None
                    deadline = time.monotonic() + 8.0
                    while time.monotonic() < deadline:
                        identity = await self._mainline_identity(client)
                        if (identity and identity[0] == quest_id and identity[3] is not None
                                and await is_free_leader_questing(client)):
                            if stable_since is None:
                                stable_since = time.monotonic()
                            elif time.monotonic() - stable_since >= 3.0:
                                logger.info('自动任务：主线已接取，已恢复追踪：{} | Quest ID {}', title, quest_id)
                                return True
                        else:
                            stable_since = None
                        await asyncio.sleep(.2)
                    raise RuntimeError('主线已在任务列表中，但重新追踪尚未确认；暂不执行任务搜寻')
                if next_page is None:
                    raise RuntimeError('已接任务检查未完成：无法唯一识别右侧翻页控件')
                logger.debug('{} Q 菜单第 {} 个检查页未命中目标主线，才执行下一页。',
                             client.title, page_number)
                await self._click_ui_window(client, next_page[0])
                deadline = time.monotonic() + 2.5
                while True:
                    await asyncio.sleep(.1)
                    changed, _, _ = await self._questbook_page(client)
                    if changed != signature:
                        break
                    if time.monotonic() >= deadline:
                        # A one-page quest book may remain on the same page.
                        break
                if changed == signature:
                    raise RuntimeError('已接任务检查未完成：翻页未确认，暂不执行任务搜寻')
            else:
                raise RuntimeError('已接任务检查达到翻页保护上限，暂不执行任务搜寻')
            logger.info('自动任务：各页未找到可确认的目标主线，继续任务搜寻。')
            return False
        finally:
            await self._close_questbook(client)

    async def _select_quest_finder(self, client: Client) -> bool:
        try:
            await client.send_key(Keycode.Q)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if await is_visible_by_path(client, quest_buttons_parent_path):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('按 Q 后任务菜单未稳定打开')

            logger.info('自动任务：正在任务菜单中寻找“任务搜寻”。')
            seen = set()
            for _ in range(self.MAINLINE_FINDER_MAX_PAGES):
                signature, finder, next_page = await self._questbook_page(client)
                if signature in seen:
                    raise RuntimeError('任务页已循环回到检查过的页面')
                seen.add(signature)
                if finder:
                    logger.debug('任务搜寻 UI 路径：{}', list(finder[1]))
                    await self._click_ui_window(client, finder[0])
                    logger.info('自动任务：已找到“任务搜寻”，开始寻找可接主线。')
                    break
                if next_page is None:
                    raise RuntimeError('无法唯一识别右侧翻页控件')
                logger.debug('任务右翻页 UI 路径：{}', list(next_page[1]))
                await self._click_ui_window(client, next_page[0])
                refresh_deadline = time.monotonic() + 2.5
                while time.monotonic() < refresh_deadline:
                    await asyncio.sleep(0.1)
                    changed, _, _ = await self._questbook_page(client)
                    if changed != signature:
                        break
                else:
                    raise RuntimeError('点击右翻页后任务页未刷新')
            else:
                raise RuntimeError('任务页翻页达到保护上限')

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                identity = await self._mainline_identity(client)
                if identity and identity[1] == 'Quest Finder':
                    return True
                await asyncio.sleep(0.1)
            raise RuntimeError('点击“任务搜寻”后未确认追踪任务切换')
        finally:
            await self._close_questbook(client)

    async def _mainline_offer_candidate(self, client: Client):
        """Accept only a titled offer linked to one verified mainline Quest ID."""
        from src.mainline_progress import match_quest, quest_rows

        path = ['WorldView', 'wndDialogMain']
        dialog = await get_window_from_path(client.root_window, path)
        if not dialog or not await dialog.is_visible():
            return None
        rows = quest_rows()
        nodes = await self._visible_window_nodes(dialog, path)
        offered = []
        for window, node_path in nodes:
            name = node_path[-1].casefold()
            if not ('title' in name or 'quest' in name and 'name' in name):
                continue
            title = await self._window_text(window)
            row = match_quest(rows, None, '', title) if title else None
            if row is not None:
                offered.append((row, title))
        if len(offered) != 1:
            return None

        row, title = offered[0]
        candidates = []
        quests = await (await client.quest_manager()).quest_data()
        for quest_id, quest in quests.items():
            try:
                if not await quest.mainline():
                    continue
                code = await quest.name_lang_key() or ''
                resolved = await client.cache_handler.get_langcode_name(code) if code else ''
                if match_quest(rows, quest_id, code, resolved or '') == row:
                    candidates.append(quest_id)
            except Exception:
                continue
        if len(candidates) != 1:
            logger.debug('主线邀请“{}”无法唯一映射 Quest ID，拒绝自动接取。', title)
            return None
        return candidates[0], row

    async def _run_mainline_finder(self, client: Client, expected_id=None) -> bool:
        from src.mainline_progress import log_mainline_progress

        identity = await self._mainline_identity(client)
        if identity is None:
            return False
        if await self._restore_owned_mainline(client, expected_id=expected_id):
            return True
        if identity[1] != 'Quest Finder' and not await self._select_quest_finder(client):
            return False

        expected_quest_id = None
        accepted_at = None
        interaction_attempts = 0
        dialogue_since = None
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline and getattr(client, 'questing_status', False):
            identity = await self._mainline_identity(client)
            if identity and identity[3] is not None:
                if ((expected_id is None or identity[0] == expected_id)
                        and (expected_quest_id is None or identity[0] == expected_quest_id)):
                    client._xuanshu_mainline_id = None
                    await log_mainline_progress(client)
                    return True
                if identity[0] != expected_id:
                    await asyncio.sleep(0.2)
                    continue
                return False
            if identity and identity[1] != 'Quest Finder':
                return False
            if await client.in_battle():
                return False
            if await client.is_loading():
                await asyncio.sleep(0.2)
                continue
            if await is_visible_by_path(client, decline_quest_path):
                if accepted_at is not None:
                    if time.monotonic() - accepted_at > 5.0:
                        return False
                    await asyncio.sleep(0.2)
                    continue
                candidate = await self._mainline_offer_candidate(client)
                if candidate is None or expected_id is not None and candidate[0] != expected_id:
                    logger.warning('自动任务：无法在接取前确认邀请属于主线，拒绝接取。')
                    await client.send_key(Keycode.ESC, 0.1)
                    return False
                expected_quest_id = candidate[0]
                await click_window_by_path(client, advance_dialog_path)
                accepted_at = time.monotonic()
                await asyncio.sleep(0.4)
                continue
            if await is_visible_by_path(client, cancel_multiple_quest_menu_path):
                await close_npc_quest_menu(client)
                return False
            if await is_visible_by_path(client, advance_dialog_path):
                # Without offer identity, this right button could itself
                # accept a side quest.  Do not advance an unknown dialogue.
                if accepted_at is not None:
                    await asyncio.sleep(0.2)
                    continue
                if dialogue_since is None:
                    dialogue_since = time.monotonic()
                elif time.monotonic() - dialogue_since >= 5.0:
                    logger.warning('自动任务：任务对话未提供可确认的邀请，拒绝盲目推进。')
                    await client.send_key(Keycode.ESC, 0.1)
                    return False
                await asyncio.sleep(0.2)
                continue
            dialogue_since = None
            if not await is_free_leader_questing(client):
                await asyncio.sleep(0.2)
                continue

            target = await client.quest_position.position()
            if not all(math.isfinite(value) for value in (target.x, target.y, target.z)):
                return False
            if calc_Distance(target, XYZ(0.0, 0.0, 0.0)) <= 1.0:
                return False
            if (await is_visible_by_path(client, npc_range_path)
                    and calc_Distance(target, await client.body.position()) < 750.0
                    and interaction_kind(await self.read_popup(client)) == 'talk'):
                if interaction_attempts >= 2:
                    return False
                interaction_attempts += 1
                await client.send_key(Keycode.X, 0.1)
                await asyncio.sleep(1.0)
            else:
                await asyncio.wait_for(self.teleport_to_quest_target(client, target), timeout=25.0)
                await asyncio.sleep(0.3)
        return False

    async def _mainline_turn_in_snapshot(self, client: Client, quest_id):
        """Keep the quest and NPC identity from before the first X press."""
        if not isinstance(quest_id, int) or quest_id <= 0:
            return None
        try:
            identity = await self._mainline_identity(client)
            if identity is None or identity[0] != quest_id or identity[3] is None:
                return None
            npc = plain_text(await get_popup_title(client)).casefold()
            if not npc or interaction_kind(await self.read_popup(client)) != "talk":
                return None
            snapshot = (quest_id, await client.zone_name(), await client.body.position(), npc)
            client.mainline_last_turn_in_snapshot = snapshot
            return snapshot
        except Exception as exc:
            logger.trace("Mainline handoff snapshot unavailable: {}", exc)
            return None

    async def _continue_mainline_chain(self, client: Client, snapshot, expected_id=None) -> None:
        """Retry a confirmed mainline handoff only while the same NPC is in reach."""
        if snapshot is None:
            return
        previous_id, zone, anchor, npc = snapshot

        def invitation_pending():
            offer = getattr(client, 'quest_invitation_state', None)
            return isinstance(offer, dict) and offer.get('attempts', 0) > 0 and not offer.get('confirmed')

        async def current_quest():
            try:
                quest_id = await client.quest_id()
                if quest_id == 0:
                    return 0, None
                identity = await self._mainline_identity(client)
                return quest_id, bool(identity and identity[3] is not None
                                      and (quest_id != previous_id or not invitation_pending()))
            except Exception:
                return None, None

        async def ready_to_retry():
            if not getattr(client, "questing_status", False):
                return False
            if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                    and client.quest_recovery_owner != 'npc_dialogue'
                    or getattr(client, "quest_party_probe_pending", False)
                    or getattr(client, "quest_party_battle_rescue_active", False)
                    or getattr(client, "quest_party_quest_worker_restart_requested", False)):
                return False
            if not await is_free_leader_questing(client):
                return False
            if (await is_visible_by_path(client, decline_quest_path)
                    or await is_visible_by_path(client, cancel_multiple_quest_menu_path)):
                return False
            if await client.zone_name() != zone or calc_Distance(
                await client.body.position(), anchor
            ) > 300:
                return False
            if not await is_visible_by_path(client, npc_range_path):
                return False
            if interaction_kind(await self.read_popup(client)) != "talk":
                return False
            return plain_text(await get_popup_title(client)).casefold() == npc

        # The game can select an unrelated nonzero quest during a handoff.
        # Do not mistake it for the next mainline before dialogue settles.
        dialogue_active = await self._advance_npc_dialogue(client)
        quest_id, _ = await current_quest()
        if (quest_id is None or quest_id == previous_id and expected_id is None
                and not dialogue_active and not invitation_pending()
                or not getattr(client, "questing_status", False)):
            return
        logger.info('{} 当前主线已完成，等待下一主线。', client.title)

        # The quest ID can clear before the last dialogue/reward page appears.
        # Require three quiet seconds before treating the handoff as missing.
        quiet_since = None
        quiet_quest = None
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            dialogue_active = await self._advance_npc_dialogue(client)
            quest_id, _ = await current_quest()
            if quest_id is None or not getattr(client, "questing_status", False):
                return
            if await client.zone_name() != zone or await client.in_battle():
                return
            if not dialogue_active and await is_free_leader_questing(client):
                if quiet_since is None or quiet_quest != quest_id:
                    quiet_since = time.monotonic()
                    quiet_quest = quest_id
                elif time.monotonic() - quiet_since >= 3.0:
                    break
            else:
                quiet_since = None
            await asyncio.sleep(0.2)
        else:
            return

        quest_id, matched = await current_quest()
        if (matched and (expected_id is None or quest_id == expected_id)
                or quest_id == previous_id and expected_id is None and not invitation_pending()
                or not await ready_to_retry()):
            return

        client.mainline_chain_retry_active = True
        pressed = False
        try:
            for attempt in range(1, 3):
                quest_id, matched = await current_quest()
                if (matched and (expected_id is None or quest_id == expected_id)
                        or quest_id is None or not await ready_to_retry()):
                    return
                logger.info(
                    f"{client.title} 下一主线未出现，尝试重新与原 NPC 交互（{attempt}/2）。"
                )
                await client.send_key(Keycode.X, 0.15)
                pressed = True
                appeared = False
                quiet_since = None
                deadline = time.monotonic() + 25.0
                while time.monotonic() < deadline:
                    dialogue_active = await self._advance_npc_dialogue(client)
                    quest_id, mainline = await current_quest()
                    if quest_id is None:
                        return
                    if (mainline and (expected_id is None or quest_id == expected_id)
                            and not dialogue_active and await is_free_leader_questing(client)):
                        if quiet_since is None:
                            quiet_since = time.monotonic()
                        elif time.monotonic() - quiet_since >= 3.0:
                            logger.info(
                                f"{client.title} 已接取下一主线：Quest ID {quest_id}。"
                            )
                            return
                        await asyncio.sleep(0.2)
                        continue
                    if (not getattr(client, "questing_status", False)
                            or await client.is_loading() or await client.in_battle()
                            or await client.zone_name() != zone):
                        return
                    if await is_visible_by_path(client, cancel_multiple_quest_menu_path):
                        await close_npc_quest_menu(client)
                        return
                    if not dialogue_active and await is_free_leader_questing(client):
                        if quiet_since is None:
                            quiet_since = time.monotonic()
                        elif time.monotonic() - quiet_since >= (1.5 if appeared else 4.0):
                            break
                    else:
                        appeared = True
                        quiet_since = None
                    await asyncio.sleep(0.2)
                await asyncio.sleep(1.25)
            logger.warning(
                f"{client.title} 原 NPC 续接未成功，准备使用任务搜寻恢复主线。"
            )
        finally:
            client.mainline_chain_retry_active = False

    async def quest_interaction_ready(self, client, xyz, leader_client=None):
        if await client.is_loading() or await client.in_battle():
            return False
        if not await is_visible_by_path(client, npc_range_path):
            return False
        if calc_Distance(await client.body.position(), xyz) >= 750:
            return False
        title = await get_popup_title(client)
        if not title:
            return False
        objective = await get_quest_name(leader_client or client)
        return quest_interaction_matches(objective, title)

    async def move_until_quest_interaction(self, client, xyz, leader_client=None):
        # Let the existing interaction handler press X once movement has stopped.
        if await self.quest_interaction_ready(client, xyz, leader_client):
            return

        async def watch_interaction():
            while True:
                await asyncio.sleep(.1)
                if await self.quest_interaction_ready(client, xyz, leader_client):
                    return

        movement = asyncio.create_task(collision_tp(client, xyz, leader_client=leader_client))
        watcher = asyncio.create_task(watch_interaction())
        try:
            done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
                movement.cancel()
            if movement in done:
                await movement
        finally:
            movement.cancel()
            watcher.cancel()
            await gather_owned(movement, watcher, return_exceptions=True)

    async def _bumbles_pet_stage(self, client):
        if await client.zone_name() != self.BUMBLES_PET_ZONE:
            return None
        identity = await self._mainline_identity(client)
        if not identity or not identity[0]:
            return None
        # Reuse tracked quest lookup. Never derive QuestID from a language suffix.
        code, title, row = identity[1:4]
        if code and code != 'QuestTitle_17442D':
            return None
        if not code and not (row and row.get('world') == 'LEMURIA' and row.get('number') == 83
                             and title in ('Nose for Clues', '嗅探线索')):
            return None
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or snapshot[0] != identity[0]:
            return None
        try:
            quest = (await (await client.quest_manager()).quest_data()).get(identity[0])
            goal = (await quest.goal_data()).get(snapshot[1])
            if goal is None:
                return None
            goal_code = await goal.name_lang_key()
        except Exception:
            return None
        if goal_code and goal_code != 'WizQst17442D_00000005':
            return None
        objective, location = split_quest_location(snapshot[2])
        objective = re.sub(r'\s+', '', objective).casefold()
        if objective not in ('使用宠物模式寻找大黄蜂', 'petmodetofindbumbles'):
            return None
        if location and re.sub(r'\s+', '', location).casefold() not in ('废料堆', 'heap'):
            return None
        if (await client.quest_id(), await client.goal_id()) != snapshot[:2]:
            return None
        return snapshot, goal_code, code

    async def _play_as_pet_state(self, client):
        """Tri-state mode check from the game's cancel/start action tooltip."""
        try:
            button = await get_window_from_path(client.root_window, play_as_pet_button_path)
            if not button:
                return None
            tip = await button.tip()
            actions = (
                ('GUI2_00001155', 'Cancel Play as Pet', True),
                ('GUI2_00001154', 'Play as Pet (Free)', False),
                ('GUI2_00001181', 'Play as Pet (costs 5 Happiness per Minute)', False),
            )
            for key, english, active in actions:
                if tip in (key, f'<string;{key}>') or plain_text(tip) == english:
                    return active
            for key, _english, active in actions:
                try:
                    localized = await client.cache_handler.get_langcode_name(key)
                except Exception:
                    continue
                if localized and plain_text(tip) == plain_text(localized):
                    return active
        except Exception:
            pass
        return None  # Missing UI/unknown tooltip never proves either mode.

    async def _finish_bumbles_pet(self, client, state):
        """Cancel pet mode once after verified stage progression; never toggle twice."""
        if (state.get('cancel_attempted')
                or getattr(client, 'refilling_potions', False)
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or await client.is_loading() or await client.in_battle()
                or await client.is_in_dialog() or not await is_free_leader_questing(client)):
            return
        if not claim_quest_recovery(client, 'bumbles_pet'):
            return
        try:
            async with automation_owner(client, 'bumbles-pet-finish'):
                async with asyncio.timeout(10):
                    async def safe():
                        snapshot = await self._dungeon_quest_snapshot(client)
                        return (client.questing_status and snapshot is not None
                                and snapshot != state['snapshot']
                                and not getattr(client, 'refilling_potions', False)
                                and not await client.is_loading() and not await client.in_battle()
                                and not await client.is_in_dialog() and await is_free_leader_questing(client))
                    if not await safe() or await self._play_as_pet_state(client) is not True:
                        return
                    button = await get_window_from_path(client.root_window, play_as_pet_button_path)
                    if not button or not await button.is_visible():
                        menu = await get_window_from_path(client.root_window, pet_system_button_path)
                        if not menu or not await menu.is_visible() or await menu.is_control_grayed():
                            raise RuntimeError('宠物菜单不可操作')
                        # Only one menu-open attempt across worker recreation.
                        if state.get('cancel_menu_attempted'):
                            return
                        state['cancel_menu_attempted'] = True
                        await click_window_by_path(client, pet_system_button_path)
                        await asyncio.sleep(.4)
                    button = await get_window_from_path(client.root_window, play_as_pet_button_path)
                    if (not button or not await button.is_visible() or await button.is_control_grayed()):
                        raise RuntimeError('取消扮演宠物按钮不可操作')
                    if not await safe() or await self._play_as_pet_state(client) is not True:
                        return
                    state['cancel_attempted'] = True
                    await click_window_by_path(client, play_as_pet_button_path)
                    stable_since = None
                    deadline = time.monotonic() + 7
                    while time.monotonic() < deadline:
                        if not await safe():
                            raise RuntimeError('取消宠物模式期间任务/客户端状态不稳定')
                        if await self._play_as_pet_state(client) is False:
                            if stable_since is None:
                                stable_since = time.monotonic()
                            elif time.monotonic() - stable_since >= 1:
                                state['wizard_confirmed'] = True
                                logger.info('自动任务：宠物任务步骤已完成，已取消扮演宠物，恢复正常任务传送。')
                                return
                        else:
                            stable_since = None
                        await asyncio.sleep(.2)
                    raise TimeoutError('取消后未确认巫师模式；不重复切换按钮')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not state.get('cancel_warned'):
                logger.warning('自动任务：取消扮演宠物未完成，继续暂停普通 TP。{}', exc)
                state['cancel_warned'] = True
        finally:
            release_quest_recovery(client, 'bumbles_pet')

    async def _maybe_handle_bumbles_pet(self, client):
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(member, 'quest_party_hitters', []) for member in self.clients)):
            return False
        owner = 'bumbles_pet'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        state = getattr(client, 'quest_bumbles_pet', None)
        stage = await self._bumbles_pet_stage(client)
        if stage is None and not isinstance(state, dict):
            return False
        mode = await self._play_as_pet_state(client)
        if stage is None:
            snapshot = await self._dungeon_quest_snapshot(client)
            zone = await client.zone_name()
            changed = ((snapshot is not None and snapshot != state['snapshot'])
                       or zone and zone != self.BUMBLES_PET_ZONE)
            if mode is True and snapshot is not None and snapshot != state['snapshot']:
                client.bumbles_pet_pending = True
                await self._finish_bumbles_pet(client, state)
                mode = await self._play_as_pet_state(client)
                # A single start tooltip read after a timed-out cancel is not
                # stable confirmation. A later iteration may re-observe it.
                if mode is False and not state.get('wizard_confirmed'):
                    state['wizard_since'] = time.monotonic()
                    return True
            if mode is False and changed:
                if not state.get('wizard_confirmed'):
                    if state.get('wizard_since') is None:
                        state['wizard_since'] = time.monotonic()
                        return True
                    if time.monotonic() - state['wizard_since'] < 1:
                        return True
                client.quest_bumbles_pet = None
                client.bumbles_pet_pending = False
                for member in getattr(client, 'quest_mainline_sync_members', [client]):
                    member.quest_mainline_sync_state = None
                return False
            state['wizard_since'] = None
            # Do not send wizard-body TP to a possibly controlled pet. Keep the
            # existing task barrier and read progress while awaiting wizard mode.
            client.bumbles_pet_pending = True
            if changed and not state.get('compatibility_logged'):
                logger.warning('自动任务：宠物步骤已变化，但仍处于宠物模式或状态不可读；暂停未确认兼容的普通 TP。')
                state['compatibility_logged'] = True
            return True
        if not isinstance(state, dict) or state.get('snapshot') != stage[0]:
            state = dict(snapshot=stage[0], goal_code=stage[1], quest_code=stage[2],
                         attempted=False, confirmed=False, warned=False)
            client.quest_bumbles_pet = state
            logger.debug('{} 宠物任务识别：Quest ID={}，Quest Key={}，Goal ID={}，Goal Key={}',
                         client.title, stage[0][0], stage[2], stage[0][1], stage[1])
        client.bumbles_pet_pending = bool(state['attempted'] or mode is True)
        if mode is True:
            if not state['confirmed']:
                logger.info('自动任务：宠物模式已开启，继续任务流程。')
            state['confirmed'] = True
            # This waypoint is completed by becoming a pet at the supplied
            # position. Re-read it; no additional ordinary teleport is required.
            await self._dungeon_quest_snapshot(client)
            return True
        if state['attempted']:
            return True  # No entire-workflow retry, even after worker recreation.
        if (getattr(client, 'refilling_potions', False)
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'mainline_chain_retry_active', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'post_combat_movement_active', False)
                or await client.is_loading() or await client.in_battle()
                or await client.is_in_dialog() or not await is_free_leader_questing(client)):
            return True
        if await self._mainline_sync_blocks_movement(client):
            return True
        if not claim_quest_recovery(client, owner):
            return True
        client.bumbles_pet_pending = True
        try:
            async with automation_owner(client, 'bumbles-pet-stage'):
                async with asyncio.timeout(30):
                    async def ready():
                        if (not client.questing_status or getattr(client, 'refilling_potions', False)
                                or await client.is_loading() or await client.in_battle()
                                or await client.is_in_dialog() or not await is_free_leader_questing(client)
                                or await self._bumbles_pet_stage(client) != stage):
                            raise RuntimeError('任务阶段变化或客户端进入忙碌状态')
                    await ready()
                    state['attempted'] = True
                    logger.info('自动任务：检测到使用宠物模式寻找大黄蜂，开始特殊任务流程。')
                    logger.debug('{} 宠物任务：Quest ID={}，Quest Key={}，Goal ID={}，Goal Key={}',
                                 client.title, stage[0][0], stage[2], stage[0][1], stage[1])
                    mode = await self._play_as_pet_state(client)
                    if mode is None:
                        raise RuntimeError('无法确定宠物模式状态，未执行 TP/点击')
                    if mode is False:
                        await client.teleport(self.BUMBLES_PET_POSITION)
                        deadline = time.monotonic() + 10
                        last = None
                        stable_since = None
                        while True:
                            await ready()
                            position = await client.body.position()
                            if (calc_Distance(position, self.BUMBLES_PET_POSITION) > 100
                                    or last is None or calc_Distance(position, last) > 2):
                                stable_since = None
                            elif stable_since is None:
                                stable_since = time.monotonic()
                            elif time.monotonic() - stable_since >= 1:
                                break
                            if time.monotonic() >= deadline:
                                raise TimeoutError('指定位置未稳定，未点击扮演宠物')
                            last = position
                            await asyncio.sleep(.2)
                        logger.info('自动任务：已到达指定位置，准备进入宠物模式。')
                        for attempt in range(2):
                            await ready()
                            if await self._play_as_pet_state(client) is True:
                                break
                            button = await get_window_from_path(client.root_window, play_as_pet_button_path)
                            if not button or not await button.is_visible():
                                menu = await get_window_from_path(client.root_window, pet_system_button_path)
                                if (not menu or not await menu.is_visible() or await menu.is_control_grayed()):
                                    raise RuntimeError('宠物菜单不可操作')
                                await click_window_by_path(client, pet_system_button_path)
                                await asyncio.sleep(.4)
                                await ready()
                            button = await get_window_from_path(client.root_window, play_as_pet_button_path)
                            if (not button or not await button.is_visible() or await button.is_control_grayed()):
                                raise RuntimeError('扮演宠物按钮不可操作')
                            # Re-check mode immediately before click; cancel is
                            # the same button, so blindly retrying could undo it.
                            if await self._play_as_pet_state(client) is not False:
                                break
                            await click_window_by_path(client, play_as_pet_button_path)
                            deadline = time.monotonic() + 5
                            stable_since = None
                            while time.monotonic() < deadline:
                                if (not client.questing_status or await client.is_loading()
                                        or await client.in_battle() or await client.is_in_dialog()):
                                    raise RuntimeError('模式切换期间进入 Loading/战斗/对话')
                                if await self._play_as_pet_state(client) is True:
                                    if stable_since is None:
                                        stable_since = time.monotonic()
                                    elif time.monotonic() - stable_since >= 1:
                                        break
                                else:
                                    stable_since = None
                                await asyncio.sleep(.2)
                            if stable_since is not None and time.monotonic() - stable_since >= 1:
                                break
                            await asyncio.sleep(.5)
                    if await self._play_as_pet_state(client) is not True:
                        raise TimeoutError('两次点击后仍未确认宠物模式')
                    state['confirmed'] = True
                    await self._dungeon_quest_snapshot(client)
                    self._krok_exit_watch.pop(id(client), None)
                    self._trigger_reentry.pop(id(client), None)
                    self._mainline_finder_observations.pop(id(client), None)
                    logger.info('自动任务：宠物模式已开启，继续任务流程。')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = await self._dungeon_quest_snapshot(client)
            if current is not None and current != state['snapshot']:
                logger.debug('{} 到达宠物任务点后阶段已推进，下一轮确认/取消宠物模式。', client.title)
            elif not state['warned']:
                logger.warning('自动任务：宠物模式切换失败。{}', exc)
                state['warned'] = True
        finally:
            release_quest_recovery(client, owner)
        return True

    def _bumbles_mind_task_matches(self, text) -> bool:
        objective, location = split_quest_location(text)
        objective = re.sub(r'\s+', '', objective).casefold()
        location = re.sub(r'\s+', '', location).casefold()
        # Exact screenshot stage. Language-table numbers are not QuestIDs.
        return (objective, location) in {
            ('退出大黄蜂的脑海', '废料堆'),
        }

    async def _maybe_handle_bumbles_mind(self, client: Client) -> bool:
        if getattr(client, 'quest_recovery_owner', None) == 'bumbles_mind':
            return True
        if (not getattr(client, 'questing_status', False)
                or await client.zone_name() != self.BUMBLES_MIND_ZONE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                or not self._bumbles_mind_task_matches(await self.read_quest_txt(client))):
            return False
        if not await client.in_battle():
            await self.teleport_to_quest_target(client, await client.quest_position.position())
        return True

    async def _recover_bumbles_mind(self, client, progress, failed_attr):
        # Combat is driven by the existing combat worker; never start a second
        # controller or move the client again once battle has begun.
        def has_progress(snapshot):
            return snapshot is not None and (
                snapshot[:2] != progress[:2]
                or not self._bumbles_mind_task_matches(snapshot[2]))

        async with asyncio.timeout(240):
            for attempt in range(2):
                if (not client.questing_status or await client.is_loading()
                        or await client.zone_name() != self.BUMBLES_MIND_ZONE):
                    raise RuntimeError('任务停止或区域变化')
                current = await self._dungeon_quest_snapshot(client)
                if has_progress(current):
                    setattr(client, failed_attr, None)
                    return
                if (current is None
                        or not self._bumbles_mind_task_matches(await self.read_quest_txt(client))):
                    raise RuntimeError('任务阶段变化或不可读')
                if await client.in_battle():
                    break
                if not await is_free_leader_questing(client):
                    raise RuntimeError('进入对话或交互')
                if await client.is_loading() or await client.zone_name() != self.BUMBLES_MIND_ZONE:
                    raise RuntimeError('区域变化')
                if await client.in_battle():
                    break
                logger.info('BumblesMind 前往战斗触发点（{}/2），QuestID={}，GoalID={}。',
                            attempt + 1, progress[0], progress[1])
                await client.teleport(self.BUMBLES_MIND_BATTLE)
                deadline = time.monotonic() + 10
                while not await client.in_battle():
                    if (not client.questing_status or await client.is_loading()
                            or await client.zone_name() != self.BUMBLES_MIND_ZONE):
                        raise RuntimeError('等待战斗期间任务停止或区域变化')
                    current = await self._dungeon_quest_snapshot(client)
                    if has_progress(current):
                        setattr(client, failed_attr, None)
                        return
                    if (current is None
                            or not self._bumbles_mind_task_matches(await self.read_quest_txt(client))
                            or not await is_free_leader_questing(client)):
                        raise RuntimeError('等待战斗期间任务阶段变化或进入交互')
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(.2)
                if await client.in_battle():
                    break
            else:
                raise TimeoutError('两次传送后未触发战斗')
            logger.info('BumblesMind 已进入战斗，等待现有战斗流程完成。')
            deadline = time.monotonic() + 180
            while await client.in_battle():
                if not client.questing_status:
                    raise RuntimeError('任务已停止')
                if time.monotonic() >= deadline:
                    raise TimeoutError('战斗完成超时')
                await asyncio.sleep(.2)
            deadline = time.monotonic() + 15
            stable_since = None
            stable_identity = None
            while time.monotonic() < deadline:
                if not client.questing_status:
                    raise RuntimeError('任务已停止')
                current = await self._dungeon_quest_snapshot(client)
                zone = await client.zone_name()
                if (not zone or await client.is_loading() or await client.in_battle()
                        or not await is_free_leader_questing(client)
                        or not has_progress(current)):
                    stable_since = None
                    stable_identity = None
                elif stable_since is None or stable_identity != (zone, current[:2], plain_text(current[2])):
                    stable_identity = (zone, current[:2], plain_text(current[2]))
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                    setattr(client, failed_attr, None)
                    logger.info('BumblesMind 战斗完成且任务已推进，恢复自动任务。')
                    return
                await asyncio.sleep(.2)
            raise TimeoutError('战斗结束但未确认任务推进')

    def _tamarin_house_task_matches(self, text) -> bool:
        objective, location = split_quest_location(text)
        objective = re.sub(r'\s+', '', objective).casefold()
        location = re.sub(r'\s+', '', location).casefold()
        # Text fallback explicitly authorized by the user; match the entire
        # screenshot stage, not a substring such as "Golden Lion" in dialogue.
        return (objective, location) in {
            ('寻找金狮', '废料堆'), ('findthegoldenlion', 'heap'),
        }

    async def _maybe_handle_tamarin_house(self, client: Client) -> bool:
        owner = 'tamarin_house'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        if (not getattr(client, 'questing_status', False)
                or await client.zone_name() != self.TAMARIN_HOUSE_ZONE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        text = await self.read_quest_txt(client)
        if not self._tamarin_house_task_matches(text):
            return False
        # Client-scoped, survives Quester recreation. This one screenshot stage
        # is attempted once even if IDs are temporarily unreadable or the helper
        # is toggled off/on. Both internal TP stages have bounded retries.
        if isinstance(getattr(client, '_xuanshu_tamarin_house_stage', None), dict):
            return False
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'mainline_chain_retry_active', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'post_combat_movement_active', False)
                or not await is_free_leader_questing(client)
                or await is_spiral_door_open(client)):
            return True
        if not claim_quest_recovery(client, owner):
            return True
        state = None
        try:
            if (await client.zone_name() != self.TAMARIN_HOUSE_ZONE
                    or not self._tamarin_house_task_matches(await self.read_quest_txt(client))
                    or not await is_free_leader_questing(client)):
                return True
            snapshot = await self._dungeon_quest_snapshot(client)
            state = dict(zone=self.TAMARIN_HOUSE_ZONE, text=plain_text(text),
                         snapshot=snapshot, active=True, result='started')
            client._xuanshu_tamarin_house_stage = state

            async def same_task():
                current_text = await self.read_quest_txt(client)
                if not plain_text(current_text):
                    raise RuntimeError('当前任务文字不可读')
                if not self._tamarin_house_task_matches(current_text):
                    return False
                current = await self._dungeon_quest_snapshot(client)
                if snapshot is not None:
                    if current is None:
                        raise RuntimeError('原任务标识暂不可读，取消强制传送')
                    if current[:2] != snapshot[:2]:
                        return False
                return True

            async def ready():
                if not client.questing_status:
                    raise RuntimeError('自动任务已停止')
                if await client.in_battle():
                    raise RuntimeError('客户端进入战斗')
                return await is_free_leader_questing(client)

            logger.info('TamarinHouse 检测到指定任务，开始两段 TP。')
            logger.info('TamarinHouse 正在前往区域切换点。')
            async with asyncio.timeout(40.0):
                for attempt in range(2):
                    if await client.zone_name() != state['zone'] or await client.is_loading():
                        break
                    if not await same_task():
                        state['result'] = 'progressed'
                        return True
                    if (not await ready() or await client.is_loading()
                            or await client.zone_name() != state['zone']):
                        raise RuntimeError('第一次 TP 前客户端不再空闲或区域已改变')
                    await client.teleport(self.TAMARIN_HOUSE_EXIT)
                    deadline = time.monotonic() + 10.0
                    while await client.zone_name() == state['zone'] and not await client.is_loading():
                        if not await ready():
                            raise RuntimeError('等待切换时进入对话或其他忙碌状态')
                        if not await same_task():
                            state['result'] = 'progressed'
                            return True
                        if time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(.2)
                    if await client.zone_name() != state['zone'] or await client.is_loading():
                        break
                else:
                    raise TimeoutError('第一次 TP 两次尝试后仍未触发区域切换')

                await wait_for_zone_change(client, current_zone=state['zone'])
                arrival_zone = None
                stable_since = None
                while True:
                    if not client.questing_status or await client.in_battle():
                        raise RuntimeError('区域切换期间任务停止或进入战斗')
                    zone = await client.zone_name()
                    if zone and zone != state['zone'] and zone != arrival_zone:
                        arrival_zone, stable_since = zone, None
                    if (not zone or zone == state['zone'] or await client.is_loading()
                            or not await ready()):
                        stable_since = None
                    elif stable_since is None:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                        break
                    await asyncio.sleep(.2)
                state['arrival_zone'] = arrival_zone
                if not await same_task():
                    state['result'] = 'progressed'
                    return True
                logger.info('TamarinHouse 区域切换完成，执行第二次 TP。')
                for attempt in range(2):
                    if not await same_task():
                        state['result'] = 'progressed'
                        return True
                    if (not await ready() or await client.zone_name() != arrival_zone
                            or await client.is_loading()):
                        raise RuntimeError('第二次 TP 前区域或客户端状态已改变')
                    await client.teleport(self.TAMARIN_HOUSE_ARRIVAL)
                    deadline = time.monotonic() + 5.0
                    stable_since = None
                    last_position = None
                    while time.monotonic() < deadline:
                        if (await client.zone_name() != arrival_zone or await client.is_loading()
                                or not await ready()):
                            raise RuntimeError('第二次 TP 后发生战斗、对话或区域切换')
                        if not await same_task():
                            state['result'] = 'progressed'
                            return True
                        position = await client.body.position()
                        if (calc_Distance(position, self.TAMARIN_HOUSE_ARRIVAL) > 100
                                or last_position is None or calc_Distance(position, last_position) > 2):
                            stable_since = None
                        elif stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                            state['returned_snapshot'] = await self._dungeon_quest_snapshot(client)
                            await client.quest_position.position()
                            state['result'] = 'completed'
                            logger.info('TamarinHouse 特殊任务处理完成，恢复自动任务。')
                            return True
                        last_position = position
                        await asyncio.sleep(.2)
                raise TimeoutError('第二次 TP 两次尝试后角色位置仍未稳定')
        except asyncio.CancelledError:
            if state is not None:
                state['result'] = 'cancelled'
            raise
        except Exception as exc:
            if state is not None:
                state['result'] = 'failed'
            logger.warning('TamarinHouse 特殊任务处理失败，退出本次恢复。原因：{}', exc)
        finally:
            if state is not None:
                state['active'] = False
            release_quest_recovery(client, owner)
        return True

    async def _recover_dueling_tent(self, client, progress, interaction_pending):
        """Called with quest recovery ownership; two bounded exit TP attempts."""
        zone = self.DUELING_TENT_ZONE

        async def can_move():
            if (not client.questing_status or getattr(client, 'refilling_potions', False)
                    or await client.is_loading() or await client.in_battle()
                    or await client.is_in_dialog()
                    or not await is_free_leader_questing(client)
                    or await interaction_pending()):
                raise RuntimeError('任务停止、正常交互或战斗/对话/Loading，停止特殊 TP')
            snapshot = await self._dungeon_quest_snapshot(client)
            if snapshot is None:
                raise RuntimeError('当前任务状态不可读取')
            return snapshot == progress

        async with automation_owner(client, 'dueling-tent-recovery'):
            async with asyncio.timeout(35):
                for attempt in range(2):
                    if await client.zone_name() != zone or await client.is_loading():
                        break
                    if not await can_move():
                        client._xuanshu_dueling_tent_failed = None
                        return  # Real quest progress; do not teleport again.
                    logger.info('DuelingTent 任务 TP 受阻且无进展，前往指定切区点（{}/2）。', attempt + 1)
                    await client.teleport(self.DUELING_TENT_EXIT)
                    deadline = time.monotonic() + 10
                    while await client.zone_name() == zone and not await client.is_loading():
                        if not await can_move():
                            client._xuanshu_dueling_tent_failed = None
                            return
                        if time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(.2)
                    if await client.zone_name() != zone or await client.is_loading():
                        break
                else:
                    raise TimeoutError('两次特殊 TP 后未发生区域切换；本任务阶段不再重复 TP')

                # Loading alone is not proof of a zone change. Require a stable,
                # nonempty new zone and readable quest state before releasing TP.
                arrival = None
                stable_since = None
                while True:
                    if not client.questing_status:
                        raise RuntimeError('等待切区期间自动任务已停止')
                    current_zone = await client.zone_name()
                    if (not current_zone or current_zone == zone or await client.is_loading()
                            or await client.in_battle() or await client.is_in_dialog()
                            or not await is_free_leader_questing(client)):
                        stable_since = None
                    elif current_zone != arrival or stable_since is None:
                        arrival = current_zone
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                        snapshot = await self._dungeon_quest_snapshot(client)
                        if snapshot is not None:
                            await get_quest_name(client)
                            await client.quest_position.position()
                            client._xuanshu_dueling_tent_failed = None
                            client._dueling_tent_recovered_at = time.monotonic()
                            for member in getattr(client, 'quest_mainline_sync_members', [client]):
                                member.quest_mainline_sync_state = None
                            client.quest_party_quest_worker_zone = arrival
                            client.quest_party_probe_pending = bool(getattr(client, 'quest_party_hitters', []))
                            self._trigger_reentry.pop(id(client), None)
                            self._npc_retry_exhausted.pop(id(client), None)
                            self._mainline_finder_observations.pop(id(client), None)
                            logger.info('DuelingTent 区域切换完成，重置卡顿计时并恢复原有打手同步。')
                            return
                    await asyncio.sleep(.2)

    async def teleport_to_quest_target(self, client, xyz, leader_client=None):
        if await self._maybe_handle_bumbles_pet(client):
            return
        if (getattr(client, "refilling_potions", False) is True
                or getattr(client, 'quest_recovery_owner', None) in ('gobblerton', 'lemuria_dungeon', 'sacred_yarn', 'tamarin_house', 'bumbles_mind', 'lemuria_navigation', 'dueling_tent')
                or isinstance(getattr(client, 'quest_lemuria_navigation_recovery', None), dict)
                and client.quest_lemuria_navigation_recovery.get('holding')
                or getattr(client, "questing_status", True) is False):
            return
        if await client.zone_name() == self.TAMARIN_HOUSE_ZONE and await self._maybe_handle_tamarin_house(client):
            return
        zone = await client.zone_name()
        key = id(client)
        private_wing = zone == self.PRIVATE_WING_ZONE
        dueling_tent = zone == self.DUELING_TENT_ZONE
        if not dueling_tent:
            client._xuanshu_dueling_tent_failed = None
        elif (await client.is_in_dialog() or not await is_free_leader_questing(client)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(member, 'quest_party_hitters', []) for member in self.clients)):
            self._krok_exit_watch.pop(key, None)
            return
        gobblerton = zone == self.GOBBLERTON_ZONE
        lemuria = zone == self.LEMURIA_DUNGEON_ZONE
        sacred_yarn = zone == self.SACRED_YARN_ZONE
        bumbles = (zone == self.BUMBLES_MIND_ZONE
                   and self._bumbles_mind_task_matches(await self.read_quest_txt(client)))
        if (sacred_yarn or bumbles) and (getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(member, 'quest_party_hitters', []) for member in self.clients)):
            return
        transition_exit = private_wing or gobblerton or lemuria or sacred_yarn or bumbles or dueling_tent
        failed_attr = ('_xuanshu_dueling_tent_failed' if dueling_tent else '_xuanshu_bumbles_mind_failed' if bumbles else
                       '_xuanshu_sacred_yarn_failed' if sacred_yarn else
                       '_xuanshu_lemuria_dungeon_failed' if lemuria else
                       '_xuanshu_gobblerton_failed' if gobblerton else '_xuanshu_private_wing_failed')
        if not sacred_yarn:
            client._xuanshu_sacred_yarn_failed = None
        if zone != self.BUMBLES_MIND_ZONE:
            client._xuanshu_bumbles_mind_failed = None
        if not lemuria and isinstance(getattr(client, '_xuanshu_lemuria_dungeon_failed', None), dict):
            client._xuanshu_lemuria_dungeon_failed = None
        if not gobblerton and isinstance(getattr(client, '_xuanshu_gobblerton_failed', None), dict):
            client._xuanshu_gobblerton_failed = None
        if not private_wing and isinstance(getattr(client, '_xuanshu_private_wing_failed', None), dict):
            client._xuanshu_private_wing_failed = None
        if transition_exit and (not getattr(client, 'questing_status', False)
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'mainline_chain_retry_active', False)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                or getattr(client, 'post_combat_movement_active', False)
                or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                and client.quest_dungeon_recovery.get('active')):
            self._krok_exit_watch.pop(key, None)
            return
        floating_exit = zone == 'Celestia/CL_Z05_The_Floating_Land'
        crystal_exit = zone == 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
        special_targets = {
            'Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove': XYZ(3124.726, -4718.231, 36.200),
            'Celestia/CL_Z09_Science_Center': XYZ(-1067.512, 343.759, -449.800),
            'Celestia/Interiors/CL_Z10i3_Kingdom_Of_The_Crabs': XYZ(3184.987, -9931.280, -1014.007),
        }
        special_exit = zone in special_targets or transition_exit
        stuck = XYZ(6420.986, -6956.173, -399.699)
        if (zone != "Krokotopia/KT_WorldTeleporter" and not floating_exit and not crystal_exit and not special_exit
                or floating_exit and calc_Distance(await client.body.position(), stuck) > 150):
            self._krok_exit_watch.pop(key, None)
            await self.move_until_quest_interaction(client, xyz, leader_client=leader_client)
            return

        async def interaction_pending():
            return (await is_spiral_door_open(client)
                    or (await is_visible_by_path(client, npc_range_path)
                        and calc_Distance(await client.body.position(), xyz) < 750)
                    or await is_visible_by_path(client, exit_dungeon_path))

        # Give world-gate interaction priority over both movement and END.
        if await client.is_loading() or await interaction_pending() or not await is_free(client):
            self._krok_exit_watch.pop(key, None)
            return

        objective = await get_quest_name(leader_client or client)
        if not objective:
            return
        target = (xyz.x, xyz.y, xyz.z)
        before = await client.body.position()
        progress = await self._dungeon_quest_snapshot(client) if transition_exit else None
        if (sacred_yarn or bumbles or dueling_tent) and progress is None:
            self._krok_exit_watch.pop(key, None)
            await self.move_until_quest_interaction(client, xyz, leader_client=leader_client)
            return
        def progress_changed(current, previous):
            if bumbles and current is not None and previous is not None:
                return (current[:2] != previous[:2]
                        or not self._bumbles_mind_task_matches(current[2]))
            return current != previous

        state = self._krok_exit_watch.get(key)
        if (state is None or state.get('zone') != zone or state['objective'] != objective
                or not (sacred_yarn or bumbles or dueling_tent) and state['target'] != target
                or transition_exit and progress is not None and state.get('progress') is not None
                and progress_changed(progress, state['progress'])):
            state = dict(zone=zone, objective=objective, target=target, anchor=before,
                         since=time.monotonic(), attempts=0, end_sent=False, progress=progress)
            self._krok_exit_watch[key] = state
        elif transition_exit and progress is not None:
            state['progress'] = progress
        if transition_exit:
            failed = getattr(client, failed_attr, None)
            if isinstance(failed, dict):
                changed = (failed['zone'] != zone or failed['objective'] != objective
                           or not (sacred_yarn or bumbles or dueling_tent) and failed['target'] != target
                           or progress is not None and failed['progress'] is not None
                           and progress_changed(progress, failed['progress']))
                if changed and objective:
                    setattr(client, failed_attr, None)
                else:
                    state['end_sent'] = True
                    if dueling_tent:
                        return  # Exhausted same-stage attempts survive worker recreation.

        transition = [False]
        async def watch_transition():
            while True:
                if (await client.is_loading() or await client.zone_name() != zone
                        or (crystal_exit or special_exit) and (await interaction_pending() or not await is_free(client))):
                    transition[0] = True
                    return
                await asyncio.sleep(.1)

        # Observe loading during the existing teleport, without adding a dwell sleep.
        watcher = asyncio.create_task(watch_transition())
        rejections_before = getattr(client, '_collision_tp_rejections', 0)
        path_blocked = False
        try:
            if dueling_tent:
                try:
                    async with asyncio.timeout(15):
                        await collision_tp(client, xyz, leader_client=leader_client)
                except TimeoutError:
                    path_blocked = True
                    logger.debug('{} DuelingTent 普通任务寻路超时，纳入受阻观察。', client.title)
            else:
                await collision_tp(client, xyz, leader_client=leader_client)
        except BaseException:
            self._krok_exit_watch.pop(key, None)
            raise
        finally:
            watcher.cancel()
            outcome = (await gather_owned(watcher, return_exceptions=True))[0]
            if isinstance(outcome, Exception):
                # A failed observation cannot establish that no loading occurred.
                transition[0] = True

        if (transition[0] or await client.is_loading() or await client.zone_name() != zone
                or await interaction_pending() or not await is_free(client)):
            self._krok_exit_watch.pop(key, None)
            return
        if dueling_tent and (await client.is_in_dialog() or not await is_free_leader_questing(client)):
            self._krok_exit_watch.pop(key, None)
            return
        current_objective = await get_quest_name(leader_client or client)
        if not current_objective:
            return
        if current_objective != objective:
            self._krok_exit_watch.pop(key, None)
            return
        if (gobblerton or lemuria or sacred_yarn or bumbles or dueling_tent) and progress_changed(await self._dungeon_quest_snapshot(client), progress):
            self._krok_exit_watch.pop(key, None)
            return

        after = await client.body.position()
        if dueling_tent:
            rejected = path_blocked or getattr(client, '_collision_tp_rejections', 0) > rejections_before
            no_approach = calc_Distance(before, xyz) - calc_Distance(after, xyz) < 100
            if not rejected and (not no_approach or calc_Distance(after, xyz) < self.TRIGGER_NEAR_DISTANCE):
                state.update(anchor=after, since=time.monotonic(), attempts=0)
                return  # Successful approach / normal arrival is not blocked navigation.
        if floating_exit and calc_Distance(after, stuck) > 150:
            self._krok_exit_watch.pop(key, None)
            return
        # Gobblerton tracks quest progress even when repeated TP moves the body.
        # Other exits retain their existing stationary-position requirement.
        if not (gobblerton or sacred_yarn or bumbles or dueling_tent) and max(calc_Distance(before, state['anchor']),
               calc_Distance(after, state['anchor'])) > 100:
            state.update(anchor=after, since=time.monotonic(), attempts=0)
            return
        state['attempts'] += 1
        if (sacred_yarn or bumbles) and state['end_sent']:
            # Let the existing mainline/dungeon recovery inspect the next iteration.
            self._krok_exit_watch.pop(key, None)
            return
        if not objective or state['end_sent'] or state['attempts'] < 3 or time.monotonic() - state['since'] < 10:
            return

        recovery_owner = ('dueling_tent' if dueling_tent else 'bumbles_mind' if bumbles else 'sacred_yarn' if sacred_yarn else 'lemuria_dungeon' if lemuria else
                          'gobblerton' if gobblerton else 'private_wing' if private_wing else 'special_zone')
        if not claim_quest_recovery(client, recovery_owner):
            return
        state['end_sent'] = True
        try:
            if dueling_tent:
                if (await client.zone_name() != zone or await self._dungeon_quest_snapshot(client) != progress
                        or not client.questing_status or await client.is_loading() or await client.in_battle()
                        or not await is_free_leader_questing(client) or await interaction_pending()):
                    return
                setattr(client, failed_attr, dict(zone=zone, objective=objective, target=target, progress=progress))
                try:
                    await self._recover_dueling_tent(client, progress, interaction_pending)
                except Exception as exc:
                    logger.error('DuelingTent 特殊恢复未完成，停止本任务阶段重复 TP：{}', exc)
                return
            if bumbles:
                if (progress_changed(await self._dungeon_quest_snapshot(client), progress)
                        or await client.zone_name() != zone
                        or not self._bumbles_mind_task_matches(await self.read_quest_txt(client))
                        or not client.questing_status or await client.is_loading()
                        or await client.in_battle() or not await is_free_leader_questing(client)
                        or await interaction_pending()):
                    return
                setattr(client, failed_attr, dict(zone=zone, objective=objective,
                                                target=target, progress=progress))
                try:
                    await self._recover_bumbles_mind(client, progress, failed_attr)
                except Exception as exc:
                    logger.warning('BumblesMind 特殊恢复失败，退出本次恢复。原因：{}', exc)
                return
            if transition_exit:
                label = ('SacredYarnTemple' if sacred_yarn else 'Lemuria Dungeon' if lemuria
                         else 'Gobblerton' if gobblerton else 'PrivateWing')
                if lemuria or sacred_yarn:
                    if (await client.zone_name() != zone
                            or await self._dungeon_quest_snapshot(client) != progress):
                        self._krok_exit_watch.pop(key, None)
                        return
                    if sacred_yarn:
                        if (not client.questing_status or await client.is_loading()
                                or await client.in_battle() or not await is_free_leader_questing(client)
                                or await interaction_pending()):
                            self._krok_exit_watch.pop(key, None)
                            return
                        logger.info('SacredYarnTemple 连续任务传送无进展，启动特殊恢复。')
                    else:
                        logger.info('自动任务：Lemuria Dungeon 连续任务传送受阻，启动特殊脱困。')
                else:
                    logger.info(f'自动任务：{label} 连续任务传送无进展，前往区域切换点。')
                setattr(client, failed_attr, dict(
                    zone=zone, objective=objective, target=target, progress=progress
                ))
                try:
                    async with asyncio.timeout(40.0 if lemuria else 25.0):
                        exit_point = (self.SACRED_YARN_EXIT if sacred_yarn else self.LEMURIA_DUNGEON_EXIT if lemuria else
                                      self.GOBBLERTON_EXIT if gobblerton else self.PRIVATE_WING_EXIT)
                        if lemuria:
                            logger.info('自动任务：正在前往指定交互坐标。')
                        if sacred_yarn:
                            logger.info('SacredYarnTemple 正在前往指定区域切换点。')
                            for attempt in range(2):
                                if await client.zone_name() != zone or await client.is_loading():
                                    break
                                if (not client.questing_status or await client.in_battle()
                                        or not await is_free_leader_questing(client)
                                        or await interaction_pending()):
                                    raise RuntimeError('任务停止或进入战斗/对话')
                                current_progress = await self._dungeon_quest_snapshot(client)
                                if current_progress is not None and current_progress != progress:
                                    setattr(client, failed_attr, None)
                                    return
                                if current_progress is None:
                                    raise RuntimeError('任务状态不可读')
                                await client.teleport(exit_point)
                                retry_deadline = time.monotonic() + 10.0
                                while await client.zone_name() == zone and not await client.is_loading():
                                    if (not client.questing_status or await client.in_battle()
                                            or not await is_free_leader_questing(client)
                                            or await interaction_pending()):
                                        raise RuntimeError('等待切换期间任务停止或进入战斗/对话')
                                    current_progress = await self._dungeon_quest_snapshot(client)
                                    if current_progress is not None and current_progress != progress:
                                        setattr(client, failed_attr, None)
                                        return
                                    if current_progress is None:
                                        raise RuntimeError('任务状态不可读')
                                    if time.monotonic() >= retry_deadline:
                                        break
                                    await asyncio.sleep(.2)
                                if await client.zone_name() != zone or await client.is_loading():
                                    break
                            else:
                                raise TimeoutError('两次传送后未触发区域切换')
                        else:
                            await client.teleport(exit_point)
                        if lemuria:
                            last_position = await client.body.position()
                            position_stable_since = None
                            attempts = 0
                            retry_at = 0.0
                            interaction_deadline = time.monotonic() + 20.0
                            while await client.zone_name() == zone and not await client.is_loading():
                                if not client.questing_status:
                                    return
                                if await client.in_battle():
                                    raise RuntimeError('交互期间进入战斗')
                                position = await client.body.position()
                                now = time.monotonic()
                                if now >= interaction_deadline:
                                    raise TimeoutError('交互提示、对话完成或区域切换响应超时')
                                if (calc_Distance(position, exit_point) > 100
                                        or calc_Distance(position, last_position) > 2):
                                    position_stable_since = None
                                elif position_stable_since is None:
                                    position_stable_since = now
                                elif (now - position_stable_since >= .5
                                        and await is_free_leader_questing(client)
                                        and await is_visible_by_path(client, npc_range_path)
                                        and now >= retry_at):
                                    if attempts >= 2:
                                        raise RuntimeError('两次 X 交互后未发生区域切换')
                                    logger.info('自动任务：已到达目标位置，尝试 X 交互（{}/2）。', attempts + 1)
                                    await client.send_key(Keycode.X, .1)
                                    attempts += 1
                                    retry_at = time.monotonic() + 4.0
                                last_position = position
                                await asyncio.sleep(.2)
                            logger.info('自动任务：交互完成，等待区域切换。')
                        if not sacred_yarn:
                            logger.info(f'自动任务：{label} 正在等待区域切换。')
                        await wait_for_zone_change(client, current_zone=zone)
                        arrival_zone = await client.zone_name()
                        if arrival_zone == zone:
                            raise TimeoutError('区域未变化')
                        stable_since = None
                        while True:
                            if not client.questing_status:
                                return
                            current_zone = await client.zone_name()
                            if (sacred_yarn and current_zone and current_zone != zone
                                    and current_zone != arrival_zone):
                                arrival_zone = current_zone
                                stable_since = None
                            if (sacred_yarn and not current_zone
                                    or current_zone == zone or current_zone != arrival_zone
                                    or await client.is_loading()
                                    or not await is_free_leader_questing(client)):
                                stable_since = None
                            elif stable_since is None:
                                stable_since = time.monotonic()
                            elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                                snapshot = await self._dungeon_quest_snapshot(client)
                                if sacred_yarn and snapshot is None:
                                    await asyncio.sleep(.2)
                                    continue
                                await get_quest_name(client)
                                await client.quest_position.position()
                                self._krok_exit_watch.pop(key, None)
                                setattr(client, failed_attr, None)
                                if sacred_yarn:
                                    logger.info('SacredYarnTemple 区域切换完成，恢复自动任务。')
                                else:
                                    logger.info(f'自动任务：{label} 区域切换完成，继续任务传送。')
                                return
                            await asyncio.sleep(.2)
                except Exception as exc:
                    if sacred_yarn:
                        logger.warning('SacredYarnTemple 特殊恢复失败，退出本次恢复。原因：{}', exc)
                    elif lemuria:
                        logger.warning('Lemuria Dungeon 特殊脱困失败，退出本次恢复。原因：{}', exc)
                    else:
                        logger.warning(f'自动任务：{label} 区域切换未成功，退出本次特殊恢复。{exc}')
                return
            if special_exit:
                logger.info(f'Client {client.title}: 连续任务传送至少 3 次且 10 秒无进展，执行区域脱困。')
                await client.teleport(special_targets[zone])
                if zone.endswith('CL_Z10i3_Kingdom_Of_The_Crabs'):
                    try:
                        async with asyncio.timeout(10):
                            while not await is_visible_by_path(client, npc_range_path):
                                if await client.is_loading() or await client.zone_name() != zone:
                                    return
                                await asyncio.sleep(.25)
                            await client.send_key(Keycode.X, .1)
                            while True:
                                current_objective = await get_quest_name(leader_client or client)
                                if current_objective and current_objective != objective:
                                    break
                                if await client.is_loading() or not await is_free(client):
                                    return
                                await asyncio.sleep(.25)
                    except TimeoutError:
                        pass
                return
            if crystal_exit:
                logger.info(f'Client {client.title}: 水晶塔连续任务传送受阻，执行脱困传送。')
                await client.teleport(XYZ(34.461, 1382.432, 0.123))
                return
            if floating_exit:
                logger.info(f'Client {client.title}: 漂浮大陆连续任务传送受阻，执行出口脱困传送。')
                await client.teleport(XYZ(6592.342, -6749.908, -250.054))
                return
            logger.debug(f"Client {client.title}: 克洛克传送室连续任务传送受阻，按 END 返回主城。")
            await client.send_key(Keycode.END, 0.1)
            try:
                async with asyncio.timeout(15):
                    while await client.is_loading() or await client.zone_name() == zone:
                        await asyncio.sleep(.1)
            except TimeoutError:
                logger.warning(f"Client {client.title}: END 回城尚未完成，本次受阻不重复按 END。")
                return
            self._krok_exit_watch.pop(key, None)
        finally:
            if sacred_yarn or bumbles or dueling_tent:
                self._krok_exit_watch.pop(key, None)
            if lemuria or dueling_tent:
                dungeon = getattr(client, 'quest_dungeon_recovery', None)
                if isinstance(dungeon, dict):
                    dungeon.update(since=time.monotonic(), waiting_logged=False)
            release_quest_recovery(client, recovery_owner)

    async def teleport_to_quest(self, hitting_client: str, follower_clients: list[Client]):
        if await self._quest_dialogue_blocks_movement(self.current_leader_client):
            return
        await gather_owned(*[self.leader_wait_for_free(p) for p in self.clients])

        if await is_free_leader_questing(self.current_leader_client):
            leader_client_objective_xyz = await self.current_leader_client.quest_position.position()
            leader_objective = await self.get_truncated_quest_objectives(self.current_leader_client)

            # complex teleport logic for defeat quests to prevent mob battle separation
            if quest_has_action(leader_objective, "defeat"):
                # if the hitting client is the leader client, they would teleport first, forcing them into battle first
                # we use a proxy leader client instead during battle teleports so that in the case that the hitting client is the leader, we can still teleport them last
                ignore_hitter = True
                proxy_leader_is_questing = True
                if hitting_client is not None:
                    # this is solely for cases where the user made a stupid config file and made their leader client the same as their hitter client
                    if hitting_client in self.current_leader_client.title:
                        ignore_hitter = False
                        followup_teleport_clients = follower_clients.copy()
                        proxy_leader_client = None
                        for c in follower_clients:
                            # prefer other concurrent questers as the proxy leader (in case we run into a solo zone)
                            if proxy_leader_client is None:
                                if await self.get_truncated_quest_objectives(c) == await self.get_truncated_quest_objectives(self.current_leader_client):
                                    proxy_leader_client = c
                                    break

                        # none of our follower clients are on the same quest (user has a really strange setup, this should never happen in practice)
                        # resort to letting a non-questing client be the proxy leader
                        if proxy_leader_client is None:
                            proxy_leader_is_questing = False
                            proxy_leader_client = follower_clients[0]

                        followup_teleport_clients.remove(proxy_leader_client)
                        followup_teleport_clients.append(self.current_leader_client)

                # user did not set a hitter client or the hitter client is not the current leader
                # in this case, change nothing - leader remains leader and followers remain followers
                if ignore_hitter:
                    proxy_leader_client = self.current_leader_client
                    followup_teleport_clients = follower_clients

                logger.debug('Leader ' + self.current_leader_client.title + ' on defeat quest - staggering teleports')

                location_before_sendback = await proxy_leader_client.body.position()
                zone_before_teleport = await proxy_leader_client.zone_name()
                await proxy_leader_client.teleport(leader_client_objective_xyz)
                await asyncio.sleep(1.0)

                # we collided and were sent back - we likely aren't in the right zone for our defeat quest
                distance = calc_Distance(location_before_sendback, await proxy_leader_client.body.position())

                # leader client collided and got sent back
                if distance < 20:
                    logger.debug('client ' + proxy_leader_client.title + ' collided on initial teleport')
                    await self.teleport_to_quest_target(client=proxy_leader_client, xyz=leader_client_objective_xyz, leader_client=self.current_leader_client)

                await asyncio.sleep(1.0)
                while await proxy_leader_client.is_loading():
                    await asyncio.sleep(.1)

                # leader_current_zone = await self.current_leader_client.zone_name()
                detected_dungeon = await self.detected_interact_from_popup(proxy_leader_client)

                # changed zones or we are in front of an interactible
                if await proxy_leader_client.zone_name() != zone_before_teleport or detected_dungeon:
                    logger.debug('leader zone changed or interactible reached - syncing all clients')
                    try:
                        await gather_owned(*[self.teleport_to_quest_target(client=c, xyz=leader_client_objective_xyz, leader_client=self.current_leader_client) for c in followup_teleport_clients])
                    except:
                        print(traceback.print_exc())

                # objective changed, let questing loop cycle and assign a new leader in case some got left behind
                # ignore this logic if the proxy leader is not a questing client - there is no way to determine if objective changed in this case and users should avoid this setup
                elif proxy_leader_is_questing and leader_objective != await self.get_truncated_quest_objectives(proxy_leader_client):
                    pass

                # leader is likely waiting for combat in the correct zone
                else:
                    logger.debug('leader waiting for combat')
                    await asyncio.sleep(1)
                    sprinter = SprintyClient(proxy_leader_client)
                    while not proxy_leader_client.entity_detect_combat_status:
                        try:
                            await sprinter.tp_to_closest_mob()
                        # wizwalker throws should update bool even with wait_on_inuse on
                        except ValueError:
                            await asyncio.sleep(1.0)

                        await asyncio.sleep(5.0)

                    while proxy_leader_client.entity_detect_combat_status:
                        await asyncio.sleep(.1)

            # if we aren't doing a mob / boss fight, we have no need to stagger teleports
            # furthermore staggered teleports can break certain quests in dungeons for certain clients
            else:
                await gather_owned(*[self.teleport_to_quest_target(p, leader_client_objective_xyz, leader_client=self.current_leader_client) for p in self.clients])

    @staticmethod
    def _is_giant_vat_photo_prompt(value):
        return '巨美大桶' in plain_text(value) and quest_has_action(value, 'photomance')

    async def _maybe_photo_giant_vat(self, client, prompt=None):
        if prompt is None:
            if not await is_visible_by_path(client, npc_range_path):
                client._xuanshu_giant_vat_photo_attempt = None
                return False
            prompt = await self.read_popup(client)
        if not self._is_giant_vat_photo_prompt(prompt):
            client._xuanshu_giant_vat_photo_attempt = None
            return False
        await self.take_photomancy_photo(client, prompt)
        return True

    async def take_photomancy_photo(self, client, objective):
        target = plain_text(objective).casefold()
        gummy_worms = '讨厌的虫子' in target or 'gummy worms' in target
        if self._is_giant_vat_photo_prompt(objective):
            before = await self._dungeon_quest_snapshot(client)
            zone = await client.zone_name()
            attempt = (zone, before)
            if getattr(client, '_xuanshu_giant_vat_photo_attempt', None) == attempt:
                return
            await client.teleport(self.GIANT_VAT_PHOTO_POSITION)
            await asyncio.sleep(.5)
            if not self._is_giant_vat_photo_prompt(await self.read_popup(client)):
                return
            client._xuanshu_giant_vat_photo_attempt = attempt
            await client.send_key(Keycode.A, 0.05)
            first, second = self.GIANT_VAT_PHOTO_ORIENTATIONS
            await client.body.write_orientation(first)
            camera = await client.game_client.selected_camera_controller()
            if camera is not None:
                await camera.update_orientation(first)
            await asyncio.sleep(.2)
            await client.send_key(Keycode.Z, 0.1)
            await client.send_key(Keycode.Z, 0.1)
            if before is None:
                logger.debug('巨美大桶拍照后任务状态不可读，跳过第二次拍照。')
                return
            for _ in range(10):
                await asyncio.sleep(.3)
                after = await self._dungeon_quest_snapshot(client)
                if after is None:
                    logger.debug('巨美大桶拍照后任务状态不可读，跳过第二次拍照。')
                    return
                if after != before:
                    return
            if (not getattr(client, 'questing_status', False)
                    or await client.zone_name() != zone
                    or await client.is_loading() or await client.in_battle()):
                return
            await client.body.write_orientation(second)
            camera = await client.game_client.selected_camera_controller()
            if camera is not None:
                await camera.update_orientation(second)
            await asyncio.sleep(.2)
            await client.send_key(Keycode.Z, 0.1)
            await client.send_key(Keycode.Z, 0.1)
            return
        if gummy_worms and await client.zone_name() == self.GUMMY_WORMS_PHOTO_ZONE:
            await client.teleport(self.GUMMY_WORMS_PHOTO_POSITION)
            await asyncio.sleep(.5)
            if not quest_has_action(await get_quest_name(client), 'photomance'):
                return
            await client.send_key(Keycode.A, 0.05)
            await client.body.write_orientation(self.GUMMY_WORMS_PHOTO_ORIENTATION)
            camera = await client.game_client.selected_camera_controller()
            if camera is not None:
                await camera.update_orientation(self.GUMMY_WORMS_PHOTO_ORIENTATION)
            await asyncio.sleep(.2)
        await client.send_key(Keycode.Z, 0.1)
        await client.send_key(Keycode.Z, 0.1)

    async def handle_normal_quests(self, follower_clients: list[Client], questing_friend_tp: bool):
        if await close_npc_quest_menu(self.current_leader_client):
            return
        if any(await gather_owned(*[close_automation_popup(c) for c in self.clients])):
            return
        if await is_spiral_door_open(self.current_leader_client):
            self._krok_exit_watch.pop(id(self.current_leader_client), None)
            await self.handle_spiral_navigation()
            return
        # Handles chest reroll menu, will always cancel
        await gather_owned(*[safe_click_window(c, cancel_chest_roll_path) for c in self.clients])
        # confirm exit dungeon early button
        await gather_owned(*[safe_click_window(c, exit_dungeon_path) for c in self.clients])

        await gather_owned(*[self.leader_wait_for_free(p) for p in self.clients])

        if await is_free_leader_questing(self.current_leader_client):
            for c in self.clients:
                while await c.is_loading():
                    await asyncio.sleep(0.1)

            current_pos = await self.current_leader_client.body.position()

            leader_client_objective_xyz = await self.current_leader_client.quest_position.position()
            if await is_visible_by_path(self.current_leader_client, npc_range_path) and calc_Distance(leader_client_objective_xyz, current_pos) < 750.0:
                # await self.handle_interactibles(current_leader_client)

                # Handles interactables
                sigil_msg_check = await self.read_popup(self.current_leader_client)
                if await self._maybe_photo_giant_vat(self.current_leader_client, sigil_msg_check):
                    return
                if is_dungeon_entry_prompt(sigil_msg_check):
                    entry_zone = await self.current_leader_client.zone_name()
                    while is_dungeon_entry_prompt(sigil_msg_check):
                        logger.debug('Entering dungeon')
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                        await asyncio.sleep(1.0)
                        for c in self.clients:
                            if await is_visible_by_path(c, dungeon_warning_path):
                                await c.send_key(Keycode.ENTER, 0.1)

                        sigil_msg_check = await self.read_popup(self.current_leader_client)

                    await self.handle_dungeon_entry(questing_friend_tp, follower_clients, entry_zone)
                else:
                    msg = sigil_msg_check.lower()
                    if interaction_kind(msg) == "talk":
                        quest_updated = await self.handle_npc_talking_quests(
                            self.current_leader_client, self.clients
                        )
                        if not quest_updated:
                            await asyncio.sleep(2.0)
                            return

                    elif interaction_kind(msg) in {"ride", "teleport"}:
                        await self.current_leader_client.send_key(Keycode.X, 0.1)
                        await asyncio.sleep(1.0)
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                    else:
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])

                    # original_zone = await self.current_leader_client.zone_name()

                    await asyncio.sleep(2)
                    was_loading = False
                    for c in self.clients:
                        while await c.is_loading():
                            was_loading = True
                            await asyncio.sleep(0.1)

                        # try to correct for zone lag on follower clients by giving follower clients a second to get into the zone before teleporting
                        if was_loading:
                            await asyncio.sleep(1)

                    # Exit NPC menus (spell menus, quest menus, etc)
                    # await asyncio.sleep(2)
                    end_of_loop_paths = (exit_recipe_shop_path, exit_equipment_shop_path, cancel_multiple_quest_menu_path, cancel_spell_vendor, exit_snack_shop_path, exit_reagent_shop_path, exit_tc_vendor, exit_minigame_sigil, exit_wysteria_tournament, exit_dungeon_path, exit_zafaria_class_picture_button, exit_pet_leveled_up_button_path, avalon_badge_exit_button_path, potion_exit_path)
                    await gather_owned(*[exit_menus(c, end_of_loop_paths) for c in self.clients])

                    await asyncio.sleep(0.75)

                    if await is_spiral_door_open(self.current_leader_client):
                        await self.handle_spiral_navigation()
            else:
                # we may be on a photomancy quest
                quest_objective = await get_quest_name(self.current_leader_client)

                if quest_has_action(quest_objective, "photomance"):
                    # Photomancy quests (WC, KM, LM)
                    await gather_owned(*[self.take_photomancy_photo(p, quest_objective) for p in self.clients])

        await asyncio.sleep(0.7)

    async def hardcoded_collect(self, incompatible_hardcoded_quests, truncated_quest_obj: str):
        entity_data = incompatible_hardcoded_quests.get(truncated_quest_obj[:-1])
        for c in self.clients:
            if c.process_id != self.current_leader_client.process_id:
                current_quest = (await self.get_truncated_quest_objectives(c)).lower()
                follower_on_collect = False
                safe_location = await c.body.position()
                while current_quest == truncated_quest_obj:
                    follower_on_collect = True
                    for coord in entity_data[1:]:
                        try:
                            await c.teleport(coord)
                            await asyncio.sleep(1.0)

                            for i in range(3):
                                await c.send_key(Keycode.X)
                                await asyncio.sleep(.15)
                        except ValueError:
                            pass

                    current_quest = (await self.get_truncated_quest_objectives(c)).lower()

                if follower_on_collect:
                    while not await is_free(c) or c.entity_detect_combat_status:
                        await asyncio.sleep(1.0)

                    if await is_free(c) and not c.entity_detect_combat_status:
                        await c.teleport(safe_location)
                    logger.debug('Waiting for collect items to respawn.')
                    await asyncio.sleep((entity_data[0] + 5))

        current_quest = truncated_quest_obj
        safe_location = await self.current_leader_client.body.position()
        while current_quest == truncated_quest_obj:
            for coord in entity_data[1:]:
                try:
                    await self.current_leader_client.teleport(coord)
                    await asyncio.sleep(1.0)

                    for i in range(3):
                        await self.current_leader_client.send_key(Keycode.X)
                        await asyncio.sleep(.15)
                except ValueError:
                    pass

            current_quest = (await self.get_truncated_quest_objectives(self.current_leader_client)).lower()

        while not await is_free(self.current_leader_client) or self.current_leader_client.entity_detect_combat_status:
            await asyncio.sleep(1.0)

        if await is_free(self.current_leader_client) and not self.current_leader_client.entity_detect_combat_status:
            await self.current_leader_client.teleport(safe_location)

        entity_data = incompatible_hardcoded_quests.get(truncated_quest_obj[:-1])
        current_quest = truncated_quest_obj
        while current_quest == truncated_quest_obj:
            await asyncio.sleep(1.0)
            current_quest = (await self.get_truncated_quest_objectives(self.current_leader_client)).lower()

    async def handle_collect_quests(self):
        leader_obj = await self.get_truncated_quest_objectives(self.current_leader_client)
        # collect one item on all clients except leader
        for c in self.clients:
            if c.process_id != self.current_leader_client.process_id:
                follower_obj = await self.get_truncated_quest_objectives(c)

                if leader_obj == follower_obj:
                    collect_quester = Quester(c, self.clients, None)
                    await collect_quester.auto_collect_rewrite(c)

        # check if all clients have finished the entire quest
        # note this isnt guaranteed as auto_collect_rewrite only collects one item per call - it does not run until quest completion
        all_clients_moved_on = True
        for c in self.clients:
            if c.process_id != self.current_leader_client.process_id:
                follower_obj = await self.get_truncated_quest_objectives(c)
                if follower_obj == leader_obj:
                    all_clients_moved_on = False

        # finally, collect items on the leader
        # only do this if all clients have already finished their own collects
        if all_clients_moved_on:
            await self.auto_collect_rewrite(self.current_leader_client)

    async def bring_clients_to_same_location(self, questing_friend_tp: bool, gear_switching_in_solo_zones: bool):
        if getattr(self.current_leader_client, "refilling_potions", False) is True:
            return
        leader_pos = await self.current_leader_client.body.position()
        teleported = False
        for c in self.clients:
            if getattr(c, "refilling_potions", False) is True or getattr(c, "questing_status", True) is False:
                continue
            if await c.zone_name() == await self.current_leader_client.zone_name():
                errored = True
                # teleport throws should update bool
                while errored:
                    try:
                        await c.teleport(leader_pos)
                        errored = False
                        teleported = True
                    except ValueError:
                        errored = True
                        await asyncio.sleep(1.0)

        if teleported:
            await asyncio.sleep(2.0)

        # only friend TPs if clients are in different zones or we think we may be in a solo zone
        if questing_friend_tp:
            maybe_solo_zone = await self.determine_solo_zone()
            await self.zone_recorrect_friend_tp(maybe_solo_zone=maybe_solo_zone, gear_switching_in_solo_zones=gear_switching_in_solo_zones)

    async def initial_auto_pet(self, energy_info, questing_clients: list[Client], ignore_pet_level_up: bool, play_dance_game: bool):
        auto_pet_on = False
        for p in self.clients:
            if p.auto_pet_status:
                auto_pet_on = True

        if auto_pet_on:
            # potentially run auto_pet once initially, if all questing_clients have high energy
            all_high_energy = True
            for i, c in enumerate(questing_clients):
                # current energy / total energy - percent of remaining energy
                if ((energy_info[i][0] / energy_info[i][1]) * 100) < 70:
                    all_high_energy = False

            if all_high_energy:
                # buy potions if necessary, otherwise auto pet will fail
                if await self.heal_and_handle_potions() is False:
                    return

                logger.debug('All questing clients have high energy, training pets on all clients.')
                await gather_owned(*[auto_pet(c, ignore_pet_level_up, play_dance_game, questing=True) for c in self.clients])

    async def leader_wait_for_free(self, p: Client):
        while not await is_free_leader_questing(p):
            self._note_dungeon_recovery_wait(p)
            await asyncio.sleep(.1)

    # TODO: Slay the beast
    async def auto_quest_leader(self, questing_friend_tp: bool, gear_switching_in_solo_zones: bool, hitting_client, ignore_pet_level_up: bool, play_dance_game: bool):
        from src.mainline_progress import log_mainline_progress
        if any(await gather_owned(*[self._maybe_handle_bumbles_pet(p) for p in self.clients])):
            return
        if any(await gather_owned(*[self._maybe_recover_lemuria_navigation(p) for p in self.clients])):
            return
        if await self._maybe_handle_bumbles_mind(self.current_leader_client):
            return
        if await self._maybe_handle_tamarin_house(self.current_leader_client):
            return
        follower_clients = await self.get_follower_clients()
        questing_clients = await self.get_questing_clients()

        # read and store the name of the client's wizard, and check energy
        if questing_friend_tp or self.current_leader_client.auto_pet_status:
            # open character screen
            await gather_owned(*[self.open_character_screen(c) for c in self.clients])

            await gather_owned(*[set_wizard_name_from_character_screen(c) for c in self.clients])
            energy_info = await gather_owned(*[return_wizard_energy_from_character_screen(c) for c in questing_clients])

            # close character screen
            await gather_owned(*[self.close_character_screen(c) for c in self.clients])

        # if all questing clients have high energy, run auto pet once initially
        if self.current_leader_client.auto_pet_status:
            await self.initial_auto_pet(energy_info, questing_clients, ignore_pet_level_up, play_dance_game)

        if not self.client.questing_status:
            return
        questing_clients = [c for c in questing_clients if c.questing_status]
        follower_clients = await self.get_follower_clients()

        # gather all clients to the same zone and exact location
        await self.bring_clients_to_same_location(questing_friend_tp, gear_switching_in_solo_zones)

        # check for solo zone again as we may have changed zones
        maybe_solo_zone = await self.determine_solo_zone()

        # leader and follower clients can dynamically change during auto questing to account for clients being left behind
        client_quests = await self.get_client_quests(questing_clients=questing_clients)

        # just for providing info to the user in the log statement
        if len(client_quests) > 0:
            s = ''
            for cl in client_quests:
                if s == '':
                    s = cl.title
                else:
                    s = s + ', ' + cl.title

            logger.info('Clients on same quest: ' + s)

        # main loop
        while self.client.questing_status:
            await asyncio.sleep(.4)

            if await self._quest_dialogue_blocks_movement(self.current_leader_client):
                continue

            # in case client(s) in combat and the questing loop continued anyway
            await gather_owned(*[self.leader_wait_for_free(p) for p in self.clients])
            if any(await gather_owned(*[self._maybe_handle_bumbles_pet(p) for p in self.clients])):
                continue
            if any(await gather_owned(*[self._maybe_recover_lemuria_navigation(p) for p in self.clients])):
                continue
            if await self._mainline_sync_blocks_movement(self.current_leader_client):
                continue

            # The leader controls movement, but each questing client owns its
            # own tracked mainline task and one-time progress log.
            await gather_owned(*[
                log_mainline_progress(p) for p in self.clients if p.questing_status
            ])

            # Collect wisps, use potions, or get potions if necessary
            if await self.heal_and_handle_potions() is False:
                questing_clients = [c for c in questing_clients if c.questing_status]
                client_quests = {c: q for c, q in client_quests.items() if c.questing_status}
                follower_clients = await self.get_follower_clients()
                continue

            # if dungeon recall button is visible, click it
            await self.handle_dungeon_recall(follower_clients=follower_clients)

            # if auto pet is enabled and all questing clients have leveled up, send all to pavilion and train pets on ALL clients
            await self.auto_pet_questing(questing_clients, ignore_pet_level_up, play_dance_game)

            # dynamically change leader client when follower's get left behind
            # if there were previously clients on the same quest check for quest objective change on all clients
            follower_clients, client_quests = await self.determine_new_leader_and_followers(client_quests, questing_clients, follower_clients)

            if await self._mainline_sync_blocks_movement(self.current_leader_client):
                continue

            if await self._maybe_recover_mainline(self.current_leader_client):
                continue
            if (await is_free_leader_questing(self.current_leader_client)
                    and await self._maybe_photo_giant_vat(self.current_leader_client)):
                continue

            # handle circumstances where any follower client is not in the same zone as the leader client
            # keep in mind, the previous leader client may now be a follower client since we have just called determine_new_leader_and_followers()
            await self.handle_zone_correction(maybe_solo_zone, questing_friend_tp, gear_switching_in_solo_zones)

            if await is_free_leader_questing(self.current_leader_client):
                quest_xyz = await self.current_leader_client.quest_position.position()
                if await self._maybe_recover_nightmare(self.current_leader_client):
                    continue
                distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))

                # we are almost certainly not on a collect quest
                if distance > 1:
                    if (await self.current_leader_client.zone_name() != self.NIGHTMARE_ZONE
                            and await self._maybe_reenter_quest_trigger(self.current_leader_client, quest_xyz)):
                        continue

                    if await self._maybe_refresh_stalled_dungeon_quest(self.current_leader_client):
                        continue

                    if await self._mainline_sync_blocks_movement(self.current_leader_client):
                        continue

                    await self.teleport_to_quest(hitting_client, follower_clients)

                    if await self._mainline_sync_blocks_movement(self.current_leader_client):
                        continue
                    await self.handle_normal_quests(follower_clients, questing_friend_tp)
                else:
                    if await self._maybe_refresh_stalled_dungeon_quest(self.current_leader_client):
                        continue
                    # Double check - sometimes wiz lies about quest position - a simple sleep and re-grabbing of the quest xyz solves the issue
                    await asyncio.sleep(3.0)
                    quest_xyz = await self.current_leader_client.quest_position.position()
                    distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))

                    if distance < 1:
                        quest_objective = await get_quest_name(self.current_leader_client)

                        truncated_quest_obj = (await self.get_truncated_quest_objectives(self.current_leader_client)).lower()
                        # Key - truncated quest name
                        # Values - 0: time between entity respawns, 1-... : xyz locations of separate entities belonging to the quest
                        # TODO: Get rid of this once scripting system allows for quest libraries
                        incompatible_hardcoded_quests = {
                            'find submarine parts in the floating land': [40, XYZ(x=-17412.53515625, y=10505.794921875, z=-429.19927978515625), XYZ(x=-17088.447265625, y=12284.244140625, z=-410.1100769042969), XYZ(x=-21681.78125, y=11547.5966796875, z=-429.19927978515625)]
                            , 'collect sea cucumbers in pitch black lake': [40, XYZ(x=-8995.333984375, y=-830.6781616210938, z=-97.31965637207031), XYZ(x=-9911.9404296875, y=823.7628173828125, z=-97.31971740722656), XYZ(x=-11497.998046875, y=-1030.2618408203125, z=-97.32334899902344), XYZ(x=-12099.0517578125, y=-4674.13623046875, z=-96.95536804199219), XYZ(x=-14215.37109375, y=-4371.93798828125, z=-97.32661437988281), XYZ(x=-14287.5556640625, y=-2835.4814453125, z=-97.3197021484375)]
                            , 'catch vonda fish in pitch black lake': [3, XYZ(x=-10030.9912109375, y=-7925.51953125, z=-347.3206787109375)]
                            , 'break mining equipment in tyrian gorge': [25, XYZ(x=-2529.12890625, y=-3015.832763671875, z=-906.33837890625), XYZ(x=-3181.15234375, y=-6233.39306640625, z=-924.6146240234375), XYZ(x=1495.8695068359375, y=-7369.19580078125, z=-905.3265380859375), XYZ(x=-204.098876953125, y=-8468.421875, z=236.10702514648438), XYZ(x=2508.4169921875, y=-4712.10302734375, z=-1232.338134765625)]
                            , 'steal barrel of kermes fire in tyrian gorge': [35, XYZ(x=1352.575927734375, y=-4787.16552734375, z=-1098.1168212890625), XYZ(x=-1936.0185546875, y=-3954.15380859375, z=-1077.83837890625), XYZ(x=-1820.606201171875, y=-6331.4541015625, z=-1077.849853515625), XYZ(x=517.3740844726562, y=-7042.94287109375, z=-1077.84130859375), XYZ(x=-4628.16650390625, y=-5603.2578125, z=-906.51318359375), XYZ(x=-3216.014892578125, y=-5965.7412109375, z=-932.0715942382812), XYZ(x=-1106.3753662109375, y=-3800.721923828125, z=-905.3256225585938), XYZ(x=-451.9595031738281, y=-4547.75537109375, z=-905.325927734375)]
                        }

                        if any(map(truncated_quest_obj.__contains__, incompatible_hardcoded_quests.keys())):
                            logger.debug('Hardcoded collect')
                            await self.hardcoded_collect(incompatible_hardcoded_quests, truncated_quest_obj)
                        # normal collect quest
                        else:
                            logger.debug('Completing collect quest')
                            await self.handle_collect_quests()

                    else:
                        logger.debug('False collect quest detected.  Quest position: ' + str(distance))

    async def handle_pending_dungeon_confirmation(self) -> bool:
        """Confirm a dungeon transition modal even if it appeared late."""
        if not await is_visible_by_path(self.client, exit_dungeon_path):
            return False

        zone_before = await self.client.zone_name()
        logger.debug(
            f"Client {self.client.title} - confirming pending dungeon transition."
        )
        await click_window_by_path(self.client, exit_dungeon_path)

        # Do not wait forever if this was a slow or stale message box.  The
        # next quest iteration can retry the click if it remains visible.
        deadline = time.monotonic() + 15.0
        saw_loading = False
        while time.monotonic() < deadline:
            if await self.client.is_loading():
                saw_loading = True
                await asyncio.sleep(0.1)
                continue

            current_zone = await self.client.zone_name()
            if current_zone and current_zone != zone_before:
                # This confirmation is an exit/transition, not proof of entry.
                self.client.quest_dungeon_recovery = None
                self.client.quest_party_confirmed_dungeon_transition = (
                    zone_before, current_zone
                )
                if getattr(self.client, "quest_party_hitters", []):
                    self.client.quest_party_quest_worker_zone = current_zone
                    self.client.quest_party_probe_pending = (
                        getattr(self.client, "quest_party_group_dungeon_zone", None)
                        != current_zone
                    )
                break

            if saw_loading or not await is_visible_by_path(
                self.client, exit_dungeon_path
            ):
                break
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.5)
        return True

    async def handle_questing_zone_change(self):
        if await self.handle_pending_dungeon_confirmation():
            return
        while await self.client.is_loading():
            await asyncio.sleep(.1)

    async def prepare_party_dungeon_entry(self) -> list[Client]:
        """Move assigned hitters to the entrance before everyone presses X."""
        entry_clients = list(self.clients)
        assigned_hitters = [
            hitter
            for hitter in getattr(self.client, "quest_party_hitters", [])
            if getattr(hitter, "questing_status", False)
            and getattr(hitter, "refilling_potions", False) is not True
        ]
        if not assigned_hitters:
            return entry_clients

        deadline = time.monotonic() + 4.0
        pending = list(assigned_hitters)
        while pending and time.monotonic() < deadline:
            quester_zone = await self.client.zone_name()
            quester_position = await self.client.body.position()
            for hitter in list(pending):
                try:
                    if (
                        not await hitter.is_loading()
                        and await hitter.zone_name() == quester_zone
                        and await is_free(hitter)
                    ):
                        await hitter.teleport(quester_position)
                        if hitter not in entry_clients:
                            entry_clients.append(hitter)
                        pending.remove(hitter)
                except Exception as exc:
                    logger.debug(
                        f"Client {hitter.title} not ready for synchronized dungeon entry: {exc}"
                    )
            if pending:
                await asyncio.sleep(0.25)

        if pending:
            logger.warning(
                "The following hitters missed the synchronized dungeon entrance: "
                + ", ".join(hitter.title for hitter in pending)
            )
        if len(entry_clients) > len(self.clients):
            logger.info(
                "Synchronized dungeon entry: "
                + ", ".join(client.title for client in entry_clients)
            )
            await asyncio.sleep(0.5)
        return entry_clients

    async def party_dungeon_entry_complete(
        self, entry_clients: list[Client], entry_zone: str
    ) -> bool:
        """Confirm every assigned party member left the entrance after the X prompt."""
        hitters = getattr(self.client, "quest_party_hitters", [])
        if (
            not entry_zone
            or not hitters
            or any(hitter not in entry_clients for hitter in hitters)
        ):
            return False
        for client in [self.client, *hitters]:
            if await client.is_loading():
                return False
            zone = await client.zone_name()
            if not zone or zone == entry_zone:
                return False
        return True

    async def party_hitters_confirmed_dungeon_transition(
        self, previous_quester_zone: str | None
    ) -> bool:
        """Check delayed dungeon confirmations against this quester's transition."""
        hitters = getattr(self.client, "quest_party_hitters", [])
        if not previous_quester_zone or not hitters:
            return False
        for hitter in hitters:
            transition = getattr(
                hitter, "quest_party_confirmed_dungeon_transition", None
            )
            if (
                not transition
                or transition[0] != previous_quester_zone
                or not transition[1]
                or await hitter.is_loading()
                or await hitter.zone_name() != transition[1]
            ):
                return False
        return True

    async def _quest_party_probe_blocks_movement(self) -> bool:
        """Pause quest movement until the assigned hitter finishes its zone probe."""
        if any(getattr(h, 'refilling_potions', False)
               or isinstance(getattr(h, 'potion_dungeon_returned', None), tuple)
               for h in getattr(self.client, 'quest_party_hitters', [])):
            return True
        if isinstance(getattr(self.client, 'potion_dungeon_returned', None), tuple):
            return True
        if not getattr(self.client, "quest_party_hitters", []):
            return False
        if await self.client.is_loading():
            return True

        try:
            current_zone = await self.client.zone_name()
        except Exception as exc:
            logger.debug(
                f"Client {self.client.title} could not verify its current zone; "
                f"keeping quest movement paused: {exc}"
            )
            return True

        group_dungeon_zone = getattr(
            self.client, "quest_party_group_dungeon_zone", None
        )
        if group_dungeon_zone is not None and group_dungeon_zone == current_zone:
            self.client.quest_party_quest_worker_zone = current_zone
            self.client.quest_party_probe_pending = False
            return False
        if group_dungeon_zone is not None:
            self.client.quest_party_group_dungeon_zone = None

        worker_zone = getattr(
            self.client, "quest_party_quest_worker_zone", None
        )
        if worker_zone is None:
            self.client.quest_party_quest_worker_zone = current_zone
        elif current_zone != worker_zone:
            self.client.quest_party_quest_worker_zone = current_zone
            self.client.quest_party_probe_pending = True
            logger.debug(
                f"Client {self.client.title} changed zones outside the previous "
                "movement check; pausing quest movement for hitter probe."
            )

        return bool(getattr(self.client, "quest_party_probe_pending", False))

    async def auto_quest_solo(self, auto_pet_disabled=False, ignore_pet_level_up=False, play_dance_game=False):
        from src.mainline_progress import log_mainline_progress
        if getattr(self.client, "refilling_potions", False) is True:
            return
        if await self._quest_dialogue_blocks_movement(self.client):
            return
        if await self._maybe_handle_bumbles_pet(self.client):
            return
        if await close_npc_quest_menu(self.client):
            return
        if await close_automation_popup(self.client):
            return
        # The confirmation can appear shortly after the movement that opened
        # it, after handle_questing_zone_change already checked once.  Handle it
        # at the top of every iteration so it cannot block auto questing.
        if await self.handle_pending_dungeon_confirmation():
            return

        # A portal dialog may open after the preceding iteration's X check.
        # Finish it before probing followers or teleporting to the quest again.
        if await is_spiral_door_open(self.client):
            self._krok_exit_watch.pop(id(self.client), None)
            if not await self.new_world_doors(self.client):
                await spiral_door_with_quest(self.client)
            return

        # In configurable quest-party mode the assigned hitter first probes a
        # newly entered zone with friend teleport.  Do not start the next quest
        # movement until that probe decides whether the zone is solo-only.
        if await self._maybe_recover_lemuria_navigation(self.client):
            return
        if await self._quest_party_probe_blocks_movement():
            return
        if await self._maybe_handle_bumbles_mind(self.client):
            return
        if await self._maybe_handle_tamarin_house(self.client):
            return
        if await self._mainline_sync_blocks_movement(self.client):
            return
        if await self._maybe_recover_mainline(self.client):
            return
        if await is_free(self.client):
            if await self._maybe_photo_giant_vat(self.client):
                return
            await log_mainline_progress(self.client)
            if await is_potion_needed(self.client) and await self.client.stats.current_mana() > 1 and await self.client.stats.current_hitpoints() > 1:
                await collect_wisps(self.client)

            if self.client.use_potions:
                if await auto_potions(self.client, True, buy=self.client.buy_potions) is False:
                    logger.error(f"Client {self.client.title} - 补药或回传失败，跳过本轮任务移动。")
                    return

            quest_xyz = await self.client.quest_position.position()
            if await self._maybe_recover_nightmare(self.client):
                return

            if self.client.auto_pet_status and not auto_pet_disabled:
                # client has leveled up
                if self.client.character_level < await self.client.stats.reference_level():
                    logger.debug('Client ' + self.client.title + ' leveled up - training pet.')
                    await auto_pet(self.client, ignore_pet_level_up, play_dance_game, questing=True)

            distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))
            if distance > 1:
                while self.client.entity_detect_combat_status:
                    await asyncio.sleep(.1)

                if (await self.client.zone_name() != self.NIGHTMARE_ZONE
                        and await self._maybe_reenter_quest_trigger(self.client, quest_xyz)):
                    return

                if await self._maybe_refresh_stalled_dungeon_quest(self.client):
                    return

                if await self._quest_dialogue_blocks_movement(self.client):
                    return
                if await self._mainline_sync_blocks_movement(self.client):
                    return
                zone_before_quest_move = await self.client.zone_name()
                await self.teleport_to_quest_target(self.client, quest_xyz)

                # confirm exit dungeon early button or wait for client to exit loading
                await self.handle_questing_zone_change()

                if (
                    getattr(self.client, "quest_party_hitters", [])
                    and await self.client.zone_name() != zone_before_quest_move
                ):
                    # Set this from the quest worker itself, rather than relying
                    # only on the follower's polling interval.  This guarantees
                    # that no second quest teleport starts before the hitter has
                    # probed the newly entered zone.
                    self.client.quest_party_quest_worker_zone = (
                        await self.client.zone_name()
                    )
                    self.client.quest_party_probe_pending = True
                    logger.debug(
                        f"Client {self.client.title} changed zones; pausing quest movement for hitter probe."
                    )
                    return

                await asyncio.sleep(.5)
                if await is_visible_by_path(self.client, cancel_chest_roll_path):
                    # Handles chest reroll menu, will always cancel
                    await click_window_by_path(self.client, cancel_chest_roll_path)

                current_pos = await self.client.body.position()
                if await is_visible_by_path(self.client, npc_range_path) and calc_Distance(quest_xyz, current_pos) < 750.0:
                    # Handles interactables
                    sigil_msg_check = await self.read_popup(self.client)
                    if await self._maybe_photo_giant_vat(self.client, sigil_msg_check):
                        return
                    if is_dungeon_entry_prompt(sigil_msg_check):
                        # Handles entering dungeons
                        entry_clients = await self.prepare_party_dungeon_entry()
                        entry_zone = await self.client.zone_name()
                        self.client.quest_party_group_dungeon_zone = None
                        await gather_owned(
                            *[p.send_key(Keycode.X, 0.1) for p in entry_clients]
                        )
                        loading_clients = []
                        for c in entry_clients:
                            loading_deadline = time.monotonic() + 15.0
                            while (
                                not await c.is_loading()
                                and time.monotonic() < loading_deadline
                            ):
                                if await is_visible_by_path(c, dungeon_warning_path):
                                    await c.send_key(Keycode.ENTER, 0.1)
                                await asyncio.sleep(0.1)
                            if await c.is_loading():
                                loading_clients.append(c)
                            else:
                                logger.warning(
                                    f"Client {c.title} did not enter the dungeon in time."
                                )

                        for c in loading_clients:
                            while await c.is_loading():
                                await asyncio.sleep(0.1)
                        current_zone = await self.client.zone_name()
                        await self._confirm_dungeon_entry(self.client, entry_zone)
                        if getattr(self.client, "quest_party_hitters", []) and (
                            current_zone and current_zone != entry_zone
                        ):
                            self.client.quest_party_quest_worker_zone = current_zone
                            if await self.party_dungeon_entry_complete(
                                entry_clients, entry_zone
                            ):
                                self.client.quest_party_group_dungeon_zone = current_zone
                                self.client.quest_party_probe_pending = False
                                logger.info(
                                    f"Client {self.client.title} and assigned hitters "
                                    "entered the dungeon; resuming party zone sync."
                                )
                            else:
                                self.client.quest_party_probe_pending = True
                                logger.debug(
                                    f"Client {self.client.title} entered a dungeon; pausing for hitter probe."
                                )
                            return
                    elif interaction_kind(sigil_msg_check) == "talk":
                        quest_updated = await self.handle_npc_talking_quests(
                            self.client, [self.client]
                        )
                        if not quest_updated:
                            await asyncio.sleep(2.0)
                            return

                    else:
                        await self.client.send_key(Keycode.X, 0.1)

                        await asyncio.sleep(0.75)
                        if await is_spiral_door_open(self.client):
                            # Handles spiral door navigation
                            if await self.new_world_doors(self.client) == False:
                                await spiral_door_with_quest(self.client)

                quest_objective = await get_quest_name(self.client)

                if quest_has_action(quest_objective, "photomance"):
                    # Photomancy quests (WC, KM, LM)
                    await self.take_photomancy_photo(self.client, quest_objective)

            else:
                if await self._maybe_refresh_stalled_dungeon_quest(self.client):
                    return
                # Double check - sometimes wiz lies about quest position - a simple sleep and re-grabbing of the quest xyz seems to solve the issue
                await asyncio.sleep(3.0)
                quest_xyz = await self.client.quest_position.position()
                distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))

                if distance < 1:
                    await self.auto_collect_rewrite(self.client)
        else:
            self._note_dungeon_recovery_wait(self.client)

    async def auto_quest(self, ignore_pet_level_up: bool, play_dance_game: bool):
        while self.client.questing_status:
            await asyncio.sleep(1)
            await self.auto_quest_solo(ignore_pet_level_up=ignore_pet_level_up, play_dance_game=play_dance_game)



async def is_free_leader_questing(client: Client):
    # Returns True if not in combat, loading screen, dialogue, or forced animation dialogue.
    dialogue_text = await read_dialogue_text(client)
    return not any([await client.is_loading(), await client.in_battle(), await is_visible_by_path(client, advance_dialog_path), client.entity_detect_combat_status, (dialogue_text != '')])


async def read_dialogue_text(p: Client) -> str:
    try:
        dialogue_text = await get_window_from_path(p.root_window, dialog_text_path)
        txtmsg = await dialogue_text.maybe_text()
    except:
        txtmsg = ''
    return txtmsg
