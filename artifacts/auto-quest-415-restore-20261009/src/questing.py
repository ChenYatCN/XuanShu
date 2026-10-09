from src.task_lifecycle import gather_owned
import asyncio
import time
import traceback
import math
from contextlib import AsyncExitStack

from loguru import logger

import re
from src.auto_pet import auto_pet
from src.interaction_prompts import (
    is_dungeon_entry_prompt, interaction_kind, quest_has_action, quest_interaction_matches,
    split_quest_location, collect_object_name, portal_kind, resolve_portal_destination,
    plain_text,
)
from src.teleport_math import *
from src import teleport_math
from wizwalker import XYZ, Orient, Keycode, MemoryReadError, Client, Rectangle, HookAlreadyActivated, HookNotActive
from wizwalker.file_readers.wad import Wad
from wizwalker.memory import DynamicClientObject
from wizwalker.memory.memory_objects.enums import WindowFlags
from wizwalker.extensions.scripting import teleport_to_friend_from_list
from src.sprinty_client import SprintyClient
from src.utils import *
from src.utils import _party_area_token
from src.paths import *
from src.collecting import collect_one, CollectSearch, Candidate
from src.script_popups import close_automation_popup
from src.window_text import read_control_text
from src.automation_ownership import automation_owner
from src.world_to_screen import get_camera_state
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
    POWER_CORE_ZONE = 'Arcanum/Interiors/AR_Z02_PowerCore'
    POWER_CORE_EXIT = XYZ(22.596, -9478.560, 9.790)
    CALLISTO_ZONE = 'Krokotopia/KT_Selenopolis/Interiors/KT_Z06I06_ChamberCallisto_New_Int'
    CALLISTO_BATTLE = XYZ(264.357, -330.901, -221.027)
    CALLISTO_JAR = XYZ(-1262.171, -403.043, -221.027)
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
    PANOPTICON_BOOK_ZONE = 'Arcanum/AR_Z01_Hub'
    PANOPTICON_BOOK_POSITION = XYZ(-7088.015, -2901.169, -104.569)
    PANOPTICON_NPC_POSITION = XYZ(-2685.747, -665.561, -105.242)
    PANOPTICON_BOOK_TITLE = '神秘发现与缺乏隐秘侦查'
    PANOPTICON_NPC_TITLE = '菲茨休姆'
    BUMBLES_MIND_ZONE = 'Lemuria/Interiors/LM_Z07_BumblesMind'
    BUMBLES_MIND_BATTLE = XYZ(2.390, -259.991, -1124.726)
    DUELING_TENT_ZONE = 'Novus/Interiors/NV_Z01_DuelingTent'
    DUELING_TENT_EXIT = XYZ(16.146, -1194.045, -4.171)
    EASTON_HOUSE_ZONE = 'Darkmoor/Interiors/DM_Z01I03_EastonHouse'
    EASTON_HOUSE_EXIT = XYZ(4.903, 1730.343, 2.000)
    EASTON_DAY_RITUAL_ZONE = 'Darkmoor/Interiors/DM_Z01I06_EastonHouseDayEncounter'
    EASTON_DAY_QUEST_ID = 142707813506621765  # Creatures of the Day, verified user log.
    EASTON_DAY_RITUAL_POSITION = XYZ(-6454.599, 2026.960, 851.999)
    EASTON_DAY_RITUAL_ORIENTATION = Orient(0.000, 0.000, 1.573)
    DARKMOOR_MAYOR_PHOTO_ZONE = 'Darkmoor/Interiors/DM_Z02_MayorHouse_A'
    DARKMOOR_MAYOR_PHOTO_POSITION = XYZ(531.067, -473.838, .999)
    DARKMOOR_MAYOR_PHOTO_ORIENTATION = Orient(0.000, 0.000, 2.469)
    DARKMOOR_MAYOR_HARP_POSITION = XYZ(-1203.329, 3168.999, .999)
    DARKMOOR_MAYOR_HARP_ORIENTATION = Orient(0.000, 0.000, 1.490)
    DARKMOOR_CASTLE_ZONE = 'Darkmoor/Interiors/DM_Z02_Castle_A'
    NO_BLOOD_HIDEOUT_ZONE = 'Darkmoor/DM_Z03_HowlingLands'
    NO_BLOOD_QUEST_ID = 136233889045723621  # There Won't Be Blood, verified user log.
    NO_BLOOD_HIDEOUT_POSITION = XYZ(-17056.808, 106.266, -483.393)
    OVERGROWN_ESTATE_ZONE = 'Darkmoor/Interiors/DM_Z03_OvergrownEstate'
    OVERGROWN_ESTATE_QUEST_ID = 2467972595808887812  # Were-Dunnit, verified user log.
    OVERGROWN_ESTATE_DIALOGUE = XYZ(777.050, 1078.755, -1699.321)
    OVERGROWN_ESTATE_WAIT = XYZ(3108.774, 1960.014, -1714.557)
    OVERGROWN_ESTATE_CLUE_PREFIX = 'DM_Estate_Clue'
    DARKMOOR_CASTLE_ROUTE = (
        ('dialogue', XYZ(918.222, 208.404, 1.000)),
        ('interact', XYZ(2961.016, 1545.579, 1.000)),
        ('dialogue', XYZ(4793.177, 1544.604, 110.311)),
        ('battle', XYZ(4793.177, 1544.604, 110.311)),
        ('battle', XYZ(5146.941, -1541.925, 1.000)),
        ('interact', XYZ(-3492.767, -461.833, 1.000)),
        ('interact', XYZ(-4876.514, -496.477, .999)),
        ('interact', XYZ(-6135.229, -463.306, .999)),
        ('battle', XYZ(-4971.130, -1658.765, 1.000)),
        ('clock', XYZ(-6331.276, 1571.537, 1.000)),
        ('interact', XYZ(-2204.191, 3274.202, 561.399)),
        ('interact', XYZ(2217.120, 3289.541, 561.400)),
        ('dialogue', XYZ(-34.002, 6605.081, -162.617)),
    )
    EASTON_DAY_GUARD_EXIT = XYZ(26.099, 1739.252, 2.000)
    DARKMOOR_CANTRIP_ZONE = 'Darkmoor/DM_Z01_Graveholm'
    DARKMOOR_GARGOYLE_QUEST_ID = 141863388596636259  # Stakes and Stones, verified user log.
    DARKMOOR_GARGOYLE_EXIT = XYZ(7074.613, -2011.943, 31.999)
    DARKMOOR_COURTYARD_ZONE = 'Darkmoor/Interiors/DM_Z01I08_SafehouseCourtyardDay_01'
    DARKMOOR_COURTYARD_EXIT = XYZ(-1746.586, -895.015, 32.000)
    DARKMOOR_CANTRIP_QUEST_ID = 121034240342521958
    DARKMOOR_CLUE_ZONE = 'Darkmoor/Interiors/DM_Z01I05_BrokenBranchHovel'
    DARKMOOR_CLUE_POSITIONS = (XYZ(404.987, 1302.033, .999), XYZ(1348.089, 782.713, 1.000))
    DARKMOOR_CLUE_NPC_POSITION = XYZ(-442.365, 456.708, 1.000)
    DARKMOOR_TRACKER_POSITION = XYZ(-12196.810, 5297.215, 2.000)
    DARKMOOR_RITUAL_POSITION = XYZ(361.769, -18787.988, 27.134)
    DARKMOOR_RITUAL_ORIENTATION = Orient(0.000, 0.000, 4.692)
    DARKMOOR_SHADOWS_ZONE = 'Darkmoor/Interiors/DM_Z04I11_Shack'
    DARKMOOR_SHADOWS_QUEST_ID = 115686215786515754  # Second, It Lies, supplied log.
    DARKMOOR_SHADOWS_POSITION = XYZ(64.687, -895.276, 1.918)
    DARKMOOR_SHADOWS_ORIENTATION = Orient(0.000, 0.000, 6.267)
    DARKMOOR_SHADOWS_RESET_POSITION = XYZ(-330.967, 60.471, .256)
    DARKMOOR_THRONE_ZONE = 'Darkmoor/Interiors/DM_Z01I11_ParliamentOfNightThroneRoom'
    DARKMOOR_THRONE_FOLLOW_POSITION = XYZ(1584.332, 6442.159, 212.394)
    SCHOLOMANCE_LAB_ZONE = 'Darkmoor/DM_Z05_ScholomanceEntrance'
    SCHOLOMANCE_LAB_DIALOGUE = XYZ(-965.466, 4169.139, 1210.233)
    SCHOLOMANCE_EXIT_QUEST_ID = 161566636966019299  # Scholduggery, supplied log.
    SCHOLOMANCE_EXIT = XYZ(-554.610, 8442.424, 1693.215)
    SCHOLOMANCE_PARLOR_ZONE = 'Darkmoor/Interiors/DM_Z05I02_Parlor'
    SCHOLOMANCE_PARLOR_EXIT = XYZ(-423.460, 2150.752, 639.664)
    BUMBLES_PET_ZONE = 'Lemuria/LM_Z07_Heap'
    OUTBACK_STORY_ZONE = 'Wallaru/WL_Z02_Outback'
    OUTBACK_STORY_POSITION = XYZ(-16788.000, -6939.999, 119.999)
    FINAL_ACT_ZONE = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
    FINAL_ACT_DIALOGUE = XYZ(-47.276, 7293.811, -234.572)
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
    TRIGGER_NO_PROGRESS_SECONDS = 30.0
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

    async def _confirm_dungeon_entry(self, client: Client, previous_zone: str, mainline_id=None) -> None:
        """Arm recovery only after an entry prompt caused a real zone change."""
        current_zone = await client.zone_name()
        if current_zone and previous_zone and current_zone != previous_zone:
            old_state = getattr(client, 'quest_dungeon_recovery', None)
            client.quest_dungeon_recovery = {
                "zone": current_zone, "snapshot": None, "since": None,
                "active": False, "attempted": False, "waiting_logged": False,
                "mainline_id": mainline_id,
            }
            if isinstance(old_state, dict) and old_state.get('entry_zone'):
                client.quest_dungeon_recovery.update(entry_zone=old_state['entry_zone'], entered=True)

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
                or (await is_visible_by_path(client, npc_range_path)
                    and (not getattr(client, 'quest_party_hitters', [])
                         or getattr(client, 'quest_party_group_dungeon_zone', None) != await client.zone_name()
                         or interaction_kind(await self.read_popup(client)) == 'talk'))
                or await is_visible_by_path(client, missing_area_path)
                or await is_visible_by_path(client, all_quests_sort_button_path)):
            return True
        return False

    async def _refresh_dungeon_quest(self, client: Client, snapshot=None) -> bool:
        """A stalled dungeon goal restores the mainline, not that dungeon goal."""
        snapshot = snapshot or await self._dungeon_quest_snapshot(client)
        if snapshot is None:
            return False
        zone = await client.zone_name()
        owner = getattr(client, 'quest_recovery_owner', None)

        async def ready():
            return (await client.zone_name() == zone
                    and await self._dungeon_quest_snapshot(client) == snapshot
                    and not await self._dungeon_recovery_blocked_for_open_menu(client)
                    and getattr(client, 'quest_recovery_owner', None) == owner
                    and not getattr(client, 'refilling_potions', False)
                    and not getattr(client, 'quest_party_quest_worker_restart_requested', False)
                    and not getattr(client, 'post_combat_movement_active', False))

        async with automation_owner(client, 'dungeon-mainline-restore'):
            if not zone or not await ready():
                return False
            state = getattr(client, 'quest_dungeon_recovery', None)
            expected_id = state.get('mainline_id') if isinstance(state, dict) else None
            # The index also contains dungeon child quests. Only a verified
            # game mainline may supersede the captured entry mainline.
            identity = await self._mainline_identity(client)
            if identity and identity[3] is not None and identity[4] is True:
                expected_id = identity[0]
            logger.debug('{} 地牢主线恢复目标 Quest ID {}；当前追踪 Quest ID {}，游戏主线标志 {}',
                         client.title, expected_id, identity[0] if identity else None,
                         identity[4] if identity else None)
            if not await ready():
                return False
            restored = await self._restore_owned_mainline(
                client, expected_id=expected_id, guard=ready)
            if not restored:
                logger.warning('{} 地牢卡顿恢复未找到可确认的主线卡片，不重选子任务或其他任务。', client.title)
            return restored

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
        current_zone = await client.zone_name()
        if (not isinstance(state, dict) and (current_zone == self.POWER_CORE_ZONE
                or bool(getattr(client, 'quest_party_hitters', []))
                and isinstance(getattr(client, 'quest_party_group_dungeon_zone', None), str)
                and getattr(client, 'quest_party_group_dungeon_zone', None) == current_zone)):
            client.quest_dungeon_recovery = state = {
                'zone': current_zone, 'snapshot': None, 'since': None,
                'active': False, 'attempted': False, 'waiting_logged': False,
            }
        if not isinstance(state, dict) or state.get("active"):
            return False
        zone = await client.zone_name()
        if not zone or zone != state["zone"]:
            if zone and (zone == getattr(client, "quest_party_group_dungeon_zone", None)
                         or zone in self.LEMURIA_DUNGEON_ROOMS):
                # The existing party probe confirmed this next dungeon room.
                state.update(zone=zone, snapshot=None, since=None,
                             attempted=False, waiting_logged=False, card_refreshed=False, manual_wait=False)
            else:
                # A different room is not proof that we are still in the instance.
                client.quest_dungeon_recovery = None
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None:
            state["since"] = None
            state["snapshot"] = None
            state['card_refreshed'] = False
            return False
        now = time.monotonic()
        if snapshot != state["snapshot"] or state["since"] is None:
            state.update(snapshot=snapshot, since=now, attempted=False, waiting_logged=False,
                         card_refreshed=False, manual_wait=False)
            return False
        shared_dungeon = (bool(getattr(client, 'quest_party_hitters', []))
                          and getattr(client, 'quest_party_group_dungeon_zone', None) == zone)
        if shared_dungeon and state.get('manual_wait'):
            return True  # Progress/zone changes above release the manual hold.
        if now - state["since"] < self.DUNGEON_NO_PROGRESS_SECONDS:
            return False
        if await self._dungeon_recovery_blocked(client):
            self._note_dungeon_recovery_wait(client)
            return False
        # Re-read after the priority checks, before taking over the UI.
        current_snapshot = await self._dungeon_quest_snapshot(client)
        if current_snapshot is None or current_snapshot != snapshot:
            state.update(snapshot=current_snapshot, since=now if current_snapshot else None,
                         attempted=False, waiting_logged=False, card_refreshed=False)
            return False
        if shared_dungeon and state.get('attempted'):
            if not state.get('manual_wait'):
                state['manual_wait'] = True
                logger.warning('{} 地牢重选任务后仍连续 3 分钟无进展，本组原地等待人工处理；不再重选或尝试好友传送。',
                               client.title)
            return True
        callisto_battle = (not shared_dungeon and zone == self.CALLISTO_ZONE
                           and self._callisto_stage(snapshot[2]) == 'battle')
        power_core_exit = not shared_dungeon and zone == self.POWER_CORE_ZONE and state.get('card_refreshed', False)
        owner = 'power_core' if power_core_exit else 'callisto' if callisto_battle else 'dungeon_quest'
        if not claim_quest_recovery(client, owner):
            return False
        state["active"] = True
        try:
            if power_core_exit:
                logger.info('自动任务：PowerCore 重选任务卡后仍连续 3 分钟无进展，前往出口切区。')
                await self._recover_power_core_exit(client, snapshot)
                return True
            if callisto_battle:
                logger.info('自动任务：噬魂者阶段连续 3 分钟无进展，前往 Callisto 战斗触发点。')
                await self._callisto_tp_step(client, snapshot, 'battle')
                return True
            logger.info("自动任务：地牢内连续 3 分钟无任务进展，尝试恢复正在进行的主线卡片。")
            refreshed = await self._refresh_dungeon_quest(client, snapshot)
            if refreshed:
                if zone == self.POWER_CORE_ZONE:
                    state['card_refreshed'] = True
                logger.info("自动任务：任务追踪已刷新，继续任务传送。")
            elif shared_dungeon:
                state['manual_wait'] = True
                logger.warning('{} 地牢任务重选未能完成，本组原地等待人工处理。', client.title)
            return True
        except Exception as exc:
            if shared_dungeon:
                state['manual_wait'] = True
            logger.warning(f"Client {client.title} - dungeon quest refresh failed: {exc}")
            return True
        finally:
            state.update(since=time.monotonic(), active=False, attempted=True,
                         waiting_logged=False)
            try:
                state["snapshot"] = await self._dungeon_quest_snapshot(client)
                if power_core_exit and state['snapshot'] != snapshot:
                    state['card_refreshed'] = False
            finally:
                release_quest_recovery(client, owner)

    async def _recover_power_core_exit(self, client: Client, snapshot) -> None:
        """One bounded exit TP; a confirmation modal is optional, not required."""
        async def ready():
            return (await self._dungeon_quest_snapshot(client) == snapshot
                    and await client.zone_name() == self.POWER_CORE_ZONE
                    and not await client.is_loading() and not await client.in_battle()
                    and not await client.is_in_dialog()
                    and client.questing_status
                    and not getattr(client, 'refilling_potions', False)
                    and getattr(client, 'quest_recovery_owner', None) == 'power_core'
                    and not getattr(client, 'quest_party_probe_pending', False)
                    and not getattr(client, 'quest_party_battle_rescue_active', False)
                    and not getattr(client, 'quest_party_quest_worker_restart_requested', False)
                    and not getattr(client, 'post_combat_movement_active', False))

        async with automation_owner(client, 'power-core-exit'):
            async with asyncio.timeout(25):
                if (not await ready() or not await is_free_leader_questing(client)
                        or await self._dungeon_recovery_blocked_for_open_menu(client)
                        or not await ready()):
                    return
                await client.teleport(self.POWER_CORE_EXIT)
                deadline = time.monotonic() + 20
                arrival = None
                stable_since = None
                confirmed = False
                while time.monotonic() < deadline:
                    if (not client.questing_status or getattr(client, 'refilling_potions', False)
                            or await client.in_battle()):
                        return
                    zone = await client.zone_name()
                    if await client.is_loading() or not zone:
                        stable_since = None
                    elif zone != self.POWER_CORE_ZONE:
                        if zone != arrival or stable_since is None:
                            arrival, stable_since = zone, time.monotonic()
                        elif time.monotonic() - stable_since >= self.PRIVATE_WING_STABLE_SECONDS:
                            client.quest_dungeon_recovery = None
                            client.quest_party_confirmed_dungeon_transition = (self.POWER_CORE_ZONE, zone)
                            if getattr(client, 'quest_party_hitters', []):
                                client.quest_party_quest_worker_zone = zone
                                client.quest_party_probe_pending = (
                                    getattr(client, 'quest_party_group_dungeon_zone', None) != zone)
                            self._mainline_finder_observations.pop(id(client), None)
                            self._mainline_finder_retry_at.pop(id(client), None)
                            logger.info('自动任务：PowerCore 出口区域切换已确认，恢复正常自动任务。')
                            return
                    else:
                        stable_since = None
                        if not await ready():
                            return
                        if not confirmed and await is_visible_by_path(client, exit_dungeon_path):
                            if await is_visible_by_path(client, exit_dungeon_path) and await ready():
                                await self.handle_pending_dungeon_confirmation(client)
                                confirmed = True
                    await asyncio.sleep(.2)
                logger.warning('自动任务：PowerCore 出口 TP 后未确认区域切换，结束本次恢复，3 分钟后重试。')

    def _callisto_stage(self, text):
        objective, location = split_quest_location(text)
        objective = re.sub(r'\s+', '', objective).casefold()
        location = re.sub(r'\s+', '', location).casefold()
        if location != 'chamberofcallisto':
            return None
        if objective in ('击败噬魂者', '击败噬魂者souleater', 'defeatsouleater'):
            return 'battle'
        if objective in ('使用罐子', 'usejar'):
            return 'jar'
        return None

    async def _maybe_handle_callisto(self, client: Client) -> bool:
        if (await client.zone_name() != self.CALLISTO_ZONE
                or not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        stage = self._callisto_stage(snapshot[2]) if snapshot else None
        if stage is None:
            client._xuanshu_callisto_jar_retry = None
            return False
        state = getattr(client, 'quest_dungeon_recovery', None)
        if not isinstance(state, dict) or state.get('zone') != self.CALLISTO_ZONE:
            client.quest_dungeon_recovery = state = dict(
                zone=self.CALLISTO_ZONE, snapshot=None, since=None,
                active=False, attempted=False, waiting_logged=False)
        if state.get('active'):
            return True
        if stage == 'battle':
            return await self._maybe_refresh_stalled_dungeon_quest(client)
        # The exact post-battle objective is itself the stage confirmation;
        # support resuming here after a worker restart or a normal battle too.
        retry = getattr(client, '_xuanshu_callisto_jar_retry', None)
        if isinstance(retry, tuple) and retry[0] == snapshot and time.monotonic() < retry[1]:
            return True
        if not claim_quest_recovery(client, 'callisto'):
            return True
        state['active'] = True
        try:
            await self._callisto_tp_step(client, snapshot, 'jar')
        except Exception as exc:
            logger.warning(f'自动任务：{client.title} Callisto 罐子交互暂未完成：{exc}')
        finally:
            state['active'] = False
            release_quest_recovery(client, 'callisto')
        return True

    async def _callisto_tp_step(self, client, snapshot, stage):
        async def ready():
            if (not client.questing_status or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None) != 'callisto'
                    or getattr(client, 'quest_party_status_session', None) is not None
                    or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                    or getattr(client, 'quest_party_probe_pending', False)
                    or getattr(client, 'quest_party_battle_rescue_active', False)
                    or getattr(client, 'post_combat_movement_active', False)
                    or getattr(client, 'mainline_chain_retry_active', False)
                    or await self._dungeon_quest_snapshot(client) != snapshot):
                return False
            if not (await client.zone_name() == self.CALLISTO_ZONE
                    and not await client.is_loading() and not await client.in_battle()
                    and await is_free_leader_questing(client)
                    and not await is_spiral_door_open(client)
                    and not any([await is_visible_by_path(client, path) for path in (
                        exit_dungeon_path, dungeon_warning_path, cancel_multiple_quest_menu_path,
                        missing_area_path, all_quests_sort_button_path)])):
                return False
            # UI and memory awaits can start a transition/recovery. Recheck
            # the task and input-critical flags at the end, not just on entry.
            return (await self._dungeon_quest_snapshot(client) == snapshot
                    and await client.zone_name() == self.CALLISTO_ZONE
                    and not await client.is_loading() and not await client.in_battle()
                    and client.questing_status
                    and not getattr(client, 'refilling_potions', False)
                    and getattr(client, 'quest_recovery_owner', None) == 'callisto'
                    and not getattr(client, 'quest_party_probe_pending', False)
                    and not getattr(client, 'quest_party_battle_rescue_active', False))

        async with automation_owner(client, 'callisto-task-step'):
            async with asyncio.timeout(15):
                if not await ready():
                    return
                point = self.CALLISTO_BATTLE if stage == 'battle' else self.CALLISTO_JAR
                if stage == 'jar':
                    client._xuanshu_callisto_jar_retry = (
                        snapshot, time.monotonic() + self.DUNGEON_NO_PROGRESS_SECONDS)
                await client.teleport(point)
                deadline = time.monotonic() + (10 if stage == 'battle' else 5)
                while time.monotonic() < deadline:
                    if stage == 'battle' and await client.in_battle():
                        logger.info('自动任务：Callisto 已进入战斗，交由现有战斗流程处理。')
                        return
                    if not await ready():
                        return
                    if stage == 'jar' and await is_visible_by_path(client, npc_range_path):
                        title = plain_text(await get_popup_title(client)).casefold()
                        if (title in ('罐子', 'jar')
                                and calc_Distance(await client.body.position(), point) < 750
                                and plain_text(await get_popup_title(client)).casefold() == title
                                and await ready()
                                and not await client.is_loading() and not await client.in_battle()):
                            await client.send_key(Keycode.X, .1)
                            logger.info('自动任务：Callisto 罐子交互已按 X，等待任务更新。')
                            return
                    await asyncio.sleep(.2)
                logger.warning('自动任务：Callisto {}未确认，保留当前任务，稍后重试。',
                               '战斗触发' if stage == 'battle' else '罐子交互')

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

    async def _trigger_reentry_blocked(
        self, client: Client, owner: str = None, target=None, *, check_prompt=True
    ) -> bool:
        current_owner = getattr(client, "quest_recovery_owner", None)
        probe_pending = getattr(client, "quest_party_probe_pending", False)
        if (probe_pending and target is not None
                and getattr(client, 'quest_party_quester', None) is None):
            progress = await self._dungeon_quest_snapshot(client)
            if progress is not None and quest_has_action(progress[2], 'explore'):
                # Ordinary quester movement already continues during a hitter's
                # probe. Its own exploration/door re-entry must do the same.
                probe_pending = False
        if (not client.questing_status
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'bumbles_pet_pending', False) is True
                or isinstance(current_owner, str) and current_owner != owner
                or probe_pending
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
                or check_prompt and (await is_visible_by_path(client, npc_range_path) if target is None
                                     else await self.quest_interaction_ready(client, target))
                or await is_visible_by_path(client, exit_dungeon_path)
                or await is_visible_by_path(client, dungeon_warning_path)
                or await is_visible_by_path(client, decline_quest_path)
                or await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                or await is_visible_by_path(client, missing_area_path)
                or await is_visible_by_path(client, all_quests_sort_button_path))

    async def _maybe_reenter_quest_trigger(self, client: Client, target: XYZ) -> bool:
        """Re-enter a missed interaction/exploration trigger; never loop TP forever."""
        if await client.zone_name() == self.DARKMOOR_CANTRIP_ZONE:
            if self._graveholm_gargoyle_stage(await self._dungeon_quest_snapshot(client)):
                return False  # This exact stalled stage uses the supplied zone-change point.
        if (await client.zone_name() == self.EASTON_DAY_RITUAL_ZONE
                and calc_Distance(target, self.EASTON_DAY_RITUAL_POSITION) < 1000):
            progress = await self._dungeon_quest_snapshot(client)
            if progress is not None and progress[0] == self.EASTON_DAY_QUEST_ID:
                return False  # This ritual uses its supplied position, not the rejected HUD point.
        if (await client.zone_name() == self.FINAL_ACT_ZONE
                and await self._final_act_stage(client) is not None):
            return False  # This exact stage has a verified dialogue TP fallback.
        if (await client.zone_name() == self.SCHOLOMANCE_LAB_ZONE
                and await self._scholomance_lab_stage(client) is not None):
            return False  # Keep this stage's repeated-TP watch in control.
        if (await client.zone_name() == self.SCHOLOMANCE_LAB_ZONE
                and self._scholomance_exit_stage(await self._dungeon_quest_snapshot(client))):
            return False  # This later stage has its own supplied area-change point.
        if (await client.zone_name() == self.SCHOLOMANCE_PARLOR_ZONE
                and self._scholomance_parlor_stage(await self._dungeon_quest_snapshot(client))):
            return False  # Enter Spiral Door uses the supplied parlor exit.
        if (await client.zone_name() == self.OVERGROWN_ESTATE_ZONE
                and await self._overgrown_estate_stage(client) is not None):
            return False  # Its repeated-TP watch must not be cut off by generic reentry.
        tent_watch = self._krok_exit_watch.get(id(client), {})
        if (await client.zone_name() == self.DUELING_TENT_ZONE
                and (tent_watch.get('attempts', 0) > 0
                     or isinstance(getattr(client, '_xuanshu_dueling_tent_failed', None), dict)
                     or getattr(client, 'quest_recovery_owner', None) == 'dueling_tent')):
            return False  # A blocked TP belongs to this map's dedicated recovery.
        if (await client.zone_name() == self.EASTON_HOUSE_ZONE
                and (tent_watch.get('attempts', 0) > 0
                     or isinstance(getattr(client, '_xuanshu_easton_house_failed', None), dict)
                     or getattr(client, 'quest_recovery_owner', None) == 'easton_house')):
            return False
        key_id = id(client)
        if await self._trigger_reentry_blocked(client, target=target):
            state = self._trigger_reentry.get(key_id)
            if state is not None:
                state["seen"] = 0
                state["since"] = None
            if await self.quest_interaction_ready(client, target):
                self._trigger_reentry.pop(key_id, None)
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
            state = {"key": key, "seen": 0, "attempts": 0, "since": None,
                     "walking": False, "landings": [], "blocked_steps": [], "retry_after": 0.0}
            self._trigger_reentry[key_id] = state
        if (not state["walking"] and (calc_Distance(position, target) > self.TRIGGER_NEAR_DISTANCE
                or abs(position.z - target.z) > 150.0)):
            state["seen"] = 0
            state["since"] = None
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
        now = time.monotonic()
        if state["since"] is None:
            state["since"] = now
        if (state["seen"] < self.TRIGGER_STABLE_OBSERVATIONS
                or now - state["since"] < self.TRIGGER_NO_PROGRESS_SECONDS):
            if state["walking"]:
                await asyncio.sleep(0.5)
                return True  # Do not TP back to the original point between walk attempts.
            return False
        # Re-read the task after the safety checks, before taking control.
        if (await self._trigger_reentry_blocked(client, target=target)
                or await client.zone_name() != zone
                or await self._dungeon_quest_snapshot(client) != snapshot
                or calc_Distance(await client.quest_position.position(), target) > 50.0):
            state["seen"] = 0
            state["since"] = None
            return False
        if not claim_quest_recovery(client, "trigger_reentry"):
            return False
        attempt = state["attempts"]
        state["attempts"] += 1
        state["seen"] = 0
        state["since"] = now
        state["walking"] = True
        logger.info(
            'Client {} - {}', client.title,
            "自动任务：同一任务点 30 秒无进展，退到可达落点后步行重新进场。"
            if attempt == 0 else
            "自动任务：步行进场仍无进展，换一个可达落点重试。"
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
        from src.collision import CollisionWorld, get_collision_data

        async def stopped():
            if (await client.zone_name() != zone
                    or await self._dungeon_quest_snapshot(client) != snapshot
                    or calc_Distance(await client.quest_position.position(), target) > 50.0):
                self._trigger_reentry.pop(id(client), None)
                return True
            # A matching interaction is ready now: NPC, collectable, door or
            # transport. Do not walk away to finish a recovery route.
            if await self.quest_interaction_ready(client, target):
                self._trigger_reentry.pop(id(client), None)
                return True
            return await self._trigger_reentry_blocked(
                client, "trigger_reentry", target, check_prompt=False
            )

        if await stopped():
            return
        state = self._trigger_reentry[id(client)]
        player_radius = await teleport_math._resolve_player_radius(client, zone)
        collision_data = await get_collision_data(client, zone)
        world = CollisionWorld()
        world.load(collision_data)
        await teleport_math._recover_near_target(
            client, target, world, zone, player_radius, state,
            min_distance=500.0, max_distance=1500.0,
            goal_radius=teleport_math._QUEST_POINT_TOLERANCE,
            stop_condition=stopped,
        )
        if (quest_has_action(snapshot[2], 'explore') and not await stopped()
                and not await self.quest_interaction_ready(client, target)):
            # Reaching the arrow coordinate is not proof of entering a door.
            # Reuse the manual TP's nav-data landing + final walk, even when
            # already within 5 units, with this client's same safety watcher.
            logger.info('{} 前往任务点仍未触发，改用导航落点后步行进入。', client.title)
            movement = asyncio.create_task(navmap_tp(client, target, reenter=True))
            async def watch_progress():
                while not movement.done() and not await stopped():
                    await asyncio.sleep(.1)
            watcher = asyncio.create_task(watch_progress())
            try:
                async with asyncio.timeout(15):
                    done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        await watcher
                    if movement in done:
                        await movement
            finally:
                movement.cancel()
                watcher.cancel()
                await gather_owned(movement, watcher, return_exceptions=True)
        # Close enough is not proof of quest progress. Keep walk-only mode until
        # a real task/target/zone change or a matching interaction is observed.
        await stopped()

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
            title = (await title_text_path.maybe_text()
                     if title_text_path and await title_text_path.is_visible() else '')
        except Exception:
            title = ""
        if portal_kind(title) is None:
            # Some menu layouts render the verified title in a separate text
            # control. Scope the fallback to THIS visible travel-button parent,
            # never an NPC label elsewhere in the world or a hidden default.
            try:
                button = await get_spiral_teleport_button(client)
                if button is not None:
                    nodes = await self._visible_window_nodes(await button.parent(), [])
                    titles = [await self._window_text(window) for window, _ in nodes
                              if await window.maybe_read_type_name() in ('ControlText', 'ControlList')]
                    matches = [text for text in titles if portal_kind(text) == 'tamed_demox']
                    if len(matches) == 1:
                        return matches[0]
            except Exception:
                pass  # Preserve the ordinary title when the fallback is unreadable.
        return title

    async def read_popup(self, p: Client) -> str:
        try:
            popup_text_path = await get_window_from_path(p.root_window, popup_msgtext_path)
            txtmsg = await read_control_text(popup_text_path) if popup_text_path else ''
        except Exception as exc:
            self._note_quest_entry_wait(p, '进入提示读取失败', detail=str(exc))
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

    async def get_zone_chunks(self, client: Client = None) -> list[XYZ]:
        client = self.client if client is None else client
        wad = await self.load_wad(await client.zone_name())
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
               or getattr(p, 'quest_recovery_owner', None) in ('lemuria_navigation', 'dueling_tent', 'easton_house', 'darkmoor_cantrip', 'avalon_grain_entry', 'azteca_beetle_entry')
               for p in self.clients):
            return
        if await self.followers_in_correct_zone():
            return
        for p in self.clients:
            if await self._maybe_handle_panopticon_book(p):
                return
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
        if any(getattr(c, 'outback_story_pending', False) is True for c in self.clients):
            return [], None
        if any(getattr(c, 'bumbles_pet_pending', False) is True for c in self.clients):
            return [], None
        if any(getattr(c, 'quest_recovery_owner', None) in ('dueling_tent', 'easton_house', 'darkmoor_cantrip') for c in self.clients):
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

    async def find_quest_zone_area_name(self, client: Client, door_locations: list, *, exact=False) -> Optional[str]:
        location = await self.get_quest_zone_name(client)
        self.d_location = resolve_portal_destination(location, door_locations, exact=exact)
        return self.d_location

    async def new_world_doors(self, client: Client) -> bool:
        kind = portal_kind(await self.read_spiral_door_title(client))
        if kind in {"streamportal", "nanavator", "tamed_demox"}:
            choices = tamed_demox_locations if kind == "tamed_demox" else streamportal_locations if kind == "streamportal" else nanavator_locations
            snapshot = await self._dungeon_quest_snapshot(client) if kind == 'tamed_demox' else None
            location = (await self.find_quest_zone_area_name(client, choices, exact=True)
                        if kind == 'tamed_demox' else await self.find_quest_zone_area_name(client, choices))
            if location is None:
                logger.warning(f"Client {client.title}: 无法确定特殊传送门的任务目的地，跳过传送。")
                return True
            if kind == 'tamed_demox':
                zone = await client.zone_name()
                async def before_input():
                    return (snapshot is not None and getattr(client, 'questing_status', True) is not False
                            and not getattr(client, 'refilling_potions', False)
                            and not isinstance(getattr(client, 'quest_recovery_owner', None), str)
                            and not await client.is_loading() and not await client.in_battle()
                            and await client.zone_name() == zone
                            and await self._dungeon_quest_snapshot(client) == snapshot
                            and portal_kind(await self.read_spiral_door_title(client)) == kind)
                success = await new_portals_cycle(client, location, before_input=before_input)
            else:
                success = await new_portals_cycle(client, location)
            if success is False:
                self.d_location = None  # Do not dispatch peers after leader selection failed.
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
            # Standard world selection belongs only to the quester. Followers
            # regroup after the quester's transition rather than selecting here.

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

    async def _maybe_enter_avalon_grain_map(self, client: Client) -> bool:
        if getattr(client, 'quest_recovery_owner', None) == 'avalon_grain_entry':
            return True
        if (not getattr(client, 'questing_status', False)
                or await client.zone_name() != CollectSearch.AVALON_GRAIN_SOURCE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        search = CollectSearch(self, client)
        search.zone = CollectSearch.AVALON_GRAIN_SOURCE
        search.quest_id = await search.read_quest_id()
        search.goal = await search.snapshot()
        if not search.avalon_grain_route_required():
            return False
        await search.enter_avalon_grain_map()
        # Even a pending/failed transition must not fall through to ordinary TP.
        return True

    async def _maybe_enter_azteca_beetle_map(self, client: Client) -> bool:
        if getattr(client, 'quest_recovery_owner', None) == 'azteca_beetle_entry':
            return True
        if (not getattr(client, 'questing_status', False)
                or await client.zone_name() != CollectSearch.AZTECA_BEETLE_SOURCE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        search = CollectSearch(self, client)
        search.zone = CollectSearch.AZTECA_BEETLE_SOURCE
        search.quest_id = await search.read_quest_id()
        search.goal = await search.snapshot()
        if not search.azteca_beetle_route_required():
            return False
        await search.enter_azteca_beetle_map()
        # A pending/failed transition must not fall through to ordinary TP.
        return True

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

    async def handle_dungeon_entry(self, questing_friend_tp: bool, follower_clients: list[Client], entry_zone: str, mainline_id=None):
        # await self.enter_dungeon()
        await gather_owned(*[c.wait_for_zone_change() for c in self.clients])

        await asyncio.sleep(1.0)
        await self._confirm_dungeon_entry(self.current_leader_client, entry_zone, mainline_id)

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
                    await asyncio.sleep(.3)
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
                    elif time.monotonic() - quiet_since >= (
                            .3 if quest_state_changed(initial_state, await read_quest_state()) else 3.0):
                        break
                else:
                    quiet_since = None
                await asyncio.sleep(0.15)

            await gather_owned(
                *[exit_menus(c, after_talking_paths) for c in present_clients]
            )

            if quest_state_changed(initial_state, await read_quest_state()):
                logger.debug(f"Client {talking_client.title} - Quest update confirmed.")
                await self._continue_mainline_chain(talking_client, mainline_turn_in)
                return True
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
            if row is None:
                try:
                    zone = await client.zone_name()
                except Exception:
                    zone = None
                if isinstance(zone, str) and zone:
                    parts = zone.casefold().split('/', 2)
                    world = 'selenopolis' if parts[:2] == ['krokotopia', 'kt_selenopolis'] else parts[0]
                    row = match_quest(rows, quest_id, code, title, world=world)
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

    @staticmethod
    def _record_npc_menu_dialogue(selection, now):
        """A real visible dialogue is a response, not proof of accepting the quest."""
        if (isinstance(selection, dict)
                and (selection.get('pending_click') or selection.get('failure_reason') == 'no_response')
                and (not selection.get('failed') or selection.get('failure_reason') == 'no_response')):
            selection.update(pending_click=False, attempts=0, failed=False,
                             failure_reason=None, started_at=now, next_at=now + 1.5)

    async def _advance_npc_dialogue(self, client: Client) -> bool:
        """One shared, serialized dialogue/offer step for task and dialogue workers."""
        castle = getattr(client, '_xuanshu_darkmoor_castle', None)
        if (isinstance(castle, dict) and castle.get('index') == 9
                and castle.get('phase') in ('landing', 'interact', 'clock', 'clock_close', 'failed')
                and await client.zone_name() == self.DARKMOOR_CASTLE_ZONE):
            return True  # Preserve the clock choice, including an uncertain click.
        owner = getattr(client, 'quest_recovery_owner', None)
        if isinstance(owner, str) and owner not in ('npc_dialogue', 'outback_story'):
            return False
        async with automation_owner(client, 'npc-dialogue-step'):
            if await client.is_loading() or await client.in_battle():
                return False
            if await self._handle_darkmoor_clue_confirmation(client):
                return True
            if (await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                    and not await is_visible_by_path(client, advance_dialog_path)):
                return await close_npc_quest_menu(client)
            selection = getattr(client, 'npc_mainline_menu_selection', None)
            if isinstance(selection, dict) and await client.zone_name() != selection['zone']:
                client.npc_mainline_menu_selection = None
                return False
            button = await get_window_from_path(client.root_window, advance_dialog_path)
            if not button or not await button.is_visible():
                client._npc_complete_state = None
                if isinstance(selection, dict):
                    completed = selection.get('complete_before')
                    if completed is not None and (
                            await client.quest_id(), await client.goal_id()) != completed:
                        logger.info('{} NPC 完成对话后的任务进度变化已确认。', client.title)
                        client.npc_mainline_menu_selection = None
                        return False
                    identity = await self._mainline_identity(client)
                    if (identity and identity[3] == selection['row']
                            and identity[0] != selection['before_id']):
                        logger.info('{} 多任务列表主线接取已确认：{} | Quest ID {}',
                                    client.title, selection['title'], identity[0])
                        client.npc_mainline_menu_selection = None
                    elif not selection.get('failed') and time.monotonic() - selection['started_at'] < 20.0:
                        return True
                    else:
                        client.npc_mainline_menu_selection = None
                if getattr(client, '_npc_dialogue_debug', None) is not None:
                    logger.debug('{} NPC 对话/邀请按钮已关闭，等待任务数据稳定。', client.title)
                    client._npc_dialogue_debug = None
                return False
            if (isinstance(castle, dict) and castle.get('index') in (0, 2, 12)
                    and castle.get('phase') in ('landing', 'wait_dialogue')
                    and await client.zone_name() == self.DARKMOOR_CASTLE_ZONE
                    and not castle['seen']):
                # The independent dialogue worker can finish a short page
                # between route ticks. Record its actual visible button first.
                castle.update(seen=True, started_at=time.monotonic())
            estate = getattr(client, '_xuanshu_overgrown_estate', None)
            if (isinstance(estate, dict) and estate.get('owner_client') is client
                    and estate.get('phase') == 'dialogue_wait'
                    and await client.zone_name() == self.OVERGROWN_ESTATE_ZONE):
                estate['dialogue_seen'] = True
            caption = plain_text(await self._window_text(button)).casefold()
            invitation = (caption in ('接受', 'accept')
                          or await is_visible_by_path(client, decline_quest_path))
            diagnostic = (caption, invitation)
            if getattr(client, '_npc_dialogue_debug', None) != diagnostic:
                logger.debug('{} NPC 状态：按钮 {!r}，任务邀请 {}', client.title, caption, invitation)
                client._npc_dialogue_debug = diagnostic
            now = time.monotonic()
            self._record_npc_menu_dialogue(selection, now)
            state = getattr(client, 'quest_invitation_state', None)
            if invitation:
                if isinstance(selection, dict):
                    if selection.get('failed'):
                        return True
                    candidate = await self._mainline_offer_candidate(client, allow_unowned=True)
                    if candidate is not None and candidate[1] != selection['row']:
                        selection['failed'] = True
                        selection['failure_reason'] = 'wrong_offer'
                        logger.warning('{} 列表所选主线与当前邀请不一致，停止自动接取。', client.title)
                        return True
                if not isinstance(state, dict) or state.get('closed'):
                    state = {'before_id': await client.quest_id(), 'attempts': 0,
                             'next_at': 0.0, 'started_at': now, 'failed': False}
                    client.quest_invitation_state = state
                if now < state['next_at'] or state['failed']:
                    return True
                if state['attempts'] >= 3:
                    # Fast clicks must not shorten the server's response window.
                    if now - state.get('started_at', now - 4.5) < 4.5:
                        return True
                    state['failed'] = True
                    logger.debug('{} 任务邀请接受重试耗尽，关闭邀请后交由现有主线恢复。', client.title)
                    await client.send_key(Keycode.ESC, 0.1)
                    return True
                # Re-read the actual UI while holding the same input owner.
                if await button.is_visible() and not await button.is_control_grayed():
                    if isinstance(selection, dict) and (
                            not (getattr(client, 'questing_status', False) is True
                                 or getattr(client, 'auto_dialogue_running', False) is True)
                            or getattr(client, 'quest_recovery_owner', None) != owner
                            or getattr(client, 'refilling_potions', False) is True
                            or getattr(client, 'quest_party_probe_pending', False) is True
                            or getattr(client, 'quest_party_battle_rescue_active', False) is True):
                        return False
                    await self._click_ui_window(client, button)
                    state['attempts'] += 1
                    state['next_at'] = now + .3
                    logger.debug('{} 任务邀请：点击接受 {}/3，接取前 Quest ID {}',
                                 client.title, state['attempts'], state['before_id'])
                    client.quest_dialogue_settle = {'snapshot': None, 'since': None}
            else:
                if caption in ('完成', 'complete', 'done', 'finish'):
                    key = (await client.zone_name(), await client.quest_id(), await client.goal_id(), caption)
                    complete = getattr(client, '_npc_complete_state', None)
                    if not isinstance(complete, dict) or complete['key'] != key:
                        complete = {'key': key, 'attempts': 0, 'next_at': 0.0}
                        client._npc_complete_state = complete
                    if now < complete['next_at']:
                        return True
                    if complete['attempts'] >= 3:
                        if not complete.get('logged'):
                            logger.warning('{} NPC 完成按钮点击后仍无变化，停止重复点击，等待界面或任务更新。', client.title)
                            complete['logged'] = True
                        return True
                    if (not await button.is_visible() or await button.is_control_grayed()
                            or plain_text(await self._window_text(button)).casefold() != caption):
                        return True
                    if (isinstance(selection, dict) and selection.get('failed')
                            or not (getattr(client, 'questing_status', False) is True
                                    or getattr(client, 'auto_dialogue_running', False) is True)
                            or getattr(client, 'quest_recovery_owner', None) != owner
                            or getattr(client, 'refilling_potions', False) is True
                            or (isinstance(selection, dict)
                                and getattr(client, 'quest_party_probe_pending', False) is True)
                            or getattr(client, 'quest_party_battle_rescue_active', False) is True
                            or await client.is_loading() or await client.in_battle()):
                        return False
                    await self._click_ui_window(client, button)
                    if isinstance(selection, dict):
                        selection['complete_before'] = key[1:3]
                    complete['attempts'] += 1
                    complete['next_at'] = now + .3
                    client.quest_dialogue_settle = {'snapshot': None, 'since': None}
                    return True
                client._npc_complete_state = None
                next_at = getattr(client, '_npc_dialogue_next_at', 0.0)
                if not isinstance(next_at, (int, float)) or now >= next_at:
                    await client.send_key(Keycode.SPACEBAR)
                    client._npc_dialogue_next_at = now + .3
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
        if now - state['since'] < .3:
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
        if (getattr(client, 'outback_story_pending', False) is True
                or any(getattr(c, 'outback_story_pending', False) is True
                       for c in [*self.clients, *getattr(client, 'quest_mainline_sync_members', [])])):
            return True
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
        """Pause an unmatched quest, then run one bounded Quest Finder pass."""
        if getattr(client, 'outback_story_pending', False) is True:
            return True
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
        if ((current_zone in self.LEMURIA_DUNGEON_ROOMS or current_zone == self.POWER_CORE_ZONE)
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

        # Side-world mainlines may carry the game flag without belonging to our
        # mainline index. An index miss follows the normal bounded Finder flow.
        # Wait for stable reads before recovering an unmatched quest.
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

        logger.info('自动任务：当前追踪未满足目标主线条件，开始检查已接主线任务。')
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
                    if (await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                            and not isinstance(getattr(client, 'npc_mainline_menu_selection', None), dict)):
                        await close_npc_quest_menu(client, select_mainlines=False)
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

    async def _questbook_page(self, client: Client, mainlines=None, *, backwards=False, cards=None):
        menu = await get_window_from_path(client.root_window, quest_buttons_parent_path)
        if not menu or not await menu.is_visible():
            raise RuntimeError('Q 任务菜单未显示')
        nodes = await self._visible_window_nodes(menu, quest_buttons_parent_path)
        page_cards = [(window, path) for window, path in nodes
                 if re.fullmatch(r'wndQuestInfo\d+', path[-1])]
        if not page_cards:
            raise RuntimeError('当前任务页没有可读取的任务卡片')

        signature = []
        finder = []
        for card, card_path in page_cards:
            descendants = [(window, path) for window, path in nodes
                           if path[:len(card_path)] == card_path]
            texts = [(await self._window_text(window), window, path)
                     for window, path in descendants]
            signature.append(tuple(text for text, _, _ in texts if text))
            if mainlines is not None or cards is not None:
                title = next((text for text, _, path in texts
                              if ('title' in path[-1].casefold()
                                  or path[-1].casefold() in ('txtquestname', 'txtname')) and text), '')
                starred = any(path[-1].casefold() in ('leftmainline', 'rightmainline')
                              for _, path in descendants)
                if title:
                    target = next(((window, path) for _, window, path in texts
                                   if path[-1] == 'txtGoal'), (card, card_path))
                    if cards is not None:
                        cards.append((title, target))
                    if mainlines is not None and starred:
                        mainlines.append((title, target))
            if any(text.casefold() in ('任务搜寻', 'quest finder')
                   for text, _, _ in texts):
                target = next(((window, path) for _, window, path in texts
                               if path[-1] == 'txtGoal'), (card, card_path))
                finder.append(target)
        if len(finder) > 1:
            raise RuntimeError('当前页出现多个“任务搜寻”卡片')

        outside_cards = [(window, path) for window, path in nodes
                         if not any(path[:len(card_path)] == card_path
                                    for _, card_path in page_cards)]
        # These actual button names are present in the live questbook logs.
        # Prefer their identity over background panels and arrow decorations.
        button_name = 'btnPrevPage' if backwards else 'btnNextPage'
        candidates = [(window, path) for window, path in outside_cards
                      if path[-1] == button_name]
        if not candidates:
            candidates = [(window, path) for window, path in outside_cards
                          if 'background' not in path[-1].casefold()
                          and re.search(r'left|prev|back' if backwards else r'right|next|forward',
                                        path[-1], re.I)]
        if len(candidates) > 1:
            # Widget-relative geometry only disambiguates named candidates;
            # it never substitutes a fixed screen position for a UI identity.
            try:
                card_positions = [(await card.scale_to_client()).center()[0]
                                  for card, _ in page_cards]
                card_x = min(card_positions) if backwards else max(card_positions)
                candidates = [(window, path) for window, path in candidates
                              if ((await window.scale_to_client()).center()[0] < card_x
                                  if backwards else
                                  (await window.scale_to_client()).center()[0] > card_x)]
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

    def _wait_npc_menu_read(self, client: Client, signature, reason) -> bool:
        """Bound the silent read-retry phase without guessing a menu action."""
        now = time.monotonic()
        state = getattr(client, 'npc_mainline_menu_read_wait', None)
        if not isinstance(state, dict) or state['signature'] != signature or state['reason'] != reason:
            state = {'signature': signature, 'reason': reason, 'since': now, 'warned': False}
            client.npc_mainline_menu_read_wait = state
            logger.debug('{} 多任务菜单等待读取：{}。', client.title, reason)
        elif not state['warned'] and now - state['since'] >= 5.0:
            state['warned'] = True
            logger.warning('{} 多任务菜单读取重试已超过 5 秒：{}；暂停自动选择，保留列表等待数据恢复或手动处理。',
                           client.title, reason)
        return True

    async def _npc_menu_quest_snapshot(self, client: Client, quest_id, zone):
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is not None:
            return snapshot
        # A menu may hide/rebuild the HUD. Reuse only the pre-X talk snapshot,
        # with the SAME quest/goal/zone and the actor still at the same position.
        context = getattr(client, 'npc_mainline_menu_context', None)
        if not isinstance(context, dict) or context['snapshot'][0] != quest_id:
            return None
        try:
            if (context['zone'] != zone or time.monotonic() - context['at'] > 60.0
                    or await client.goal_id() != context['snapshot'][1]
                    or calc_Distance(await client.body.position(), context['anchor']) > 100
                    or await client.zone_name() != zone or await client.quest_id() != quest_id
                    or await client.goal_id() != context['snapshot'][1]):
                return None
            return context['snapshot']
        except Exception:
            return None

    async def _select_npc_mainline_menu(self, client: Client, expected_id=None) -> bool:
        """Select an indexed title from the actual NPC list, never an icon or fixed position."""
        from src.mainline_progress import match_quest, quest_rows

        async with automation_owner(client, 'npc-mainline-menu'):
            owner = getattr(client, 'quest_recovery_owner', None)
            if (isinstance(owner, str) and owner not in ('npc_dialogue', 'mainline_finder')
                    or getattr(client, 'refilling_potions', False) is True
                    or getattr(client, '_character_selection_active', False) is True
                    or getattr(client, 'quest_party_quest_worker_restart_requested', False) is True
                    or getattr(client, 'quest_party_probe_pending', False) is True
                    or getattr(client, 'quest_party_battle_rescue_active', False) is True
                    or await client.is_loading() or await client.in_battle()):
                return True
            if (not await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                    or await is_visible_by_path(client, advance_dialog_path)):
                return False
            path = cancel_multiple_quest_menu_path[:2]
            menu = await get_window_from_path(client.root_window, path)
            if not menu or not await menu.is_visible():
                return True
            rows = quest_rows()
            zone = await client.zone_name()
            before_id = await client.quest_id()
            identity = await self._mainline_identity(client)
            zone_parts = zone.casefold().split('/', 2)
            world = ('selenopolis' if zone_parts[:2] == ['krokotopia', 'kt_selenopolis']
                     else zone_parts[0])
            if identity and identity[3] is not None:
                world = identity[3]['world'].split('(', 1)[0].strip().casefold()
            world_rows = [row for row in rows
                          if row['world'].split('(', 1)[0].strip().casefold() == world]
            nodes = await self._visible_window_nodes(menu, path)
            matches = []
            ambiguous = []
            for window, node_path in nodes:
                title = await self._window_text(window)
                row = (match_quest(world_rows, None, '', title)
                       or match_quest(rows, None, '', title)) if title else None
                if row is not None:
                    matches.append((row, title, window, node_path))
                elif title and any(match_quest([item], None, '', title) is not None for item in rows):
                    ambiguous.append((node_path, title))
            # A button and its text child can expose the same title. Click the
            # deepest actual text window; distinct duplicate entries stay ambiguous.
            matches = [item for item in matches if not any(
                other[0] == item[0] and len(other[3]) > len(item[3])
                and other[3][:len(item[3])] == item[3] for other in matches)]
            menu_signature = (zone, before_id, tuple((item[3], item[1]) for item in matches))
            if getattr(client, '_npc_mainline_menu_debug', None) != menu_signature:
                client._npc_mainline_menu_debug = menu_signature
                logger.debug('{} 多任务菜单已读取：当前 Quest ID {}，识别主线选项 {}。',
                             client.title, before_id, [item[1] for item in matches])
            if not matches:
                if ambiguous:
                    signature = (zone, before_id, tuple(ambiguous))
                    state = getattr(client, 'npc_mainline_menu_selection', None)
                    if not isinstance(state, dict) or state['signature'] != signature:
                        client.npc_mainline_menu_selection = {
                            'signature': signature, 'row': None, 'title': '', 'zone': zone,
                            'before_id': before_id, 'started_at': time.monotonic(), 'failed': True,
                        }
                        logger.warning('{} NPC 列表存在同名主线，当前世界无法消除歧义，保留列表。', client.title)
                    return True
                return False
            selection = getattr(client, 'npc_mainline_menu_selection', None)
            selected_confirmed = bool(
                isinstance(selection, dict) and identity and identity[3] is not None
                and identity[3] == selection.get('row')
                and (identity[0] != selection.get('before_id')
                     or selection.get('before_goal') is not None
                     and await client.goal_id() != selection['before_goal'])
            )
            owned_here = (identity and identity[3] is not None
                          and any(item[0] == identity[3] for item in matches))
            owned_turn_in = False
            dismiss_owned = selected_confirmed
            snapshot = None
            actor_window = None
            actor_title = None
            if expected_id is None and owned_here and not selected_confirmed:
                snapshot = await self._npc_menu_quest_snapshot(client, before_id, zone)
                if snapshot is None or snapshot[0] != before_id:
                    return self._wait_npc_menu_read(client, menu_signature, '当前任务目标不可读')
                if quest_has_action(snapshot[2], 'talk'):
                    # The outdoor NPCRange popup often disappears on opening
                    # this menu. Inspect visible text in the verified menu's
                    # existing dialogue subtree, not nearby actors or choices.
                    actors = {}
                    dialog_path = tuple(cancel_multiple_quest_menu_path[:-1])
                    for window, node_path in nodes:
                        if (node_path[:len(dialog_path)] != dialog_path
                                or node_path[-1] == dialog_text_path[-1]):
                            continue
                        if await window.maybe_read_type_name() != 'ControlText':
                            continue
                        title = await self._window_text(window)
                        if title and quest_interaction_matches(snapshot[2], title):
                            actors.setdefault(title.casefold(), (title, window))
                    if len(actors) > 1:
                        return self._wait_npc_menu_read(client, menu_signature, '菜单内 NPC 身份存在歧义')
                    if actors:
                        actor_title, actor_window = next(iter(actors.values()))
                    npc = actor_title or await get_popup_title(client)
                    if not npc:
                        return self._wait_npc_menu_read(client, menu_signature, '菜单内 NPC 名称及附近交互提示均不可读')
                    owned_turn_in = quest_interaction_matches(snapshot[2], npc)
                dismiss_owned = not owned_turn_in
            client.npc_mainline_menu_read_wait = None
            if dismiss_owned:
                # Acceptance can return straight to the NPC list, bypassing the
                # button-hidden confirmation branch. Close it, don't reselect.
                if (await client.is_loading() or await client.in_battle()
                        or await client.zone_name() != zone or await client.quest_id() != before_id
                        or snapshot is not None and await client.goal_id() != snapshot[1]
                        or await is_visible_by_path(client, advance_dialog_path)
                        or getattr(client, 'quest_recovery_owner', None) != owner
                        or getattr(client, 'refilling_potions', False) is True
                        or getattr(client, 'quest_party_probe_pending', False) is True
                        or getattr(client, 'quest_party_battle_rescue_active', False) is True
                        or not (getattr(client, 'questing_status', False) is True
                                or getattr(client, 'auto_dialogue_running', False) is True)):
                    return True
                logger.info('{} NPC 列表中的主线已接取且无需重选，关闭列表继续当前任务：{}',
                            client.title, identity[2])
                client.npc_mainline_menu_selection = None
                return False  # Existing close_npc_quest_menu owns the cancel button.
            if expected_id is not None:
                quest = (await (await client.quest_manager()).quest_data()).get(expected_id)
                code = await quest.name_lang_key() if quest is not None else ''
                expected = match_quest(rows, expected_id, code or '', '', world=world)
                choices = [item for item in matches if expected is not None and item[0] == expected]
            else:
                choices = [item for item in matches
                           if item[0]['world'].split('(', 1)[0].strip().casefold() == world]
                # Some mainline NPCs offer the next world while standing outside it.
                if not choices and len({item[0]['world'] for item in matches}) == 1:
                    choices = matches
                if owned_turn_in:
                    choices = [item for item in choices if item[0] == identity[3]]
            if choices:
                first = min(item[0]['number'] for item in choices)
                choices = [item for item in choices if item[0]['number'] == first]
            signature = (zone, before_id, tuple((item[3], item[1]) for item in matches))
            now = time.monotonic()
            if len(choices) != 1:
                state = getattr(client, 'npc_mainline_menu_selection', None)
                if not isinstance(state, dict) or state['signature'] != signature:
                    client.npc_mainline_menu_selection = {
                        'signature': signature, 'row': None, 'title': '', 'zone': zone,
                        'before_id': before_id, 'started_at': now, 'failed': True,
                    }
                    logger.warning('{} NPC 列表主线分支或同名选项无法唯一确认，保留列表等待手动选择。', client.title)
                return True
            row, title, target, _ = choices[0]
            state = getattr(client, 'npc_mainline_menu_selection', None)
            if not isinstance(state, dict) or state['signature'] != signature:
                state = {'signature': signature, 'row': row, 'title': title, 'zone': zone,
                         'before_id': before_id, 'attempts': 0, 'next_at': 0.0,
                         'started_at': now, 'failed': False, 'click_count': 0,
                         'pending_click': False, 'failure_reason': None}
                client.npc_mainline_menu_selection = state
            if owned_turn_in and snapshot is not None:
                state.setdefault('before_goal', snapshot[1])
            if state['failed'] or now < state['next_at']:
                return True
            if state['attempts'] >= 2:
                state['failed'] = True
                state['failure_reason'] = 'no_response'
                logger.warning('{} NPC 主线列表选择两次未响应，停止重复点击并保留列表。', client.title)
                return True
            if state.get('click_count', 0) >= 4:
                state.update(failed=True, failure_reason='unconfirmed_response')
                logger.warning('{} NPC 列表已切换对话，但主线接取仍未确认，保留列表等待手动选择。', client.title)
                return True
            # All awaited reads above can race a worker/zone/UI change.
            if (getattr(client, 'quest_recovery_owner', None) != owner
                    or getattr(client, 'refilling_potions', False) is True
                    or getattr(client, 'quest_party_probe_pending', False) is True
                    or getattr(client, 'quest_party_battle_rescue_active', False) is True
                    or await client.is_loading() or await client.in_battle()
                    or await client.zone_name() != zone or await client.quest_id() != before_id
                    or snapshot is not None and await client.goal_id() != snapshot[1]
                    or actor_window is not None and (
                        not await actor_window.is_visible()
                        or await self._window_text(actor_window) != actor_title)
                    or not await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                    or await is_visible_by_path(client, advance_dialog_path)
                    or not await target.is_visible() or await self._window_text(target) != title):
                return True
            if (not (getattr(client, 'questing_status', False) is True
                     or getattr(client, 'auto_dialogue_running', False) is True)
                    or getattr(client, '_character_selection_active', False) is True
                    or getattr(client, 'quest_party_quest_worker_restart_requested', False) is True
                    or getattr(client, 'refilling_potions', False) is True
                    or getattr(client, 'quest_party_probe_pending', False) is True
                    or getattr(client, 'quest_party_battle_rescue_active', False) is True
                    or getattr(client, 'quest_recovery_owner', None) != owner):
                return True
            # The selected text window belongs to the verified, visible list subtree.
            await self._click_ui_window(client, target)
            state.update(attempts=state['attempts'] + 1, next_at=now + 1.5,
                         click_count=state.get('click_count', 0) + 1, pending_click=True)
            client.quest_invitation_state = None
            client.quest_dialogue_settle = {'snapshot': None, 'since': None}
            client.mainline_finder_offer_guard = False
            logger.info('{} NPC 多任务列表：选择主线 {}（{} 第 {}），等待现有对话流程接取。',
                        client.title, title, row['world'], row['number'])
            return True

    async def _restore_owned_mainline(self, client: Client, expected_id=None, *, guard=None) -> bool:
        """Scan existing quest cards before searching for an unaccepted quest."""
        from src.mainline_progress import match_quest, normalize_name, quest_rows

        rows = quest_rows()
        owned = []
        zone = await client.zone_name()
        zone_parts = zone.casefold().split('/', 2)
        world = ('selenopolis' if zone_parts[:2] == ['krokotopia', 'kt_selenopolis']
                 else zone_parts[0])
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
            row = match_quest(rows, quest_id, code, title or '', world=world)
            if row is not None:
                owned.append((quest_id, title or '', row))

        eligible = [item for item in owned if (
            expected_id is not None and item[0] == expected_id
            or expected_id is None
            and item[2]['world'].split('(', 1)[0].strip().casefold() == world)]
        if eligible:
            first = min(item[2]['number'] for item in eligible)
            eligible = [item for item in eligible if item[2]['number'] == first]
            if len({item[0] for item in eligible}) > 1:
                raise RuntimeError('存在多条同序号的当前世界主线，无法唯一恢复追踪')
        preferred_ids = {item[0] for item in eligible}
        try:
            if guard is not None and not await guard():
                return False
            if not await is_visible_by_path(client, quest_buttons_parent_path):
                await client.send_key(Keycode.Q)
            deadline = time.monotonic() + 4.0
            while (not await is_visible_by_path(client, quest_buttons_parent_path)
                   or not await is_visible_by_path(client, all_quests_sort_button_path)):
                if guard is not None and not await guard():
                    return False
                if time.monotonic() >= deadline:
                    raise RuntimeError('检查已接主线时任务菜单未稳定打开')
                await asyncio.sleep(.1)
            if guard is not None and not await guard():
                return False
            await click_window_by_path(client, all_quests_sort_button_path)
            await asyncio.sleep(.3)
            logger.info('自动任务：检查各页已接任务，优先恢复现有主线追踪。')
            seen = set()
            load_deadline = time.monotonic() + 4.0
            for page_number in range(1, self.MAINLINE_FINDER_MAX_PAGES + 1):
                while True:
                    if guard is not None and not await guard():
                        return False
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
                logger.debug('{} Q 菜单已检查第 {} 页（从打开时当前页开始），带星主线卡片数 {}',
                             client.title, page_number, len(cards))
                selected = None
                for title, _target in cards:
                    row = match_quest(rows, None, '', title)
                    if row is None:
                        # New worlds can reuse an older world's quest title.
                        # Only the already verified, eligible identities may
                        # resolve that ambiguity; never choose by title alone.
                        row = match_quest([item[2] for item in eligible], None, '', title)
                    matches = [item for item in owned
                               if (item[2] == row if row is not None else
                                   normalize_name(item[1]) == normalize_name(title))]
                    if row is not None and len(matches) != 1:
                        raise RuntimeError('任务列表中存在主线，但 Quest ID 暂无法唯一确认；暂不执行任务搜寻')
                    if len(matches) == 1:
                        quest_id, _, matched = matches[0]
                        if quest_id in preferred_ids:
                            selected = (quest_id, title, _target)
                            break
                if selected is not None:
                    quest_id, title, target = selected
                    logger.debug('{} Q 菜单第 {} 个检查页命中主线：{}，Quest ID {}，停止翻页',
                                 client.title, page_number, title, quest_id)
                    if guard is None:
                        await self._click_ui_window(client, target[0])
                    else:
                        async with client.mouse_handler:
                            current_cards = []
                            current, _, _ = await self._questbook_page(client, mainlines=current_cards)
                            fresh = [candidate for text, candidate in current_cards
                                     if normalize_name(text) == normalize_name(title)]
                            if (current != signature or len(fresh) != 1
                                    or fresh[0][1] != target[1] or not await guard()):
                                return False
                            await client.mouse_handler.click_window(fresh[0][0])
                    await self._close_questbook(client)
                    stable_since = None
                    deadline = time.monotonic() + 8.0
                    while time.monotonic() < deadline:
                        if guard is not None and (
                                not client.questing_status or getattr(client, 'refilling_potions', False)
                                or getattr(client, 'quest_party_probe_pending', False)
                                or getattr(client, 'quest_party_battle_rescue_active', False)
                                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                                or await client.zone_name() != zone):
                            return False
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
                if guard is not None and not await guard():
                    return False
                await self._click_ui_window(client, next_page[0])
                deadline = time.monotonic() + 2.5
                while True:
                    await asyncio.sleep(.1)
                    if guard is not None and not await guard():
                        return False
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
                if (await is_visible_by_path(client, quest_buttons_parent_path)
                        and await is_visible_by_path(client, all_quests_sort_button_path)):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('按 Q 后任务菜单未稳定打开')

            await click_window_by_path(client, all_quests_sort_button_path)
            await asyncio.sleep(.3)
            logger.info('自动任务：正在任务菜单中寻找“任务搜寻”。')
            seen = set()
            for _ in range(self.MAINLINE_FINDER_MAX_PAGES):
                signature, finder, next_page = await self._questbook_page(client, backwards=True)
                if signature in seen:
                    raise RuntimeError('任务页已循环回到检查过的页面')
                seen.add(signature)
                if finder:
                    logger.debug('任务搜寻 UI 路径：{}', list(finder[1]))
                    await self._click_ui_window(client, finder[0])
                    logger.info('自动任务：已找到“任务搜寻”，开始寻找可接主线。')
                    break
                if next_page is None:
                    raise RuntimeError('无法唯一识别左侧翻页控件')
                logger.debug('任务左翻页 UI 路径：{}', list(next_page[1]))
                await self._click_ui_window(client, next_page[0])
                refresh_deadline = time.monotonic() + 2.5
                while time.monotonic() < refresh_deadline:
                    await asyncio.sleep(0.1)
                    changed, _, _ = await self._questbook_page(client, backwards=True)
                    if changed != signature:
                        break
                else:
                    raise RuntimeError('点击左翻页后任务页未刷新')
            else:
                raise RuntimeError('任务页翻页达到保护上限')

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                identity = await self._mainline_identity(client)
                if identity and identity[1].casefold() in ('quest finder', '任务搜寻', '任务搜索'):
                    return True
                await asyncio.sleep(0.1)
            raise RuntimeError('点击“任务搜寻”后未确认追踪任务切换')
        finally:
            await self._close_questbook(client)

    async def _mainline_offer_candidate(self, client: Client, *, allow_unowned=False):
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
            if row is None and title:
                try:
                    zone = await client.zone_name()
                except Exception:
                    continue
                if not isinstance(zone, str) or not zone:
                    continue
                zone_parts = zone.casefold().split('/', 2)
                world = ('selenopolis' if zone_parts[:2] == ['krokotopia', 'kt_selenopolis']
                         else zone_parts[0])
                world_rows = [item for item in rows
                              if item['world'].split('(', 1)[0].strip().casefold() == world]
                row = match_quest(world_rows, None, '', title)
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
                if match_quest(rows, quest_id, code, resolved or '', world=row['world']) == row:
                    candidates.append(quest_id)
            except Exception:
                continue
        if len(candidates) != 1:
            if allow_unowned and not candidates:
                return None, row
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
        if identity[1].casefold() not in ('quest finder', '任务搜寻', '任务搜索') and not await self._select_quest_finder(client):
            return False

        expected_quest_id = None
        accepted_at = None
        interaction_attempts = 0
        dialogue_since = None
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline and getattr(client, 'questing_status', False):
            identity = await self._mainline_identity(client)
            if identity and identity[3] is not None:
                selection = getattr(client, 'npc_mainline_menu_selection', None)
                if isinstance(selection, dict) and identity[3] != selection['row']:
                    return False
                if ((expected_id is None or identity[0] == expected_id)
                        and (expected_quest_id is None or identity[0] == expected_quest_id)):
                    client._xuanshu_mainline_id = None
                    client.npc_mainline_menu_selection = None
                    await log_mainline_progress(client)
                    return True
                if identity[0] != expected_id:
                    await asyncio.sleep(0.2)
                    continue
                return False
            if identity and identity[1].casefold() not in ('quest finder', '任务搜寻', '任务搜索'):
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
                selection = getattr(client, 'npc_mainline_menu_selection', None)
                if await is_visible_by_path(client, advance_dialog_path):
                    self._record_npc_menu_dialogue(selection, time.monotonic())
                selected_mainline = isinstance(selection, dict) and not selection.get('failed')
                candidate = (await self._mainline_offer_candidate(client, allow_unowned=True)
                             if selected_mainline else await self._mainline_offer_candidate(client))
                # Finder points to an unaccepted quest; its Quest ID may not
                # exist in owned quest data until we accept the guided offer.
                if ((candidate is None and interaction_attempts == 0 and not selected_mainline)
                        or candidate is not None and selected_mainline and candidate[1] != selection['row']
                        or candidate is not None and expected_id is not None
                        and candidate[0] is not None and candidate[0] != expected_id):
                    logger.warning('自动任务：无法在接取前确认邀请属于主线，拒绝接取。')
                    await client.send_key(Keycode.ESC, 0.1)
                    return False
                expected_quest_id = candidate[0] if candidate is not None and candidate[0] is not None else expected_id
                await click_window_by_path(client, advance_dialog_path)
                accepted_at = time.monotonic()
                await asyncio.sleep(0.4)
                continue
            if await is_visible_by_path(client, cancel_multiple_quest_menu_path):
                if await self._select_npc_mainline_menu(client, expected_id=expected_id):
                    await asyncio.sleep(.2)
                    continue
                await close_npc_quest_menu(client)
                return False
            if await is_visible_by_path(client, advance_dialog_path):
                self._record_npc_menu_dialogue(
                    getattr(client, 'npc_mainline_menu_selection', None), time.monotonic()
                )
                # Only advance an unindexed dialogue after this Finder pass
                # interacted with the NPC at its tracked destination.
                if accepted_at is not None:
                    await asyncio.sleep(0.2)
                    continue
                if dialogue_since is None:
                    dialogue_since = time.monotonic()
                elif time.monotonic() - dialogue_since >= 5.0:
                    logger.warning('自动任务：任务对话未提供可确认的邀请，拒绝盲目推进。')
                    await client.send_key(Keycode.ESC, 0.1)
                    return False
                if interaction_attempts > 0:
                    await click_window_by_path(client, advance_dialog_path)
                    await asyncio.sleep(0.4)
                    continue
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
            try:
                goal = await self._dungeon_quest_snapshot(client)
                if (goal is not None and goal[0] == quest_id
                        and quest_has_action(goal[2], 'talk')
                        and quest_interaction_matches(goal[2], npc)
                        and await client.zone_name() == snapshot[1]
                        and await client.quest_id() == goal[0]
                        and await client.goal_id() == goal[1]):
                    client.npc_mainline_menu_context = {
                        'snapshot': goal, 'zone': snapshot[1], 'anchor': snapshot[2],
                        'at': time.monotonic(),
                    }
            except Exception:
                pass  # Optional menu context must not break existing handoff.
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

    def _note_quest_entry_wait(self, client, reason, **details):
        """Keep entrance failures observable without logging every movement tick."""
        now = time.monotonic()
        records = getattr(client, '_quest_entry_diagnostic', None)
        if not isinstance(records, dict):
            client._quest_entry_diagnostic = records = {}
        key = (reason, details.get('member'))
        previous = records.get(key)
        signature = repr(details)
        if (isinstance(previous, tuple) and previous[0] == signature
                and now - previous[1] < 15):
            return
        records[key] = (signature, now)
        logger.debug('{} 入口检查：{}；{}', client.title, reason, details)

    async def _visible_team_up_control(self, client):
        # Both names already occur in the supported NPC-range layouts. Only
        # actual visibility is evidence; a retained hidden widget is not.
        for path in (team_up_wait_path, team_up_button_path):
            if await is_visible_by_path(client, path):
                return True
        return False

    async def party_dungeon_entry_visible(self, client, prompt=None):
        """A visible Team Up control distinguishes a sigil from an ordinary door."""
        if prompt is None:
            prompt = await self.read_popup(client)
        return (is_dungeon_entry_prompt(prompt)
                and await is_visible_by_path(client, npc_range_path)
                and await self._visible_team_up_control(client))

    async def quest_interaction_ready(self, client, xyz, leader_client=None, *, require_objective_match=True):
        if await client.is_loading() or await client.in_battle():
            return False
        if not await is_visible_by_path(client, npc_range_path):
            return False
        if calc_Distance(await client.body.position(), xyz) >= 750:
            return False
        if not require_objective_match:
            # 4.1.5 ordinary X input uses the visible range window and quest
            # position, even when a portal's title differs from its destination.
            # A nearby NPC is not arrival at a different tracked destination.
            # Otherwise its talk popup cancels navmap's landing + final walk.
            if interaction_kind(await self.read_popup(client)) == 'talk':
                title = await get_popup_title(client)
                objective = await get_quest_name(leader_client or client)
                if (not quest_has_action(objective, 'talk')
                        and not quest_interaction_matches(objective, title)):
                    self._note_quest_entry_wait(client, '附近 NPC 不是当前任务目标，继续任务传送',
                                               title=title, objective=objective)
                    return False
            return True
        title = await get_popup_title(client)
        if not title:
            self._note_quest_entry_wait(client, '范围窗可见但入口标题为空')
            return False
        prompt = await self.read_popup(client)
        kind = interaction_kind(prompt)
        if kind is None and not plain_text(prompt) and '<string;' not in str(prompt):
            self._note_quest_entry_wait(client, '范围窗可见但交互提示为空', title=title)
            return False
        objective = await get_quest_name(leader_client or client)
        if await self.party_dungeon_entry_visible(client, prompt):
            # The current HUD arrow may name the boss inside, or use another
            # translation of the entrance. Position + visible sigil are evidence.
            source = leader_client or client
            quest_id, goal_id = await source.quest_id(), await source.goal_id()
            valid = (type(quest_id) is int and quest_id > 0 and type(goal_id) is int
                     and bool(plain_text(objective)))
            if not valid:
                self._note_quest_entry_wait(client, '组队入口可见但任务数据未就绪',
                                           quest_id=quest_id, goal_id=goal_id, objective=objective)
            return valid
        if (portal_kind(title) == 'world_gate' and kind in {'enter', 'teleport'}
                and quest_has_action(objective, 'explore')):
            # The HUD arrow locates this known world gate; its generic title
            # need not repeat the destination. Existing world routing chooses it.
            return True
        matched = quest_interaction_matches(objective, title)
        if not matched:
            self._note_quest_entry_wait(client, '目标未匹配且未确认组队入口',
                                       title=title, prompt=prompt, kind=kind, objective=objective,
                                       team_up=await self._visible_team_up_control(client))
        return matched

    async def handle_quest_interaction(self, client, xyz) -> bool:
        """Use the existing handlers once this client's tracked prompt is ready."""
        if not await self.quest_interaction_ready(client, xyz, require_objective_match=False):
            return False
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'post_combat_cleanup_active', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or not await is_free_leader_questing(client)):
            return True  # Do not fall through to movement while input is owned.
        prompt = await self.read_popup(client)
        if await self._maybe_photo_giant_vat(client, prompt):
            return True
        if (await self.party_dungeon_entry_visible(client, prompt)
                or (is_dungeon_entry_prompt(prompt)
                    and getattr(client, 'quest_party_hitters', [])
                    and not (getattr(client, 'in_solo_zone', False)
                             and getattr(client, 'quest_party_group_dungeon_zone', None) is None
                             and getattr(client, 'quest_party_quest_worker_zone', None) == await client.zone_name()
                             and not getattr(client, 'quest_party_probe_pending', False)))):
            before = await client.quest_id(), await client.goal_id(), await client.zone_name()
            title = plain_text(await get_popup_title(client))
            async def entry_ready():
                return (type(before[0]) is int and before[0] > 0 and type(before[1]) is int
                        and bool(before[2]) and getattr(client, 'questing_status', False)
                        and (await client.quest_id(), await client.goal_id(), await client.zone_name()) == before
                        and not await client.is_loading() and not await client.in_battle()
                        and await self.quest_interaction_ready(client, xyz, require_objective_match=False)
                        and plain_text(await get_popup_title(client)) == title
                        and is_dungeon_entry_prompt(await self.read_popup(client)))
            self._note_quest_entry_wait(client, '已识别入口，准备本组', title=title)
            entry_clients = await self.prepare_party_dungeon_entry(entry_ready=entry_ready)
            if not await self.enter_party_dungeon(entry_clients, entry_ready=entry_ready):
                self._note_quest_entry_wait(client, '本组入口检查未通过或尚未完成切区', title=title)
        elif interaction_kind(prompt) == 'talk':
            if not await self.handle_npc_talking_quests(client, [client]):
                await asyncio.sleep(2.0)
        else:
            try:
                before = await client.quest_id(), await client.goal_id(), await client.zone_name()
            except Exception:
                return True
            if (type(before[0]) is not int or before[0] <= 0 or type(before[1]) is not int
                    or not isinstance(before[2], str) or not before[2]):
                return True  # Unreadable task/zone identity never authorizes X.
            async with automation_owner(client, 'quest-interaction'):
                if (not getattr(client, 'questing_status', False)
                        or getattr(client, 'refilling_potions', False)
                        or getattr(client, 'post_combat_cleanup_active', False)
                        or getattr(client, 'quest_party_battle_rescue_active', False)
                        or isinstance(getattr(client, 'quest_recovery_owner', None), str)
                        or not await is_free_leader_questing(client)
                        or (await client.quest_id(), await client.goal_id(), await client.zone_name()) != before
                        or not await self.quest_interaction_ready(client, xyz, require_objective_match=False)
                        or plain_text(await self.read_popup(client)) != plain_text(prompt)):
                    return True
                await client.send_key(Keycode.X, .1)
            await asyncio.sleep(.75)
            if await is_spiral_door_open(client):
                if not await self.new_world_doors(client):
                    await spiral_door_with_quest(client)
        return True

    async def move_until_quest_interaction(self, client, xyz, leader_client=None):
        # Let the existing interaction handler press X once movement has stopped.
        async def interaction_pending():
            return (not await is_free_leader_questing(client)
                    or await is_spiral_door_open(client)
                    or await self.quest_interaction_ready(client, xyz, leader_client, require_objective_match=False))

        if await interaction_pending():
            return

        # Keep recovery on the quester, inside the movement path so assigned
        # parties have already passed their shared-area preflight. Hitters use
        # this same target, but must not inspect/recover their own tracked goal.
        if (client is (leader_client or self.client)
                and getattr(client, 'quest_party_quester', None) is None
                and await client.zone_name() != self.NIGHTMARE_ZONE
                and await self._maybe_reenter_quest_trigger(client, xyz)):
            return

        async def watch_interaction():
            while True:
                await asyncio.sleep(.1)
                if await interaction_pending():
                    return

        movement = asyncio.create_task(navmap_tp(client, xyz, leader_client=leader_client))
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

    async def _outback_story_stage(self, client):
        """Confirm the indexed quest and its footprint goal, never a title alone."""
        identity = await self._mainline_identity(client)
        if (not identity or not identity[0] or identity[1] != 'QuestTitle_17D895'
                or identity[3] is None
                or identity[3]['world'].casefold() != 'wallaru'
                or identity[3]['number'] != 15):
            return None
        try:
            goal_id = await client.goal_id()
            quest = (await (await client.quest_manager()).quest_data()).get(identity[0])
            goal = (await quest.goal_data()).get(goal_id)
            code = await goal.name_lang_key()
            if code != 'WizQst17D895_00000007':
                return None
            if (await client.quest_id(), await client.goal_id()) != (identity[0], goal_id):
                return None
            return identity[0], goal_id, code
        except Exception:
            return None

    async def _outback_story_missing_target(self, client):
        valid_target = False
        try:
            target = await client.quest_position.position()
            valid_target = (all(math.isfinite(v) for v in (target.x, target.y, target.z))
                            and calc_Distance(target, XYZ(0, 0, 0)) > 1.0)
        except Exception:
            pass
        try:
            arrow = await get_window_from_path(client.root_window, quest_helper_arrow_path)
            distance = await get_window_from_path(client.root_window, quest_helper_distance_path)
            if arrow is not None and distance is not None:
                visible = await arrow.is_visible()
                has_distance = bool(re.search(r'\d', plain_text(await distance.maybe_text())))
                if visible and has_distance:
                    return False
                hud = await get_window_from_path(client.root_window, quest_helper_hud_path)
                if hud is not None and await hud.is_visible() and not visible and not has_distance:
                    return True  # The guide is absent; a leftover XYZ is not navigation proof.
        except Exception:
            pass
        return not valid_target

    async def _outback_story_settled(self, client, state):
        """Advance dialogue first; inspect quest identity only after it closes."""
        if await client.is_loading() or await client.in_battle():
            state['stable'] = None
            return False
        dialogue = (await client.is_in_dialog()
                    or await is_visible_by_path(client, advance_dialog_path)
                    or bool(plain_text(await read_dialogue_text(client))))
        if dialogue:
            if not state['dialogue_seen']:
                logger.info('{} 已检测到剧情对话，暂停任务传送。', client.title)
                state['dialogue_seen'] = True
            state['stable'] = None
            await self._advance_npc_dialogue(client)
            return False
        if not await is_free_leader_questing(client):
            state['stable'] = None
            return False
        quest_id = await client.quest_id()
        if isinstance(quest_id, int) and quest_id > 0 and quest_id != state['phase'][0]:
            state['stage_ended'] = True  # Never TP again after a readable ID change.
        identity = await self._mainline_identity(client)
        goal_id = await client.goal_id()
        changed = identity and identity[0] == quest_id and identity[3] is not None
        if changed and quest_id == state['phase'][0]:
            # This quest can first advance to defeating Goannas before the next
            # QuestID. Require a real, different goal as well as a matched quest.
            quest = (await (await client.quest_manager()).quest_data()).get(quest_id)
            goal = (await quest.goal_data()).get(goal_id)
            code = await goal.name_lang_key() if goal is not None else None
            changed = bool(code and code != state['phase'][2] and goal_id != state['phase'][1])
        if not changed:
            state['stable'] = None
            return False
        state['stage_ended'] = True
        snapshot = (quest_id, identity[1], goal_id, await client.zone_name())
        now = time.monotonic()
        if not state['stable'] or state['stable'][0] != snapshot:
            state['stable'] = (snapshot, now)
            return False
        if now - state['stable'][1] < 3.0:
            return False
        state.update(holding=False, result='complete')
        client.outback_story_pending = False
        client.quest_dialogue_settle = None
        for member in getattr(client, 'quest_mainline_sync_members', [client]):
            member.quest_mainline_sync_state = None
        for member in getattr(client, 'quest_mainline_sync_members', [client]):
            member.quest_mainline_sync_state = None
        self._mainline_finder_observations.pop(id(client), None)
        self._mainline_finder_retry_at.pop(id(client), None)
        logger.info('{} 对话结束并确认后续主线阶段，恢复自动任务。', client.title)
        return True

    async def _maybe_handle_outback_story(self, client: Client) -> bool:
        owner = 'outback_story'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        if (getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        state = getattr(client, 'quest_outback_story', None)
        if not getattr(client, 'questing_status', False):
            client.outback_story_pending = False
            return False
        if isinstance(state, dict) and state['holding']:
            client.outback_story_pending = True
            if not claim_quest_recovery(client, owner):
                return True
            try:
                return not await self._outback_story_settled(client, state)
            except Exception:
                state['stable'] = None
                return True  # Read gaps cannot rearm TP or escape the story hold.
            finally:
                release_quest_recovery(client, owner)
        if await client.zone_name() != self.OUTBACK_STORY_ZONE:
            return False
        # Let the existing first-entry probe decide whether followers can join
        # before this stage takes ownership; otherwise the solo worker stalls.
        if getattr(client, 'quest_party_probe_pending', False):
            return False
        if (await client.is_loading() or await client.in_battle()
                or await client.is_in_dialog() or not await is_free_leader_questing(client)
                or await is_spiral_door_open(client)):
            return False
        phase = await self._outback_story_stage(client)
        if phase is None:
            return False
        if isinstance(state, dict) and state['phase'][::2] == phase[::2]:
            return False  # Completed/cancelled attempts survive worker recreation.
        if not await self._outback_story_missing_target(client):
            client._outback_story_observation = None
            return False
        observation = getattr(client, '_outback_story_observation', None)
        now = time.monotonic()
        if not isinstance(observation, dict) or observation['phase'] != phase:
            client._outback_story_observation = {'phase': phase, 'since': now, 'reads': 1}
            return True
        observation['reads'] += 1
        if observation['reads'] < 3 or now - observation['since'] < 1.5:
            return True
        if (getattr(client, 'mainline_chain_retry_active', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'post_combat_movement_active', False)
                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                or not claim_quest_recovery(client, owner)):
            return True
        try:
            if (await client.zone_name() != self.OUTBACK_STORY_ZONE
                    or not await is_free_leader_questing(client)
                    or not await self._outback_story_missing_target(client)
                    or await self._outback_story_stage(client) != phase):
                return True
            state = dict(phase=phase, holding=True, dialogue_seen=False,
                         stage_ended=False, stable=None, result='started', warned=False)
            client.quest_outback_story = state
            client.outback_story_pending = True
            logger.info('{} Wallaru Outback 检测到无任务指引特殊主线，前往剧情触发点。Quest ID {}，Goal ID {}，Language Key QuestTitle_17D895。',
                        client.title, phase[0], phase[1])
            async with asyncio.timeout(150):
                for attempt in range(2):
                    if await self._outback_story_settled(client, state):
                        return True
                    if (state['dialogue_seen'] or state['stage_ended']
                            or await client.zone_name() != self.OUTBACK_STORY_ZONE):
                        break
                    if (not client.questing_status or not await is_free_leader_questing(client)
                            or await self._outback_story_stage(client) != phase):
                        break
                    if not await self._outback_story_missing_target(client):
                        state.update(holding=False, result='navigation-restored')
                        client.outback_story_pending = False
                        return True
                    if attempt and calc_Distance(await client.body.position(), self.OUTBACK_STORY_POSITION) <= 100:
                        break  # Already at the trigger: do not keep re-teleporting.
                    async with automation_owner(client, 'outback-story-tp'):
                        await client.teleport(self.OUTBACK_STORY_POSITION)
                    if calc_Distance(await client.body.position(), self.OUTBACK_STORY_POSITION) <= 100:
                        logger.info('{} 已到达 Outback 剧情触发点，等待对话。', client.title)
                    else:
                        logger.debug('{} Outback 传送后位置尚未确认，等待剧情或有限重试。', client.title)
                    deadline = time.monotonic() + 10.0
                    while time.monotonic() < deadline and client.questing_status:
                        if await self._outback_story_settled(client, state):
                            return True
                        if state['dialogue_seen'] or state['stage_ended']:
                            break
                        await asyncio.sleep(.2)
                    if state['dialogue_seen'] or state['stage_ended']:
                        break
                deadline = time.monotonic() + (120 if state['dialogue_seen'] else 15)
                while time.monotonic() < deadline and client.questing_status:
                    if await self._outback_story_settled(client, state):
                        return True
                    await asyncio.sleep(.2)
                raise TimeoutError('剧情触发或任务更新未确认')
        except asyncio.CancelledError:
            if isinstance(state, dict):
                state['result'] = 'cancelled'
            raise
        except Exception as exc:
            if isinstance(state, dict) and not state['warned']:
                state.update(warned=True, result='waiting')
                logger.warning('{} Outback 特殊主线剧情未触发或更新未确认，等待后续恢复：{}', client.title, exc)
        finally:
            release_quest_recovery(client, owner)
            if not client.questing_status:
                client.outback_story_pending = False
        return True

    async def _final_act_stage(self, client):
        if (await client.zone_name() != self.FINAL_ACT_ZONE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return None
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or type(snapshot[1]) is not int:
            return None
        objective, location = split_quest_location(snapshot[2])
        objective = re.sub(r'\s+', '', objective).casefold()
        location = re.sub(r'\s+', '', location).casefold()
        if (objective not in ('寻找流氓剧院', 'findroguetheater')
                or location != 'marketplaceofideas'):
            return None
        identity = await self._mainline_identity(client)
        # The live user log supplies this key and QuestID; do not infer IDs
        # from the language suffix or apply the point to another quest stage.
        if (identity is None or identity[0] != snapshot[0]
                or identity[1] != 'QuestTitle_00002057'):
            return None
        return snapshot

    async def _scholomance_lab_stage(self, client):
        if (await client.zone_name() != self.SCHOLOMANCE_LAB_ZONE
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return None
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or type(snapshot[0]) is not int or type(snapshot[1]) is not int:
            return None
        objective, _ = split_quest_location(snapshot[2])
        # Exact supplied completed-stage text. No unsupplied ID, English alias
        # or prior-stage location is invented from the subsequent quest HUD.
        if re.sub(r'\s+', '', objective).rstrip('.。') != '前往米兰达的实验室':
            return None
        return snapshot

    async def _recover_final_act(self, client, progress, *, scholomance=False):
        zone = self.SCHOLOMANCE_LAB_ZONE if scholomance else self.FINAL_ACT_ZONE
        point = self.SCHOLOMANCE_LAB_DIALOGUE if scholomance else self.FINAL_ACT_DIALOGUE
        owner = 'darkmoor_cantrip' if scholomance else 'final_act'
        failed_attr = '_xuanshu_scholomance_lab_failed' if scholomance else '_xuanshu_final_act_failed'
        stage = self._scholomance_lab_stage if scholomance else self._final_act_stage
        label = 'Scholomance 前往米兰达的实验室' if scholomance else '最终一幕寻找流氓剧院'
        async def outcome():
            if (not client.questing_status or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None) != owner
                    or getattr(client, 'quest_party_probe_pending', False)
                    or getattr(client, 'quest_party_battle_rescue_active', False)
                    or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                    or getattr(client, 'post_combat_movement_active', False)
                    or getattr(client, 'mainline_chain_retry_active', False)
                    or await client.is_loading() or await client.in_battle()
                    or await client.zone_name() != zone):
                raise RuntimeError('任务停止、区域变化或进入其他忙碌状态')
            # Hand actual dialogue back immediately; never send X/END or
            # run a second dialogue worker inside this recovery lock.
            if (await is_visible_by_path(client, advance_dialog_path)
                    or await client.is_in_dialog() or await read_dialogue_text(client)):
                client.quest_dialogue_settle = {'snapshot': None, 'since': None}
                return True
            current = await self._dungeon_quest_snapshot(client)
            if current is None:
                raise RuntimeError('任务进度暂不可读')
            if current != progress:
                return True
            if (await stage(client) != progress
                    or not await is_free_leader_questing(client)
                    or await is_spiral_door_open(client)
                    or any([await is_visible_by_path(client, path) for path in (
                        npc_range_path, exit_dungeon_path, dungeon_warning_path, decline_quest_path,
                        cancel_multiple_quest_menu_path, missing_area_path, all_quests_sort_button_path)])
                    or not client.questing_status or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None) != owner
                    or getattr(client, 'quest_party_probe_pending', False)
                    or getattr(client, 'quest_party_battle_rescue_active', False)
                    or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                    or getattr(client, 'post_combat_movement_active', False)
                    or getattr(client, 'mainline_chain_retry_active', False)
                    or await client.is_loading() or await client.in_battle()
                    or await client.zone_name() != zone):
                raise RuntimeError('任务阶段变化或进入交互/加载')
            return False

        async with automation_owner(client, 'scholomance-lab-dialogue-trigger' if scholomance else 'final-act-dialogue-trigger'):
            async with asyncio.timeout(25):
                for attempt in range(2):
                    if await outcome():
                        break
                    logger.info('自动任务：{}连续 TP 无进展，前往对话触发点（{}/2）。', label, attempt + 1)
                    if scholomance:
                        getattr(client, failed_attr)['attempts'] += 1
                    await client.teleport(point)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if await outcome():
                            break
                        await asyncio.sleep(.2)
                    else:
                        continue
                    break
                else:
                    raise TimeoutError('两次特殊 TP 后仍未确认对话或任务推进')
        if not scholomance or await self._dungeon_quest_snapshot(client) != progress:
            setattr(client, failed_attr, None)
        self._krok_exit_watch.pop(id(client), None)
        logger.info('自动任务：{}已出现对话或任务推进，交回原有任务/对话流程。', label)

    async def _overgrown_estate_stage(self, client):
        if (await client.zone_name() != self.OVERGROWN_ESTATE_ZONE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return None
        snapshot = await self._dungeon_quest_snapshot(client)
        if (snapshot is None or snapshot[0] != self.OVERGROWN_ESTATE_QUEST_ID
                or type(snapshot[1]) is not int):
            return None
        identity = await self._mainline_identity(client)
        if (identity is None or identity[0] != snapshot[0]
                or identity[1] != 'QuestTitle_00002170'):
            return None
        return snapshot

    @staticmethod
    def overgrown_estate_paused(client, *, manual_only=False):
        state = getattr(client, '_xuanshu_overgrown_estate', None)
        return (isinstance(state, dict)
                and getattr(state.get('owner_client'), 'questing_status', False) is True
                and (state.get('phase') == 'wait_player' if manual_only else
                     state.get('phase') not in ('completed', 'aborted')))

    async def _estate_clue_candidates(self, client, entities, done):
        candidates = {}
        for entity in entities:
            try:
                # The developer entity list displays the instance name; the
                # template name is also available in the existing collector.
                internal = await entity.object_name() or ''
                template = await entity.object_template()
                if not internal.startswith(self.OVERGROWN_ESTATE_CLUE_PREFIX):
                    internal = await template.object_name() if template else ''
                if not internal or not internal.startswith(self.OVERGROWN_ESTATE_CLUE_PREFIX):
                    continue
                xyz = await entity.location()
                if not all(math.isfinite(v) for v in (xyz.x, xyz.y, xyz.z)):
                    continue
                code = await template.display_name() if template else ''
                display = ''
                if code:
                    try:
                        display = await client.cache_handler.get_langcode_name(code) or ''
                    except (ValueError, KeyError):
                        pass
                candidate = Candidate(entity, xyz, code, display, internal, None, 100)
                if candidate.key not in done:
                    candidates[candidate.key] = candidate
            except Exception as exc:
                logger.trace('庄园线索实体暂不可读：{}', exc)
        return list(candidates.values())

    async def _maybe_handle_overgrown_estate(self, client):
        state = getattr(client, '_xuanshu_overgrown_estate', None)
        if not self.overgrown_estate_paused(client):
            return False
        if state['owner_client'] is not client:
            return True  # A peer must not run a second clue search or quest TP.
        participants = state['participants']
        if state['phase'] == 'wait_player':
            # Completion is simultaneous outside-zone evidence, never just the
            # quester's departure or a loading/empty/unreadable zone.
            try:
                if len({id(member) for member in participants}) < 2:
                    return True
                for _ in range(2):
                    for member in participants:
                        if await member.is_loading():
                            return True
                        zone = await member.zone_name()
                        if not zone or zone == self.OVERGROWN_ESTATE_ZONE:
                            return True
            except Exception:
                return True
            state['phase'] = 'completed'
            logger.info('Were-Dunnit：已确认两名角色均离开庄园地牢，恢复自动任务。')
            return True
        if await client.is_loading():
            return True
        if await client.zone_name() != self.OVERGROWN_ESTATE_ZONE:
            state['phase'] = 'aborted'
            logger.warning('Were-Dunnit：专用步骤结束前离开庄园，未判定线索或副本完成。')
            return True
        if state['phase'] == 'failed':
            return True

        async def dialogue_open():
            return bool(await client.is_in_dialog()
                        or await is_visible_by_path(client, advance_dialog_path)
                        or plain_text(await read_dialogue_text(client)))

        if state['phase'] in ('dialogue_wait', 'clue_settle'):
            if await dialogue_open():
                if state['phase'] == 'dialogue_wait':
                    state['dialogue_seen'] = True
                await self._quest_dialogue_blocks_movement(client)
                return True
            if await self._quest_dialogue_blocks_movement(client):
                return True

        owner = 'overgrown_estate'
        if not claim_quest_recovery(client, owner):
            return True
        async def ready():
            if (self._darkmoor_clue_input_blocked(client, owner)
                    or await client.zone_name() != self.OVERGROWN_ESTATE_ZONE
                    or await client.is_loading() or await client.in_battle()
                    or not await is_free_leader_questing(client) or await client.is_in_dialog()
                    or getattr(client, 'quest_party_status_session', None) is not None
                    or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                    or await is_spiral_door_open(client)
                    or any([await is_visible_by_path(client, path) for path in (
                        advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                        exit_dungeon_path, dungeon_warning_path, missing_area_path,
                        all_quests_sort_button_path, quest_buttons_parent_path)])):
                return False
            return (not self._darkmoor_clue_input_blocked(client, owner)
                    and await client.zone_name() == self.OVERGROWN_ESTATE_ZONE
                    and not await client.is_loading() and not await client.in_battle()
                    and not self._darkmoor_clue_input_blocked(client, owner)
                    and not getattr(client, 'entity_detect_combat_status', False))

        def fail(message):
            state['phase'] = 'failed'
            logger.warning('Were-Dunnit：{}；暂停本次专用步骤，不重复 TP/X。', message)

        try:
            async with automation_owner(client, 'overgrown-estate-step'):
                if not await ready():
                    return True
                now = time.monotonic()
                phase = state['phase']
                if phase == 'dialogue_move':
                    current = await self._overgrown_estate_stage(client)
                    if current is None:
                        return True  # An unreadable goal is not evidence of a changed task.
                    if current != state['progress']:
                        state['phase'] = 'aborted'
                        return True
                    if not await ready():
                        return True
                    state.update(phase='dialogue_wait', started_at=now, dialogue_seen=False, x_sent=False)
                    logger.info('Were-Dunnit：连续 TP 无进展，前往对话点 {}。', self.OVERGROWN_ESTATE_DIALOGUE)
                    await asyncio.wait_for(client.teleport(self.OVERGROWN_ESTATE_DIALOGUE), timeout=5)
                    return True
                if phase == 'dialogue_wait':
                    if state['dialogue_seen']:
                        state.update(phase='search_init', started_at=now)
                    elif (calc_Distance(await client.body.position(), self.OVERGROWN_ESTATE_DIALOGUE) < 150
                            and not state['x_sent'] and await is_visible_by_path(client, npc_range_path)
                            and interaction_kind(await self.read_popup(client)) == 'talk'):
                        if not await ready():
                            return True
                        state['x_sent'] = True
                        await client.send_key(Keycode.X, .1)
                    elif now - state['started_at'] >= 15:
                        fail('未确认指定点实际对话，不能开始线索搜索')
                    return True
                if phase == 'search_init':
                    route = await self.get_zone_chunks(client)
                    origin = await client.body.position()
                    route.sort(key=lambda p: calc_Distance(origin, p))
                    if not route or not all(all(math.isfinite(v) for v in (p.x, p.y, p.z)) for p in route):
                        fail('地图分区不可读，不能宣称完成全图线索搜索')
                        return True
                    state.update(route=route, cursor=0, done=set(), pending={}, phase='scan')
                    logger.info('Were-Dunnit：开始全图搜索 {} 前缀线索，地图分区数 {}。',
                                self.OVERGROWN_ESTATE_CLUE_PREFIX, len(route))
                    return True
                if phase == 'region_move':
                    point = state['route'][state['cursor']]
                    if not await ready():
                        return True
                    # Same region-loading height convention as CollectSearch.run.
                    point = XYZ(point.x, point.y, point.z - 550)
                    state.update(phase='region_landing', region_point=point, started_at=now)
                    await asyncio.wait_for(client.teleport(point), timeout=5)
                    return True
                if phase == 'region_landing':
                    if calc_Distance(await client.body.position(), state['region_point']) < 150:
                        state.update(phase='scan', cursor=state['cursor'] + 1)
                    elif now - state['started_at'] >= 5:
                        fail('未确认搜索分区落地，不能跳过该区域')
                    return True
                if phase == 'scan':
                    search = CollectSearch(self, client)
                    search.zone = self.OVERGROWN_ESTATE_ZONE
                    entities = await search.loaded_entities()
                    candidates = await self._estate_clue_candidates(client, entities, state['done'])
                    if not await ready():
                        return True
                    for candidate in candidates:
                        state['pending'][candidate.key] = candidate
                    if state['pending']:
                        position = await client.body.position()
                        candidate = min(state['pending'].values(), key=lambda c: calc_Distance(position, c.xyz))
                        state.update(candidate=candidate, phase='clue_move')
                    elif state['cursor'] < len(state['route']):
                        state['phase'] = 'region_move'
                    elif state['done']:
                        state['phase'] = 'return_move'
                    else:
                        fail('全图扫描未找到可交互线索，不能交付已收集结果')
                    return True
                if phase == 'clue_move':
                    candidate = state['candidate']
                    if not await ready():
                        return True
                    state.update(phase='clue_interact', started_at=now)
                    # Visiting another clue can unload this pointer. Use its
                    # verified position, then reacquire the live entity before X.
                    await asyncio.wait_for(client.teleport(candidate.xyz), timeout=5)
                    return True
                if phase == 'clue_interact':
                    candidate = state['candidate']
                    search = CollectSearch(self, client)
                    search.zone = self.OVERGROWN_ESTATE_ZONE
                    live = await self._estate_clue_candidates(client, await search.loaded_entities(), state['done'])
                    if not await ready():
                        return True
                    for found in live:
                        state['pending'][found.key] = found
                    candidate = next((found for found in live if found.key == candidate.key), None)
                    if candidate is None:
                        if time.monotonic() - state['started_at'] >= 5:
                            fail('未重新确认所到位置的前缀线索实体，不按 X')
                        return True
                    if (calc_Distance(await client.body.position(), candidate.xyz) < 150
                            and calc_Distance(await candidate.entity.location(), candidate.xyz) < 100
                            and await is_visible_by_path(client, npc_range_path)):
                        title = plain_text(await get_popup_title(client))
                        prompt = await self.read_popup(client)
                        if candidate.display and title.casefold() != plain_text(candidate.display).casefold():
                            fail(f'{candidate.internal} 附近交互标题不匹配，不按 X')
                            return True
                        # A verified clue can expose an investigation/dialogue
                        # prompt rather than a generic Collect label. Its actual
                        # display title still must match; never enter a sigil.
                        kind = interaction_kind(prompt)
                        if (not title or not plain_text(prompt) or kind in ('enter', 'ride', 'teleport')
                                or not candidate.display and kind != 'collect'):
                            if time.monotonic() - state['started_at'] >= 5:
                                fail(f'{candidate.internal} 线索交互提示未确认')
                            return True
                        if (calc_Distance(await candidate.entity.location(), candidate.xyz) >= 100
                                or calc_Distance(await client.body.position(), candidate.xyz) >= 150):
                            fail(f'{candidate.internal} 输入前实体/角色位置已改变')
                            return True
                        if not await ready():
                            return True
                        # Record before X; worker cancellation cannot replay an uncertain pickup.
                        state['done'].add(candidate.key)
                        state['pending'].pop(candidate.key, None)
                        state.update(phase='clue_settle', started_at=now)
                        await client.send_key(Keycode.X, .1)
                        logger.info('Were-Dunnit：已到达 {} 并按 X 交互（已交互 {} 个）。',
                                    candidate.internal, len(state['done']))
                    elif now - state['started_at'] >= 5:
                        fail(f'{candidate.internal} 落地/拾取提示未确认')
                    return True
                if phase == 'clue_settle':
                    if now - state['started_at'] >= 1:
                        state['phase'] = 'scan'
                    return True
                if phase == 'return_move':
                    state.update(phase='return_landing', started_at=now)
                    await asyncio.wait_for(client.teleport(self.OVERGROWN_ESTATE_WAIT), timeout=5)
                    return True
                if phase == 'return_landing':
                    if calc_Distance(await client.body.position(), self.OVERGROWN_ESTATE_WAIT) < 150:
                        state['phase'] = 'wait_player'
                        logger.info('Were-Dunnit：已回到 {}，等待玩家完成副本；两名角色都离开该区域才恢复。\n'
                                    'Tranett = Silver Necklace\n特拉内特 = 银项链\n'
                                    'Tawni = Lock of Hair\nTawni = 一绺头发\n'
                                    'Ignacio = Red Cloth\nIgnacio = 红色布料\n'
                                    'Dimiti = Strange Footprints\nDimiti = 奇怪的脚印', self.OVERGROWN_ESTATE_WAIT)
                    elif now - state['started_at'] >= 5:
                        fail('未确认回到指定等待点')
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            fail(str(exc))
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

    @staticmethod
    def _darkmoor_cantrip_stage(text):
        objective, location = split_quest_location(text)
        if location.casefold() != 'graveholm':
            return None
        objective = re.sub(r'\s+', '', objective).casefold().rstrip('.。')
        if objective in ('castcalescenttracker在断棍上', 'castcalescenttrackeronbrokenstick',
                         'followcalescenttracker前往断枝窝'):
            return 'tracker'
        if objective in ('cantrip仪式物品', 'cantripritualobjects', 'cantripritualobject'):
            return 'ritual'
        return None

    async def _darkmoor_cantrip_texts(self, client):
        nodes = await self._visible_window_nodes(client.root_window, [])
        return [await self._window_text(window) for window, _ in nodes
                if await window.maybe_read_type_name() in ('ControlText', 'ControlList')]

    @staticmethod
    def _darkmoor_throne_tracker_stage(text):
        objective, location = split_quest_location(text)
        if location.casefold() != 'graveholm':
            return None
        objective = re.sub(r'\s+', '', objective).casefold().rstrip('.。')
        if objective == 'castcalescenttracker找到灰魔':
            return 'throne_tracker'
        if objective == 'followcalescenttracker找到灰魔':
            return 'throne_follow'
        return None

    @staticmethod
    def _darkmoor_shadows_task_matches(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'black lagoon'
                and re.sub(r'\s+', '', objective).casefold().rstrip('.。')
                == 'castmagictouchtodispelfalseshadows')

    async def _darkmoor_cantrip_targeting(self, client):
        return any(re.sub(r'\s+', '', text).casefold() in (
            '单击目标来释放场外魔咒', 'clickonthetargetofthecantrip')
            for text in await self._darkmoor_cantrip_texts(client))

    async def _darkmoor_cantrip_point(self, client, x, y):
        # User-marked 1152x864 screenshots, not a desktop coordinate. Spell
        # selection is independently confirmed by its live name before clicking.
        camera = await get_camera_state(client)
        if camera is None or camera['client_h'] <= 0:
            raise RuntimeError('无法读取游戏画面尺寸')
        width, height = camera['client_w'], camera['client_h']
        if abs(width / height - 4 / 3) > .03:
            raise RuntimeError('当前画面比例不同于标记截图，停止固定位置点击')
        return round(x * width / 1152), round(y * height / 864)

    async def _darkmoor_cantrip_card(self, client, stage):
        # Verified installed Root.wad Locale/en-US/Cantrips.lang identities.
        tracker = stage in ('tracker', 'throne_tracker')
        code = 'Cantrips_00000209' if tracker else 'Cantrips_00000026'
        titles = ('calescent tracker',) if tracker else ('magic touch', '魔法之触')
        matches = []
        for window in await client.root_window.get_windows_with_type('SpellCheckBox'):
            if not await window.is_visible() or not all([
                    await parent.is_visible() for parent in await window.get_parents()]):
                continue
            spell = await window.maybe_graphical_spell(check_type=True)
            template = await spell.spell_template() if spell else None
            if template is None:
                continue
            if await template.display_name() == code:
                if await window.maybe_spell_grayed(check_type=True):
                    raise RuntimeError('指定 Cantrip 当前不可施放，不点击灰色卡片')
                matches.append(window)
        if len(matches) > 1:
            raise RuntimeError('出现多个同名 Cantrip，停止选择')
        return (matches[0] if matches else None), titles

    @staticmethod
    def _darkmoor_clue_task_matches(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'graveholm'
                and re.sub(r'\s+', '', objective).casefold().rstrip('.。')
                in ('调查线索', 'investigateclues', 'investigateclue'))

    @staticmethod
    def _darkmoor_clue_input_blocked(client, owner):
        return (not (getattr(client, 'questing_status', False) is True
                     or getattr(client, 'auto_dialogue_running', False) is True)
                or getattr(client, 'quest_recovery_owner', None) != owner
                or any(getattr(client, attr, False) is True for attr in (
                    'refilling_potions', 'quest_party_probe_pending',
                    'quest_party_battle_rescue_active', 'quest_party_quest_worker_restart_requested',
                    'post_combat_movement_active', 'mainline_chain_retry_active'))
                or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                and client.quest_dungeon_recovery.get('active'))

    async def _handle_darkmoor_clue_confirmation(self, client):
        """Handle only the supplied clue choice, never the ordinary offer layout."""
        if await client.zone_name() != self.DARKMOOR_CLUE_ZONE:
            return False
        async with automation_owner(client, 'darkmoor-clue-confirmation'):
            dialog = await get_window_from_path(client.root_window, advance_dialog_path[:-1])
            if not dialog or not await dialog.is_visible():
                return False
            nodes = await self._visible_window_nodes(dialog, advance_dialog_path[:-1])
            texts = [(window, plain_text(await self._window_text(window))) for window, _ in nodes]
            body = plain_text(await read_dialogue_text(client))
            if (not any(text == '线索' for _, text in texts)
                    or '阿克托指挥官' not in body or not any(char in body for char in ('吗', '?', '？'))):
                return False
            # Even if identity/buttons are unreadable, keep this known choice
            # out of generic invitation clicking and ESC/SPACE handling.
            snapshot = await self._dungeon_quest_snapshot(client)
            if (snapshot is None or snapshot[0] != self.DARKMOOR_CANTRIP_QUEST_ID
                    or not self._darkmoor_clue_task_matches(snapshot[2])):
                return True
            buttons = [window for window, text in texts if text.casefold() in ('是', 'yes')
                       and await window.name() in (advance_dialog_path[-1], decline_quest_path[-1])]
            owner = getattr(client, 'quest_recovery_owner', None)
            if (len(buttons) != 1 or owner not in (None, 'npc_dialogue', 'darkmoor_cantrip')
                    or getattr(client, 'quest_party_status_session', None) is not None
                    or self._darkmoor_clue_input_blocked(client, owner)):
                return True
            button = buttons[0]
            state = getattr(client, '_xuanshu_darkmoor_clues', None)
            if isinstance(state, dict) and state.get('phase') in ('npc', 'npc_dialogue', 'failed', 'cancelled', 'completed'):
                return True  # No repeated YES while its UI response is pending.
            if (not await button.is_visible() or await button.is_control_grayed()
                    or plain_text(await self._window_text(button)).casefold() not in ('是', 'yes')
                    or plain_text(await read_dialogue_text(client)) != body
                    or await self._dungeon_quest_snapshot(client) != snapshot
                    or await client.zone_name() != self.DARKMOOR_CLUE_ZONE
                    or await client.is_loading() or await client.in_battle()
                    or self._darkmoor_clue_input_blocked(client, owner)):
                return True
            if not isinstance(state, dict):
                # An already-open manually triggered clue is resumable only
                # when its supplied world position independently identifies it.
                position = await client.body.position()
                indices = [index for index, point in enumerate(self.DARKMOOR_CLUE_POSITIONS)
                           if calc_Distance(position, point) < 150]
                if len(indices) != 1:
                    return True
                state = {'snapshot': snapshot, 'index': indices[0], 'phase': 'confirm', 'active': False}
                client._xuanshu_darkmoor_clues = state
                if (self._darkmoor_clue_input_blocked(client, owner)
                        or await client.is_loading() or await client.in_battle()
                        or await client.zone_name() != self.DARKMOOR_CLUE_ZONE
                        or plain_text(await read_dialogue_text(client)) != body):
                    return True
            if (not await button.is_visible() or await button.is_control_grayed()
                    or await self._dungeon_quest_snapshot(client) != snapshot
                    or plain_text(await self._window_text(button)).casefold() not in ('是', 'yes')
                    or self._darkmoor_clue_input_blocked(client, owner)
                    or await client.zone_name() != self.DARKMOOR_CLUE_ZONE
                    or await client.is_loading() or await client.in_battle()):
                return True
            state['phase'] = 'npc'
            state['confirmed_at'] = time.monotonic()
            await self._click_ui_window(client, button)
            logger.info('断枝窝线索 {}/2：已按实际按钮文字点击“是”。', state['index'] + 1)
            return True

    async def _maybe_handle_darkmoor_clues(self, client):
        owner = 'darkmoor_cantrip'  # Reuse existing task/follower/watchdog ownership.
        if await client.zone_name() != self.DARKMOOR_CLUE_ZONE:
            return False
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or snapshot[0] != self.DARKMOOR_CANTRIP_QUEST_ID:
            return False
        state = getattr(client, '_xuanshu_darkmoor_clues', None)
        if isinstance(state, dict) and state['phase'] in ('failed', 'cancelled', 'completed'):
            return state['phase'] != 'completed' and snapshot == state['snapshot']
        if not isinstance(state, dict) and not self._darkmoor_clue_task_matches(snapshot[2]):
            return False
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or self._darkmoor_clue_input_blocked(client, None)
                or await client.is_loading() or await client.in_battle()):
            return True
        dialogue = (await client.is_in_dialog() or await is_visible_by_path(client, advance_dialog_path)
                    or plain_text(await read_dialogue_text(client)))
        if isinstance(state, dict) and state['phase'] == 'npc_dialogue':
            if dialogue:
                state['dialogue_seen'] = True
                return False  # Ordinary dialogue worker advances this NPC.
            if not state.get('dialogue_seen'):
                if time.monotonic() - state['started_at'] > 8:
                    state['phase'] = 'failed'
                    logger.warning('断枝窝 NPC 对话未确认，停止重复交互。')
                return True
            if isinstance(getattr(client, 'quest_dialogue_settle', None), dict):
                return False
            if not self._darkmoor_clue_task_matches(snapshot[2]) or state['index'] == 1:
                state['phase'] = 'completed'
                return False
            state['index'] = 1
            state['phase'] = 'clue'
        if dialogue and (not isinstance(state, dict) or state['phase'] != 'confirm'):
            if await self._handle_darkmoor_clue_confirmation(client):
                return True
            return False
        if not dialogue and not await is_free_leader_questing(client):
            return True
        if not claim_quest_recovery(client, owner):
            return True
        try:
            async with automation_owner(client, 'darkmoor-clue-route'):
                async with asyncio.timeout(25):
                    if not isinstance(state, dict):
                        state = {'snapshot': snapshot, 'index': 0, 'phase': 'clue', 'active': True}
                        client._xuanshu_darkmoor_clues = state
                    state['active'] = True

                    async def ready(*, moving=False):
                        current = await self._dungeon_quest_snapshot(client)
                        if (current is None or current[0] != self.DARKMOOR_CANTRIP_QUEST_ID
                                or state['phase'] in ('clue', 'confirm')
                                and not self._darkmoor_clue_task_matches(current[2])):
                            raise RuntimeError('线索任务已改变或不可读')
                        if (await client.zone_name() != self.DARKMOOR_CLUE_ZONE
                                or await client.is_loading() or await client.in_battle()
                                or not client.questing_status or self._darkmoor_clue_input_blocked(client, owner)):
                            raise RuntimeError('线索输入被任务停止、切区、战斗或其他流程接管')
                        if moving and (not await is_free_leader_questing(client)
                                       or await is_spiral_door_open(client)
                                       or any([await is_visible_by_path(client, path) for path in (
                                           advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                                           exit_dungeon_path, dungeon_warning_path, missing_area_path,
                                           all_quests_sort_button_path)])):
                            raise RuntimeError('其他界面阻止线索 TP/X')
                        if (self._darkmoor_clue_input_blocked(client, owner) or not client.questing_status
                                or await client.zone_name() != self.DARKMOOR_CLUE_ZONE
                                or await client.is_loading() or await client.in_battle()):
                            raise RuntimeError('输入前状态已改变')

                    async def interact(point, title):
                        await ready(moving=True)
                        await client.teleport(point)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            await ready(moving=True)
                            if (await is_visible_by_path(client, npc_range_path)
                                    and plain_text(await get_popup_title(client)) == title
                                    and calc_Distance(await client.body.position(), point) < 150
                                    and plain_text(await get_popup_title(client)) == title):
                                await ready(moving=True)
                                # Mark before X, so cancellation never replays an uncertain interaction.
                                state['phase'] = 'confirm' if title == '线索' else 'npc_dialogue'
                                state['started_at'] = time.monotonic()
                                state['dialogue_seen'] = False
                                await client.send_key(Keycode.X, .1)
                                return
                            await asyncio.sleep(.2)
                        raise TimeoutError('指定对象交互提示未出现，不盲按 X')

                    if state['phase'] == 'clue':
                        await interact(self.DARKMOOR_CLUE_POSITIONS[state['index']], '线索')
                    if state['phase'] == 'confirm':
                        deadline = time.monotonic() + 8
                        while state['phase'] == 'confirm' and time.monotonic() < deadline:
                            await ready()
                            await self._handle_darkmoor_clue_confirmation(client)
                            await asyncio.sleep(.2)
                        if state['phase'] == 'confirm':
                            raise TimeoutError('未确认线索的“是”按钮，不继续 NPC TP')
                    if state['phase'] == 'npc':
                        # YES must actually close before any world movement.
                        deadline = time.monotonic() + 5
                        while (await client.is_in_dialog() or await is_visible_by_path(client, advance_dialog_path)
                               or plain_text(await read_dialogue_text(client))):
                            await ready()
                            if time.monotonic() >= deadline:
                                raise TimeoutError('线索确认框未关闭，不继续 TP')
                            await asyncio.sleep(.2)
                        await interact(self.DARKMOOR_CLUE_NPC_POSITION, '阿克托指挥官')
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            await ready()
                            if (await client.is_in_dialog() or await is_visible_by_path(client, advance_dialog_path)
                                    or plain_text(await read_dialogue_text(client))):
                                state['dialogue_seen'] = True
                                client.quest_dialogue_settle = {'snapshot': None, 'since': None}
                                return True
                            await asyncio.sleep(.2)
                        raise TimeoutError('NPC 对话未触发，不调查下一条线索')
        except asyncio.CancelledError:
            if isinstance(state, dict):
                state['phase'] = 'cancelled'
            raise
        except Exception as exc:
            if isinstance(state, dict):
                state['phase'] = 'failed'
            logger.warning('断枝窝线索流程停止，避免重复 TP/选择：{}', exc)
        finally:
            if isinstance(state, dict):
                state['active'] = False
            release_quest_recovery(client, owner)
        return True

    async def _maybe_handle_darkmoor_cantrips(self, client: Client) -> bool:
        if await self._maybe_handle_mayor_photo(client):
            return True
        if await self._maybe_exit_easton_day_guard(client):
            return True
        if await self._maybe_handle_darkmoor_clues(client):
            return True
        owner = 'darkmoor_cantrip'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        cantrip_zone = await client.zone_name()
        shadows = cantrip_zone == self.DARKMOOR_SHADOWS_ZONE
        throne = cantrip_zone == self.DARKMOOR_THRONE_ZONE
        cantrip_quest_id = self.DARKMOOR_SHADOWS_QUEST_ID if shadows else self.DARKMOOR_CANTRIP_QUEST_ID
        if (not getattr(client, 'questing_status', False)
                or cantrip_zone not in (self.DARKMOOR_CANTRIP_ZONE, self.DARKMOOR_SHADOWS_ZONE, self.DARKMOOR_THRONE_ZONE)
                or (not shadows and not throne and getattr(client, 'quest_party_status_session', None) is not None)
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        if throne and snapshot is not None:
            # No Quest ID was supplied for this scene: latch the live identity
            # only after the exact region/HUD stage has identified it below.
            cantrip_quest_id = snapshot[0]
        if snapshot is None or snapshot[0] != cantrip_quest_id:
            return False
        state = getattr(client, '_xuanshu_darkmoor_cantrip_stage', None)
        if (isinstance(state, dict) and (state['snapshot'][0] != cantrip_quest_id
                or state.get('zone', self.DARKMOOR_SHADOWS_ZONE if state.get('phase', '').startswith('shadows')
                             else self.DARKMOOR_CANTRIP_ZONE) != cantrip_zone)):
            state = None  # Never carry a sequence into a different scene.
        stage = (self._darkmoor_throne_tracker_stage(snapshot[2]) if throne else
                 ('shadows' if self._darkmoor_shadows_task_matches(snapshot[2]) else None) if shadows else
                 self._darkmoor_cantrip_stage(snapshot[2]))
        if (shadows and isinstance(state, dict) and state['snapshot'][0] == cantrip_quest_id
                and state.get('phase') in ('shadows_dialogue', 'shadows_exit', 'shadows_return', 'shadows_return_dialogue')):
            stage = 'shadows'  # Continue this sequence after the cast's HUD advances.
        if (shadows and isinstance(state, dict) and state.get('phase') in ('failed', 'cancelled', 'completed')
                and state['snapshot'] == snapshot):
            return state['phase'] != 'completed'
        if stage is None:
            if throne and isinstance(state, dict):
                state.update(snapshot=snapshot, phase='completed')
            return False
        if isinstance(state, dict) and state.get('phase') in ('failed', 'cancelled', 'completed'):
            if state['snapshot'] == snapshot:
                return state['phase'] != 'completed'
            state = None  # A genuinely different task stage may be handled.
        if (await client.is_in_dialog() or await is_visible_by_path(client, advance_dialog_path)
                or plain_text(await read_dialogue_text(client))):
            if isinstance(state, dict) and state.get('phase') in ('tracker_dialogue', 'shadows_dialogue', 'shadows_return_dialogue', 'throne_dialogue', 'throne_cast_dialogue'):
                state['dialogue_seen'] = True
            return False  # The ordinary dialogue worker owns dialogue buttons.
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                or getattr(client, 'post_combat_movement_active', False)
                or getattr(client, 'mainline_chain_retry_active', False)
                or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                and client.quest_dungeon_recovery.get('active')
                or not await is_free_leader_questing(client)):
            return True
        if (not isinstance(state, dict) and stage == 'tracker'
                and calc_Distance(await client.body.position(), self.DARKMOOR_TRACKER_POSITION) > 150):
            return False  # Let ordinary task TP reach the supplied screenshot site.
        cast_position = None
        if throne and stage == 'throne_tracker' and not isinstance(state, dict):
            # Use this task's live target, never an old tracker casting XYZ.
            try:
                target = await client.quest_position.position()
                cast_position = await client.body.position()
                if (not all(math.isfinite(v) for v in (target.x, target.y, target.z,
                                                     cast_position.x, cast_position.y, cast_position.z))
                        or calc_Distance(target, XYZ(0, 0, 0)) <= 1
                        or calc_Distance(cast_position, target) > 150):
                    return False
            except Exception:
                return False
        if isinstance(state, dict) and state['phase'] in ('tracker_dialogue', 'ritual_dialogue', 'shadows_dialogue', 'shadows_return_dialogue', 'throne_dialogue', 'throne_cast_dialogue'):
            if isinstance(getattr(client, 'quest_dialogue_settle', None), dict):
                return False  # Let the normal quest/dialogue stability check finish.
            if state['phase'] == 'tracker_dialogue' and not state.get('dialogue_seen') and stage != 'ritual':
                return True  # Never skip the requested first dialogue.
            if shadows and not state.get('dialogue_seen'):
                return True
            if state['phase'] == 'shadows_return_dialogue':
                state.update(snapshot=snapshot, phase='completed')
                return False
            if throne and (state['phase'] == 'throne_dialogue' or stage == 'throne_tracker'):
                if time.monotonic() - state['follow_started_at'] > 8:
                    state.update(snapshot=snapshot, phase='failed')
                    logger.warning('王座厅追踪对话后未确认任务推进，停止重复输入。')
                return True
        if not claim_quest_recovery(client, owner):
            return True
        try:
            async with automation_owner(client, 'darkmoor-cantrip-task'):
                async with asyncio.timeout(35):
                    if not isinstance(state, dict):
                        state = {'snapshot': snapshot, 'zone': cantrip_zone, 'phase': stage, 'active': True,
                                 'dialogue_seen': False, 'position_attempts': 0, 'target_clicks': 0}
                        if throne and stage == 'throne_tracker':
                            state['cast_position'] = cast_position
                        client._xuanshu_darkmoor_cantrip_stage = state
                    else:
                        state['active'] = True

                    async def ready(expected=None):
                        current = await self._dungeon_quest_snapshot(client)
                        if current is None:
                            raise RuntimeError('任务状态不可读，停止 Cantrip 输入')
                        if (current[0] != cantrip_quest_id
                                or expected is not None and current != expected):
                            raise RuntimeError('任务阶段已改变，停止旧阶段输入')
                        if (await client.zone_name() != cantrip_zone
                                or await client.is_loading() or await client.in_battle()
                                or await client.is_in_dialog() or not await is_free_leader_questing(client)
                                or await is_spiral_door_open(client)
                                or any([await is_visible_by_path(client, p) for p in (
                                    all_quests_sort_button_path, exit_dungeon_path, dungeon_warning_path,
                                    decline_quest_path, cancel_multiple_quest_menu_path, missing_area_path)])):
                            raise RuntimeError('区域、战斗、对话或其他界面阻止 Cantrip 输入')
                        if (not client.questing_status or getattr(client, 'refilling_potions', False)
                                or getattr(client, 'quest_recovery_owner', None) != owner
                                or getattr(client, 'quest_party_probe_pending', False)
                                or getattr(client, 'quest_party_battle_rescue_active', False)
                                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                                or getattr(client, 'post_combat_movement_active', False)
                                or getattr(client, 'mainline_chain_retry_active', False)
                                or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                                and client.quest_dungeon_recovery.get('active')):
                            raise RuntimeError('其他流程已接管，停止 Cantrip 输入')
                        return current

                    async def dialogue_started():
                        if not (await client.is_in_dialog() or await is_visible_by_path(client, advance_dialog_path)
                                or plain_text(await read_dialogue_text(client))):
                            return False
                        if (await client.zone_name() != cantrip_zone
                                or await client.is_loading() or await client.in_battle()
                                or not client.questing_status or getattr(client, 'refilling_potions', False)
                                or getattr(client, 'quest_recovery_owner', None) != owner):
                            raise RuntimeError('对话出现时客户端状态已改变')
                        client.quest_dialogue_settle = {'snapshot': None, 'since': None}
                        return True

                    if throne and stage == 'throne_follow':
                        state.update(snapshot=snapshot, phase='throne_follow', follow_started_at=time.monotonic())
                        await ready(snapshot)
                        await client.teleport(self.DARKMOOR_THRONE_FOLLOW_POSITION)
                        deadline = time.monotonic() + 8
                        while True:
                            if await dialogue_started():
                                state.update(phase='throne_dialogue', dialogue_seen=True)
                                return True  # Ordinary dialogue worker owns all buttons.
                            current = await ready()
                            if current != snapshot:
                                if self._darkmoor_throne_tracker_stage(current[2]) is not None:
                                    raise RuntimeError('追踪阶段发生未确认变化，停止重复 TP')
                                state.update(snapshot=current, phase='completed')
                                return True
                            if time.monotonic() >= deadline:
                                raise TimeoutError('王座厅追踪 TP 后未确认对话或任务推进，停止重复 TP')
                            await asyncio.sleep(.2)

                    if shadows and state['phase'] in ('shadows_dialogue', 'shadows_exit', 'shadows_return'):
                        state['snapshot'] = snapshot
                        for phase, position in (('shadows_exit', self.DARKMOOR_SHADOWS_RESET_POSITION),
                                                ('shadows_return', self.DARKMOOR_SHADOWS_POSITION)):
                            if state['phase'] == 'shadows_return' and phase == 'shadows_exit':
                                continue
                            await ready(snapshot)
                            state['phase'] = phase
                            await client.teleport(position)
                            deadline = time.monotonic() + 5
                            while True:
                                await ready(snapshot)
                                if calc_Distance(await client.body.position(), position) <= 20:
                                    break
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('False Shadows 回访 TP 未确认到达')
                                await asyncio.sleep(.2)
                        deadline = time.monotonic() + 8
                        while True:
                            if await dialogue_started():
                                state.update(phase='shadows_return_dialogue', dialogue_seen=True)
                                return True
                            current = await ready(snapshot)
                            if (await is_visible_by_path(client, npc_range_path)
                                    and interaction_kind(await self.read_popup(client)) == 'talk'
                                    and await self.quest_interaction_ready(client, self.DARKMOOR_SHADOWS_POSITION)):
                                await ready(current)
                                if not state.get('revisit_x_sent'):
                                    state['revisit_x_sent'] = True
                                    await client.send_key(Keycode.X, .1)
                            if time.monotonic() >= deadline:
                                raise TimeoutError('False Shadows 回访未出现对话，停止重复 TP/交互')
                            await asyncio.sleep(.2)

                    if shadows and calc_Distance(await client.body.position(), self.DARKMOOR_SHADOWS_POSITION) > 20:
                        await ready(snapshot)
                        state['position_attempts'] += 1
                        await client.teleport(self.DARKMOOR_SHADOWS_POSITION)
                        deadline = time.monotonic() + 5
                        while calc_Distance(await client.body.position(), self.DARKMOOR_SHADOWS_POSITION) > 20:
                            await ready(snapshot)
                            if time.monotonic() >= deadline:
                                raise TimeoutError('False Shadows 施法 TP 未确认到达')
                            await asyncio.sleep(.2)

                    if state['phase'] == 'tracker_dialogue' or stage == 'ritual':
                        state['phase'] = 'ritual_position'
                    if state['phase'] in ('ritual_position', 'ritual_dialogue'):
                        if calc_Distance(await client.body.position(), self.DARKMOOR_RITUAL_POSITION) > 100:
                            if state['position_attempts'] >= 2:
                                raise TimeoutError('两次 TP 后未到仪式位置')
                            await ready()
                            state['position_attempts'] += 1
                            await client.teleport(self.DARKMOOR_RITUAL_POSITION)
                            deadline = time.monotonic() + 5
                            while calc_Distance(await client.body.position(), self.DARKMOOR_RITUAL_POSITION) > 100:
                                await ready()
                                if time.monotonic() >= deadline:
                                    raise TimeoutError('仪式 TP 未确认到达')
                                await asyncio.sleep(.2)
                        deadline = time.monotonic() + 8
                        while True:
                            if await dialogue_started():
                                state['phase'] = 'ritual_dialogue'
                                return True
                            current = await ready()
                            if self._darkmoor_cantrip_stage(current[2]) == 'ritual':
                                snapshot, stage = current, 'ritual'
                                state.update(snapshot=current, phase='ritual', target_clicks=0)
                                break
                            if (await is_visible_by_path(client, npc_range_path)
                                    and interaction_kind(await self.read_popup(client)) == 'talk'
                                    and await self.quest_interaction_ready(client, self.DARKMOOR_RITUAL_POSITION)):
                                await ready(current)
                                if not state.get('npc_x_sent'):
                                    state['npc_x_sent'] = True
                                    await client.send_key(Keycode.X, .1)
                            if time.monotonic() >= deadline:
                                raise TimeoutError('仪式位置未出现对话或对应新任务')
                            await asyncio.sleep(.2)

                    snapshot = await ready(snapshot if stage in ('tracker', 'shadows', 'throne_tracker') else state['snapshot'])
                    if stage in ('ritual', 'shadows'):
                        orientation = self.DARKMOOR_SHADOWS_ORIENTATION if shadows else self.DARKMOOR_RITUAL_ORIENTATION
                        await client.body.write_orientation(orientation)
                        await ready(snapshot)
                        camera = await client.game_client.selected_camera_controller()
                        if camera is None:
                            raise RuntimeError('无法取得镜头以校正仪式视角')
                        await ready(snapshot)
                        await camera.update_orientation(orientation)
                        await asyncio.sleep(.3)
                    if not await is_visible_by_path(client, open_cantrips_path):
                        raise RuntimeError('Cantrip 法杖按钮不可见')
                    card, titles = await self._darkmoor_cantrip_card(client, stage)
                    if card is None:
                        await ready(snapshot)
                        await click_window_by_path(client, open_cantrips_path)
                        deadline = time.monotonic() + 3
                        while card is None and time.monotonic() < deadline:
                            await ready(snapshot)
                            card, titles = await self._darkmoor_cantrip_card(client, stage)
                            if card is None:
                                await asyncio.sleep(.2)
                    if card is None:
                        # Some versions render Cantrips with a different control
                        # class. Hover the marked slot, then require its real title.
                        point = await self._darkmoor_cantrip_point(client, 470 if stage in ('tracker', 'throne_tracker') else 307, 766)
                        await ready(snapshot)
                        async with client.mouse_handler:
                            await client.mouse_handler.set_mouse_position(*point)
                        deadline = time.monotonic() + 3
                        while not any(text.casefold() in titles for text in await self._darkmoor_cantrip_texts(client)):
                            await ready(snapshot)
                            if time.monotonic() >= deadline:
                                raise TimeoutError('标记槽位未确认指定法术名称，不盲选')
                            await asyncio.sleep(.2)
                        await ready(snapshot)
                        async with client.mouse_handler:
                            await client.mouse_handler.click(*point)
                    else:
                        await ready(snapshot)
                        await self._click_ui_window(client, card)
                    deadline = time.monotonic() + 3
                    while not await self._darkmoor_cantrip_targeting(client):
                        await ready(snapshot)
                        if time.monotonic() >= deadline:
                            raise TimeoutError('选择法术后未出现单击目标提示')
                        await asyncio.sleep(.2)
                    state['phase'] = stage + '_wait'
                    for attempt in range(2):
                        if attempt and stage == 'ritual':
                            if not await self._darkmoor_cantrip_targeting(client):
                                raise RuntimeError('施法目标模式已关闭，不再微调或点击')
                            await ready(snapshot)
                            await client.send_key(Keycode.A, .1)
                            await ready(snapshot)
                            await client.body.write_orientation(self.DARKMOOR_RITUAL_ORIENTATION)
                            await ready(snapshot)
                            await camera.update_orientation(self.DARKMOOR_RITUAL_ORIENTATION)
                            await asyncio.sleep(.3)
                        point = await self._darkmoor_cantrip_point(client,
                            620 if shadows else 575 if stage in ('tracker', 'throne_tracker') else 543,
                            380 if shadows else 710 if stage in ('tracker', 'throne_tracker') else 186)
                        if not await self._darkmoor_cantrip_targeting(client):
                            raise RuntimeError('施法目标模式已关闭，不重复点击')
                        await ready(snapshot)
                        expected_position = (state['cast_position'] if throne else self.DARKMOOR_SHADOWS_POSITION if shadows else
                                             self.DARKMOOR_TRACKER_POSITION if stage == 'tracker' else self.DARKMOOR_RITUAL_POSITION)
                        if calc_Distance(await client.body.position(), expected_position) > (20 if shadows else 150):
                            raise RuntimeError('角色已离开标记位置，停止目标点击')
                        await ready(snapshot)
                        state['target_clicks'] += 1
                        async with client.mouse_handler:
                            await client.mouse_handler.click(*point)
                        deadline = time.monotonic() + 8
                        retry = False
                        while time.monotonic() < deadline:
                            if await dialogue_started():
                                state.update(phase='throne_cast_dialogue' if throne else 'shadows_dialogue' if shadows else 'tracker_dialogue' if stage == 'tracker' else 'completed',
                                             dialogue_seen=True)
                                if throne:
                                    state['follow_started_at'] = time.monotonic()
                                return True
                            current = await ready()
                            if throne and current != snapshot:
                                if self._darkmoor_throne_tracker_stage(current[2]) != 'throne_follow':
                                    raise RuntimeError('施法后未确认找到灰魔步骤，停止旧阶段输入')
                                state.update(snapshot=current, phase='throne_follow')
                                return True  # Next task tick follows; never cast again.
                            if current != snapshot and stage == 'ritual':
                                state['phase'] = 'completed'
                                return True
                            if current != snapshot and stage == 'tracker':
                                state['snapshot'] = current
                            if attempt == 0 and time.monotonic() >= deadline - 7 and await self._darkmoor_cantrip_targeting(client):
                                retry = True  # Still armed: no spell consumed by first click.
                                break
                            await asyncio.sleep(.2)
                        if not retry:
                            break
                    raise TimeoutError('Cantrip 目标点击后未确认任务进展或对话；停止重复施法')
        except asyncio.CancelledError:
            if isinstance(state, dict):
                state['phase'] = 'cancelled'
            raise
        except Exception as exc:
            if isinstance(state, dict):
                state['phase'] = 'failed'
            logger.warning('Darkmoor Cantrip 特殊步骤未完成：{}', exc)
        finally:
            if isinstance(state, dict):
                state['active'] = False
            release_quest_recovery(client, owner)
        return True

    def _panopticon_book_task_matches(self, text) -> bool:
        objective, location = split_quest_location(text)
        # Exact user screenshot stage; no guessed Quest ID or language key.
        return (re.sub(r'\s+', '', objective), location.casefold()) == (
            '带正确的书交给菲茨休姆', 'panopticon')

    async def _maybe_handle_panopticon_book(self, client: Client) -> bool:
        owner = 'panopticon_book'
        if getattr(client, 'quest_recovery_owner', None) == owner:
            return True
        if (not getattr(client, 'questing_status', False)
                or await client.zone_name() != self.PANOPTICON_BOOK_ZONE
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        state = getattr(client, '_xuanshu_panopticon_book_stage', None)
        if isinstance(state, dict) and state['phase'] in ('completed', 'failed', 'progressed', 'cancelled'):
            return False  # Do not replay a completed/failed book on worker restart.
        if not isinstance(state, dict) and not self._panopticon_book_task_matches(await self.read_quest_txt(client)):
            return False
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or getattr(client, 'refilling_potions', False)
                or getattr(client, 'quest_party_probe_pending', False)
                or getattr(client, 'quest_party_battle_rescue_active', False)
                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                or getattr(client, 'post_combat_movement_active', False)
                or getattr(client, 'mainline_chain_retry_active', False)
                or not await is_free_leader_questing(client)):
            return True
        if not claim_quest_recovery(client, owner):
            return True
        try:
            async with automation_owner(client, 'panopticon-book-and-dialogue'):
                async with asyncio.timeout(35):
                    if not isinstance(state, dict):
                        snapshot = await self._dungeon_quest_snapshot(client)
                        text = snapshot[2] if snapshot else await self.read_quest_txt(client)
                        if not self._panopticon_book_task_matches(text):
                            return True
                        state = {'snapshot': snapshot, 'phase': 'book', 'active': True}
                        client._xuanshu_panopticon_book_stage = state
                    else:
                        state['active'] = True
                    snapshot = state['snapshot']

                    async def same_task():
                        current = await self._dungeon_quest_snapshot(client)
                        text = current[2] if current else await self.read_quest_txt(client)
                        if not plain_text(text) or snapshot is not None and current is None:
                            raise RuntimeError('当前任务标识或文字不可读')
                        if snapshot is not None and (current[0] != snapshot[0]
                                or state['phase'] == 'book' and current[:2] != snapshot[:2]):
                            return False
                        if self._panopticon_book_task_matches(text):
                            return True
                        # Collection may advance to talking to this same NPC.
                        # Only accept that continuation when the live ID is known.
                        _, location = split_quest_location(text)
                        return (snapshot is not None and state['phase'] != 'book'
                                and location.casefold() == 'panopticon'
                                and quest_has_action(text, 'talk')
                                and quest_interaction_matches(text, self.PANOPTICON_NPC_TITLE))

                    async def input_ready():
                        if (await client.zone_name() != self.PANOPTICON_BOOK_ZONE
                                or await client.is_loading() or await client.in_battle()
                                or await client.is_in_dialog() or not await is_free_leader_questing(client)
                                or await is_spiral_door_open(client)
                                or any([await is_visible_by_path(client, path) for path in (
                                    advance_dialog_path, exit_dungeon_path, dungeon_warning_path,
                                    decline_quest_path, cancel_multiple_quest_menu_path,
                                    missing_area_path, all_quests_sort_button_path)])):
                            raise RuntimeError('区域切换、战斗、对话或其他界面阻止特殊任务输入')
                        if not await same_task():
                            state['phase'] = 'progressed'
                            return False
                        # Recheck after awaited quest/UI reads, before TP or X.
                        if (await client.zone_name() != self.PANOPTICON_BOOK_ZONE
                                or await client.is_loading() or await client.in_battle()
                                or await client.is_in_dialog() or not client.questing_status
                                or getattr(client, 'quest_recovery_owner', None) != owner
                                or getattr(client, 'refilling_potions', False)
                                or getattr(client, 'quest_party_probe_pending', False)
                                or getattr(client, 'quest_party_battle_rescue_active', False)
                                or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                                or getattr(client, 'post_combat_movement_active', False)
                                or getattr(client, 'mainline_chain_retry_active', False)):
                            raise RuntimeError('特殊任务输入前客户端状态已改变')
                        return True

                    async def dialogue_started():
                        if (await client.is_in_dialog()
                                or await is_visible_by_path(client, advance_dialog_path)):
                            if (await client.zone_name() != self.PANOPTICON_BOOK_ZONE
                                    or await client.is_loading() or await client.in_battle()
                                    or not client.questing_status or getattr(client, 'refilling_potions', False)
                                    or getattr(client, 'quest_recovery_owner', None) != owner):
                                raise RuntimeError('出现对话时区域或客户端状态已改变')
                            client.quest_dialogue_settle = {'snapshot': None, 'since': None}
                            return True
                        return False

                    if state['phase'] == 'book':
                        for attempt in range(2):
                            if not await input_ready():
                                return True
                            await client.teleport(self.PANOPTICON_BOOK_POSITION)
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                if not await input_ready():
                                    return True
                                if (await is_visible_by_path(client, npc_range_path)
                                        and plain_text(await get_popup_title(client)) == self.PANOPTICON_BOOK_TITLE
                                        and calc_Distance(await client.body.position(), self.PANOPTICON_BOOK_POSITION) < 750
                                        and plain_text(await get_popup_title(client)) == self.PANOPTICON_BOOK_TITLE
                                        and await input_ready()):
                                    # Mark before sending X so an exception/cancellation
                                    # cannot restart the first interaction blindly.
                                    state['phase'] = 'book_wait'
                                    await client.send_key(Keycode.X, .1)
                                    logger.info('Panopticon 已对指定书按 X，等待交互响应。')
                                    break
                                await asyncio.sleep(.2)
                            if state['phase'] == 'book_wait':
                                break
                        else:
                            raise TimeoutError('两次书本 TP 后未出现指定书的交互提示')

                    if state['phase'] == 'book_wait':
                        deadline = time.monotonic() + 8
                        quiet_since = None
                        while time.monotonic() < deadline:
                            if (not client.questing_status or getattr(client, 'refilling_potions', False)
                                    or await client.zone_name() != self.PANOPTICON_BOOK_ZONE
                                    or await client.is_loading() or await client.in_battle()):
                                raise RuntimeError('书本交互后任务停止、切区或进入战斗/加载')
                            if await dialogue_started():
                                return True  # Release owner; existing dialogue worker handles the book.
                            if not await input_ready():
                                return True
                            current = await self._dungeon_quest_snapshot(client)
                            if snapshot is not None and current is not None and current != snapshot:
                                state['phase'] = 'npc'
                                break
                            if (not await is_visible_by_path(client, npc_range_path)
                                    or plain_text(await get_popup_title(client)) != self.PANOPTICON_BOOK_TITLE):
                                if quiet_since is None:
                                    quiet_since = time.monotonic()
                                elif time.monotonic() - quiet_since >= .5:
                                    state['phase'] = 'npc'
                                    break
                            else:
                                quiet_since = None
                            await asyncio.sleep(.2)
                        else:
                            raise TimeoutError('按 X 后书本交互未确认，不执行 NPC TP')

                    for attempt in range(2):
                        if not await input_ready():
                            return True
                        await client.teleport(self.PANOPTICON_NPC_POSITION)
                        deadline = time.monotonic() + 5
                        pressed = False
                        while time.monotonic() < deadline:
                            if await dialogue_started():
                                state['phase'] = 'completed'
                                logger.info('Panopticon NPC 对话已触发，交由原有对话流程处理。')
                                return True
                            if not await input_ready():
                                return True
                            if (not pressed and await is_visible_by_path(client, npc_range_path)
                                    and plain_text(await get_popup_title(client)) == self.PANOPTICON_NPC_TITLE
                                    and calc_Distance(await client.body.position(), self.PANOPTICON_NPC_POSITION) < 750
                                    and plain_text(await get_popup_title(client)) == self.PANOPTICON_NPC_TITLE
                                    and await input_ready()):
                                await client.send_key(Keycode.X, .1)
                                pressed = True
                            await asyncio.sleep(.2)
                    raise TimeoutError('两次 NPC TP/交互后未触发对话')
        except asyncio.CancelledError:
            if isinstance(state, dict):
                state['phase'] = 'cancelled'
            raise
        except Exception as exc:
            if isinstance(state, dict):
                state['phase'] = 'failed'
            logger.warning('Panopticon 指定书/NPC 任务处理未完成，停止本次特殊操作：{}', exc)
        finally:
            if isinstance(state, dict):
                state['active'] = False
            release_quest_recovery(client, owner)
        return True

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

    @staticmethod
    def _easton_day_guard_matches(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'graveholm'
                and re.sub(r'\s+', '', objective).rstrip('.。') == '站立守卫')

    @staticmethod
    def _safehouse_courtyard_follow_matches(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'graveholm'
                and re.sub(r'\s+', '', objective).rstrip('.。') == '跟随断枝')

    async def _maybe_exit_easton_day_guard(self, client):
        """Direct exits for the supplied guard/courtyard stages, without a stall timer."""
        zone = await client.zone_name()
        courtyard = zone == self.DARKMOOR_COURTYARD_ZONE
        if zone not in (self.EASTON_DAY_RITUAL_ZONE, self.DARKMOOR_COURTYARD_ZONE):
            return False
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        snapshot = await self._dungeon_quest_snapshot(client)
        quest_id = self.DARKMOOR_GARGOYLE_QUEST_ID if courtyard else self.EASTON_DAY_QUEST_ID
        matches = self._safehouse_courtyard_follow_matches if courtyard else self._easton_day_guard_matches
        if snapshot is None or snapshot[0] != quest_id or not matches(snapshot[2]):
            return False
        owner = 'easton_house'
        failed_attr = '_xuanshu_safehouse_courtyard_failed' if courtyard else '_xuanshu_easton_day_guard_failed'
        failed = getattr(client, failed_attr, None)
        if isinstance(failed, dict) and failed.get('snapshot') == snapshot:
            return True  # A failed/cancelled exit is not replayed by a new worker.
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or self._darkmoor_clue_input_blocked(client, None)
                or await client.is_loading() or await client.in_battle()
                or not await is_free_leader_questing(client)):
            return True
        if not claim_quest_recovery(client, owner):
            return True

        async def interaction_pending():
            return (await is_spiral_door_open(client)
                    or any([await is_visible_by_path(client, path) for path in (
                        advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                        exit_dungeon_path, dungeon_warning_path, missing_area_path,
                        all_quests_sort_button_path)]))

        try:
            setattr(client, failed_attr, {'snapshot': snapshot, 'attempts': 0})
            await self._recover_dueling_tent(client, snapshot, interaction_pending,
                                           stand_guard=not courtyard, courtyard=courtyard)
            if getattr(client, failed_attr, None) is None:
                self._krok_exit_watch.pop(id(client), None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('{} 切区未确认，停止本阶段重复 TP：{}',
                           '庭院“跟随断枝”' if courtyard else '日间生物“站立守卫”', exc)
        finally:
            failed = getattr(client, failed_attr, None)
            if isinstance(failed, dict) and failed.get('attempts') == 0:
                setattr(client, failed_attr, None)  # No input yet: priority takeover only defers.
            release_quest_recovery(client, owner)
        return True

    @staticmethod
    def _graveholm_gargoyle_stage(snapshot):
        if snapshot is None or snapshot[0] != Quester.DARKMOOR_GARGOYLE_QUEST_ID:
            return False
        objective, location = split_quest_location(snapshot[2])
        return (location.casefold() == 'graveholm'
                and re.sub(r'\s+', '', objective).casefold().rstrip('.。') == 'wait在石像鬼形态中')

    @staticmethod
    def _scholomance_exit_stage(snapshot):
        if snapshot is None or snapshot[0] != Quester.SCHOLOMANCE_EXIT_QUEST_ID:
            return None
        objective, location = split_quest_location(snapshot[2])
        objective = re.sub(r'\s+', '', objective).rstrip('.。')
        return snapshot if (objective == '面对米兰达·布莱尔'
                            and location.casefold() == 'scholomance') else None

    @staticmethod
    def _scholomance_parlor_stage(snapshot):
        if (snapshot is None or type(snapshot[0]) is not int
                or type(snapshot[1]) is not int):
            return None
        objective, location = split_quest_location(snapshot[2])
        return snapshot if (re.sub(r'\s+', '', objective).casefold().rstrip('.。') == '进入spiraldoor'
                            and location.casefold() == 'scholomance') else None

    async def _recover_dueling_tent(self, client, progress, interaction_pending, *, easton_house=False, stand_guard=False, gargoyle=False, courtyard=False, scholomance=False, parlor=False):
        """Called with quest recovery ownership; two bounded exit TP attempts."""
        easton_recovery = easton_house or stand_guard or gargoyle or courtyard or scholomance or parlor
        zone = self.SCHOLOMANCE_PARLOR_ZONE if parlor else self.SCHOLOMANCE_LAB_ZONE if scholomance else self.DARKMOOR_COURTYARD_ZONE if courtyard else self.DARKMOOR_CANTRIP_ZONE if gargoyle else self.EASTON_DAY_RITUAL_ZONE if stand_guard else self.EASTON_HOUSE_ZONE if easton_house else self.DUELING_TENT_ZONE
        exit_point = self.SCHOLOMANCE_PARLOR_EXIT if parlor else self.SCHOLOMANCE_EXIT if scholomance else self.DARKMOOR_COURTYARD_EXIT if courtyard else self.DARKMOOR_GARGOYLE_EXIT if gargoyle else self.EASTON_DAY_GUARD_EXIT if stand_guard else self.EASTON_HOUSE_EXIT if easton_house else self.DUELING_TENT_EXIT
        label = 'Scholomance Parlor Spiral Door' if parlor else 'Scholomance Confront Miranda' if scholomance else 'SafehouseCourtyard Follow Broken Branch' if courtyard else 'Graveholm Gargoyle' if gargoyle else 'EastonDay Stand Guard' if stand_guard else 'EastonHouse' if easton_house else 'DuelingTent'
        owner = 'easton_house' if easton_recovery else 'dueling_tent'
        failed_attr = '_xuanshu_scholomance_parlor_failed' if parlor else '_xuanshu_scholomance_exit_failed' if scholomance else '_xuanshu_safehouse_courtyard_failed' if courtyard else '_xuanshu_graveholm_gargoyle_failed' if gargoyle else '_xuanshu_easton_day_guard_failed' if stand_guard else '_xuanshu_easton_house_failed' if easton_house else '_xuanshu_dueling_tent_failed'

        async def can_move():
            snapshot = await self._dungeon_quest_snapshot(client)
            if snapshot is None:
                raise RuntimeError('当前任务状态不可读取')
            if (not client.questing_status or getattr(client, 'refilling_potions', False)
                    or await client.is_loading() or await client.in_battle()
                    or await client.is_in_dialog()
                    or not await is_free_leader_questing(client)
                    or await interaction_pending()):
                raise RuntimeError('任务停止、正常交互或战斗/对话/Loading，停止特殊 TP')
            if easton_recovery and (await client.zone_name() != zone or await client.is_loading()):
                raise RuntimeError('区域已变化或进入 Loading，停止特殊 TP')
            if easton_recovery and (not client.questing_status
                    or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None) != owner
                    or getattr(client, 'mainline_chain_retry_active', False)
                    or getattr(client, 'quest_party_probe_pending', False)
                    or getattr(client, 'quest_party_battle_rescue_active', False)
                    or getattr(client, 'quest_party_quest_worker_restart_requested', False)
                    or getattr(client, 'post_combat_movement_active', False)
                    or not (scholomance or parlor) and getattr(client, 'quest_party_status_session', None) is not None
                    or any(client in getattr(member, 'quest_party_hitters', []) for member in self.clients)
                    or isinstance(getattr(client, 'quest_dungeon_recovery', None), dict)
                    and client.quest_dungeon_recovery.get('active')):
                raise RuntimeError('其他任务流程已接管，停止特殊 TP')
            return snapshot == progress

        async with automation_owner(client, 'easton-house-recovery' if easton_recovery else 'dueling-tent-recovery'):
            async with asyncio.timeout(35):
                for attempt in range(2):
                    if await client.zone_name() != zone or await client.is_loading():
                        break
                    if not await can_move():
                        setattr(client, failed_attr, None)
                        return  # Real quest progress; do not teleport again.
                    if not easton_recovery and (await client.zone_name() != zone or await client.is_loading()):
                        break
                    logger.info('{} 任务 TP 受阻且无进展，前往指定切区点（{}/2）。', label, attempt + 1)
                    if easton_recovery and isinstance(getattr(client, failed_attr, None), dict):
                        getattr(client, failed_attr)['attempts'] = attempt + 1
                    await client.teleport(exit_point)
                    deadline = time.monotonic() + 10
                    while await client.zone_name() == zone and not await client.is_loading():
                        if not await can_move():
                            setattr(client, failed_attr, None)
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
                            setattr(client, failed_attr, None)
                            setattr(client, '_scholomance_parlor_recovered_at' if parlor else '_scholomance_exit_recovered_at' if scholomance else '_easton_house_recovered_at' if easton_recovery else '_dueling_tent_recovered_at', time.monotonic())
                            for member in getattr(client, 'quest_mainline_sync_members', [client]):
                                member.quest_mainline_sync_state = None
                            client.quest_party_quest_worker_zone = arrival
                            client.quest_party_probe_pending = bool(getattr(client, 'quest_party_hitters', []))
                            self._trigger_reentry.pop(id(client), None)
                            self._npc_retry_exhausted.pop(id(client), None)
                            self._mainline_finder_observations.pop(id(client), None)
                            logger.info('{} 区域切换完成，重置卡顿计时并恢复原有打手同步。', label)
                            return
                    await asyncio.sleep(.2)

    @staticmethod
    def _is_easton_day_ritual_photo(text):
        objective, _ = split_quest_location(text)
        return (quest_has_action(objective, 'photomance')
                and any(name in plain_text(objective).casefold() for name in ('仪式', 'ritual')))

    @staticmethod
    def _is_mayor_servant_photo(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'mortal plain'
                and quest_has_action(objective, 'photomance')
                and '半死不活的附庸' in plain_text(objective))

    @staticmethod
    def _is_mayor_harp_photo(text):
        objective, location = split_quest_location(text)
        return (location.casefold() == 'mortal plain'
                and quest_has_action(objective, 'photomance')
                and '预兆竖琴' in plain_text(objective))

    async def _maybe_handle_darkmoor_castle(self, client):
        """Advance the supplied castle route only on observed game events."""
        zone = await client.zone_name()
        state = getattr(client, '_xuanshu_darkmoor_castle', None)
        if zone != self.DARKMOOR_CASTLE_ZONE:
            if zone and not await client.is_loading():
                client._xuanshu_darkmoor_castle = None
            return False
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        if isinstance(state, dict) and state['phase'] == 'completed':
            return False
        owner = 'darkmoor_cantrip'  # Existing input, follower and watchdog guards.
        if (isinstance(getattr(client, 'quest_recovery_owner', None), str)
                or self._darkmoor_clue_input_blocked(client, None)
                or await client.is_loading()):
            if isinstance(state, dict):
                state['quiet_since'] = None
            return isinstance(state, dict) or zone == self.DARKMOOR_CASTLE_ZONE
        if (isinstance(state, dict) and state['phase'] in ('landing', 'wait_battle')
                and self.DARKMOOR_CASTLE_ROUTE[state['index']][0] == 'battle'
                and await client.in_battle()):
            # Battle entry moves the body onto the duel circle and can hide
            # the HUD. Observe the battle itself, not the original TP position.
            now = time.monotonic()
            if not state['seen']:
                state.update(phase='wait_battle', seen=True, started_at=now)
            elif now - state['started_at'] > 900:
                state['phase'] = 'failed'
                logger.warning('{} 城堡战斗超过等待上限，停止后续路线。', client.title)
            state['quiet_since'] = None
            return True
        snapshot = await self._dungeon_quest_snapshot(client)
        final_dialogue_seen = (isinstance(state, dict)
            and state['index'] == len(self.DARKMOOR_CASTLE_ROUTE) - 1 and state['seen'])
        if snapshot is None and not final_dialogue_seen:
            return True  # Never guess a route from an unreadable tracked quest.
        if not isinstance(state, dict):
            identity = await self._mainline_identity(client)
            objective, location = split_quest_location(snapshot[2])
            if (not identity or identity[0] != snapshot[0] or identity[1] != 'QuestTitle_00002192'
                    or location.casefold() != 'mortal plain'
                    or re.sub(r'\s+', '', objective) != '找到方法过去的安全'):
                return False
            state = dict(quest_id=snapshot[0], index=0, phase='move', seen=False,
                         started_at=time.monotonic(), quiet_since=None, active=False)
            client._xuanshu_darkmoor_castle = state
            logger.info('{} 红死之任务：开始城堡专属路线。', client.title)
        if state['phase'] in ('failed', 'cancelled'):
            return True
        # The final dialogue can complete this parent task. Earlier changes are
        # not evidence that the remaining, user-supplied steps should be run.
        if snapshot is not None and snapshot[0] != state['quest_id'] and not final_dialogue_seen:
            state['phase'] = 'failed'
            logger.warning('{} 城堡主任务已改变，停止旧路线。', client.title)
            return True
        kind, point = self.DARKMOOR_CASTLE_ROUTE[state['index']]
        now = time.monotonic()

        def finish_step():
            state.update(index=state['index'] + 1, phase='move', seen=False,
                         started_at=time.monotonic(), quiet_since=None)
            if state['index'] == len(self.DARKMOOR_CASTLE_ROUTE):
                state['phase'] = 'completed'
                logger.info('{} 城堡路线已完成，恢复普通任务传送。', client.title)

        async def dialogue_open():
            return bool(await client.is_in_dialog()
                        or await is_visible_by_path(client, advance_dialog_path)
                        or plain_text(await read_dialogue_text(client)))

        async def input_ready(*, clock=False):
            if (not getattr(client, 'questing_status', False)
                    or self._darkmoor_clue_input_blocked(client, owner)
                    or await client.zone_name() != self.DARKMOOR_CASTLE_ZONE
                    or await client.is_loading() or await client.in_battle()
                    or getattr(client, 'entity_detect_combat_status', False)
                    or getattr(client, 'quest_party_status_session', None) is not None
                    or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                    or await self._dungeon_quest_snapshot(client) != snapshot
                    or await is_spiral_door_open(client)
                    or any([await is_visible_by_path(client, path) for path in (
                        exit_dungeon_path, dungeon_warning_path, missing_area_path,
                        all_quests_sort_button_path, quest_buttons_parent_path)])
                    or not clock and (await dialogue_open()
                        or await is_visible_by_path(client, cancel_multiple_quest_menu_path))):
                return False
            # Recheck after all awaited quest/UI reads, immediately before input.
            return (not self._darkmoor_clue_input_blocked(client, owner)
                    and await client.zone_name() == self.DARKMOOR_CASTLE_ZONE
                    and not await client.is_loading() and not await client.in_battle()
                    and await self._dungeon_quest_snapshot(client) == snapshot)

        try:
            if state['phase'] == 'move':
                if not await is_free_leader_questing(client):
                    await self._quest_dialogue_blocks_movement(client)
                    return True
                if not claim_quest_recovery(client, owner):
                    return True
                try:
                    async with automation_owner(client, 'darkmoor-castle-tp'):
                        if not await input_ready():
                            return True
                        # Persist before TP: cancellation cannot replay an uncertain move.
                        state.update(phase='landing', started_at=now, active=True,
                                     before=snapshot, seen=False, quiet_since=None)
                        logger.info('{} 城堡步骤 {}/{}：{}，前往 {}。', client.title,
                                    state['index'] + 1, len(self.DARKMOOR_CASTLE_ROUTE), kind, point)
                        await asyncio.wait_for(client.teleport(point), timeout=5)
                finally:
                    state['active'] = False
                    release_quest_recovery(client, owner)
                return True
            if state['phase'] == 'landing':
                if calc_Distance(await client.body.position(), point) > 150:
                    if now - state['started_at'] > 5:
                        raise TimeoutError('指定坐标落地未确认')
                    return True
                state.update(phase='interact' if kind in ('interact', 'clock') else 'wait_' + kind,
                             started_at=now)
            if state['phase'] == 'wait_battle':
                if await client.in_battle():
                    if not state['seen']:
                        state.update(seen=True, started_at=now)
                    elif now - state['started_at'] > 900:
                        raise TimeoutError('战斗结束未确认')
                    state['quiet_since'] = None
                    return True
                if not state['seen']:
                    if now - state['started_at'] > 25:
                        raise TimeoutError('未确认实际入战，不跳过战斗步骤')
                    return True
                if now - state['started_at'] > 900:
                    raise TimeoutError('战斗结束未确认')
            elif state['phase'] == 'wait_dialogue':
                if await dialogue_open():
                    if not state['seen']:
                        state.update(seen=True, started_at=now)
                    elif now - state['started_at'] > 180:
                        raise TimeoutError('对话结束未确认')
                    state['quiet_since'] = None
                    await self._quest_dialogue_blocks_movement(client)
                    return True
                if not state['seen']:
                    if now - state['started_at'] > 15:
                        raise TimeoutError('触发点对话未确认')
                    return True
                if now - state['started_at'] > 180:
                    raise TimeoutError('对话结束未确认')
            elif state['phase'] == 'interact':
                if not claim_quest_recovery(client, owner):
                    return True
                try:
                    async with automation_owner(client, 'darkmoor-castle-interact'):
                        if not await input_ready():
                            return True
                        if (calc_Distance(await client.body.position(), point) > 150
                                or not await is_visible_by_path(client, npc_range_path)
                                or kind == 'clock' and plain_text(await get_popup_title(client)) != '落地钟'):
                            if now - state['started_at'] > 8:
                                raise TimeoutError('指定交互提示未出现')
                            return True
                        if not await input_ready():
                            return True
                        state.update(phase='clock' if kind == 'clock' else 'wait_interact',
                                     started_at=now, before=snapshot, seen=False)
                        await client.send_key(Keycode.X, .1)  # Exactly once at this waypoint.
                finally:
                    release_quest_recovery(client, owner)
                return True
            elif state['phase'] == 'wait_interact':
                if await dialogue_open():
                    if now - state['started_at'] > 180:
                        raise TimeoutError('交互对话结束未确认')
                    state['seen'] = True
                    state['quiet_since'] = None
                    await self._quest_dialogue_blocks_movement(client)
                    return True
                if (snapshot != state['before']
                        or not await is_visible_by_path(client, npc_range_path)):
                    state['seen'] = True
                if not state['seen']:
                    if now - state['started_at'] > 15:
                        raise TimeoutError('单次交互结果未确认，不重复按 X')
                    return True
            elif state['phase'] == 'clock':
                if now - state['started_at'] > 15:
                    raise TimeoutError('落地钟选项未能安全点击')
                path = cancel_multiple_quest_menu_path[:2]
                menu = await get_window_from_path(client.root_window, path)
                if not menu or not await menu.is_visible():
                    if now - state['started_at'] > 10:
                        raise TimeoutError('落地钟选项菜单未确认')
                    return True
                nodes = await self._visible_window_nodes(menu, path)
                texts = [(window, node_path, await self._window_text(window))
                         for window, node_path in nodes]
                choices = [(window, node_path) for window, node_path, text in texts
                           if re.sub(r'\s+', '', text).replace('：', ':') == '3:33']
                choices = [item for item in choices if not any(
                    len(other[1]) > len(item[1]) and other[1][:len(item[1])] == item[1]
                    for other in choices)]
                if (len(choices) != 1 or not (any(text == '落地钟' for _, _, text in texts)
                        or plain_text(await get_popup_title(client)) == '落地钟')):
                    raise RuntimeError('落地钟身份或唯一 3:33 选项不可确认，不猜选项')
                button = choices[0][0]
                if not claim_quest_recovery(client, owner):
                    return True
                try:
                    async with automation_owner(client, 'darkmoor-castle-clock'):
                        if (not await input_ready(clock=True)
                                or calc_Distance(await client.body.position(), point) > 150
                                or not await button.is_visible() or await button.is_control_grayed()
                                or re.sub(r'\s+', '', await self._window_text(button)).replace('：', ':') != '3:33'
                                or not await menu.is_visible() or not await input_ready(clock=True)):
                            return True
                        state.update(phase='clock_close', started_at=now)
                        await self._click_ui_window(client, button)
                        logger.info('{} 落地钟：已点击实际选项 3:33。', client.title)
                finally:
                    release_quest_recovery(client, owner)
                return True
            elif state['phase'] == 'clock_close':
                menu = await get_window_from_path(client.root_window, cancel_multiple_quest_menu_path[:2])
                if menu and await menu.is_visible():
                    if now - state['started_at'] > 15:
                        raise TimeoutError('落地钟点击结果未确认，不重复点击')
                    return True
                state['phase'] = 'settle'
            # Require three quiet seconds after each observed result; no input
            # lock is held while dialogue, combat or follower sync is running.
            if not await is_free_leader_questing(client):
                state['quiet_since'] = None
                return True
            if await self._quest_dialogue_blocks_movement(client):
                state['quiet_since'] = None
                return True
            if state['quiet_since'] is None:
                state['quiet_since'] = now
            elif now - state['quiet_since'] >= 3:
                finish_step()
            return state['phase'] != 'completed'
        except asyncio.CancelledError:
            # Keep the exact in-flight phase. Recreated workers can observe its
            # result, but can never blindly repeat a TP, X or clock click.
            raise
        except Exception as exc:
            state.update(phase='failed', active=False)
            logger.warning('{} 城堡步骤 {} 未完成，停止重复输入：{}', client.title, state['index'] + 1, exc)
            return True

    async def _maybe_handle_mayor_photo(self, client):
        if await client.zone_name() != self.DARKMOOR_MAYOR_PHOTO_ZONE:
            return False
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)):
            return False
        progress = await self._dungeon_quest_snapshot(client)
        if progress is None or not (self._is_mayor_servant_photo(progress[2])
                                    or self._is_mayor_harp_photo(progress[2])):
            return False
        identity = await self._mainline_identity(client)
        # No numeric QuestID was supplied: resolve the actual tracked quest
        # through its existing, verified Recent Victim language-table identity.
        if not identity or identity[0] != progress[0] or identity[1] != 'QuestTitle_19BE7D':
            return False
        await self._recover_easton_day_ritual(client, progress, photo=True, mayor_photo=True)
        return True

    async def _recover_easton_day_ritual(self, client, progress, *, photo=False, mayor_photo=False):
        """One guarded position/viewpoint attempt per actual arrival/photo goal."""
        owner = 'darkmoor_cantrip'  # Existing Darkmoor task input/follower guards.
        zone = self.DARKMOOR_MAYOR_PHOTO_ZONE if mayor_photo else self.EASTON_DAY_RITUAL_ZONE
        position = self.DARKMOOR_MAYOR_PHOTO_POSITION if mayor_photo else self.EASTON_DAY_RITUAL_POSITION
        orientation = self.DARKMOOR_MAYOR_PHOTO_ORIENTATION if mayor_photo else self.EASTON_DAY_RITUAL_ORIENTATION
        attempt_attr = '_xuanshu_darkmoor_mayor_photo_attempt' if mayor_photo else '_xuanshu_easton_day_ritual_attempt'
        quest_id = progress[0] if mayor_photo else self.EASTON_DAY_QUEST_ID
        photo_matches = self._is_mayor_servant_photo if mayor_photo else self._is_easton_day_ritual_photo
        label = '最近的受害者附庸拍照' if mayor_photo else '日间生物仪式'
        harp = mayor_photo and self._is_mayor_harp_photo(progress[2])
        if harp:
            position = self.DARKMOOR_MAYOR_HARP_POSITION
            orientation = self.DARKMOOR_MAYOR_HARP_ORIENTATION
            photo_matches = self._is_mayor_harp_photo
            label = '最近的受害者预兆竖琴拍照'
        attempt = (zone, progress)
        if getattr(client, attempt_attr, None) == attempt:
            return
        current_owner = getattr(client, 'quest_recovery_owner', None)
        claimed = current_owner is None and claim_quest_recovery(client, owner)
        if not claimed and current_owner != owner:
            return
        try:
            async with automation_owner(client, 'mayor-harp-photo' if harp else 'mayor-servant-photo' if mayor_photo else 'easton-day-ritual'):
                async with asyncio.timeout(15):
                    async def ready():
                        current = await self._dungeon_quest_snapshot(client)
                        if (current is None or current != progress or current[0] != quest_id
                                or photo and not photo_matches(current[2])):
                            raise RuntimeError('仪式任务阶段已改变或不可读，停止旧阶段输入')
                        if (not getattr(client, 'questing_status', False)
                                or self._darkmoor_clue_input_blocked(client, owner)
                                or getattr(client, 'quest_party_status_session', None) is not None
                                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                                or await client.zone_name() != zone
                                or await client.is_loading() or await client.in_battle()
                                or await client.is_in_dialog() or not await is_free_leader_questing(client)
                                or await is_spiral_door_open(client)
                                or any([await is_visible_by_path(client, path) for path in (
                                    advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                                    exit_dungeon_path, dungeon_warning_path, missing_area_path,
                                    all_quests_sort_button_path)])):
                            raise RuntimeError('战斗、对话、切区或其他流程阻止仪式输入')
                        if (await self._dungeon_quest_snapshot(client) != progress
                                or await client.zone_name() != zone
                                or await client.is_loading() or await client.in_battle()
                                or not getattr(client, 'questing_status', False)
                                or self._darkmoor_clue_input_blocked(client, owner)):
                            raise RuntimeError('仪式输入前任务或状态已改变')

                    await ready()
                    # Persist before TP: an interrupted/uncertain attempt is not replayed.
                    setattr(client, attempt_attr, attempt)
                    await client.teleport(position)
                    await asyncio.sleep(.5)
                    deadline = time.monotonic() + 4
                    while calc_Distance(await client.body.position(), position) > 150:
                        await ready()
                        if time.monotonic() >= deadline:
                            raise TimeoutError('未确认仪式备用点落地，不调整视角或拍照')
                        await asyncio.sleep(.2)
                    await ready()
                    await client.body.write_orientation(orientation)
                    camera = await client.game_client.selected_camera_controller()
                    await ready()
                    if camera is None:
                        raise RuntimeError('相机不可读，不盲拍')
                    await camera.update_orientation(orientation)
                    await asyncio.sleep(.2)
                    if photo:
                        await ready()
                        await client.send_key(Keycode.S, .2)
                        await ready()
                        await client.send_key(Keycode.Z, .1)
                        await asyncio.sleep(.2)
                        await ready()
                        await client.send_key(Keycode.Z, .1)
                        logger.info('{}：已在指定点按指定视角拍照，等待实际任务推进。', label)
                    else:
                        logger.info('日间生物：已到仪式备用点并设定视角；到达阶段不拍照。')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('{} 恢复停止，避免同阶段重复 TP/拍照：{}', label, exc)
        finally:
            if claimed:
                release_quest_recovery(client, owner)

    async def teleport_to_quest_target(self, client, xyz, leader_client=None):
        if (getattr(client, 'refilling_potions', False) is True
                or getattr(client, 'questing_status', True) is False):
            return
        if await self._maybe_handle_overgrown_estate(client):
            return
        if await self._maybe_handle_no_blood_hideout(client):
            return
        if await self._maybe_handle_darkmoor_castle(client):
            return
        if await self._maybe_handle_darkmoor_cantrips(client):
            return
        if await self._maybe_handle_panopticon_book(client):
            return
        if await self._maybe_handle_outback_story(client):
            return
        if await self._maybe_handle_bumbles_pet(client):
            return
        if await self._maybe_handle_callisto(client):
            return
        if (getattr(client, "refilling_potions", False) is True
                or getattr(client, 'quest_recovery_owner', None) in ('gobblerton', 'lemuria_dungeon', 'sacred_yarn', 'tamarin_house', 'avalon_grain_entry', 'azteca_beetle_entry', 'bumbles_mind', 'lemuria_navigation', 'dueling_tent', 'easton_house', 'darkmoor_cantrip', 'final_act', 'overgrown_estate')
                or isinstance(getattr(client, 'quest_lemuria_navigation_recovery', None), dict)
                and client.quest_lemuria_navigation_recovery.get('holding')
                or getattr(client, "questing_status", True) is False):
            return
        if await client.zone_name() == self.TAMARIN_HOUSE_ZONE and await self._maybe_handle_tamarin_house(client):
            return
        zone = await client.zone_name()
        key = id(client)
        final_act_progress = await self._final_act_stage(client) if zone == self.FINAL_ACT_ZONE else None
        final_act = final_act_progress is not None
        estate_progress = await self._overgrown_estate_stage(client) if zone == self.OVERGROWN_ESTATE_ZONE else None
        estate = estate_progress is not None
        lab_progress = await self._scholomance_lab_stage(client) if zone == self.SCHOLOMANCE_LAB_ZONE else None
        scholomance = lab_progress is not None
        dialogue_recovery = final_act or estate or scholomance
        private_wing = zone == self.PRIVATE_WING_ZONE
        dueling_tent = zone == self.DUELING_TENT_ZONE
        easton_house = zone == self.EASTON_HOUSE_ZONE
        gargoyle_progress = await self._dungeon_quest_snapshot(client) if zone == self.DARKMOOR_CANTRIP_ZONE else None
        gargoyle = self._graveholm_gargoyle_stage(gargoyle_progress)
        scholomance_progress = await self._dungeon_quest_snapshot(client) if zone == self.SCHOLOMANCE_LAB_ZONE else None
        scholomance_exit = self._scholomance_exit_stage(scholomance_progress) is not None
        parlor_progress = await self._dungeon_quest_snapshot(client) if zone == self.SCHOLOMANCE_PARLOR_ZONE else None
        parlor = self._scholomance_parlor_stage(parlor_progress) is not None
        bounded_exit = dueling_tent or easton_house or gargoyle or scholomance_exit or parlor
        day_progress = await self._dungeon_quest_snapshot(client) if zone == self.EASTON_DAY_RITUAL_ZONE else None
        day_ritual = (day_progress is not None and day_progress[0] == self.EASTON_DAY_QUEST_ID
                      and calc_Distance(xyz, self.EASTON_DAY_RITUAL_POSITION) < 1000)
        bounded_navigation = bounded_exit or day_ritual
        if not dueling_tent:
            client._xuanshu_dueling_tent_failed = None
        if not easton_house:
            client._xuanshu_easton_house_failed = None
        if not gargoyle:
            client._xuanshu_graveholm_gargoyle_failed = None
        if not scholomance_exit:
            client._xuanshu_scholomance_exit_failed = None
        if not parlor:
            client._xuanshu_scholomance_parlor_failed = None
        if bounded_navigation and (await client.is_in_dialog() or not await is_free_leader_questing(client)
                or not (scholomance_exit or parlor) and getattr(client, 'quest_party_status_session', None) is not None
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
        transition_exit = private_wing or gobblerton or lemuria or sacred_yarn or bumbles or bounded_navigation
        failed_attr = ('_xuanshu_scholomance_parlor_failed' if parlor else '_xuanshu_scholomance_exit_failed' if scholomance_exit else '_xuanshu_scholomance_lab_failed' if scholomance else '_xuanshu_overgrown_estate_failed' if estate else '_xuanshu_graveholm_gargoyle_failed' if gargoyle else '_xuanshu_easton_day_ritual_failed' if day_ritual else '_xuanshu_final_act_failed' if final_act else '_xuanshu_easton_house_failed' if easton_house else '_xuanshu_dueling_tent_failed' if dueling_tent else '_xuanshu_bumbles_mind_failed' if bumbles else
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
        if (transition_exit or dialogue_recovery) and (not getattr(client, 'questing_status', False)
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
        special_exit = zone in special_targets or transition_exit or dialogue_recovery
        stuck = XYZ(6420.986, -6956.173, -399.699)
        if (zone != "Krokotopia/KT_WorldTeleporter" and not floating_exit and not crystal_exit and not special_exit
                or floating_exit and calc_Distance(await client.body.position(), stuck) > 150):
            self._krok_exit_watch.pop(key, None)
            await self.move_until_quest_interaction(client, xyz, leader_client=leader_client)
            return

        async def interaction_pending():
            statue_prompt = (gargoyle and await is_visible_by_path(client, npc_range_path)
                             and plain_text(await get_popup_title(client)) == '石雕怪兽基座')
            return (await is_spiral_door_open(client)
                    or (await is_visible_by_path(client, npc_range_path)
                        and calc_Distance(await client.body.position(), xyz) < 750 and not statue_prompt)
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
        progress = parlor_progress if parlor else scholomance_progress if scholomance_exit else lab_progress if scholomance else estate_progress if estate else gargoyle_progress if gargoyle else day_progress if day_ritual else final_act_progress if final_act else await self._dungeon_quest_snapshot(client) if transition_exit else None
        if day_ritual and getattr(client, '_xuanshu_easton_day_ritual_attempt', None) == (zone, progress):
            return  # Do not restart normal or fallback TP for the same attempted ritual goal.
        if (sacred_yarn or bumbles or bounded_exit) and progress is None:
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
                or not (sacred_yarn or bumbles or bounded_navigation or dialogue_recovery) and state['target'] != target
                or (transition_exit or dialogue_recovery) and progress is not None and state.get('progress') is not None
                and progress_changed(progress, state['progress'])):
            state = dict(zone=zone, objective=objective, target=target, anchor=before,
                         since=time.monotonic(), attempts=0, end_sent=False, progress=progress)
            self._krok_exit_watch[key] = state
        elif (transition_exit or dialogue_recovery) and progress is not None:
            state['progress'] = progress
        if transition_exit or dialogue_recovery:
            failed = getattr(client, failed_attr, None)
            if isinstance(failed, dict):
                changed = (failed['zone'] != zone or failed['objective'] != objective
                           or not (sacred_yarn or bumbles or bounded_navigation or dialogue_recovery) and failed['target'] != target
                           or progress is not None and failed['progress'] is not None
                           and progress_changed(progress, failed['progress']))
                if changed and objective:
                    setattr(client, failed_attr, None)
                else:
                    state['end_sent'] = True
                    if bounded_exit or dialogue_recovery:
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
            if bounded_navigation:
                try:
                    async with asyncio.timeout(15):
                        await collision_tp(client, xyz, leader_client=leader_client)
                except TimeoutError:
                    path_blocked = True
                    logger.debug('{} {} 普通任务寻路超时，纳入受阻观察。', client.title, 'Scholomance Parlor' if parlor else 'Scholomance Miranda' if scholomance_exit else 'Graveholm Gargoyle' if gargoyle else 'EastonDay Ritual' if day_ritual else 'EastonHouse' if easton_house else 'DuelingTent')
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
        if bounded_navigation and (await client.is_in_dialog() or not await is_free_leader_questing(client)):
            self._krok_exit_watch.pop(key, None)
            return
        current_objective = await get_quest_name(leader_client or client)
        if not current_objective:
            return
        if current_objective != objective:
            self._krok_exit_watch.pop(key, None)
            return
        if (gobblerton or lemuria or sacred_yarn or bumbles or bounded_navigation or dialogue_recovery) and progress_changed(await self._dungeon_quest_snapshot(client), progress):
            self._krok_exit_watch.pop(key, None)
            return

        after = await client.body.position()
        if bounded_navigation:
            rejected = path_blocked or getattr(client, '_collision_tp_rejections', 0) > rejections_before
            no_approach = calc_Distance(before, xyz) - calc_Distance(after, xyz) < 100
            if not rejected and (not no_approach or not (gargoyle or scholomance_exit or parlor) and calc_Distance(after, xyz) < self.TRIGGER_NEAR_DISTANCE):
                state.update(anchor=after, since=time.monotonic(), attempts=0)
                return  # Successful approach / normal arrival is not blocked navigation.
        if floating_exit and calc_Distance(after, stuck) > 150:
            self._krok_exit_watch.pop(key, None)
            return
        # Gobblerton tracks quest progress even when repeated TP moves the body.
        # Other exits retain their existing stationary-position requirement.
        if not (gobblerton or sacred_yarn or bumbles or bounded_navigation or dialogue_recovery) and max(calc_Distance(before, state['anchor']),
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

        recovery_owner = ('overgrown_estate' if estate else 'darkmoor_cantrip' if day_ritual or scholomance else 'final_act' if final_act else 'easton_house' if easton_house or gargoyle or scholomance_exit or parlor else 'dueling_tent' if dueling_tent else 'bumbles_mind' if bumbles else 'sacred_yarn' if sacred_yarn else 'lemuria_dungeon' if lemuria else
                          'gobblerton' if gobblerton else 'private_wing' if private_wing else 'special_zone')
        if not claim_quest_recovery(client, recovery_owner):
            return
        state['end_sent'] = True
        try:
            if estate:
                if (await self._overgrown_estate_stage(client) != progress
                        or not client.questing_status or await client.is_loading() or await client.in_battle()
                        or not await is_free_leader_questing(client)
                        or self._darkmoor_clue_input_blocked(client, recovery_owner)):
                    state['end_sent'] = False
                    return
                if self.overgrown_estate_paused(client):
                    return  # Another legacy peer may have started this shared visit during TP.
                participants = [client, *getattr(client, 'quest_party_hitters', [])]
                if len(participants) == 1:
                    participants = [client, *[p for p in self.clients if p is not client]]
                estate_state = dict(owner_client=client, participants=participants,
                                    progress=progress, phase='dialogue_move')
                for member in participants:
                    member._xuanshu_overgrown_estate = estate_state
                self._krok_exit_watch.pop(key, None)
                release_quest_recovery(client, recovery_owner)
                await self._maybe_handle_overgrown_estate(client)
                return
            if day_ritual:
                await self._recover_easton_day_ritual(client, progress,
                    photo=self._is_easton_day_ritual_photo(progress[2]))
                if getattr(client, '_xuanshu_easton_day_ritual_attempt', None) != (zone, progress):
                    state['end_sent'] = False  # Priority takeover before TP only defers.
                return
            if final_act or scholomance:
                setattr(client, failed_attr, dict(zone=zone, objective=objective, target=target, progress=progress, attempts=0))
                try:
                    if scholomance:
                        await self._recover_final_act(client, progress, scholomance=True)
                    else:
                        await self._recover_final_act(client, progress)
                except Exception as exc:
                    logger.warning('{}对话触发暂未完成，停止本阶段重复 TP：{}', '米兰达实验室' if scholomance else '最终一幕', exc)
                finally:
                    if scholomance:
                        failed = getattr(client, failed_attr, None)
                        if isinstance(failed, dict) and failed.get('attempts') == 0:
                            setattr(client, failed_attr, None)
                            state['end_sent'] = False  # Takeover before first TP only defers.
                return
            if bounded_exit:
                if (await client.zone_name() != zone or await self._dungeon_quest_snapshot(client) != progress
                        or not client.questing_status or await client.is_loading() or await client.in_battle()
                        or not await is_free_leader_questing(client) or await interaction_pending()):
                    return
                setattr(client, failed_attr, dict(zone=zone, objective=objective, target=target, progress=progress))
                try:
                    if parlor:
                        getattr(client, failed_attr)['attempts'] = 0
                        await self._recover_dueling_tent(client, progress, interaction_pending, parlor=True)
                    elif scholomance_exit:
                        getattr(client, failed_attr)['attempts'] = 0
                        await self._recover_dueling_tent(client, progress, interaction_pending, scholomance=True)
                    elif gargoyle:
                        getattr(client, failed_attr)['attempts'] = 0
                        await self._recover_dueling_tent(client, progress, interaction_pending, gargoyle=True)
                    elif easton_house:
                        getattr(client, failed_attr)['attempts'] = 0
                        await self._recover_dueling_tent(client, progress, interaction_pending, easton_house=True)
                    else:
                        await self._recover_dueling_tent(client, progress, interaction_pending)
                except Exception as exc:
                    logger.error('{} 特殊恢复未完成，停止本任务阶段重复 TP：{}', 'Scholomance Parlor' if parlor else 'Scholomance Miranda' if scholomance_exit else 'Graveholm Gargoyle' if gargoyle else 'EastonHouse' if easton_house else 'DuelingTent', exc)
                finally:
                    failed = getattr(client, failed_attr, None)
                    if (easton_house or gargoyle or scholomance_exit or parlor) and isinstance(failed, dict) and failed.get('attempts') == 0:
                        setattr(client, failed_attr, None)
                        if scholomance_exit or parlor:
                            state['end_sent'] = False
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
            if sacred_yarn or bumbles or bounded_exit:
                self._krok_exit_watch.pop(key, None)
            if lemuria or bounded_exit:
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
        if (await client.zone_name() == self.DARKMOOR_MAYOR_PHOTO_ZONE
                and (self._is_mayor_servant_photo(objective) or self._is_mayor_harp_photo(objective))):
            await self._maybe_handle_mayor_photo(client)
            return  # This photo must never fall through to an unpositioned generic Z.
        if (await client.zone_name() == self.EASTON_DAY_RITUAL_ZONE
                and self._is_easton_day_ritual_photo(objective)):
            progress = await self._dungeon_quest_snapshot(client)
            if (progress is not None and progress[0] == self.EASTON_DAY_QUEST_ID
                    and self._is_easton_day_ritual_photo(progress[2])):
                await self._recover_easton_day_ritual(client, progress, photo=True)
            return  # Never use unguarded generic Z for this ritual.
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
            await client.send_key(Keycode.S, .2)
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
        await client.send_key(Keycode.S, .2)
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
                if await self.party_dungeon_entry_visible(self.current_leader_client, sigil_msg_check):
                    entry_zone = await self.current_leader_client.zone_name()
                    entry_mainline = await self._mainline_identity(self.current_leader_client)
                    entry_mainline_id = (entry_mainline[0] if entry_mainline and entry_mainline[3] is not None
                                         and entry_mainline[4] is True else None)
                    while await self.party_dungeon_entry_visible(self.current_leader_client, sigil_msg_check):
                        logger.debug('Entering dungeon')
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                        await asyncio.sleep(1.0)
                        for c in self.clients:
                            if await is_visible_by_path(c, dungeon_warning_path):
                                await c.send_key(Keycode.ENTER, 0.1)

                        sigil_msg_check = await self.read_popup(self.current_leader_client)

                    await self.handle_dungeon_entry(questing_friend_tp, follower_clients, entry_zone, entry_mainline_id)
                else:
                    msg = sigil_msg_check.lower()
                    if portal_kind(await get_popup_title(self.current_leader_client)) == 'world_gate':
                        await self.current_leader_client.send_key(Keycode.X, .1)
                    elif interaction_kind(msg) == "talk":
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
        if await self._maybe_enter_azteca_beetle_map(self.current_leader_client):
            return
        if await self._maybe_enter_avalon_grain_map(self.current_leader_client):
            return
        if await self._maybe_handle_overgrown_estate(self.current_leader_client):
            return
        if await self._maybe_handle_no_blood_hideout(self.current_leader_client):
            return
        if await self._maybe_handle_darkmoor_cantrips(self.current_leader_client):
            return
        if await self._maybe_handle_panopticon_book(self.current_leader_client):
            return
        if any(await gather_owned(*[self._maybe_handle_outback_story(p) for p in self.clients])):
            return
        if any(await gather_owned(*[self._maybe_handle_bumbles_pet(p) for p in self.clients])):
            return
        if any(await gather_owned(*[self._maybe_recover_lemuria_navigation(p) for p in self.clients])):
            return
        if await self._maybe_handle_bumbles_mind(self.current_leader_client):
            return
        if await self._maybe_handle_tamarin_house(self.current_leader_client):
            return
        if await self._maybe_handle_callisto(self.current_leader_client):
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

            if await self._maybe_handle_overgrown_estate(self.current_leader_client):
                continue
            if any(await gather_owned(*[self._maybe_handle_outback_story(p) for p in self.clients])):
                continue
            if any(await gather_owned(*[self._maybe_handle_darkmoor_castle(p) for p in self.clients])):
                continue
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

            if await self._maybe_handle_no_blood_hideout(self.current_leader_client):
                continue
            if await self._maybe_handle_panopticon_book(self.current_leader_client):
                continue
            if await self._maybe_handle_darkmoor_cantrips(self.current_leader_client):
                continue
            if await self._maybe_handle_callisto(self.current_leader_client):
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
                    if await self._maybe_refresh_stalled_dungeon_quest(self.current_leader_client):
                        continue

                    if await self._mainline_sync_blocks_movement(self.current_leader_client):
                        continue

                    await self.teleport_to_quest(hitting_client, follower_clients)

                    if self.overgrown_estate_paused(self.current_leader_client):
                        continue
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

    async def handle_pending_dungeon_confirmation(self, client: Client = None) -> bool:
        """Confirm a dungeon transition modal even if it appeared late."""
        client = self.client if client is None else client
        if not await is_visible_by_path(client, exit_dungeon_path):
            return False

        zone_before = await client.zone_name()
        logger.debug(
            f"Client {client.title} - confirming pending dungeon transition."
        )
        await click_window_by_path(client, exit_dungeon_path)

        # Do not wait forever if this was a slow or stale message box.  The
        # next quest iteration can retry the click if it remains visible.
        deadline = time.monotonic() + 15.0
        saw_loading = False
        while time.monotonic() < deadline:
            if await client.is_loading():
                saw_loading = True
                await asyncio.sleep(0.1)
                continue

            current_zone = await client.zone_name()
            if current_zone and current_zone != zone_before:
                # This confirmation is an exit/transition, not proof of entry.
                client.quest_dungeon_recovery = None
                client.quest_party_confirmed_dungeon_transition = (
                    zone_before, current_zone
                )
                if getattr(client, "quest_party_hitters", []):
                    client.quest_party_quest_worker_zone = current_zone
                    client.quest_party_probe_pending = (
                        getattr(client, "quest_party_group_dungeon_zone", None)
                        != current_zone
                    )
                break

            if saw_loading or not await is_visible_by_path(
                client, exit_dungeon_path
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

    async def _no_blood_hideout_stage(self, client):
        if (not getattr(client, 'questing_status', False)
                or getattr(client, 'quest_party_status_session', None) is not None
                or any(client in getattr(c, 'quest_party_hitters', []) for c in self.clients)
                or await client.zone_name() != self.NO_BLOOD_HIDEOUT_ZONE):
            return None
        snapshot = await self._dungeon_quest_snapshot(client)
        if snapshot is None or snapshot[0] != self.NO_BLOOD_QUEST_ID:
            return None
        objective, location = split_quest_location(snapshot[2])
        if (re.sub(r'\s+', '', objective) != '跟随踪迹'
                or location.casefold() != 'howling lands'):
            return None
        identity = await self._mainline_identity(client)
        if (identity is None or identity[0] != snapshot[0]
                or not identity[1].endswith('QuestTitle_18F18A')):
            return None
        return snapshot[0], snapshot[1], plain_text(snapshot[2])

    async def _maybe_handle_no_blood_hideout(self, client) -> bool:
        """The supplied trail stage has no usable HUD target; go to its sigil."""
        progress = await self._no_blood_hideout_stage(client)
        if progress is None:
            return False
        owner = 'no_blood_hideout'

        async def ready():
            if (self._darkmoor_clue_input_blocked(client, owner)
                    or await client.is_loading() or await client.in_battle()
                    or await client.is_in_dialog() or not await is_free_leader_questing(client)
                    or await is_spiral_door_open(client)
                    or any([await is_visible_by_path(client, path) for path in (
                        advance_dialog_path, decline_quest_path, cancel_multiple_quest_menu_path,
                        exit_dungeon_path, dungeon_warning_path, missing_area_path,
                        all_quests_sort_button_path, quest_buttons_parent_path)])):
                return False
            return (await self._no_blood_hideout_stage(client) == progress
                    and not self._darkmoor_clue_input_blocked(client, owner)
                    and not await client.is_loading() and not await client.in_battle())

        state = getattr(client, '_xuanshu_no_blood_hideout', None)
        if not isinstance(state, dict) or state['progress'] != progress:
            state = {'progress': progress, 'phase': 'move'}
            client._xuanshu_no_blood_hideout = state
        if state['phase'] in ('failed', 'entered'):
            return True
        if not claim_quest_recovery(client, owner):
            return True
        try:
            async with automation_owner(client, 'no-blood-hideout-tp'):
                if not await ready():
                    return True
                if state['phase'] == 'move':
                    # Record before awaiting input: a recreated worker cannot replay an uncertain TP.
                    state.update(phase='landing', started_at=time.monotonic())
                    logger.info('{} 不会有鲜血：直接前往藏身处入口 {}。',
                                client.title, self.NO_BLOOD_HIDEOUT_POSITION)
                    await asyncio.wait_for(client.teleport(self.NO_BLOOD_HIDEOUT_POSITION), timeout=5)
                    return True
                if calc_Distance(await client.body.position(), self.NO_BLOOD_HIDEOUT_POSITION) > 150:
                    if time.monotonic() - state['started_at'] >= 5:
                        state['phase'] = 'failed'
                        logger.warning('{} 未确认藏身处入口落地，停止同阶段重复 TP。', client.title)
                    return True
                if (not await is_visible_by_path(client, npc_range_path)
                        or not is_dungeon_entry_prompt(await self.read_popup(client))):
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state['phase'] = 'failed'
            logger.warning('{} 藏身处入口传送停止，避免同阶段重复 TP：{}', client.title, exc)
            return True
        finally:
            release_quest_recovery(client, owner)

        # Do not hold the quest recovery/input locks while followers need to catch up.
        async def catchup_ready():
            if not claim_quest_recovery(client, owner):
                return False
            try:
                return await ready()
            finally:
                release_quest_recovery(client, owner)

        entry_clients = await self.prepare_party_dungeon_entry(client, entry_ready=catchup_ready)
        for member in [client, *getattr(client, 'quest_party_hitters', []), *self.clients]:
            if (member not in entry_clients or not getattr(member, 'questing_status', False)
                    or getattr(member, 'refilling_potions', False)
                    or isinstance(getattr(member, 'quest_recovery_owner', None), str)
                    or await member.is_loading() or not await is_free_leader_questing(member)
                    or await member.zone_name() != self.NO_BLOOD_HIDEOUT_ZONE
                    or calc_Distance(await member.body.position(), self.NO_BLOOD_HIDEOUT_POSITION) > 150):
                return True
        if not claim_quest_recovery(client, owner):
            return True
        try:
            async with automation_owner(client, 'no-blood-hideout-entry'):
                async def entry_ready():
                    for member in entry_clients:
                        if (not getattr(member, 'questing_status', False)
                                or getattr(member, 'refilling_potions', False)
                                or member is not client and isinstance(getattr(member, 'quest_recovery_owner', None), str)
                                or await member.is_loading() or not await is_free_leader_questing(member)
                                or await member.zone_name() != self.NO_BLOOD_HIDEOUT_ZONE
                                or calc_Distance(await member.body.position(), self.NO_BLOOD_HIDEOUT_POSITION) > 150):
                            return False
                    return (await is_visible_by_path(client, npc_range_path)
                            and is_dungeon_entry_prompt(await self.read_popup(client))
                            and await ready())

                await self.enter_party_dungeon(entry_clients, client, entry_ready=entry_ready)
                if await client.zone_name() != self.NO_BLOOD_HIDEOUT_ZONE:
                    state['phase'] = 'entered'
        finally:
            release_quest_recovery(client, owner)
        return True

    async def prepare_party_dungeon_entry(self, client: Client = None, *, entry_ready=None) -> list[Client]:
        """Move assigned hitters to the entrance before everyone presses X."""
        explicit_client = client is not None
        client = self.client if client is None else client
        entry_clients = list(self.clients)
        assigned_hitters = [
            hitter
            for hitter in getattr(client, "quest_party_hitters", [])
        ]
        if explicit_client:
            # The special entrance also serves the legacy leader: its other
            # questing clients must arrive before the shared entry presses X.
            assigned_hitters += [c for c in self.clients if c is not client and c not in assigned_hitters
                                and getattr(c, 'questing_status', False)
                                and not getattr(c, 'refilling_potions', False)]
        if not assigned_hitters:
            return entry_clients

        deadline = time.monotonic() + 4.0
        pending = list(assigned_hitters)
        while pending and time.monotonic() < deadline:
            if entry_ready is not None and not await entry_ready():
                return entry_clients
            quester_zone = await client.zone_name()
            quester_position = await client.body.position()
            for hitter in list(pending):
                try:
                    if (
                        getattr(hitter, 'questing_status', False)
                        and not getattr(hitter, 'refilling_potions', False)
                        and not isinstance(getattr(hitter, 'quest_recovery_owner', None), str)
                        and not isinstance(getattr(hitter, 'potion_dungeon_returned', None), tuple)
                        and not getattr(hitter, 'post_combat_cleanup_active', False)
                        and not await hitter.is_loading()
                        and await hitter.zone_name() == quester_zone
                        and await is_free(hitter)
                        and await clients_share_live_area(client, hitter)
                    ):
                        if entry_ready is not None and not await entry_ready():
                            return entry_clients
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
        self, entry_clients: list[Client], entry_zone: str, client: Client = None
    ) -> bool:
        """Confirm this whole party arrived in the same live destination."""
        client = self.client if client is None else client
        hitters = getattr(client, "quest_party_hitters", [])
        if (
            not entry_zone
            or not hitters
            or any(hitter not in entry_clients for hitter in hitters)
        ):
            return False
        try:
            destination = await client.zone_name()
            if not destination or destination == entry_zone or await client.is_loading():
                return False
            for member in hitters:
                if (not getattr(member, 'questing_status', False)
                        or await member.is_loading()
                        or await member.zone_name() != destination
                        or not await clients_share_live_area(client, member)):
                    return False
            return not await client.is_loading() and await client.zone_name() == destination
        except Exception:
            return False

    async def enter_party_dungeon(self, entry_clients: list[Client], client: Client = None, *, entry_ready=None):
        """Reuse the normal sigil entry and its actual-transition confirmation."""
        client = self.client if client is None else client
        entry_zone = await client.zone_name()
        # Ordinary doors also need the assigned party at the entrance. Only a
        # visible sigil establishes dungeon state before the transition.
        sigil = await self.party_dungeon_entry_visible(client)
        if not sigil and not (getattr(client, 'quest_party_hitters', [])
                              and is_dungeon_entry_prompt(await self.read_popup(client))
                              and await is_visible_by_path(client, npc_range_path)
                              and await self.quest_interaction_ready(client, await client.quest_position.position())):
            return False
        entry_mainline = await self._mainline_identity(client)
        entry_mainline_id = (entry_mainline[0] if entry_mainline and entry_mainline[3] is not None
                             and entry_mainline[4] is True else None)
        if entry_ready is not None and not await entry_ready():
            return False
        hitters = list(getattr(client, 'quest_party_hitters', []))
        if hitters:
            # Never let a partial entrance list silently become a solo entry.
            if client not in entry_clients or any(p not in entry_clients for p in hitters):
                self._note_quest_entry_wait(client, '入口集合缺少本组成员',
                                           ready=[p.title for p in entry_clients], expected=[client.title, *[p.title for p in hitters]])
                return False
            entry_clients = [client, *hitters]
            entrance_title = plain_text(await get_popup_title(client))
            entrance_position = await client.body.position()
            if not entrance_title:
                return False
            for member in [client, *hitters]:
                if (not getattr(member, 'questing_status', False)
                        or getattr(member, 'refilling_potions', False)
                        or getattr(member, 'post_combat_cleanup_active', False)
                        or (getattr(member, 'quest_party_target_sync_active', False) is True
                            and getattr(client, 'quest_party_target_sync_active', False) is not True)
                        or member is not client and isinstance(getattr(member, 'quest_recovery_owner', None), str)
                        or isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)):
                    self._note_quest_entry_wait(client, '成员任务停止或由补药/战后恢复占用', member=member.title)
                    return False
                if await member.is_loading() or not await is_free(member):
                    self._note_quest_entry_wait(client, '成员加载或忙碌', member=member.title)
                    return False
                member_zone = await member.zone_name()
                if member_zone != entry_zone:
                    self._note_quest_entry_wait(client, '成员不在入口区域', member=member.title, zone=member_zone, expected=entry_zone)
                    return False
                if member is not client and not await clients_share_live_area(client, member):
                    self._note_quest_entry_wait(client, '成员同实例证据未通过', member=member.title)
                    return False
                member_distance = calc_Distance(await member.body.position(), entrance_position)
                if member_distance >= 750:
                    self._note_quest_entry_wait(client, '成员尚未靠近入口', member=member.title, distance=round(member_distance))
                    return False
                member_title = plain_text(await get_popup_title(member))
                if member_title != entrance_title:
                    self._note_quest_entry_wait(client, '成员入口标题不同或未显示', member=member.title, title=member_title, expected=entrance_title)
                    return False
                if (not await self.party_dungeon_entry_visible(member) if sigil else
                        not await is_visible_by_path(member, npc_range_path)
                        or not is_dungeon_entry_prompt(await self.read_popup(member))):
                    self._note_quest_entry_wait(client, '成员未显示可确认的组队进入提示', member=member.title, prompt=await self.read_popup(member))
                    return False
            if entry_ready is not None and not await entry_ready():
                return False
            # A previously checked hitter can leave while a later member's
            # prompt is being read. Recheck every assigned member before X.
            for member in entry_clients:
                if (await member.is_loading() or await member.zone_name() != entry_zone
                        or not await is_free(member)
                        or calc_Distance(await member.body.position(), entrance_position) >= 750
                        or plain_text(await get_popup_title(member)) != entrance_title
                        or not is_dungeon_entry_prompt(await self.read_popup(member))
                        or member is not client and not await clients_share_live_area(client, member)):
                    self._note_quest_entry_wait(client, '进入前成员状态已改变，继续等待本组', member=member.title)
                    return False
            if (await client.is_loading() or await client.zone_name() != entry_zone
                    or (not await self.party_dungeon_entry_visible(client) if sigil else
                        not await is_visible_by_path(client, npc_range_path)
                        or not is_dungeon_entry_prompt(await self.read_popup(client)))
                    or plain_text(await get_popup_title(client)) != entrance_title
                    or any(not getattr(p, 'questing_status', False)
                           or getattr(p, 'refilling_potions', False)
                           or getattr(p, 'post_combat_cleanup_active', False)
                           or (getattr(p, 'quest_party_target_sync_active', False) is True
                               and getattr(client, 'quest_party_target_sync_active', False) is not True)
                           or isinstance(getattr(p, 'potion_dungeon_returned', None), tuple)
                           or p is not client and isinstance(getattr(p, 'quest_recovery_owner', None), str)
                           for p in [client, *hitters])):
                return False
            client.quest_party_probe_pending = True
            # Preserve the verified entrance while one member starts loading
            # before the other. The follower must not try friend teleport here.
            if sigil or getattr(client, 'quest_party_group_dungeon_zone', None) is not None:
                client.quest_party_group_dungeon_zone = entry_zone
            client.in_solo_zone = False
            recovery = getattr(client, 'quest_dungeon_recovery', None)
            if sigil and (not isinstance(recovery, dict) or not recovery.get('entry_zone')):
                client.quest_dungeon_recovery = {
                    'zone': entry_zone, 'entry_zone': entry_zone, 'entered': False,
                    'snapshot': None, 'since': None, 'active': False,
                    'attempted': False, 'waiting_logged': False,
                }
        interaction = getattr(client, 'quest_party_dungeon_interaction', None)
        if isinstance(interaction, dict) and interaction.get('zone') == entry_zone:
            for member in entry_clients:
                interaction['attempts'][id(member)] = 1
        # Retain the last confirmed party room while a new entrance is loading.
        # Equality with the destination plus live proof still controls readiness.
        previous_sync = [getattr(member, 'quest_party_target_sync_active', False) is True
                         for member in entry_clients]
        previous_entry = getattr(client, 'quest_party_dungeon_entry_active', False) is True
        client.quest_party_dungeon_entry_active = True
        for member in entry_clients:
            member.quest_party_target_sync_active = True
        try:
            await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in entry_clients])
            # Countdown and loading are part of the same entry operation.
            # Independent coordinate/friend follow must not pull a member off
            # the sigil after X while another member is already entering.
            loading_clients = []
            for c in entry_clients:
                loading_deadline = time.monotonic() + 15.0
                while not await c.is_loading() and time.monotonic() < loading_deadline:
                    member_zone = await c.zone_name()
                    if member_zone and member_zone != entry_zone:
                        break  # A fast load may have finished between samples.
                    if await is_visible_by_path(c, dungeon_warning_path):
                        await c.send_key(Keycode.ENTER, 0.1)
                    await asyncio.sleep(0.1)
                if await c.is_loading():
                    loading_clients.append(c)
                elif not await c.zone_name() or await c.zone_name() == entry_zone:
                    logger.warning(f"Client {c.title} did not enter the dungeon in time.")
            for c in loading_clients:
                while await c.is_loading():
                    await asyncio.sleep(0.1)
            current_zone = await client.zone_name()
            await self._confirm_dungeon_entry(client, entry_zone, entry_mainline_id)
            if getattr(client, "quest_party_hitters", []) and current_zone and current_zone != entry_zone:
                client.quest_party_quest_worker_zone = current_zone
                if await self.party_dungeon_entry_complete(entry_clients, entry_zone, client):
                    if sigil or getattr(client, 'quest_party_group_dungeon_zone', None) is not None:
                        client.quest_party_group_dungeon_zone = current_zone
                    client.quest_party_probe_pending = False
                    logger.info(f"Client {client.title} and assigned hitters "
                                "entered the dungeon; resuming party zone sync.")
                else:
                    client.quest_party_probe_pending = True
                    logger.debug(f"Client {client.title} entered a dungeon; waiting for assigned hitters to sync.")
                return True
            return False
        finally:
            client.quest_party_dungeon_entry_active = previous_entry
            for member, was_active in zip(entry_clients, previous_sync):
                member.quest_party_target_sync_active = was_active

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
        return await self.party_dungeon_entry_complete(
            [self.client, *hitters], previous_quester_zone)

    async def _quest_party_probe_blocks_movement(self) -> bool:
        """Pause only this party's next movement until a zone transition is proved."""
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

        if not current_zone:
            return True
        if (getattr(self.client, 'in_solo_zone', False) is True
                and getattr(self.client, 'quest_party_group_dungeon_zone', None) is None
                and getattr(self.client, 'quest_party_quest_worker_zone', None) == current_zone
                and not getattr(self.client, 'quest_party_probe_pending', False)):
            return False  # A genuinely confirmed solo area has no party barrier.
        group_dungeon_zone = getattr(
            self.client, "quest_party_group_dungeon_zone", None
        )
        worker_zone = getattr(
            self.client, "quest_party_quest_worker_zone", None
        )
        interaction = getattr(self.client, 'quest_party_dungeon_interaction', None)
        transition_pending = (
            getattr(self.client, 'quest_party_probe_pending', False) is True
            or worker_zone is not None and current_zone != worker_zone
            or group_dungeon_zone is not None and current_zone != group_dungeon_zone
            or isinstance(interaction, dict) and interaction.get('phase') == 'transition'
            and interaction.get('zone') != current_zone
        )
        if not transition_pending:
            self.client.quest_party_quest_worker_zone = current_zone
            return False
        self.client.quest_party_probe_pending = True
        # Read only assigned hitters. Failed/late probes must not release the
        # gate merely because one hitter arrived or a timeout expired.
        try:
            async with asyncio.timeout(5):
                for hitter in list(self.client.quest_party_hitters):
                    if (not getattr(hitter, 'questing_status', False)
                            or await hitter.is_loading()
                            or await hitter.zone_name() != current_zone
                            or not await clients_share_live_area(self.client, hitter)):
                        return True
                if await self.client.is_loading() or await self.client.zone_name() != current_zone:
                    return True
        except Exception:
            return True
        self.client.quest_party_quest_worker_zone = current_zone
        if group_dungeon_zone is not None or isinstance(interaction, dict):
            self.client.quest_party_group_dungeon_zone = current_zone
            recovery = getattr(self.client, 'quest_dungeon_recovery', None)
            if isinstance(recovery, dict) and recovery.get('entry_zone'):
                if current_zone != recovery['entry_zone']:
                    recovery['entered'] = True
                elif recovery.get('entered'):
                    for member in [self.client, *self.client.quest_party_hitters]:
                        member.quest_party_group_dungeon_zone = None
                        member.quest_party_confirmed_dungeon_transition = None
                    self.client.quest_party_dungeon_interaction = None
                    self.client.quest_dungeon_recovery = None
        self.client.quest_party_probe_pending = False
        return False

    async def teleport_party_to_quest_target(self, xyz) -> bool:
        """Preflight the whole shared room, then move and drain every member."""
        client = self.client
        hitters = list(getattr(client, 'quest_party_hitters', []))
        zone = await client.zone_name() if hitters else None
        group_zone = getattr(client, 'quest_party_group_dungeon_zone', None)
        confirmed_solo = (getattr(client, 'in_solo_zone', False) is True
                          and group_zone is None
                          and getattr(client, 'quest_party_quest_worker_zone', None) == zone
                          and not getattr(client, 'quest_party_probe_pending', False))
        if not hitters or confirmed_solo:
            await self.teleport_to_quest_target(client, xyz)
            return True
        if not zone or group_zone is not None and group_zone != zone:
            return False  # A split shared room must never fall back to solo TP.

        participants = [client, *hitters]
        tasks = []
        movement = watcher = None
        flagged = []
        try:
            try:
                before = await client.quest_id(), await client.goal_id()
                if not all(type(value) is int for value in before) or before[0] <= 0:
                    before = None
            except Exception:
                before = None
            if before is None:
                return False

            # No leader input may start until all assigned hitters are ready.
            # Presence checks after starting the leader can lose the source
            # zone as soon as a door/trigger changes rooms.
            async with asyncio.timeout(5):
                for member in participants:
                    if (not getattr(member, 'questing_status', False)
                            or getattr(member, 'refilling_potions', False)
                            or getattr(member, 'post_combat_cleanup_active', False)
                            or isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)
                            or isinstance(getattr(member, 'quest_recovery_owner', None), str)
                            or getattr(member, 'quest_party_target_sync_active', False)
                            or await member.is_loading() or not await is_free(member)
                            or await member.zone_name() != zone
                            or member is not client and not await clients_share_live_area(client, member)):
                        return False
                if (await client.is_loading() or await client.zone_name() != zone
                        or getattr(client, 'quest_party_group_dungeon_zone', None) != group_zone
                        or before is not None and (await client.quest_id(), await client.goal_id()) != before):
                    return False
                for member in participants:
                    if (await member.is_loading() or await member.zone_name() != zone
                            or not await is_free(member)):
                        return False
                # Refresh non-awaiting flags after the last presence read.
                if any(not getattr(p, 'questing_status', False)
                       or getattr(p, 'refilling_potions', False)
                       or getattr(p, 'post_combat_cleanup_active', False)
                       or isinstance(getattr(p, 'quest_recovery_owner', None), str)
                       or getattr(p, 'quest_party_target_sync_active', False)
                       for p in participants):
                    return False
                for member in participants:
                    member.quest_party_target_sync_active = True
                    flagged.append(member)
                client.in_solo_zone = False  # Whole-party live proof supersedes stale solo classification.

            source_tokens = {}
            if group_zone == zone:
                for member in hitters:
                    pair = getattr(client, '_party_area_peers', {}).get(id(member))
                    if pair:
                        source_tokens[id(member)] = pair[1]
            client.quest_party_shared_target = {
                'zone': zone, 'identity': before, 'xyz': xyz,
                'members': tuple(id(p) for p in participants), 'source_tokens': source_tokens,
            }

            async def task_changed():
                while True:
                    await asyncio.sleep(.2)
                    if any(not getattr(p, 'questing_status', False) for p in participants):
                        return
                    # A zone transition is the intended result, not a reason
                    # to cancel slower peers still moving through the doorway.
                    if (await client.zone_name() == zone and not await client.is_loading()
                            and (await client.quest_id(), await client.goal_id()) != before):
                        return

            tasks = [asyncio.create_task(self.teleport_to_quest_target(p, xyz, leader_client=client))
                     for p in participants]
            movement = asyncio.gather(*tasks)
            async with asyncio.timeout(30):
                if before is None:
                    await movement
                else:
                    watcher = asyncio.create_task(task_changed())
                    done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        await watcher
                        logger.info('{} 地牢任务目标已改变或本组已停止，取消整组旧目标移动。', client.title)
                        return False
                    await movement
            return True
        except TimeoutError:
            logger.warning('{} 整组地牢传送未完成，暂停本轮；等待任务进展或人工处理。', client.title)
            return False
        finally:
            for task in [movement, watcher, *tasks]:
                if task is not None and not task.done():
                    task.cancel()
            await gather_owned(*[task for task in [movement, watcher, *tasks] if task is not None],
                               return_exceptions=True)
            for member in flagged:
                member.quest_party_target_sync_active = False

    async def _resume_party_dungeon_interaction(self, hitter=None) -> bool:
        """Recover only the follower; never occupy the quester's task loop."""
        client = self.client
        if hitter is None:
            return False
        state = getattr(client, 'quest_party_dungeon_interaction', None)
        if isinstance(state, dict) and portal_kind(state.get('title', '')) == 'world_gate':
            client.quest_party_dungeon_interaction = None
            return False  # Discard old shared-X recovery for a quester-only world selector.
        participants = [client, *getattr(client, 'quest_party_hitters', [])]
        context = getattr(client, 'quest_party_shared_target', None)
        if (not isinstance(state, dict) or state.get('phase') != 'transition'):
            # Collision movement and post-battle triggers can change rooms
            # without an X prompt. Retain the source move for a lagging hitter.
            if (not isinstance(context, dict) or not context.get('source_tokens')
                    or context.get('members') != tuple(id(p) for p in participants)
                    or getattr(client, 'quest_party_group_dungeon_zone', None) is None
                    or hitter not in participants[1:]
                    or await client.is_loading()
                    or await client.zone_name() == context['zone']
                    or await client.quest_id() != context['identity'][0]):
                return False
            source = context['zone']
            now = time.monotonic()
            state = {
                'members': context['members'], 'zone': source, 'xyz': context['xyz'],
                'phase': 'transition', 'movement': True, 'started_at': now,
                'deadline': now + 30, 'attempts': {}, 'next_at': {}, 'warned': False,
                'destination': await client.zone_name(), 'changed': True, 'stable': 0,
                'title': '', 'prompt': '', 'source_tokens': context['source_tokens'],
            }
            client.quest_party_dungeon_interaction = state
            client.quest_party_probe_pending = True
            logger.info('{} 自动切区后等待整组到齐；保留 {} 的原任务目标供打手补跟。', client.title, source)
        if not isinstance(state, dict) or 'phase' not in state:
            return False
        if (tuple(id(p) for p in participants) != state['members']
                or not all(getattr(p, 'questing_status', False) for p in participants)):
            client.quest_party_dungeon_interaction = None
            return True
        if hitter not in participants[1:] or state['phase'] == 'waiting':
            return False

        # The source target belongs to the interaction that split the party,
        # not to the quester's now possibly advanced navigation arrow.
        try:
            # A collision move may need several retreat/walk steps. Keep the
            # original read budget for X recovery, but let source movement use
            # the remaining bounded catch-up window instead of cancelling at 5s.
            budget = max(5, state['deadline'] - time.monotonic()) if state.get('movement') else 5
            async with asyncio.timeout(budget):
                zones = []
                for member in participants:
                    await observe_party_area(member)
                    if await member.is_loading():
                        state['changed'] = True
                        zones.append(None)
                        continue
                    if (member is hitter and time.monotonic() < state['deadline']
                            and not getattr(member, 'refilling_potions', False)
                            and not isinstance(getattr(member, 'quest_recovery_owner', None), str)
                            and not isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)
                            and await is_free(member)):
                        async with automation_owner(member, 'party-dungeon-interaction'):
                            if (is_dungeon_entry_prompt(state['prompt'])
                                    and not state.setdefault('entry_confirmed', {}).get(id(member))
                                    and await is_visible_by_path(member, dungeon_warning_path)):
                                state['entry_confirmed'][id(member)] = True
                                await member.send_key(Keycode.ENTER, .1)
                            await self.handle_pending_dungeon_confirmation(member)
                    current = await member.zone_name()
                    zones.append(current)
                    if current and current != state['zone']:
                        state['changed'] = True
                        if state['destination'] is None:
                            state['destination'] = current

                destination = state['destination']
                reunited_at_source = destination and all(z == state['zone'] for z in zones)
                reunited_at_leader = zones[0] and zones[0] != state['zone'] and all(z == zones[0] for z in zones)
                if reunited_at_leader or reunited_at_source:
                    shared = all(await gather_owned(*[
                        clients_share_live_area(client, p) for p in participants[1:]
                    ]))
                    state['stable'] = state['stable'] + 1 if shared else 0
                    if state['stable'] >= 3:
                        if reunited_at_source:
                            state.update(phase='waiting', changed=False, destination=None, stable=0)
                            return True  # Keep the old input count until real goal progress.
                        destination = zones[0]  # Leader may already have advanced another room.
                        for member in participants:
                            member.quest_party_confirmed_dungeon_transition = (state['zone'], destination)
                            member.quest_party_group_dungeon_zone = destination
                        recovery = getattr(client, 'quest_dungeon_recovery', None)
                        if (isinstance(recovery, dict) and recovery.get('entered')
                                and destination == recovery.get('entry_zone')):
                            for member in participants:
                                member.quest_party_group_dungeon_zone = None
                                member.quest_party_confirmed_dungeon_transition = None
                            client.quest_dungeon_recovery = None
                        client.quest_party_quest_worker_zone = destination
                        client.quest_party_probe_pending = False
                        client.quest_party_dungeon_interaction = None
                        if isinstance(context, dict) and context.get('zone') == state['zone']:
                            context['source_tokens'] = {}
                        logger.info('{} 全组已确认交互切区 {} -> {}，打手恢复跟随。', client.title, state['zone'], destination)
                        return True
                    if reunited_at_source and time.monotonic() < state['deadline']:
                        return True  # Do not send anyone back out while proving a rollback.
                else:
                    state['stable'] = 0

                now = time.monotonic()
                if now >= state['deadline']:
                    if not state['warned']:
                        state['warned'] = True
                        logger.warning('{} 整组交互切区超过 30 秒，停止重复按键和好友传送，本组等待区域同步或人工归队。', client.title)
                    return False
                if not state['changed']:
                    if now - state['started_at'] >= 3:
                        return False
                    return True

                # Only lagging members can replay the exact, prevalidated
                # source interaction. Never TP coordinates across zones, or
                # replay the button on a member that already entered.
                for member, zone in zip(participants, zones):
                    key = id(member)
                    if (member is not hitter or destination is None or zone != state['zone'] or state['attempts'].get(key, 0) >= 2
                            or now < state['next_at'].get(key, 0)
                            or getattr(member, 'refilling_potions', False)
                            or isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)
                            or isinstance(getattr(member, 'quest_recovery_owner', None), str)
                            or getattr(member, 'quest_party_target_sync_active', False)):
                        continue
                    async with automation_owner(member, 'party-dungeon-interaction'):
                        try:
                            source_token = await _party_area_token(member)
                        except Exception:
                            continue  # No uninterrupted source-instance evidence: no retry.
                        if source_token != state['source_tokens'].get(key):
                            continue
                        if state.get('movement'):
                            if (not getattr(member, 'questing_status', False)
                                    or await member.is_loading() or await member.zone_name() != state['zone']
                                    or not await is_free(member)
                                    or getattr(member, 'post_combat_cleanup_active', False)
                                    or getattr(client, 'refilling_potions', False)
                                    or getattr(client, 'quest_party_target_sync_active', False)):
                                continue
                            state['attempts'][key] = state['attempts'].get(key, 0) + 1
                            state['next_at'][key] = time.monotonic() + 1.5
                            # Use the source coordinate only in the proven
                            # source room, never the leader's new-room arrow.
                            member.quest_party_target_sync_active = True
                            try:
                                logger.info('{} 在原房间 {} 补跟整组任务 TP：{}。', member.title, state['zone'], state['xyz'])
                                async with asyncio.timeout(max(.1, state['deadline'] - time.monotonic())):
                                    await collision_tp(member, state['xyz'])
                            finally:
                                member.quest_party_target_sync_active = False
                            continue
                        if (not getattr(member, 'questing_status', False)
                                or getattr(member, 'refilling_potions', False)
                                or isinstance(getattr(member, 'quest_recovery_owner', None), str)
                                or isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)
                                or time.monotonic() >= state['deadline']
                                or await member.is_loading() or await member.zone_name() != state['zone']
                                or not await is_free(member)
                                or calc_Distance(await member.body.position(), state['xyz']) >= 750
                                or not await is_visible_by_path(member, npc_range_path)
                                or plain_text(await get_popup_title(member)) != state['title']
                                or plain_text(await self.read_popup(member)) != state['prompt']):
                            continue
                        state['attempts'][key] = state['attempts'].get(key, 0) + 1
                        state['next_at'][key] = time.monotonic() + 1.5
                        await member.send_key(Keycode.X, .1)
        except TimeoutError:
            logger.debug('{} 地牢协同恢复读取超时，保留恢复状态，下一轮继续确认。', client.title)
            return time.monotonic() < state['deadline']
        except Exception as exc:
            logger.debug('{} 打手切区恢复状态暂不可读，本组保持区域同步等待：{}', hitter.title, exc)
            return time.monotonic() < state['deadline']
        return True

    async def handle_party_dungeon_interaction(self, xyz) -> bool:
        """Act on the quester's prompt; followers recover independently if needed."""
        client = self.client
        state = getattr(client, 'quest_party_dungeon_interaction', None)
        hitters = list(getattr(client, 'quest_party_hitters', []))
        zone = await client.zone_name() if hitters else None
        if (not hitters or getattr(client, 'in_solo_zone', False)
                or getattr(client, 'quest_party_group_dungeon_zone', None) != zone):
            return False  # Keep the existing ordinary/solo-client flow.
        participants = [client, *hitters]
        snapshot = await self._dungeon_quest_snapshot(client)
        signature = (zone, snapshot, tuple(id(p) for p in participants))
        context = getattr(client, 'quest_party_shared_target', None)
        if isinstance(context, dict) and context['identity'] is not None:
            if ((await client.quest_id(), await client.goal_id()) != context['identity']
                    or context['zone'] != zone or calc_Distance(context['xyz'], xyz) > 1):
                client.quest_party_dungeon_interaction = None
                return True  # A new task invalidates the old landing's input.
        prompt = plain_text(await self.read_popup(client))
        title = plain_text(await get_popup_title(client))

        if portal_kind(title) == 'world_gate':
            return False  # Let the ordinary quester-only interaction handle world selection.

        async def ready(member):
            return (getattr(member, 'questing_status', False)
                    and not getattr(member, 'refilling_potions', False)
                    and not isinstance(getattr(member, 'potion_dungeon_returned', None), tuple)
                    and not isinstance(getattr(member, 'quest_recovery_owner', None), str)
                    and not await member.is_loading() and await member.zone_name() == zone
                    and await is_free(member)
                    and calc_Distance(await member.body.position(), xyz) < 750
                    and await is_visible_by_path(member, npc_range_path)
                    and plain_text(await get_popup_title(member)) == title
                    and plain_text(await self.read_popup(member)) == prompt)

        if not prompt or not title or not await ready(client):
            return False
        if interaction_kind(prompt) is None:
            return False  # Generic handler logs/waits; party input must not bypass it.
        # A quest-local NPC may exist only on the quester. No follower prompt,
        # readiness check or snapshot is a precondition for this conversation.
        if interaction_kind(prompt) == 'talk':
            await self.handle_npc_talking_quests(client, [client])
            return True
        # Unlike NPC dialogue, doors/mechanisms are a shared-room action.
        # Missing/busy hitters consume this iteration rather than falling
        # through to the generic quester-only X handler.
        for member in hitters:
            if not await ready(member) or not await clients_share_live_area(client, member):
                return True
        if snapshot is None:
            return True  # Unreadable shared progress never grants solo input.
        signature = (zone, snapshot, signature[2], title, prompt)
        if not isinstance(state, dict) or state.get('signature') != signature:
            state = {
                'signature': signature, 'members': signature[2], 'zone': zone,
                'snapshot': snapshot, 'xyz': xyz, 'phase': 'transition',
                'started_at': time.monotonic(), 'deadline': time.monotonic() + 30,
                'attempts': {}, 'next_at': {}, 'warned': False,
                'destination': None, 'changed': False, 'stable': 0,
                'title': title, 'prompt': prompt, 'source_tokens': {},
            }
            client.quest_party_dungeon_interaction = state
        if state['attempts'].get(id(client), 0):
            return True  # Only this unchanged quester button has already been sent.
        for member in participants:
            member.quest_party_target_sync_active = True
        try:
            if (getattr(client, 'in_solo_zone', False)
                    or getattr(client, 'quest_party_group_dungeon_zone', None) != zone
                    or not await ready(client)):
                return True
            if await self._dungeon_quest_snapshot(client) != snapshot:
                return True
            source_tokens = {}
            # Only retained presence proof can enable later follower input.
            # Do not read/wait for a hitter here: the quester goes first.
            for member in hitters:
                try:
                    pair = getattr(client, '_party_area_peers', {}).get(id(member))
                    if pair is not None:
                        source_tokens[id(member)] = pair[1]
                except Exception:
                    pass
            state['source_tokens'] = source_tokens
            if await self.party_dungeon_entry_visible(client, prompt):
                entry_clients = await self.prepare_party_dungeon_entry()
                await self.enter_party_dungeon(entry_clients)
            else:
                async with AsyncExitStack() as owners:
                    for member in participants:
                        await owners.enter_async_context(automation_owner(member, 'party-dungeon-interaction'))
                    for member in participants:
                        if not await ready(member):
                            return True
                    if await self._dungeon_quest_snapshot(client) != snapshot:
                        return True
                    for member in participants:
                        state['attempts'][id(member)] = 1
                    await gather_owned(*[member.send_key(Keycode.X, .1) for member in participants])
            if state['attempts'].get(id(client), 0):
                logger.info('{} 整组已执行地牢交互；等待整组切区确认，NPC 对话仍独立。', client.title)
        finally:
            for member in participants:
                member.quest_party_target_sync_active = False
        return True

    async def auto_quest_solo(self, auto_pet_disabled=False, ignore_pet_level_up=False, play_dance_game=False):
        from src.mainline_progress import log_mainline_progress
        if await self._maybe_handle_overgrown_estate(self.client):
            return
        if getattr(self.client, "refilling_potions", False) is True:
            return
        if await self._maybe_handle_darkmoor_castle(self.client):
            return
        if await self._maybe_handle_outback_story(self.client):
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

        # Followers probe newly entered zones independently. Only the quester's
        # own loading/return state can stop its next movement here.
        if await self._maybe_recover_lemuria_navigation(self.client):
            return
        if await self._quest_party_probe_blocks_movement():
            # A nearby tracked NPC remains independent of follower arrival.
            # Always end this iteration afterward: no new movement may slip
            # through if that prompt disappears during the dialogue checks.
            if (interaction_kind(await self.read_popup(self.client)) == 'talk'
                    and not await self.client.is_loading()):
                target = await self.client.quest_position.position()
                await self.handle_quest_interaction(self.client, target)
            return
        if await self._maybe_handle_no_blood_hideout(self.client):
            return
        if await self._maybe_handle_bumbles_mind(self.client):
            return
        if await self._maybe_handle_tamarin_house(self.client):
            return
        if await self._maybe_handle_panopticon_book(self.client):
            return
        if await self._maybe_handle_darkmoor_cantrips(self.client):
            return
        if await self._mainline_sync_blocks_movement(self.client):
            return
        if await self._maybe_enter_azteca_beetle_map(self.client):
            return
        if await self._maybe_enter_avalon_grain_map(self.client):
            return
        if await self._maybe_handle_callisto(self.client):
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
                    await reconcile_combat_state(self.client)
                    if not self.client.questing_status:
                        return
                    await asyncio.sleep(.1)

                if await self._quest_dialogue_blocks_movement(self.client):
                    return
                if await self._maybe_refresh_stalled_dungeon_quest(self.client):
                    return
                shared_room = (bool(getattr(self.client, 'quest_party_hitters', []))
                               and getattr(self.client, 'quest_party_group_dungeon_zone', None)
                               == await self.client.zone_name())
                shared_target = getattr(self.client, 'quest_party_shared_target', None)
                shared_move_pending = shared_room and (
                    not isinstance(shared_target, dict)
                    or shared_target['zone'] != await self.client.zone_name()
                    or shared_target['identity'] != (await self.client.quest_id(), await self.client.goal_id())
                    or calc_Distance(shared_target['xyz'], quest_xyz) > 1)
                visible_entry = await self.party_dungeon_entry_visible(self.client)
                if ((not shared_move_pending or visible_entry)
                        and await self.quest_interaction_ready(self.client, quest_xyz, require_objective_match=False)):
                    self._trigger_reentry.pop(id(self.client), None)
                    if not visible_entry and await self.handle_party_dungeon_interaction(quest_xyz):
                        return
                    if await self.handle_quest_interaction(self.client, quest_xyz):
                        return

                if await self._mainline_sync_blocks_movement(self.client):
                    return
                zone_before_quest_move = await self.client.zone_name()
                if not await self.teleport_party_to_quest_target(quest_xyz):
                    return

                if await self._quest_dialogue_blocks_movement(self.client):
                    return
                if self.overgrown_estate_paused(self.client):
                    return
                # confirm exit dungeon early button or wait for client to exit loading
                await self.handle_questing_zone_change()

                if (
                    getattr(self.client, "quest_party_hitters", [])
                    and await self.client.zone_name() != zone_before_quest_move
                ):
                    # Notify the follower without waiting for its probe. The
                    # next worker iteration reads the quester's new target.
                    self.client.quest_party_quest_worker_zone = (
                        await self.client.zone_name()
                    )
                    self.client.quest_party_probe_pending = True
                    logger.debug(
                        f"Client {self.client.title} changed zones; hitters probe independently."
                    )
                    return

                await asyncio.sleep(.5)
                if await is_visible_by_path(self.client, cancel_chest_roll_path):
                    # Handles chest reroll menu, will always cancel
                    await click_window_by_path(self.client, cancel_chest_roll_path)

                if await self.handle_party_dungeon_interaction(quest_xyz):
                    return

                if await self.handle_quest_interaction(self.client, quest_xyz):
                    return

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
            await asyncio.sleep(.3)
            await self.auto_quest_solo(ignore_pet_level_up=ignore_pet_level_up, play_dance_game=play_dance_game)



async def is_free_leader_questing(client: Client):
    # Returns True if not in combat, loading screen, dialogue, or forced animation dialogue.
    await reconcile_combat_state(client)
    dialogue_text = await read_dialogue_text(client)
    return not any([await client.is_loading(), await client.in_battle(), await is_visible_by_path(client, advance_dialog_path), client.entity_detect_combat_status, getattr(client, 'post_combat_cleanup_active', False), (dialogue_text != '')])


async def read_dialogue_text(p: Client) -> str:
    try:
        dialogue_text = await get_window_from_path(p.root_window, dialog_text_path)
        if (not dialogue_text or not await dialogue_text.is_visible()
                or not all([await parent.is_visible() for parent in await dialogue_text.get_parents()])):
            return ''
        txtmsg = await dialogue_text.maybe_text()
    except:
        txtmsg = ''
    return txtmsg
