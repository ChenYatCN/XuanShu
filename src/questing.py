from src.task_lifecycle import gather_owned
import asyncio
import time
import traceback
import math

from loguru import logger
from src.logging_utils import log_blocked, log_recovered, log_exception

import re
from src.auto_pet import auto_pet
from src.interaction_prompts import (
    is_dungeon_entry_prompt, interaction_kind, quest_has_action, quest_interaction_matches,
    split_quest_location, collect_object_name, portal_kind, resolve_portal_destination, matches_text,
)
from src.teleport_math import *
from src.teleport_math import (_movement_task_snapshot, _same_movement_task,
                               _movement_interaction_handoff, _movement_failure_blocks_interaction,
                               _movement_failure_for_target, _movement_stop_reason,
                               _movement_body, _movement_position, _movement_control_changed)
from src.teleport_math import _movement_interaction_signature
from src.utils import _party_area_token
from src.automation_ownership import automation_owner
from wizwalker import XYZ, Orient, Keycode, MemoryReadError, Client, Rectangle, HookAlreadyActivated, HookNotActive
from wizwalker.errors import MemoryInvalidated, ExceptionalTimeout
from wizwalker.file_readers.wad import Wad
from wizwalker.memory import DynamicClientObject
from wizwalker.extensions.scripting import teleport_to_friend_from_list
from src.sprinty_client import SprintyClient
from src.utils import *
from src.paths import *
from src.collecting import collect_one
from src.script_popups import close_automation_popup, discard_photo_popup
from src.quest_party import (
    begin_dungeon_entry, observe_dungeon_session, dungeon_session_for,
    dungeon_member_inside, record_dungeon_exit, clear_dungeon_session,
    pause_dungeon_for_potions, finish_dungeon_potions,
    quest_party_members_ready, remember_dungeon_transition,
)
from thefuzz import fuzz


class Quester():
    GUMMY_WORMS_PHOTO_ZONE = 'Karamelle/Interiors/KM_Z04_BonBon'

    GUMMY_WORMS_PHOTO_POSITION = XYZ(1258.114, 2119.140, 2.000)

    GUMMY_WORMS_PHOTO_ORIENTATION = Orient(0.000, 0.000, 1.590)

    GIANT_VAT_PHOTO_POSITION = XYZ(48.393, 187.929, 2.000)

    GIANT_VAT_PHOTO_ORIENTATIONS = (
        Orient(0.000, 0.000, 3.855), Orient(0.000, 0.000, 3.146),
    )

    EASTON_DAY_RITUAL_ZONE = 'Darkmoor/Interiors/DM_Z01I06_EastonHouseDayEncounter'

    EASTON_DAY_QUEST_ID = 142707813506621765

    EASTON_DAY_RITUAL_POSITION = XYZ(-6454.599, 2026.960, 851.999)

    EASTON_DAY_RITUAL_ORIENTATION = Orient(0.000, 0.000, 1.573)

    DARKMOOR_MAYOR_PHOTO_ZONE = 'Darkmoor/Interiors/DM_Z02_MayorHouse_A'

    DARKMOOR_MAYOR_PHOTO_POSITION = XYZ(531.067, -473.838, .999)

    DARKMOOR_MAYOR_PHOTO_ORIENTATION = Orient(0.000, 0.000, 2.469)

    DARKMOOR_MAYOR_HARP_POSITION = XYZ(-1203.329, 3168.999, .999)

    DARKMOOR_MAYOR_HARP_ORIENTATION = Orient(0.000, 0.000, 1.490)

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

    @staticmethod

    def _is_giant_vat_photo_prompt(value):
        return '巨美大桶' in plain_text(value) and quest_has_action(value, 'photomance')

    async def _finish_photomancy_photo(self, client, zone):
        # Use the existing result-window handler after the shutter, including
        # delayed results. This wait is local to a photo, never normal movement.
        for _ in range(10):
            if (not client.questing_status or await client.is_loading()
                    or await client.in_battle() or await client.zone_name() != zone):
                return False
            if await discard_photo_popup(client):
                return True
            await asyncio.sleep(.3)
        return False

    async def _maybe_photo_giant_vat(self, client, prompt=None):
        if prompt is None:
            if not await is_visible_by_path(client, npc_range_path):
                return False
            prompt = await self.read_popup(client)
        if not self._is_giant_vat_photo_prompt(prompt):
            return False
        await self.take_photomancy_photo(client, prompt)
        return True

    async def _maybe_handle_mayor_photo(self, client):
        if await client.zone_name() != self.DARKMOOR_MAYOR_PHOTO_ZONE:
            return False
        progress = await self._dungeon_quest_snapshot(client)
        if progress is None or not (self._is_mayor_servant_photo(progress[2])
                                    or self._is_mayor_harp_photo(progress[2])):
            return False
        # e9bdd91 identifies Recent Victim by this actual language-table key.
        # Read it directly: no mainline finder/index/coordination dependency.
        try:
            quest = (await (await client.quest_manager()).quest_data()).get(progress[0])
            if quest is None or await quest.name_lang_key() != 'QuestTitle_19BE7D':
                return False
        except Exception:
            return False
        await self._recover_easton_day_ritual(client, progress, photo=True, mayor_photo=True)
        return True

    async def _recover_easton_day_ritual(self, client, progress, *, photo=False, mayor_photo=False):
        # Restore only the photograph stage, with the historical position/view.
        if not photo:
            return
        zone = self.DARKMOOR_MAYOR_PHOTO_ZONE if mayor_photo else self.EASTON_DAY_RITUAL_ZONE
        position = self.DARKMOOR_MAYOR_PHOTO_POSITION if mayor_photo else self.EASTON_DAY_RITUAL_POSITION
        orientation = self.DARKMOOR_MAYOR_PHOTO_ORIENTATION if mayor_photo else self.EASTON_DAY_RITUAL_ORIENTATION
        if mayor_photo and self._is_mayor_harp_photo(progress[2]):
            position, orientation = self.DARKMOOR_MAYOR_HARP_POSITION, self.DARKMOOR_MAYOR_HARP_ORIENTATION
        async def ready():
            return (client.questing_status and not await client.is_loading() and not await client.in_battle()
                    and await client.zone_name() == zone
                    and await self._dungeon_quest_snapshot(client) == progress)
        if not await ready():
            return
        await client.teleport(position)
        await asyncio.sleep(.5)
        if not await ready():
            return
        await client.body.write_orientation(orientation)
        if not await ready():
            return
        camera = await client.game_client.selected_camera_controller()
        if camera is None or not await ready():
            return
        await camera.update_orientation(orientation)
        await asyncio.sleep(.2)
        if not await ready():
            return
        await client.send_key(Keycode.S, .2)
        if not await ready():
            return
        await client.send_key(Keycode.Z, .1)
        await asyncio.sleep(.2)
        if not await ready():
            return
        await client.send_key(Keycode.Z, .1)
        await self._finish_photomancy_photo(client, zone)

    async def take_photomancy_photo(self, client, objective):
        zone = await client.zone_name()
        async def ready():
            return (client.questing_status and not await client.is_loading() and not await client.in_battle()
                    and await client.zone_name() == zone)
        async def view(orientation):
            if not await ready():
                return False
            await client.body.write_orientation(orientation)
            if not await ready():
                return False
            camera = await client.game_client.selected_camera_controller()
            if camera is not None:
                if not await ready():
                    return False
                await camera.update_orientation(orientation)
            await asyncio.sleep(.2)
            return await ready()
        async def shoot(back=True):
            if not await ready():
                return False
            if back:
                await client.send_key(Keycode.S, .2)
            if not await ready():
                return False
            await client.send_key(Keycode.Z, .1)
            if not await ready():
                return False
            await client.send_key(Keycode.Z, .1)
            await self._finish_photomancy_photo(client, zone)
            return True
        if not await ready():
            return
        if (zone == self.DARKMOOR_MAYOR_PHOTO_ZONE
                and (self._is_mayor_servant_photo(objective) or self._is_mayor_harp_photo(objective))):
            await self._maybe_handle_mayor_photo(client)
            return
        if zone == self.EASTON_DAY_RITUAL_ZONE and self._is_easton_day_ritual_photo(objective):
            progress = await self._dungeon_quest_snapshot(client)
            if (progress is not None and progress[0] == self.EASTON_DAY_QUEST_ID
                    and self._is_easton_day_ritual_photo(progress[2])):
                await self._recover_easton_day_ritual(client, progress, photo=True)
            return
        target = plain_text(objective).casefold()
        if self._is_giant_vat_photo_prompt(objective):
            before = await self._dungeon_quest_snapshot(client)
            if not await ready():
                return
            await client.teleport(self.GIANT_VAT_PHOTO_POSITION)
            await asyncio.sleep(.5)
            if not await ready() or not self._is_giant_vat_photo_prompt(await self.read_popup(client)):
                return
            if not await ready():
                return
            await client.send_key(Keycode.A, .05)
            first, second = self.GIANT_VAT_PHOTO_ORIENTATIONS
            if not await view(first) or not await shoot():
                return
            if before is None:
                return
            for _ in range(10):
                if not await ready():
                    return
                await asyncio.sleep(.3)
                after = await self._dungeon_quest_snapshot(client)
                if after is None or after != before:
                    return
            if await view(second):
                await shoot(back=False)
            return
        if ('讨厌的虫子' in target or 'gummy worms' in target) and zone == self.GUMMY_WORMS_PHOTO_ZONE:
            await client.teleport(self.GUMMY_WORMS_PHOTO_POSITION)
            await asyncio.sleep(.5)
            if not await ready() or not quest_has_action(await self.read_quest_txt(client), 'photomance'):
                return
            if not await ready():
                return
            await client.send_key(Keycode.A, .05)
            if not await view(self.GUMMY_WORMS_PHOTO_ORIENTATION):
                return
        await shoot()

    def __init__(self, client: Client, clients: list[Client], leader_pid: int):
        self.client = client
        self.clients = clients
        self.leader_pid = leader_pid
        self.current_leader_client = client
        self.current_leader_pid = leader_pid
        self.d_location = None
        self._krok_exit_watch = {}

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
        zone = await self.current_leader_client.zone_name()
        for c in self.clients:
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
            if c.process_id != self.current_leader_client.process_id:
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
        if await self.followers_in_correct_zone():
            return
        for p in self.clients:
            await p.send_key(Keycode.END)
            await p.send_key(Keycode.END)
            await asyncio.sleep(3)
            # use is_loading instead of wait_for_change, as one client could already be in the hub
            while await p.is_loading():
                await asyncio.sleep(0.1)

        await asyncio.sleep(2)

    async def friend_teleport(self, maybe_solo_zone: bool):
        clients_in_solo_zone = []
        solo_zone = None
        leader_in_solo_zone = False
        was_loading = False

        for c in self.clients:
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

        any_client_needs_potions = False
        for p in self.clients:
            # If we have less than 1 potion left, send all clients to get potions (even if some don't need it).  Only do this once per questing loop
            if await p.stats.potion_charge() < 1.0 and await p.stats.reference_level() >= 6:
                any_client_needs_potions = True

        if any_client_needs_potions:
            saved_refilling = [(c, getattr(c, 'refilling_potions', False)) for c in self.clients]
            for c, _ in saved_refilling:
                c.refilling_potions = True
            recalled = []
            original_zones = {}
            try:
                for c in self.clients:
                    await pause_dungeon_for_potions(c)
                # Try the mark twice.  If the first input is lost, the shared helper
                # backs up for three seconds before trying PgDn again.
                for p in self.clients:
                    original_zone = await p.zone_name()
                    if original_zone != 'WizardCity/WC_Hub':
                        original_zones[p] = original_zone
                        if not await ensure_teleport_mark(p):
                            logger.warning(
                                f'Client {p.title} - Continuing potion refill even '
                                'though the return mark was not confirmed.'
                            )

                await gather_owned(*[refill_potions(c, mark=False, recall=False, original_zone=await c.zone_name()) for c in self.clients])
                # Clients that started in the Commons do not need a recall.  All
                # others use bounded retries instead of pressing PageUp forever.
                recalled = await gather_owned(
                    *[
                        self.gather_clients_from_potion_buy(c, original_zone)
                        for c, original_zone in original_zones.items()
                    ]
                )
            finally:
                return_results = dict(zip(map(id, original_zones), recalled))
                for c, previous in saved_refilling:
                    c.refilling_potions = previous
                    if not previous:
                        await finish_dungeon_potions(c, recalled=return_results.get(id(c), False))

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

    async def gather_clients_from_potion_buy(
        self, p: Client, original_zone: str
    ) -> bool:
        recalled = await recall_to_teleport_mark(
            p, expected_zone=original_zone, attempts=3
        )
        if not recalled:
            logger.error(
                f'Client {p.title} - Could not return to the pre-potion '
                'location; auto questing will continue recovery from the Commons.'
            )
        return recalled

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
        if await self._wait_internal_quest_failure(client):
            return True  # Still handled; do not fall through to unrelated portal input.
        kind = portal_kind(await self.read_spiral_door_title(client))
        if kind in {"streamportal", "nanavator"}:
            choices = streamportal_locations if kind == "streamportal" else nanavator_locations
            location = await self.find_quest_zone_area_name(client, choices)
            if location is None:
                logger.warning(f"Client {client.title}: 无法确定特殊传送门的任务目的地，跳过传送。")
                return True
            if await new_portals_cycle(client, location) is False:
                self.d_location = None
                await self._defer_internal_quest_failure(client,
                    '特殊传送菜单读取或选择失败，有限重取后仍无法确认', kind='portal', delay=5)
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

    async def handle_dungeon_entry(self, questing_friend_tp: bool, follower_clients: list[Client]):
        # await self.enter_dungeon()
        await gather_owned(*[c.wait_for_zone_change() for c in self.clients])

        await asyncio.sleep(1.0)

        # check for instance desync, and attempt to fix if it has happened
        if questing_friend_tp:
            num_visible_clients = await self.num_clients_in_same_area()

            if num_visible_clients == len(self.clients):
                pass

            elif num_visible_clients > 1:
                # teleport all, this clearly isn't a solo zone
                logger.debug('One or more clients was separated from the group - teleporting all to leader')
                await self.correct_dungeon_desync(follower_clients)

    async def _npc_dialogue_visible(self, client):
        """Observe actual UI and remember that its quest update must settle."""
        visible = (await is_visible_by_path(client, advance_dialog_path)
                or await is_visible_by_path(client, decline_quest_path)
                or await is_visible_by_path(client, cancel_multiple_quest_menu_path)
                or (await is_visible_by_path(client, dialog_text_path)
                    and bool(await read_dialogue_text(client))))
        if visible:
            client._quest_dialogue_update_pending = True
        return visible

    async def _quest_interaction_signature(self, client, prompt=None):
        """The range popup has text identity, not an entity ID."""
        return await _movement_interaction_signature(client, prompt,
            read_popup=self.read_popup, read_title=get_popup_title, visible=is_visible_by_path)

    async def _quest_interaction_retry_state(self, client, target, task_client=None, *, task_snapshot=None):
        """Reuse failed identities while the actual task/target stays unchanged."""
        task = task_snapshot if task_snapshot is not None else await _movement_task_snapshot(task_client or client)
        if task is None:
            return None
        old = getattr(client, '_quest_interaction_retry', None)
        if (not isinstance(old, dict) or not _same_movement_task(old['task'], task)
                or calc_Distance(old['target'], target) > 1):
            stale = set()
            if (isinstance(old, dict) and old['task'][3] == task[3]
                    and calc_Distance(old['target'], target) > 1):
                if old.get('signature'):
                    stale.add(old['signature'])
                elif None in old['failed']:
                    stale.add(None)
            from copy import copy
            old = dict(task=task, target=copy(target), failed=set(), stale=stale, signature=None)
            client._quest_interaction_retry = old
        return old

    async def _recover_quest_x_direction(
        self, client, progress, target, zone, present_clients, *, failed_interaction=None
    ):
        """Search one measured A turn after an unchanged interaction, locally."""
        if progress is None or time.monotonic() < getattr(client, '_quest_x_turn_next_at', 0.0):
            return False

        async def state():
            if (not client.questing_status or await client.is_loading() or await client.in_battle()
                    or client.entity_detect_combat_status or getattr(client, 'refilling_potions', False)
                    or await client.zone_name() != zone):
                return 'stop'
            if await dialogue_visible():
                return 'ready'  # Temporary quest reads cannot end a real dialogue.
            current = await self._dungeon_quest_snapshot(client)
            if await dialogue_visible():
                return 'ready'
            if current is None:
                return 'stop'
            if current != progress:
                if retry is not None:
                    retry['failed'].clear()
                if not log_recovered(client, '已确认任务状态推进，结束 A 搜索'):
                    logger.bind(client=client.title).info('任务状态已推进，结束 A 搜索')
                logger.debug(f'Client {client.title} - stopping A search at {math.degrees(abs(rotation)):.1f}°.')
                return 'progress'
            current_target = await client.quest_position.position()
            current_position = await client.body.position()
            if await dialogue_visible():
                return 'ready'
            if (not all(math.isfinite(v) for v in (current_target.x, current_target.y, current_target.z))
                    or calc_Distance(current_target, target) > 1.0
                    or calc_Distance(current_position, target) >= 750.0):
                return 'stop'
            return 'ready'

        async def dialogue_visible():
            return (await self._npc_dialogue_visible(client)
                    or getattr(client, '_quest_dialogue_update_pending', False))

        rotation = 0.0
        retry = await self._quest_interaction_retry_state(client, target,
            task_snapshot=(progress[0], progress[1], target, zone))
        failed_interactions = retry['failed'] if retry is not None else set()
        pending_interaction = None
        try:
            first_interaction = failed_interaction or await self._quest_interaction_signature(client)
            if first_interaction:
                failed_interactions.add(first_interaction)
            outcome = await state()
            if outcome != 'ready':
                return outcome == 'progress'
            last_yaw = await client.body.yaw()
            if not math.isfinite(last_yaw):
                return False
            best_rotation = 0.0
            moved_at = time.monotonic()
            logger.debug(f'Client {client.title} - X unchanged; starting measured A search.')
            while True:
                outcome = await state()
                if outcome != 'ready':
                    return outcome == 'progress'
                if await is_spiral_door_open(client):
                    return False
                if await dialogue_visible():
                    # One existing dialogue pass, without recursively starting
                    # another turn or consuming the remaining search angle.
                    if await self.handle_npc_talking_quests(
                            client, present_clients, direction_search=False, before_progress=progress):
                        return True
                    outcome = await state()
                    if outcome != 'ready':
                        return outcome == 'progress'
                    if not await dialogue_visible() and pending_interaction:
                        failed_interactions.add(pending_interaction)
                        pending_interaction = None
                    last_yaw = await client.body.yaw()
                    if not math.isfinite(last_yaw):
                        return False
                    moved_at = time.monotonic()
                    continue
                if abs(rotation) >= math.tau:
                    client._quest_x_turn_next_at = time.monotonic() + 5.0
                    log_blocked(client, (progress[0], progress[1], None, zone), 'A 搜索一圈仍无任务进展', delay=5)
                    logger.debug(f'Client {client.title} - A search covered {math.degrees(abs(rotation)):.1f}°.')
                    return False
                if time.monotonic() - moved_at >= 2.0:
                    client._quest_x_turn_next_at = time.monotonic() + 5.0
                    log_blocked(client, (progress[0], progress[1], None, zone), 'A 搜索未确认转向进展', delay=5)
                    logger.debug(f'Client {client.title} - A search stopped at {math.degrees(abs(rotation)):.1f}°.')
                    return False
                # timed_send_key releases A in its finally block, including
                # cancellation. No key remains held during X/dialogue waits.
                await client.send_key(Keycode.A, .1)
                yaw = await client.body.yaw()
                if not math.isfinite(yaw):
                    return False
                if await dialogue_visible():
                    # A dialogue can also change the wizard's facing during
                    # this short pulse. Do not count its camera/forced turn.
                    last_yaw = yaw
                    moved_at = time.monotonic()
                    continue
                rotation += math.atan2(math.sin(yaw - last_yaw), math.cos(yaw - last_yaw))
                last_yaw = yaw
                if abs(rotation) > best_rotation + .0001:
                    best_rotation = abs(rotation)
                    moved_at = time.monotonic()
                outcome = await state()
                if outcome != 'ready':
                    return outcome == 'progress'
                if await dialogue_visible():
                    continue
                if await is_spiral_door_open(client):
                    return False  # Existing world-selector flow owns this UI.
                if await is_visible_by_path(client, npc_range_path):
                    prompt = await self.read_popup(client)
                    if is_dungeon_entry_prompt(prompt):
                        return False  # Keep multiplayer entry in its existing path.
                    signature = await self._quest_interaction_signature(client, prompt)
                    if signature is None or signature in failed_interactions:
                        continue
                    if not failed_interactions:
                        # No readable identity for the original failed X: this
                        # first complete read is a baseline, not a new object.
                        failed_interactions.add(signature)
                        continue
                    outcome = await state()
                    if outcome != 'ready':
                        return outcome == 'progress'
                    if await dialogue_visible():
                        continue
                    if not await is_visible_by_path(client, npc_range_path):
                        continue
                    pending_interaction = signature
                    await client.send_key(Keycode.X, .1)
                    for _ in range(3):
                        outcome = await state()
                        if outcome != 'ready':
                            return outcome == 'progress'
                        if await dialogue_visible():
                            break
                        await asyncio.sleep(.1)
                    if not await dialogue_visible():
                        failed_interactions.add(signature)
                        pending_interaction = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            client._quest_x_turn_next_at = time.monotonic() + 5.0
            log_exception(client, 'A 搜索无法完成', exc, level='DEBUG')
            log_blocked(client, (progress[0], progress[1], None, zone), f'A 搜索无法完成：{type(exc).__name__}: {exc}', delay=5)
            return False
        finally:
            logger.debug(f'Client {client.title} - A search finished at '
                         f'{math.degrees(abs(rotation)):.1f}°; keys released by timed input.')

    async def handle_npc_talking_quests(
        self, talking_client: Client, present_clients: list[Client], *, direction_search=True,
        before_progress=None, failed_interaction=None
    ) -> bool:
        """Wait for NPC dialogue and confirm the tracked quest actually advances."""

        async def read_quest_state():
            progress = await self._dungeon_quest_snapshot(talking_client)
            quest_text = progress[2] if progress is not None else await self.read_quest_txt(talking_client)
            try:
                quest_xyz = await talking_client.quest_position.position()
            except Exception:
                quest_xyz = None
            return quest_text, quest_xyz, progress

        def quest_state_changed(before, after) -> bool:
            before_text, before_xyz, before_progress = before
            after_text, after_xyz, after_progress = after
            if before_progress is not None and after_progress is not None and before_progress != after_progress:
                return True
            if after_text != before_text:
                return True
            if before_xyz is not None and after_xyz is not None:
                return calc_Distance(before_xyz, after_xyz) > 1.0
            return False

        dialogue_observed = (bool(getattr(talking_client, '_quest_dialogue_update_pending', False))
                             or await self._npc_dialogue_visible(talking_client))
        initial_state = await read_quest_state()
        initial_zone = await talking_client.zone_name()
        retry = (await self._quest_interaction_retry_state(talking_client, initial_state[1],
                    task_snapshot=(*initial_state[2][:2], initial_state[1], initial_zone))
                 if initial_state[1] is not None and initial_state[2] is not None else None)
        movement_failure = (_movement_failure_for_target(talking_client, initial_state[1],
            (*initial_state[2][:2], initial_state[1], initial_zone), initial_zone)
            if initial_state[1] is not None and initial_state[2] is not None else None)
        landing = getattr(talking_client, '_quest_tp_landing', None)

        def progress_confirmed():
            log_recovered(talking_client, '已确认任务状态推进')
            if retry is not None:
                retry['failed'].clear()
            if movement_failure is not None and getattr(talking_client, '_quest_tp_rejected', None) is movement_failure:
                talking_client._quest_tp_rejected = None
            if landing is not None and getattr(talking_client, '_quest_tp_landing', None) is landing:
                talking_client._quest_tp_landing = None
            return True

        async def changed_without_dialogue():
            nonlocal dialogue_observed
            if await self._npc_dialogue_visible(talking_client):
                dialogue_observed = True
                talking_client._quest_dialogue_update_pending = True
            if dialogue_observed:
                return False
            current = await read_quest_state()
            if await self._npc_dialogue_visible(talking_client):
                dialogue_observed = True
                talking_client._quest_dialogue_update_pending = True
                return False
            return quest_state_changed(initial_state, current)

        if await self._npc_dialogue_visible(talking_client):
            dialogue_observed = True
            talking_client._quest_dialogue_update_pending = True
        if (not dialogue_observed and before_progress is not None and initial_state[2] is not None
                and before_progress != initial_state[2]):
            return progress_confirmed()
        search_progress = initial_state[2]
        after_talking_paths = (
            exit_zafaria_class_picture_button,
            exit_pet_leveled_up_button_path,
            avalon_badge_exit_button_path,
        )

        for attempt in range(1, 4 if direction_search else 2):
            if (not talking_client.questing_status or await talking_client.is_loading()
                    or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone):
                return False
            if await changed_without_dialogue():
                return progress_confirmed()
            if attempt > 1:
                logger.debug(
                    f"Client {talking_client.title} - Quest did not update after "
                    f"dialogue; retrying NPC interaction ({attempt}/3)."
                )
                await asyncio.sleep(1.0)
                if (not talking_client.questing_status or await talking_client.is_loading()
                        or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone):
                    return False
                if await changed_without_dialogue():
                    return progress_confirmed()
                if not dialogue_observed:
                    failed_interaction = await self._quest_interaction_signature(talking_client)
                    await talking_client.send_key(Keycode.X, 0.15)

            # A missed X press previously left this loop waiting forever.  Give
            # the game a few seconds to display dialogue, then retry the input.
            appear_deadline = time.monotonic() + 4.0
            while (
                not dialogue_observed and not await self._npc_dialogue_visible(talking_client)
                and time.monotonic() < appear_deadline
            ):
                if (not talking_client.questing_status or await talking_client.is_loading()
                        or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone):
                    return False
                if await changed_without_dialogue():
                    return progress_confirmed()
                await asyncio.sleep(0.15)

            if not dialogue_observed and not await self._npc_dialogue_visible(talking_client):
                continue

            if (not talking_client.questing_status or await talking_client.is_loading()
                    or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone):
                return False

            logger.debug(
                f"Client {talking_client.title} - Detected dialogue; waiting "
                "for it and the quest update to finish."
            )
            dialogue_observed = True
            talking_client._quest_dialogue_update_pending = True

            # Dialogue may briefly close and immediately open another page or
            # reward window.  Require a quiet period before allowing movement.
            quiet_since = None
            quiet_complete = False
            dialogue_deadline = time.monotonic() + 60.0
            while time.monotonic() < dialogue_deadline:
                if (not talking_client.questing_status or await talking_client.is_loading()
                        or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone):
                    return False
                await close_npc_quest_menu(talking_client)
                if not await self._npc_dialogue_visible(talking_client):
                    if quiet_since is None:
                        quiet_since = time.monotonic()
                    elif time.monotonic() - quiet_since >= 3.0:
                        quiet_complete = True
                        break
                else:
                    quiet_since = None
                await asyncio.sleep(0.15)
            if not quiet_complete:
                return False  # Leave the existing task alive, still awaiting dialogue.

            await gather_owned(
                *[exit_menus(c, after_talking_paths) for c in present_clients]
            )

            # Reuse the existing update waits, confirming the complete read
            # twice after dialogue closes instead of accepting the first change.
            previous = await read_quest_state()
            for _ in range(2):
                await asyncio.sleep(1.25)
                if (not talking_client.questing_status or await talking_client.is_loading()
                        or await talking_client.in_battle() or await talking_client.zone_name() != initial_zone
                        or await self._npc_dialogue_visible(talking_client)):
                    return False
                current = await read_quest_state()
                if await self._npc_dialogue_visible(talking_client):
                    return False
                complete = (current[2] is not None and previous[2] is not None
                            and isinstance(current[2][1], int) and current[2][1] > 0
                            and current[1] is not None and previous[1] is not None
                            and all(math.isfinite(v) for point in (previous[1], current[1])
                                    for v in (point.x, point.y, point.z)))
                if complete and not quest_state_changed(previous, current):
                    talking_client._quest_dialogue_update_pending = False
                    dialogue_observed = False
                    if (quest_state_changed(initial_state, current)
                            or before_progress is not None and before_progress != current[2]):
                        logger.debug(f"Client {talking_client.title} - Stable quest update confirmed.")
                        return progress_confirmed()
                    break
                previous = current
            else:
                return False  # Unstable/incomplete reads stay in the dialogue wait.

        if (retry is not None and (direction_search or failed_interaction is not None)
                and not await self._npc_dialogue_visible(talking_client)
                and not getattr(talking_client, '_quest_dialogue_update_pending', False)
                and _same_movement_task(retry['task'], await _movement_task_snapshot(talking_client))):
            retry['failed'].add(failed_interaction)
        if not direction_search:
            return False
        logger.debug(
            f"Client {talking_client.title} - Could not confirm quest acceptance "
            "after 3 attempts; searching with A near the NPC."
        )
        if initial_state[1] is None:
            return False
        log_blocked(talking_client, retry['task'] if retry is not None else None,
            'NPC 三次交互仍未确认任务推进，开始 A 搜索')
        return await self._recover_quest_x_direction(
            talking_client, search_progress, initial_state[1], initial_zone, present_clients,
            failed_interaction=failed_interaction)

    async def _defer_internal_quest_failure(self, client, reason, *, kind, delay,
                                            task=None, target=None, task_client=None):
        """End this attempt, preserving the user's flag and the existing TP failure."""
        source = task_client or client
        snapshot = task if task is not None else await _movement_task_snapshot(source)
        try:
            position = copy(await _movement_position(client))
            zone = await client.zone_name()
        except (MemoryReadError, MemoryInvalidated, ExceptionalTimeout, HookNotActive, ValueError):
            position = None
            zone = None
        session = dungeon_session_for(client)
        release = session.transition if session is not None else None
        state = dict(task=snapshot, position=position, zone=zone, source=source,
            target=copy(target if target is not None else snapshot[2]) if target is not None or snapshot else None,
            shared=release.get('movement_task') if release else None,
            progress=await self._dungeon_quest_snapshot(source), kind=kind,
            control=getattr(client, '_quest_movement_control', None),
            retry_at=time.monotonic()+delay, reason=reason)
        previous = getattr(client, '_quest_internal_block_reason', '')
        client._quest_internal_failure_wait = state
        client._quest_internal_block_reason = reason
        log_blocked(client, snapshot, reason, delay=delay)

    async def _wait_internal_quest_failure(self, client):
        state = getattr(client, '_quest_internal_failure_wait', None)
        if not isinstance(state, dict):
            return False
        if not getattr(client, 'questing_status', True):
            return True  # A retry must never undo an explicit user stop.
        log_blocked(client, state['task'], state['reason'])
        try:
            if (await client.is_loading() or await client.in_battle()
                    or any(getattr(client, flag, False) for flag in (
                        'refilling_potions', 'quest_recovery_owner', 'quest_party_safe_return_active',
                        'post_combat_movement_active', 'feeding_pet_status', 'entity_detect_combat_status'))):
                await asyncio.sleep(.5)
                return True  # Keep the saved task/budget through temporary control loss.
            if (await self._npc_dialogue_visible(client)
                    or getattr(client, '_quest_dialogue_update_pending', False)):
                return False  # Keep the wait; let the existing dialogue handler settle.
        except (MemoryReadError, MemoryInvalidated, ExceptionalTimeout):
            await asyncio.sleep(.5)
            return True
        current = await _movement_task_snapshot(state['source'])
        progress = await self._dungeon_quest_snapshot(state['source'])
        changed = (current is not None and state['task'] is not None
                   and not _same_movement_task(state['task'], current))
        changed |= (progress is not None and state['progress'] is not None
                    and progress != state['progress'])
        session = dungeon_session_for(client)
        release = session.transition if session is not None else None
        shared = release.get('movement_task') if release else None
        changed |= (shared is not None and state['shared'] is not None
                    and not _same_movement_task(state['shared'], shared))
        try:
            zone = await client.zone_name()
            changed |= state['zone'] is not None and zone != state['zone']
        except (MemoryReadError, MemoryInvalidated, ExceptionalTimeout):
            zone = None
        if (not changed and current is not None and state['position'] is not None
                and state['target'] is not None):
            try:
                pos = await _movement_position(client)
                changed = (calc_Distance(pos, state['position']) > 5
                    and calc_Distance(pos, state['target']) < calc_Distance(state['position'], state['target'])-1)
            except (MemoryReadError, MemoryInvalidated, ExceptionalTimeout, HookNotActive, ValueError):
                pass
        changed |= state.get('control') != getattr(client, '_quest_movement_control', None)
        remaining = state['retry_at']-time.monotonic()
        if not changed and remaining > 0:
            await asyncio.sleep(min(.5, remaining))
            return True  # Poll actual progress; no input or new worker during cooldown.
        failure = (_movement_failure_for_target(client, state['target'], current, zone)
                   if state['target'] is not None else None)
        if (failure is not None and not failure.get('pending')
                and failure.get('origin_identity_waited') and failure.get('origin_signature') is None):
            failure['origin_identity_recovered'] = True
        if state['kind'] == 'exhausted' and current is not None and not changed:
            failure = _movement_failure_for_target(client, state['target'], current, zone)
            if failure is not None and not failure.get('pending'):
                failure.update(walks=0, announced=False)
                # Keep origin/rejected/ready: a new finite walk batch cannot
                # turn a rejected TP or an origin popup into successful arrival.
        client._quest_internal_failure_wait = None
        client._quest_internal_block_reason = ''
        return False

    async def _quest_target_first_move_state(self, client, xyz, leader_client=None):
        """Confirm changes using existing snapshots; retain one attempt per client."""
        try:
            await _movement_body(client)
        except (MemoryReadError, HookNotActive, ValueError):
            await self._defer_internal_quest_failure(client, '受控对象暂时不可读',
                kind='movement', delay=2, target=xyz, task_client=leader_client)
            return None
        async def observe():
            task = await _movement_task_snapshot(leader_client or client)
            if (task is None or type(task[0]) is not int or task[0] <= 0
                    or type(task[1]) is not int
                    or not all(math.isfinite(v) for v in (*task[2], *xyz))):
                return None
            session = dungeon_session_for(client)
            release = session.transition if session is not None else None
            shared = release.get('movement_task') if release else None
            if shared is not None and calc_Distance(shared[2], xyz) > 1:
                shared = None
            # Hitters use their own task for movement cancellation, but a
            # confirmed shared Goal change must still count at identical XYZ.
            if session is None and leader_client is None and calc_Distance(task[2], xyz) > 1:
                return None  # The caller still holds an old/transient arrow.
            return dict(task=task, target=copy(xyz), shared=shared)

        def same(first, second):
            return (first is not None and second is not None
                    and _same_movement_task(first['task'], second['task'])
                    and calc_Distance(first['target'], second['target']) <= 1
                    and (first['shared'] is None or second['shared'] is None
                         or _same_movement_task(first['shared'], second['shared'])))

        current = await observe()
        if current is None:
            return None  # Do not replace a good baseline with a failed read.
        state = getattr(client, '_quest_target_first_move', None)
        if state is None:
            current.update(pending=False, active=False)
            client._quest_target_first_move = current
            return current  # First observation is a baseline, not a goal change.
        if same(state, current):
            if state['shared'] is None and current['shared'] is not None:
                state['shared'] = current['shared']
            return state  # Preserve the anchor and consumed bit through jitter.
        await asyncio.sleep(.75)  # Match the existing shared-target confirmation.
        if not same(current, await observe()):
            return None
        latest = getattr(client, '_quest_target_first_move', None)
        if same(latest, current):
            return latest  # Another observation must not reset a consumed attempt.
        if (_same_movement_task(state['task'], current['task'])
                and calc_Distance(state['target'], current['target']) <= 1
                and state['shared'] is not None and current['shared'] is not None
                and not _same_movement_task(state['shared'], current['shared'])):
            # A hitter can retain its own source Goal at the same XYZ while
            # the released quester Goal advances. Completed old-Goal records
            # belong to that old shared target, not this new first attempt.
            for attr in ('_quest_tp_rejected', '_quest_tp_landing'):
                record = getattr(client, attr, None)
                if (isinstance(record, dict) and not record.get('pending')
                        and _movement_failure_for_target(client, xyz, current['task'],
                            await client.zone_name(), record=record) is not None):
                    setattr(client, attr, None)
        current.update(pending=True, active=False)
        client._quest_target_first_move = current
        logger.debug(f"[quest_target] {client.title}: confirmed new target; first movement pending "
                     f"task={current['task']} target={xyz}")
        return current

    async def quest_interaction_ready(self, client, xyz, leader_client=None, *, task_before=None):
        if not getattr(client, 'questing_status', True) or await client.is_loading() or await client.in_battle():
            return False
        if (await self._npc_dialogue_visible(client)
                or getattr(client, '_quest_dialogue_update_pending', False)):
            return task_before is None  # Movement yields; a new X cannot interrupt real dialogue.
        first_move = await self._quest_target_first_move_state(client, xyz, leader_client)
        if first_move is None:
            return False
        if first_move['pending'] and any(getattr(client, flag, False) for flag in (
                'refilling_potions', 'quest_recovery_owner', 'quest_party_safe_return_active',
                'post_combat_movement_active', 'feeding_pet_status', 'entity_detect_combat_status')):
            return False
        if first_move['pending'] and not first_move['active']:
            try:
                position = await _movement_position(client)
            except (MemoryReadError, HookNotActive, ValueError):
                return False
            if (calc_Distance(position, xyz) > 5
                    or await _movement_failure_blocks_interaction(client, xyz, first_move['task'])):
                return False
            first_move['pending'] = False  # Existing actual-target arrival rule.
        if task_before is not None and not _same_movement_task(
                task_before, await _movement_task_snapshot(leader_client or client)):
            logger.debug(f"[quest_interaction] {client.title}: reason=task-changed-before-interaction "
                         f"previous={task_before} target={xyz}")
            return False
        retry = await self._quest_interaction_retry_state(client, xyz, leader_client)
        signature = await self._quest_interaction_signature(client)
        if retry is not None:
            if signature is not None and None in retry['failed']:
                retry['failed'].discard(None)
                retry['failed'].add(signature)  # First readable identity is a baseline, not a new object.
            if signature is not None and None in retry['stale']:
                retry['stale'].discard(None)
                retry['stale'].add(signature)
            retry['signature'] = signature
        if (await self._npc_dialogue_visible(client)
                or getattr(client, '_quest_dialogue_update_pending', False)):
            return task_before is None
        if task_before is not None and not _same_movement_task(
                task_before, await _movement_task_snapshot(leader_client or client)):
            logger.debug(f"[quest_interaction] {client.title}: reason=task-changed-during-interaction-read "
                         f"previous={task_before} target={xyz}")
            return False
        ready = await _movement_interaction_handoff(client, xyz,
            retry['task'] if retry is not None else None, signature=signature,
            range_visible=await is_visible_by_path(client, npc_range_path),
            entry_visible=await is_visible_by_path(client, team_up_button_path))
        failure = _movement_failure_for_target(client, xyz,
            retry['task'] if retry is not None else None, await client.zone_name())
        if not ready and failure is not None and failure.get('interaction_issue'):
            if not getattr(client, '_quest_internal_failure_wait', None):
                await self._defer_internal_quest_failure(client, failure['interaction_issue'],
                    kind='movement', delay=2, target=xyz, task_client=leader_client)
        if ready and task_before is not None and not _same_movement_task(
                task_before, await _movement_task_snapshot(leader_client or client)):
            return False
        if ready:
            log_recovered(client, '已确认可交互状态，交回原交互流程')
        return ready

    async def move_until_quest_interaction(self, client, xyz, leader_client=None):
        # Let the existing interaction handler press X once movement has stopped.
        if not getattr(client, 'questing_status', True) or await self._wait_internal_quest_failure(client):
            return 'blocked'
        first_move = await self._quest_target_first_move_state(client, xyz, leader_client)
        if first_move is None:
            return 'blocked'
        if not first_move['pending'] and await self.quest_interaction_ready(client, xyz, leader_client):
            return 'ready'
        task_before = await _movement_task_snapshot(leader_client or client)
        zone_before = await client.zone_name()
        if not _same_movement_task(first_move['task'], task_before):
            return 'changed' if task_before is not None else 'blocked'

        control = getattr(client, '_quest_movement_control', None)
        async def stopped():
            if any(getattr(client, flag, False) for flag in (
                    'refilling_potions', 'quest_recovery_owner', 'quest_party_safe_return_active',
                    'post_combat_movement_active', 'feeding_pet_status', 'entity_detect_combat_status')):
                return 'movement control unavailable'
            return await _movement_stop_reason(client, xyz, zone_before,
                task_before, leader_client, interaction=False, control=control)

        if await stopped():
            return 'blocked'  # Waiting does not consume the confirmed first move.
        try:
            position = await _movement_position(client)
        except (MemoryReadError, HookNotActive, ValueError):
            await self._defer_internal_quest_failure(client, '受控位置暂时不可读',
                kind='movement', delay=2, target=xyz, task_client=leader_client)
            return 'blocked'
        if (first_move['pending'] and calc_Distance(position, xyz) <= 5
                and not await _movement_failure_blocks_interaction(client, xyz, task_before)):
            first_move['pending'] = False
            return 'ready'

        async def watch_movement_state():
            while True:
                await asyncio.sleep(.1)
                if await stopped():
                    return

        first_move['active'] = first_move['pending']
        previous_failure = getattr(client, '_quest_tp_rejected', None)
        movement = asyncio.create_task(collision_tp(client, xyz, leader_client=leader_client))
        watcher = asyncio.create_task(watch_movement_state())
        result = None
        try:
            done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
            result = None
            if watcher in done:
                await watcher
                movement.cancel()
                current = await _movement_task_snapshot(leader_client or client)
                result = ('changed' if current is not None and task_before is not None
                          and not _same_movement_task(task_before, current) else 'blocked')
            if movement in done:
                result = await movement
            if result == 'exhausted':
                if not _same_movement_task(task_before, await _movement_task_snapshot(leader_client or client)):
                    return 'changed'  # An exhausted old goal must not stop a newly advanced task.
                await self._defer_internal_quest_failure(client,
                    '3次补走后仍未确认可交互位置', kind='exhausted', delay=10,
                    task=task_before, target=xyz, task_client=leader_client)
            elif result == 'failed':
                if _same_movement_task(task_before, await _movement_task_snapshot(leader_client or client)):
                    await self._defer_internal_quest_failure(client,
                        '任务导航或补走尚未成功', kind='movement', delay=2,
                        task=task_before, target=xyz, task_client=leader_client)
            return result
        finally:
            movement.cancel()
            watcher.cancel()
            await gather_owned(movement, watcher, return_exceptions=True)
            if first_move['active']:
                # Existing validator records prove that input was attempted,
                # including cancellation during its settle wait. Pre-input
                # cancellation/state blocking leaves the first move pending.
                attempted = False
                input_blocked = False
                for attr in ('_quest_tp_rejected', '_quest_tp_landing'):
                    record = getattr(client, attr, None)
                    if (not isinstance(record, dict) or _movement_failure_for_target(
                            client, xyz, task_before, zone_before, record=record) is None):
                        continue
                    attempted |= bool(record.get('pending') or record.get('rejected')
                                      or record.get('confirmed'))
                    input_blocked |= bool(record.get('input_blocked'))
                    if (attr == '_quest_tp_rejected' and record is not previous_failure
                            and not attempted and not record.get('walks')):
                        # The flow can create its unverified record before the
                        # final safety check/input. Discard only that new empty
                        # record so a deferred first TP is not mistaken for failure.
                        client._quest_tp_rejected = previous_failure
                if attempted or not input_blocked and result in ('ready', 'failed', 'exhausted'):
                    first_move['pending'] = False
                first_move['active'] = False

    async def teleport_to_quest_target(self, client, xyz, leader_client=None):
        zone = await client.zone_name()
        try:
            position = await _movement_position(client)
        except (MemoryReadError, HookNotActive, ValueError):
            await self._defer_internal_quest_failure(client, '受控位置暂时不可读',
                kind='movement', delay=2, target=xyz, task_client=leader_client)
            return 'blocked'
        control = getattr(client, '_quest_movement_control', None)
        key = id(client)
        floating_exit = zone == 'Celestia/CL_Z05_The_Floating_Land'
        crystal_exit = zone == 'DragonSpire/DS_A3_Kings/Interiors/DS_Crystal_T9'
        special_targets = {
            'Krokotopia/KI_Selenopolis/Interiors/KI_Z04101_BlendedGrove': XYZ(3124.726, -4718.231, 36.200),
            'Celestia/CL_Z09_Science_Center': XYZ(-1067.512, 343.759, -449.800),
            'Celestia/Interiors/CL_Z10i3_Kingdom_Of_The_Crabs': XYZ(3184.987, -9931.280, -1014.007),
        }
        special_exit = zone in special_targets
        stuck = XYZ(6420.986, -6956.173, -399.699)
        if (zone != "Krokotopia/KT_WorldTeleporter" and not floating_exit and not crystal_exit and not special_exit
                or floating_exit and calc_Distance(position, stuck) > 150):
            self._krok_exit_watch.pop(key, None)
            await self._remember_door_approach(client, xyz)
            result = await self.move_until_quest_interaction(client, xyz, leader_client=leader_client)
            await self._remember_door_approach(client, xyz, landing=True)
            return result

        async def interaction_pending():
            try:
                return (await is_spiral_door_open(client)
                        or (await is_visible_by_path(client, npc_range_path)
                            and calc_Distance(await _movement_position(client), xyz) < 750)
                        or await is_visible_by_path(client, exit_dungeon_path))
            except (MemoryReadError, HookNotActive, ValueError):
                return True  # No movement or exit input without a current position.

        # Give world-gate interaction priority over both movement and END.
        if await client.is_loading() or await interaction_pending() or not await is_free(client):
            self._krok_exit_watch.pop(key, None)
            return

        objective = await get_quest_name(leader_client or client)
        target = (xyz.x, xyz.y, xyz.z)
        if await _movement_control_changed(client, control):
            self._krok_exit_watch.pop(key, None)
            return 'blocked'
        before = await _movement_position(client)
        state = self._krok_exit_watch.get(key)
        if state is None or state.get('zone') != zone or state['objective'] != objective or state['target'] != target or state.get('control') != control:
            state = dict(zone=zone, objective=objective, target=target, anchor=before,
                         since=time.monotonic(), attempts=0, end_sent=False, control=control)
            self._krok_exit_watch[key] = state

        transition = [False]
        async def watch_transition():
            while True:
                if (await _movement_control_changed(client, control) or await client.is_loading() or await client.zone_name() != zone
                        or (crystal_exit or special_exit) and (await interaction_pending() or not await is_free(client))):
                    transition[0] = True
                    return
                await asyncio.sleep(.1)

        # Observe loading during the existing teleport, without adding a dwell sleep.
        watcher = asyncio.create_task(watch_transition())
        try:
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

        if (await _movement_control_changed(client, control) or transition[0] or await client.is_loading() or await client.zone_name() != zone
                or await interaction_pending() or not await is_free(client)
                or await get_quest_name(leader_client or client) != objective):
            self._krok_exit_watch.pop(key, None)
            return

        after = await _movement_position(client)
        if await _movement_control_changed(client, control):
            self._krok_exit_watch.pop(key, None)
            return 'blocked'
        if floating_exit and calc_Distance(after, stuck) > 150:
            self._krok_exit_watch.pop(key, None)
            return
        # Both endpoints must stay within the same small area across attempts.
        if max(calc_Distance(before, state['anchor']),
               calc_Distance(after, state['anchor'])) > 100:
            state.update(anchor=after, since=time.monotonic(), attempts=0)
            return
        state['attempts'] += 1
        if not objective or state['end_sent'] or state['attempts'] < 3 or time.monotonic() - state['since'] < 10:
            return

        state['end_sent'] = True
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
                        while await get_quest_name(leader_client or client) == objective:
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

    async def teleport_to_quest(self, hitting_client: str, follower_clients: list[Client]):
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
        await gather_owned(*[Quester(c, [c], None).handle_pending_dungeon_confirmation()
                             for c in self.clients])

        await gather_owned(*[self.leader_wait_for_free(p) for p in self.clients])

        if await is_free_leader_questing(self.current_leader_client):
            for c in self.clients:
                while await c.is_loading():
                    await asyncio.sleep(0.1)

            try:
                current_pos = await _movement_position(self.current_leader_client)
            except (MemoryReadError, HookNotActive, ValueError):
                await self._defer_internal_quest_failure(self.current_leader_client,
                    '受控位置暂时不可读', kind='movement', delay=2)
                return
            control = getattr(self.current_leader_client, '_quest_movement_control', None)

            leader_client_objective_xyz = await self.current_leader_client.quest_position.position()
            if await is_visible_by_path(self.current_leader_client, npc_range_path) and calc_Distance(leader_client_objective_xyz, current_pos) < 750.0:
                if await _movement_control_changed(self.current_leader_client, control):
                    return
                if await _movement_failure_blocks_interaction(self.current_leader_client,
                        leader_client_objective_xyz, await _movement_task_snapshot(self.current_leader_client)):
                    failure = getattr(self.current_leader_client, '_quest_tp_rejected', None)
                    if isinstance(failure, dict) and failure.get('interaction_issue'):
                        await self._defer_internal_quest_failure(self.current_leader_client,
                            failure['interaction_issue'], kind='movement', delay=2,
                            target=leader_client_objective_xyz)
                    return
                # await self.handle_interactibles(current_leader_client)

                # Handles interactables
                sigil_msg_check = await self.read_popup(self.current_leader_client)
                if await self._maybe_photo_giant_vat(self.current_leader_client, sigil_msg_check):
                    return
                if is_dungeon_entry_prompt(sigil_msg_check):
                    while is_dungeon_entry_prompt(sigil_msg_check):
                        logger.debug('Entering dungeon')
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                        await asyncio.sleep(1.0)
                        for c in self.clients:
                            if await is_visible_by_path(c, dungeon_warning_path):
                                await c.send_key(Keycode.ENTER, 0.1)

                        sigil_msg_check = await self.read_popup(self.current_leader_client)

                    await self.handle_dungeon_entry(questing_friend_tp, follower_clients)
                else:
                    msg = sigil_msg_check.lower()
                    if interaction_kind(msg) == "talk":
                        before_progress = await self._dungeon_quest_snapshot(self.current_leader_client)
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                        logger.debug('Talking to NPC')
                        quest_updated = await self.handle_npc_talking_quests(
                            self.current_leader_client, self.clients, before_progress=before_progress
                        )
                        if not quest_updated:
                            await asyncio.sleep(2.0)
                            return

                    elif interaction_kind(msg) in {"ride", "teleport"}:
                        await self.current_leader_client.send_key(Keycode.X, 0.1)
                        await asyncio.sleep(1.0)
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                    else:
                        search_progress = await self._dungeon_quest_snapshot(self.current_leader_client)
                        search_zone = await self.current_leader_client.zone_name()
                        await gather_owned(*[p.send_key(Keycode.X, 0.1) for p in self.clients])
                        await asyncio.sleep(.75)
                        if await self._recover_quest_x_direction(
                            self.current_leader_client, search_progress, leader_client_objective_xyz,
                            search_zone, self.clients):
                            return

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

            for c in self.clients:
                if await is_visible_by_path(c, missing_area_path):
                    # Handles when an area hasn't been downloaded yet
                    while not await is_visible_by_path(c, missing_area_retry_path):
                        if await closed_dungeon_popup(c, dismiss=True):
                            return
                        await asyncio.sleep(0.1)
                    await click_window_by_path(c, missing_area_retry_path, True)

        await asyncio.sleep(0.7)

    async def handle_repeated_normal_quest_failures(self, last_leader_pid, last_leader_zone: str, iterations_since_last_quest_change: int):
        # In its current form, this attempts to correct for situations where you are standing too close to an NPC or sigil, and need to move away then return to get the popup to talk / enter
        # if after a certain number of loops we've failed to move on from our quest, something is wrong, and we try to correct for this in case this is the cause

        # this could be expanded to matching entity name to mob name for situations like the Labyrinth where the game tells you to defeat an enemy but gives you an inaccurate location

        # logger.info('ITERATIONS: ' + str(iterations_since_last_quest_change))
        if last_leader_pid == self.current_leader_client.process_id and last_leader_zone == await self.current_leader_client.zone_name():
            # more serious than 5 - the issue may be that the XYZ is just incorrect
            # we should scan for entities, teleport to the one that is closest to the given XYZ
            # if that fails, try the next one
            # if iterations_since_last_quest_change == ?:

            # first check - most likely scenario (and easiest to solve) is that we just need to move away and back towards the NPC or sigil
            if iterations_since_last_quest_change >= 5:
                location = await self.current_leader_client.body.position()
                await gather_owned(*[p.teleport(XYZ(location.x + 500, location.y, location.z - 1500)) for p in self.clients])
                await asyncio.sleep(2.0)

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
        leader_pos = await self.current_leader_client.body.position()
        teleported = False
        for c in self.clients:
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
                await self.heal_and_handle_potions()

                logger.debug('All questing clients have high energy, training pets on all clients.')
                await gather_owned(*[auto_pet(c, ignore_pet_level_up, play_dance_game, questing=True) for c in self.clients])

    async def leader_wait_for_free(self, p: Client):
        while not await is_free_leader_questing(p):
            await asyncio.sleep(.1)

    # TODO: Slay the beast
    async def auto_quest_leader(self, questing_friend_tp: bool, gear_switching_in_solo_zones: bool, hitting_client, ignore_pet_level_up: bool, play_dance_game: bool):
        from src.mainline_progress import log_mainline_progress
        await log_mainline_progress(self.client)
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

        # information needed for handle_repeated_normal_quest_failures()
        iterations_since_last_quest_change = 0
        leader_last_full_quest = await get_quest_name(self.current_leader_client)
        last_leader_pid = self.current_leader_client.process_id
        last_leader_zone = await self.current_leader_client.zone_name()

        # main loop
        while self.client.questing_status:
            await asyncio.sleep(.4)
            if await self._wait_internal_quest_failure(self.current_leader_client):
                continue

            # in case client(s) in combat and the questing loop continued anyway
            await gather_owned(*[self.leader_wait_for_free(p) for p in self.clients])

            # Collect wisps, use potions, or get potions if necessary
            await self.heal_and_handle_potions()

            # if dungeon recall button is visible, click it
            await self.handle_dungeon_recall(follower_clients=follower_clients)

            # if auto pet is enabled and all questing clients have leveled up, send all to pavilion and train pets on ALL clients
            await self.auto_pet_questing(questing_clients, ignore_pet_level_up, play_dance_game)

            # dynamically change leader client when follower's get left behind
            # if there were previously clients on the same quest check for quest objective change on all clients
            follower_clients, client_quests = await self.determine_new_leader_and_followers(client_quests, questing_clients, follower_clients)

            # handle circumstances where any follower client is not in the same zone as the leader client
            # keep in mind, the previous leader client may now be a follower client since we have just called determine_new_leader_and_followers()
            await self.handle_zone_correction(maybe_solo_zone, questing_friend_tp, gear_switching_in_solo_zones)

            # Recovery is an entry gate only. Selected/Finder navigation uses the
            # same movement and interaction code below, after potion handling.
            from src.mainline_recovery import maybe_recover_mainline
            await log_mainline_progress(self.current_leader_client)
            if await maybe_recover_mainline(self.current_leader_client):
                continue

            if await is_free_leader_questing(self.current_leader_client):
                quest_xyz = await self.current_leader_client.quest_position.position()
                distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))

                # we are almost certainly not on a collect quest
                if distance > 1:
                    # attempt to fix cases where we loop through several times without completing a quest
                    await self.handle_repeated_normal_quest_failures(last_leader_pid, last_leader_zone, iterations_since_last_quest_change)

                    await self.teleport_to_quest(hitting_client, follower_clients)

                    await self.handle_normal_quests(follower_clients, questing_friend_tp)
                else:
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

            # keep track of how many loops we've gone through without changing our quest
            # if leader happened to change, reset the counter
            leader_full_current_quest = await get_quest_name(self.current_leader_client)
            # quest hasn't changed, new leader has not been assigned, and zone hasn't changed
            # this means we have failed to complete the last single quest objective
            if leader_full_current_quest == leader_last_full_quest and last_leader_pid == self.current_leader_client.process_id and last_leader_zone == await self.current_leader_client.zone_name():
                iterations_since_last_quest_change += 1
            else:
                leader_last_full_quest = await get_quest_name(self.current_leader_client)
                last_leader_pid = self.current_leader_client.process_id
                last_leader_zone = await self.current_leader_client.zone_name()
                iterations_since_last_quest_change = 0

    async def handle_pending_dungeon_confirmation(self) -> bool:
        """Confirm a dungeon transition modal even if it appeared late."""
        async def confirmation():
            modal = await get_window_from_path(self.client.root_window, [exit_dungeon_path[0]])
            if not modal or not await modal.is_visible():
                return None
            captions = await modal.get_windows_with_name('CaptionText')
            exiting = any([matches_text(await read_control_text(c), 'GUI_InstanceExit')
                           for c in captions if await c.is_visible()])
            layout = exit_dungeon_path[1:-1]
            if exiting:
                # Installed Root.wad GUI/MessageBoxWindow.gui: the Yes/No
                # layout uses leftButton (GG_ACCEPT, Y) and rightButton (No).
                # Only the verified exit caption enables this new Yes path.
                yes = await get_window_from_path(modal, [*layout, 'leftButton'])
                if yes and await yes.is_visible():
                    return modal, yes, True
            # Preserve the existing centerButton transition handling.
            center = await get_window_from_path(modal, [*layout, 'centerButton'])
            if center and await center.is_visible():
                return modal, center, exiting
            return None

        pending = await confirmation()
        if pending is None:
            return False
        zone_before = await self.client.zone_name()
        exiting = pending[2]
        deadline = time.monotonic() + 15.0

        async def click_confirmation():
            async with self.client.mouse_handler:
                # Resolve again after input acquisition; never confirm a replacement.
                current = await confirmation()
                if (current is None or current[2] != exiting
                        or await self.client.is_loading()):
                    return None
                modal, button, _ = current
                await self.client.mouse_handler.click_window(button)
                return modal

        try:
            modal = (await asyncio.wait_for(click_confirmation(), timeout=5.0)
                     if exiting else await click_confirmation())
        except asyncio.TimeoutError:
            logger.debug(f"Client {self.client.title} - dungeon exit input timed out; exit unconfirmed.")
            return False
        if modal is None:
            return False
        logger.debug(
            f"Client {self.client.title} - confirming pending dungeon transition."
        )

        # Do not wait forever if this was a slow or stale message box.  The
        # next quest iteration can retry the click if it remains visible.
        saw_loading = False
        confirmed = False
        attempts = 1
        next_click_at = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if await self.client.is_loading():
                saw_loading = True
                await asyncio.sleep(0.1)
                continue

            current_zone = await self.client.zone_name()
            if current_zone != zone_before:
                confirmed = True
                if exiting:
                    record_dungeon_exit(self.client, zone_before, current_zone)
                if getattr(self.client, "quest_party_hitters", []):
                    self.client.quest_party_quest_worker_zone = current_zone
                    self.client.quest_party_probe_pending = True
                break

            if saw_loading or (not await modal.is_visible() if exiting
                               else not await is_visible_by_path(self.client, exit_dungeon_path)):
                confirmed = True
                break
            if exiting and attempts < 3 and time.monotonic() >= next_click_at:
                try:
                    current = await asyncio.wait_for(click_confirmation(),
                        timeout=min(5.0, max(.01, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    break
                if current is None:
                    break
                modal = current
                attempts += 1
                next_click_at = time.monotonic() + 1.5
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.5)
        if exiting and not (confirmed or saw_loading):
            logger.debug(f"Client {self.client.title} - dungeon exit confirmation still visible; exit unconfirmed.")
            return False
        return True

    async def handle_questing_zone_change(self):
        if await self.handle_pending_dungeon_confirmation():
            return
        while await self.client.is_loading():
            await asyncio.sleep(.1)

    async def prepare_party_dungeon_entry(self) -> list[Client]:
        """Move assigned hitters to the entrance before everyone presses X."""
        entry_clients = list(self.clients)
        session = dungeon_session_for(self.client)
        finishing = bool(session and session.confirmed and id(self.client) not in session.exited)
        if finishing:
            entry_clients = [c for c in entry_clients if id(c) not in session.exited]
        assigned_hitters = [
            hitter
            for hitter in getattr(self.client, "quest_party_hitters", [])
            if not finishing or id(hitter) not in session.exited
        ]
        if not assigned_hitters:
            if finishing and not await quest_party_members_ready(self.client):
                return []
            return entry_clients
        if portal_kind(await get_popup_title(self.client)) == 'world_gate':
            return [self.client]

        deadline = time.monotonic() + 4.0
        pending = list(assigned_hitters)
        while pending and time.monotonic() < deadline:
            quester_zone = await self.client.zone_name()
            quester_position = await self.client.body.position()
            for hitter in list(pending):
                try:
                    if (
                        hitter.questing_status and hitter.is_running()
                        and not getattr(hitter, 'refilling_potions', False)
                        and not getattr(hitter, 'quest_recovery_owner', None)
                        and not getattr(hitter, 'quest_party_safe_return_active', False)
                        and not await hitter.is_loading()
                        and await hitter.zone_name() == quester_zone
                        and await is_free(hitter)
                        and await clients_share_live_area(self.client, hitter)
                    ):
                        await hitter.teleport(quester_position)
                        if (not await clients_share_live_area(self.client, hitter)
                                or calc_Distance(await hitter.body.position(), quester_position) >= 750):
                            continue
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
            return []
        if len(entry_clients) > len(self.clients):
            logger.info(
                "Synchronized dungeon entry: "
                + ", ".join(client.title for client in entry_clients)
            )
            await asyncio.sleep(0.5)
        if not await quest_party_members_ready(self.client):
            return []
        entrance = await self.client.body.position()
        if any([calc_Distance(await c.body.position(), entrance) >= 750
                for c in entry_clients]):
            return []
        return entry_clients

    async def _quest_party_probe_blocks_movement(self) -> bool:
        """Pause quest movement until the assigned hitter finishes its zone probe."""
        session = await observe_dungeon_session(self.client)
        if await self.client.is_loading():
            return True
        # The follower can cross first. Finish this client's recorded source
        # entrance in its existing worker before waiting for whole-group proof.
        if session and await self.recover_dungeon_transition():
            return True
        if (session and not session.confirmed and session.entry_evidence
                and id(self.client) in session.entry_zones):
            # The old primary probe can finish before a second hitter arrives.
            # Keep the quester at the recorded entrance until the group is proven.
            return True
        if session and session.confirmed:
            if (id(self.client) in session.potion_areas
                    or getattr(self.client, 'refilling_potions', False)
                    or getattr(self.client, 'quest_recovery_owner', None)):
                return True
            if (id(self.client) not in session.exited
                    and all(id(c) in session.exited for c in session.members[1:])):
                # Exited hitters no longer participate in this dungeon's gate;
                # the last quester must be able to finish its own exit.
                if not await quest_party_members_ready(self.client):
                    return True
                if getattr(self.client, 'quest_party_probe_pending', False):
                    logger.debug(f"Client {self.client.title} - releasing stale probe: "
                                 "all assigned hitters have confirmed exits; quester is ready.")
                self.client.quest_party_quest_worker_zone = await self.client.zone_name()
                self.client.quest_party_probe_pending = False
                return False
            if any(getattr(c, 'quest_party_safe_return_active', False)
                   or getattr(c, 'post_combat_movement_active', False)
                   for c in session.members if id(c) not in session.exited):
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

        if (session and session.confirmed and id(self.client) not in session.exited
                and not await quest_party_members_ready(self.client, zone=current_zone)):
            self.client.quest_party_probe_pending = True
        return bool(getattr(self.client, "quest_party_probe_pending", False))

    async def _remember_door_approach(self, client, target, *, landing=False):
        """Keep a local approach direction/budget without changing ordinary TP."""
        from copy import copy
        try:
            snapshot = await _movement_task_snapshot(client)
            progress = await self._dungeon_quest_snapshot(client)
            await observe_party_area(client)
            token = await _party_area_token(client)
            pos = await _movement_position(client)
            if (snapshot is None or progress is None
                    or not all(math.isfinite(v) for p in (pos, target) for v in (p.x, p.y, p.z))
                    or any(type(v) is not int or v <= 0 for v in token[1:3])):
                return
            session = dungeon_session_for(client)
            record = getattr(client, '_quest_door_approach', None)
            same_source = (isinstance(record, dict) and record['session'] is session
                    and record['task'][:2] == snapshot[:2] and record['task'][3] == snapshot[3]
                    and record['token'] == token and record['progress'] == progress
                    and calc_Distance(record['task'][2], snapshot[2]) <= 1)
            state = session.transition if session else None
            refined_entry = (not landing and same_source and state is not None
                    and state['source_tokens'].get(id(client)) == token
                    and state['source_tasks'].get(id(client)) == snapshot[:2]
                    and calc_Distance(target, state['target']) <= 1
                    and calc_Distance(pos, target) <= 1
                    and calc_Distance(record['target'], target) <= 150)
            if landing and (not same_source or calc_Distance(record['target'], target) > 1):
                return  # Arrival in another room cannot recapture its coordinates as an old door.
            if refined_entry:
                record['target'] = copy(target)  # Keep the same door's direction and attempt budget.
            if (not isinstance(record, dict) or record['session'] is not session
                    or record['task'][:2] != snapshot[:2] or record['task'][3] != snapshot[3]
                    or record['token'] != token or record['progress'] != progress
                    or calc_Distance(record['task'][2], snapshot[2]) > 1
                    or calc_Distance(record['target'], target) > 1):
                record = dict(session=session, task=snapshot, progress=progress, token=token,
                    target=copy(target), direction=None, attempts=0, next_at=0.0, ended=False)
                client._quest_door_approach = record
            dx, dy = target.x - pos.x, target.y - pos.y
            gap = math.hypot(dx, dy)
            if 1 < gap <= 150:
                # A remote TP origin is not proof of the doorway's approach axis.
                record['direction'] = (dx / gap, dy / gap)
        except Exception:
            pass  # Missing evidence never changes the original movement path.

    async def _recover_door_approach(self, client, target, *, x_attempted=False, source_entry=None):
        """One short measured step in the existing worker, only for an evidenced door."""
        try:
            _, control = await _movement_body(client)
        except (MemoryReadError, HookNotActive, ValueError):
            return False
        record = getattr(client, '_quest_door_approach', None)
        if (not isinstance(record, dict) or calc_Distance(target, record['target']) > 1
                or record['ended'] or record['attempts'] >= 2
                or time.monotonic() < record['next_at']):
            return False
        target = record['target']  # Small read jitter cannot restart a wall-pulse budget.

        def report(reason, **details):
            if reason in ('source-entry-invalid', 'no-entry-evidence', 'party-not-ready',
                          'task-change', 'source-area-change'):
                details = {**record.get('entry_check', {}), **details}
            if record.get('report_details') != (reason, details):
                logger.debug(f"[door_approach] {client.title}: end={reason} {details}")
                record['reported'] = reason
                record['report_details'] = (reason, details)

        async def current():
            if await _movement_control_changed(client, control):
                return 'controlled-object-changed'
            if not client.questing_status:
                return 'stopped'
            if await client.is_loading() or await client.zone_name() != record['task'][3]:
                return 'transition'
            if (await client.in_battle() or not await is_free(client)
                    or getattr(client, 'entity_detect_combat_status', False)
                    or getattr(client, 'quest_party_battle_rescue_active', False)
                    or getattr(client, 'refilling_potions', False)
                    or getattr(client, 'quest_recovery_owner', None)
                    or getattr(client, 'feeding_pet_status', False)
                    or getattr(client, 'post_combat_movement_active', False)
                    or getattr(client, 'quest_party_safe_return_active', False)
                    or await self._npc_dialogue_visible(client)
                    or (record['session'] is None or client is record['session'].quester)
                    and getattr(client, '_quest_dialogue_update_pending', False)):
                return 'busy'
            progress = await self._dungeon_quest_snapshot(client)
            if progress is None:
                return 'task-unreadable'
            if progress != record['progress']:
                record['entry_check'] = dict(field='progress', saved=record['progress'], actual=progress)
                return 'task-change'
            task = await _movement_task_snapshot(client)
            if (task is None or not all(math.isfinite(v) for v in (task[2].x, task[2].y, task[2].z))):
                return 'task-unreadable'
            if task[:2] != record['task'][:2] or calc_Distance(task[2], record['task'][2]) > 1:
                record['entry_check'] = dict(field='task', saved=record['task'], actual=task)
                return 'task-change'
            await observe_party_area(client)
            token = await _party_area_token(client)
            if token != record['token']:
                record['entry_check'] = dict(field='source_token', saved=record['token'], actual=token)
                return 'source-area-change'
            session = dungeon_session_for(client)
            if session is not record['session']:
                return 'session-change'
            record['peer_crossed'] = False
            if session:
                state = session.transition
                invalid = {}
                if id(client) in session.exited:
                    invalid['member_exited'] = True
                if session.potion_areas:
                    invalid['potion_members'] = list(session.potion_areas)
                if not state:
                    invalid['transition'] = 'missing'
                else:
                    if source_entry is not None and state is not source_entry:
                        invalid['transition'] = 'replaced'
                    if time.monotonic() >= state['deadline']:
                        invalid['deadline'] = dict(saved=state['deadline'], now=time.monotonic())
                    for field, saved, actual in (
                            ('source_zone', state['source_zone'], record['task'][3]),
                            ('source_token', state['source_tokens'].get(id(client)), record['token']),
                            ('source_task', state['source_tasks'].get(id(client)), record['task'][:2])):
                        if saved != actual:
                            invalid[field] = dict(saved=saved, actual=actual)
                    # Existing X can refine the entrance to the local body point.
                    # A distant/new arrow cannot borrow another entrance's arrival.
                    if calc_Distance(state['target'], target) > (1 if source_entry is not None else 150):
                        invalid['source_target'] = dict(saved=tuple(state['target']), actual=tuple(target))
                record['entry_check'] = dict(source_zone=record['task'][3],
                    quest_id=record['task'][0], goal_id=record['task'][1], invalid_fields=invalid,
                    mode='source-replay' if source_entry is not None else 'current-goal')
                # A live goal/prompt is independent of an expired shared release.
                # Replaying an old entrance still requires that exact valid record.
                if (source_entry is not None and invalid
                        or id(client) in session.exited or session.potion_areas):
                    return 'source-entry-invalid'
                crossed = False
                for member in session.members:
                    if member is client or invalid or id(member) not in state['source_tokens']:
                        continue
                    if (getattr(member, 'refilling_potions', False)
                            or getattr(member, 'quest_recovery_owner', None)
                            or getattr(member, 'entity_detect_combat_status', False)
                            or getattr(member, 'quest_party_battle_rescue_active', False)
                            or getattr(member, 'post_combat_movement_active', False)
                            or getattr(member, 'quest_party_safe_return_active', False)
                            or await member.in_battle()):
                        return 'party-busy'
                    if await member.is_loading():
                        arrival = state.get('arrivals', {}).get(id(member))
                        if arrival and arrival[0] != state['source_zone']:
                            crossed = True  # Previously confirmed arrival, not loading alone.
                        continue
                    await observe_party_area(member)
                    token = await _party_area_token(member)
                    if (token[0] != state['source_zone']
                            and token != state['source_tokens'][id(member)]
                            and all(type(v) is int and v > 0 for v in token[1:3])
                            and not await member.is_loading() and await member.zone_name() == token[0]):
                        state.setdefault('arrivals', {})[id(member)] = token
                        crossed = True
                record['peer_crossed'] = crossed
                quester = session.quester
                if not crossed:
                    if client is not quester or source_entry is not None:
                        record['entry_check']['readiness_rejection'] = 'no-peer-arrival-for-source-replay'
                        return 'party-not-ready'
                    if (not await quest_party_members_ready(quester, zone=record['task'][3])
                            or await self._npc_dialogue_visible(session.quester)
                            or getattr(session.quester, '_quest_dialogue_update_pending', False)):
                        return 'party-not-ready'
            elif getattr(client, 'quest_party_hitters', ()) and not await quest_party_members_ready(client):
                return 'party-not-ready'
            return 'unchanged'

        try:
            async with automation_owner(client, 'door-approach'):
                reason = await current()
                if reason != 'unchanged':
                    report(reason)
                    return reason in ('transition', 'task-change')
                if await is_spiral_door_open(client) or portal_kind(await get_popup_title(client)) == 'world_gate':
                    return False
                visible = await is_visible_by_path(client, npc_range_path)
                kind = interaction_kind(await self.read_popup(client)) if visible else None
                if kind in ('talk', 'collect'):
                    return False
                evidence = 'entry-prompt' if kind in ('enter', 'open') else None
                record.setdefault('entry_check', {}).update(prompt_visible=visible, prompt_kind=kind)
                session = record['session']
                if session and record.get('peer_crossed', False):
                    evidence = 'peer-crossed-source-entry'
                # The supplied 2026-10-13 log confirms this exact stair target
                # leaves Headquarters for SkyCity. It does not prove W caused
                # that transition. No prefix/name or generic proximity rule.
                if (evidence is None
                        and record['task'][3] == 'Lemuria/Interiors/LM_Z06_Headquarters'
                        and calc_Distance(target, XYZ(-681.4431762695312, 7998.80078125,
                                                     -.00012207029794808477)) <= 1):
                    evidence = 'observed-stair-route'
                    record['entry_check'].update(source_zone=record['task'][3],
                        destination='Lemuria/LM_Z06_SkyCity', path_evidence='log-confirmed-exact-target')
                if evidence is None:
                    quester = session.quester if session else client
                    quest_id, goal_id = await quester.quest_id(), await quester.goal_id()
                    quests = await (await quester.quest_manager()).quest_data()
                    quest = quests.get(quest_id)
                    goal = (await quest.goal_data()).get(goal_id) if quest else None
                    destination = await goal.goal_destination_zone() if goal else None
                    same_goal = (await quester.quest_id(), await quester.goal_id()) == (quest_id, goal_id)
                    record['entry_check'].update(quest_id=quest_id, goal_id=goal_id,
                        source_zone=record['task'][3], destination=destination,
                        destination_rejection=('quest-missing' if quest is None else
                            'goal-missing' if goal is None else 'destination-empty' if not destination else
                            'same-source-zone' if destination == record['task'][3] else
                            'task-changed' if not same_goal else None))
                    if destination and destination != record['task'][3] and same_goal:
                        evidence = 'goal-destination-zone'
                if evidence is None:
                    report('no-entry-evidence')
                    return False
                if visible and not x_attempted:
                    report('handoff-to-existing-x')
                    return False
                pos = await _movement_position(client)
                dx, dy = target.x - pos.x, target.y - pos.y
                gap = math.hypot(dx, dy)
                if not all(math.isfinite(v) for v in (pos.x, pos.y, pos.z)) or gap > 150:
                    report('outside-local-approach-range', distance=round(gap, 2), limit=150)
                    return False
                if gap > 1:
                    direction, source = (dx / gap, dy / gap), 'actual-position-to-target'
                    record['direction'] = direction
                else:
                    direction, source = record['direction'], 'saved-local-entry-direction'
                    if direction is None:
                        report('coincident-target-without-direction')
                        return False
                from wizwalker.constants import WIZARD_SPEED
                speed = WIZARD_SPEED * (1 + await client.client_object.speed_multiplier() / 100)
                if not math.isfinite(speed) or speed <= 0:
                    report('speed-unreadable')
                    return False
                seconds = min(.1, (min(gap, 30) if gap > 1 else 30) / speed)
                reason = await current()
                if reason != 'unchanged':
                    report(reason, direction_source=source, displacement=0.0)
                    return reason in ('transition', 'task-change')
                toward = XYZ(pos.x + direction[0], pos.y + direction[1], pos.z)
                yaw = calculate_yaw(pos, toward)
                body, current_control = await _movement_body(client)
                if current_control != control:
                    return False
                await body.write_yaw(yaw)
                reason = await current()
                if reason != 'unchanged':
                    report(reason, direction_source=source, displacement=0.0)
                    return reason in ('transition', 'task-change')
                record['attempts'] += 1
                record['next_at'] = time.monotonic() + 1.5
                if control is None:
                    await client.send_key(Keycode.W, seconds)
                else:
                    from src.ibao_runtime import complete_before_cancel
                    await complete_before_cancel(asyncio.create_task(client.send_key(Keycode.W, seconds)))
                reason = await current()
                try:
                    if reason == 'controlled-object-changed':
                        moved = None
                    else:
                        after = await _movement_position(client)
                        moved = math.hypot(after.x - pos.x, after.y - pos.y)
                        if not math.isfinite(moved):
                            moved = None
                except Exception:
                    moved = None  # Loading can make the position temporarily unreadable.
                if reason == 'unchanged' and moved is not None and moved < 1:
                    await asyncio.sleep(.05)  # Reuse the existing stale-position-frame recheck.
                    reason = await current()
                    after = await _movement_position(client)
                    moved = math.hypot(after.x - pos.x, after.y - pos.y)
                if reason == 'unchanged':
                    if moved is None:
                        reason = 'position-unreadable'
                        record['ended'] = True
                    elif await is_visible_by_path(client, npc_range_path):
                        reason = 'handoff-to-existing-x'
                    elif moved < 1:
                        reason = 'no-displacement'
                        record['ended'] = True
                    else:
                        reason = ('approach-budget-exhausted-without-progress' if record['attempts'] >= 2
                                  else 'measured-step-without-progress')
                        record['ended'] = record['attempts'] >= 2
                report(reason, direction_source=source, evidence=evidence,
                       yaw=round(yaw, 4),
                       displacement=round(moved, 2) if moved is not None else None,
                       attempt=record['attempts'])
                return True  # This iteration was consumed; W alone never proves success.
        except asyncio.CancelledError:
            report('cancelled')
            raise
        except Exception as exc:
            report('evidence-unreadable', error=str(exc))
            return False

    async def recover_dungeon_transition(self) -> bool:
        """Replay one old-room entrance step; group proof alone releases movement."""
        client = self.client
        session = dungeon_session_for(client)
        if not session or client not in session.members:
            return False
        quester, state = session.quester, session.transition
        def discard_expired():
            if (state and session.transition is state and time.monotonic() >= state['deadline']):
                session.transition = None  # Only this entrance operation owns this record.
                logger.debug(f"[source_entry] {client.title}: end=source-entry-invalid "
                             f"field=deadline saved={state['deadline']} now={time.monotonic()}; record released")
                return True
            return False

        if not state or discard_expired():
            return False
        if not session.confirmed and not session.entry_evidence:
            return False
        key = id(client)
        rejection = None

        def reject_source(field, **details):
            nonlocal rejection
            if rejection != (field, details):
                logger.debug(f"[source_entry] {client.title}: end=source-entry-invalid "
                             f"field={field} {details}")
                rejection = (field, details)
            return False

        async def source_is_current():
            try:
                for field, invalid in (
                        ('session', dungeon_session_for(client) is not session),
                        ('transition', session.transition is not state),
                        ('assignment', getattr(client, 'quest_party_quester', quester) is not quester),
                        ('member_exited', id(client) in session.exited),
                        ('potion_members', bool(session.potion_areas))):
                    if invalid:
                        return reject_source(field)
                if time.monotonic() >= state['deadline']:
                    discard_expired()
                    return False
                if (not getattr(client, 'questing_status', True)
                        or getattr(client, 'quest_recovery_owner', None)
                        or getattr(quester, 'quest_recovery_owner', None)
                        or getattr(client, 'entity_detect_combat_status', False)
                        or getattr(client, 'quest_party_safe_return_active', False)
                        or getattr(client, 'post_combat_movement_active', False)
                        or getattr(client, 'refilling_potions', False)
                        or getattr(quester, 'refilling_potions', False)
                        or getattr(quester, 'post_combat_movement_active', False)
                        or getattr(quester, 'quest_party_safe_return_active', False)
                        or await client.is_loading() or await client.in_battle()
                        or await quester.in_battle()
                        or not await is_free(client) or await self._npc_dialogue_visible(client)):
                    return reject_source('safety-or-control', loading=await client.is_loading(),
                        battle=await client.in_battle(), recovery_owner=getattr(client, 'quest_recovery_owner', None))
                zone = await client.zone_name()
                if zone != state['source_zone']:
                    return reject_source('source_zone', saved=state['source_zone'], actual=zone)
                await observe_party_area(client)
                token = await _party_area_token(client)
                if token != state['source_tokens'].get(key):
                    return reject_source('source_token', saved=state['source_tokens'].get(key), actual=token)
                task = await client.quest_id(), await client.goal_id()
                if task != state['source_tasks'].get(key):
                    return reject_source('source_task', saved=state['source_tasks'].get(key), actual=task)
                # A peer's new goal is irrelevant to this unchanged source member.
                # Only its own recorded area/task authorizes the old entrance.
                crossed = False
                for member in session.members:
                    member_key = id(member)
                    if member is client or member_key not in state['source_tokens']:
                        continue
                    if (getattr(member, 'refilling_potions', False)
                            or getattr(member, 'quest_recovery_owner', None)
                            or await member.in_battle()):
                        continue
                    if await member.is_loading():
                        arrival = state.get('arrivals', {}).get(member_key)
                        if arrival and arrival[0] != state['source_zone']:
                            crossed = True
                        continue
                    await observe_party_area(member)
                    token = await _party_area_token(member)
                    if (token[0] != state['source_zone']
                            and all(type(v) is int and v > 0 for v in token[1:3])
                            and token != state['source_tokens'][member_key]
                            and not await member.is_loading() and await member.zone_name() == token[0]):
                        state.setdefault('arrivals', {})[member_key] = token
                        crossed = True
                valid = (crossed and dungeon_session_for(client) is session
                        and session.transition is state
                        and await _party_area_token(client) == state['source_tokens'].get(key)
                        and (await client.quest_id(), await client.goal_id()) == state['source_tasks'].get(key))
                if not valid:
                    return reject_source('peer-arrival-or-source-recheck', peer_crossed=crossed)
                return True
            except Exception as exc:
                logger.debug(f"Client {client.title} - source entrance evidence unavailable; "
                             f"keeping existing party wait/recovery: {exc}")
                return False

        if not await source_is_current():
            return False
        if portal_kind(await get_popup_title(client)) == 'world_gate':
            return reject_source('world-gate')
        if state['attempts'].get(key, 0) >= 2:
            return reject_source('attempt-budget', attempts=state['attempts'][key], limit=2)
        if time.monotonic() < state['next_attempt_at'].get(key, 0):
            return True  # Wait for this member's first X/load to take effect.
        state['attempts'][key] = state['attempts'].get(key, 0) + 1
        state['next_attempt_at'][key] = time.monotonic() + 1.5
        logger.debug(f"Client {client.title} - replaying recorded source entrance "
                     f"in {state['source_zone']}; peers crossed, local task/area unchanged "
                     f"(attempt {state['attempts'][key]}/2).")

        async def approach():
            async with automation_owner(client, 'dungeon-source-entry'):
                if not await source_is_current():
                    return False
                move_result = await self.teleport_to_quest_target(client, state['target'])
                if not await source_is_current():
                    return False
                if move_result in ('failed', 'exhausted', 'blocked', 'changed'):
                    return False
                x_attempted = (calc_Distance(await client.body.position(), state['target']) < 750
                    and await is_visible_by_path(client, npc_range_path)
                    and portal_kind(await get_popup_title(client)) != 'world_gate')
                if x_attempted:
                    await client.send_key(Keycode.X, .1)
                    await asyncio.sleep(.1)
                stepped = await self._recover_door_approach(client, state['target'],
                    x_attempted=x_attempted, source_entry=state)
                return x_attempted or stepped

        async def watch_source():
            while await source_is_current():
                await asyncio.sleep(.1)
            return False

        movement = asyncio.create_task(approach())
        watcher = asyncio.create_task(watch_source())
        try:
            done, _ = await asyncio.wait((movement, watcher),
                timeout=max(0, state['deadline'] - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED)
            if movement in done:
                return await movement
            if watcher in done:
                await watcher
            return False
        finally:
            movement.cancel()
            watcher.cancel()
            await gather_owned(movement, watcher, return_exceptions=True)
            discard_expired()

    async def _confirmed_dungeon_target(self, session):
        """Confirm one quester snapshot for both existing workers before release."""
        quester = session.quester

        async def ready():
            if (dungeon_session_for(self.client) is not session
                    or not dungeon_member_inside(quester)
                    or not await quest_party_members_ready(quester)):
                return False
            for member in session.members:
                if id(member) in session.exited:
                    continue
                if (not await is_free(member) or await self._npc_dialogue_visible(member)
                        or member is quester and getattr(member, '_quest_dialogue_update_pending', False)
                        or getattr(member, 'feeding_pet_status', False)
                        or getattr(member, 'entity_detect_combat_status', False)):
                    return False
            return True

        def matches(first, second):
            return (first is not None and second is not None
                    and first[:2] == second[:2] and first[3] == second[3]
                    and all(math.isfinite(v) for v in (second[2].x, second[2].y, second[2].z))
                    and calc_Distance(first[2], second[2]) <= 1)

        if not await ready():
            return None
        snapshot = await _movement_task_snapshot(quester)
        if (snapshot is None or type(snapshot[0]) is not int or snapshot[0] <= 0
                or type(snapshot[1]) is not int
                or not all(math.isfinite(v) for v in (snapshot[2].x, snapshot[2].y, snapshot[2].z))
                or calc_Distance(snapshot[2], XYZ(0, 0, 0)) < 1):
            return None
        state = session.transition
        cached = (state and time.monotonic() < state['deadline']
                  and matches(state.get('movement_task'), snapshot))
        if cached:
            for member in session.members:
                if id(member) in session.exited:
                    continue
                await observe_party_area(member)
                if await _party_area_token(member) != state['source_tokens'].get(id(member)):
                    cached = False
                    break
        if not cached:
            # Separate observations; two back-to-back reads can catch the same
            # transient arrow while the NPC goal update is still in progress.
            await asyncio.sleep(.75)
        if (not await ready() or not matches(snapshot, await _movement_task_snapshot(quester))
                or not await remember_dungeon_transition(quester, snapshot[2])
                or not await ready() or not matches(snapshot, await _movement_task_snapshot(quester))):
            return None
        state = session.transition
        state.setdefault('movement_task', snapshot)
        return state['movement_task']

    async def move_dungeon_member(self, *, require_local_goal=False, _quester_snapshot=None) -> bool:
        """One shared movement step in the existing quester or hitter worker."""
        client = self.client
        session = await observe_dungeon_session(client)
        if (not dungeon_member_inside(client) or not await is_free(client)
                or getattr(client, 'entity_detect_combat_status', False)
                or getattr(client, 'quest_recovery_owner', None)
                or getattr(client, 'feeding_pet_status', False)):
            return False
        if not require_local_goal:
            quester = session.quester
            if (client is quester and _quester_snapshot is None
                    or getattr(client, 'quest_party_quester', quester) is not quester
                    or not dungeon_member_inside(quester)
                    or getattr(quester, 'quest_recovery_owner', None)
                    or not await quest_party_members_ready(quester)):
                return False
            try:
                snapshot = (_quester_snapshot if client is quester
                            else await self._confirmed_dungeon_target(session))
            except Exception as exc:
                logger.debug(f"Client {client.title} - shared dungeon target unreadable: {exc}")
                return False
            if snapshot is None:
                return False
            state = session.transition

            async def target_is_current():
                try:
                    if (dungeon_session_for(client) is not session
                            or getattr(client, 'quest_party_quester', quester) is not quester
                            or session.transition is not state or time.monotonic() >= state['deadline']
                            or not dungeon_member_inside(client) or session.potion_areas
                            or any(getattr(c, 'refilling_potions', False)
                                   for c in session.members if id(c) not in session.exited)
                            or getattr(client, 'quest_recovery_owner', None)
                            or getattr(quester, 'quest_recovery_owner', None)
                            or any(getattr(c, 'quest_party_safe_return_active', False)
                                   or getattr(c, 'post_combat_movement_active', False)
                                   or getattr(c, 'entity_detect_combat_status', False)
                                   for c in (client, quester))
                            or not await is_free(client)
                            or await self._npc_dialogue_visible(client)
                            or client is quester and getattr(client, '_quest_dialogue_update_pending', False)):
                        return False
                    await observe_party_area(client)
                    if (await _party_area_token(client) != state['source_tokens'].get(id(client))
                            or (await client.quest_id(), await client.goal_id()) != state['source_tasks'].get(id(client))):
                        return False
                    # Release was proven as a whole group. A peer loading/crossing
                    # cannot revoke this member's unchanged source entrance.
                    crossing = False
                    quester_in_source = client is quester
                    for member in session.members:
                        if member is client or id(member) in session.exited:
                            continue
                        if await member.is_loading():
                            crossing = True
                            continue
                        await observe_party_area(member)
                        token = await _party_area_token(member)
                        if token[0] != state['source_zone']:
                            if any(type(v) is not int or v <= 0 for v in token[1:3]):
                                return False
                            crossing = True
                        elif token != state['source_tokens'].get(id(member)):
                            return False
                        elif member is quester:
                            quester_in_source = True
                    if (quester_in_source and (not await is_free(quester)
                            or await self._npc_dialogue_visible(quester)
                            or getattr(quester, '_quest_dialogue_update_pending', False))):
                        return False
                    if crossing:
                        return True
                    if (not dungeon_member_inside(quester) or not await is_free(quester)
                            or await self._npc_dialogue_visible(quester)
                            or getattr(quester, '_quest_dialogue_update_pending', False)
                            or not await quest_party_members_ready(quester, zone=snapshot[3])):
                        return False
                    current = await _movement_task_snapshot(quester)
                    return (current is not None and current[:2] == snapshot[:2]
                            and current[3] == snapshot[3]
                            and calc_Distance(current[2], snapshot[2]) <= 1)
                except Exception:
                    return False  # Unreadable member evidence never releases input.

            if not await target_is_current():
                return False

            async def move_shared():
                async with automation_owner(client, 'dungeon-shared-target'):
                    if not await target_is_current():
                        return False
                    # Coordinates come from the shared quester snapshot; the
                    # lower movement guard follows this member's own source task.
                    if client is quester:
                        move_result = await self.teleport_to_quest_target(client, snapshot[2])
                    else:
                        move_result = await self.teleport_to_quest_target(client, snapshot[2], leader_client=client)
                    return move_result not in ('failed', 'exhausted', 'blocked', 'changed') and await target_is_current()

            async def watch_target():
                while await target_is_current():
                    await asyncio.sleep(.1)
                return False

            movement = asyncio.create_task(move_shared())
            watcher = asyncio.create_task(watch_target())
            try:
                done, _ = await asyncio.wait((movement, watcher), return_when=asyncio.FIRST_COMPLETED)
                if movement in done:
                    moved = await movement
                    if moved:
                        await self.handle_questing_zone_change()
                    return moved
                await watcher
                return False
            finally:
                movement.cancel()
                watcher.cancel()
                await gather_owned(movement, watcher, return_exceptions=True)
        zone = await client.zone_name()
        if require_local_goal:
            # After a confirmed quester exit, a stale arrow alone cannot keep
            # a hitter in the local branch. Use its active goal's actual zone.
            try:
                quest_id, goal_id = await client.quest_id(), await client.goal_id()
                quest = (await (await client.quest_manager()).quest_data()).get(quest_id)
                goal = (await quest.goal_data()).get(goal_id) if quest else None
                destination = await goal.goal_destination_zone() if goal else None
                if (not destination or destination != zone
                        or await client.quest_id() != quest_id
                        or await client.goal_id() != goal_id):
                    return False
            except Exception as exc:
                logger.debug(f"Client {client.title} - cannot confirm a local dungeon goal: {exc}")
                return False
        try:
            target = await client.quest_position.position()
        except Exception as exc:
            if not require_local_goal:
                raise
            logger.debug(f"Client {client.title} - cannot read the local dungeon target: {exc}")
            return False
        if require_local_goal and (not all(math.isfinite(v) for v in (target.x, target.y, target.z))
                or await client.quest_id() != quest_id or await client.goal_id() != goal_id):
            return False
        if (not dungeon_member_inside(client) or await client.zone_name() != zone
                or await client.is_loading() or await client.in_battle()):
            return False
        if calc_Distance(target, XYZ(0, 0, 0)) < 1:
            await self.auto_collect_rewrite(client)
        else:
            await self.teleport_to_quest_target(client, target)
            await self.handle_questing_zone_change()
        await observe_dungeon_session(client)
        return True

    async def auto_quest_solo(self, auto_pet_disabled=False, ignore_pet_level_up=False, play_dance_game=False):
        from src.mainline_progress import log_mainline_progress
        if (await self._npc_dialogue_visible(self.client)
                or getattr(self.client, '_quest_dialogue_update_pending', False)):
            await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
            return
        if await self._wait_internal_quest_failure(self.client):
            return
        await log_mainline_progress(self.client)
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
        if await self._quest_party_probe_blocks_movement():
            return
        if await is_free(self.client):
            if await is_potion_needed(self.client) and await self.client.stats.current_mana() > 1 and await self.client.stats.current_hitpoints() > 1:
                await collect_wisps(self.client)

            if self.client.use_potions:
                if await auto_potions(self.client, True, buy=self.client.buy_potions) is False:
                    logger.error(f"Client {self.client.title} - 补药或回传失败，跳过本轮任务移动。")
                    return

            # A completed purchase/recall must not skip the existing return probe.
            if (dungeon_session_for(self.client) is not None
                    and await self._quest_party_probe_blocks_movement()):
                return

            from src.mainline_recovery import maybe_recover_mainline
            if await maybe_recover_mainline(self.client):
                return

            quest_xyz = await self.client.quest_position.position()

            if self.client.auto_pet_status and not auto_pet_disabled:
                # client has leveled up
                if self.client.character_level < await self.client.stats.reference_level():
                    logger.debug('Client ' + self.client.title + ' leveled up - training pet.')
                    await auto_pet(self.client, ignore_pet_level_up, play_dance_game, questing=True)

            distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))
            if distance > 1:
                while self.client.entity_detect_combat_status:
                    await asyncio.sleep(.1)

                zone_before_quest_move = await self.client.zone_name()
                quest_move_task = await _movement_task_snapshot(self.client)
                move_result = None
                if (await self._npc_dialogue_visible(self.client)
                        or getattr(self.client, '_quest_dialogue_update_pending', False)):
                    await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
                    return
                if dungeon_member_inside(self.client):
                    try:
                        snapshot = await self._confirmed_dungeon_target(dungeon_session_for(self.client))
                    except Exception as exc:
                        logger.debug(f"Client {self.client.title} - shared dungeon target unreadable: {exc}")
                        return
                    if snapshot is None:
                        return
                    quest_xyz = snapshot[2]
                    if not await self.move_dungeon_member(_quester_snapshot=snapshot):
                        if await self.client.zone_name() != zone_before_quest_move:
                            self.client.quest_party_quest_worker_zone = await self.client.zone_name()
                            self.client.quest_party_probe_pending = True
                        return
                else:
                    move_result = await self.teleport_to_quest_target(self.client, quest_xyz)
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
                if (await self._npc_dialogue_visible(self.client)
                        or getattr(self.client, '_quest_dialogue_update_pending', False)):
                    await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
                    return
                if (quest_move_task is not None
                        and not _same_movement_task(quest_move_task, await _movement_task_snapshot(self.client))):
                    logger.debug(f"[quest_interaction] {self.client.title}: reason=task-changed-after-movement "
                                 f"task={quest_move_task} target={quest_xyz}; ending old interaction")
                    return
                if move_result in ('failed', 'exhausted', 'blocked', 'changed') or not self.client.questing_status:
                    return
                if await is_visible_by_path(self.client, cancel_chest_roll_path):
                    # Handles chest reroll menu, will always cancel
                    await click_window_by_path(self.client, cancel_chest_roll_path)

                current_pos = await self.client.body.position()
                if (await is_visible_by_path(self.client, npc_range_path)
                        and await self.quest_interaction_ready(self.client, quest_xyz, task_before=quest_move_task)):
                    # Handles interactables
                    sigil_msg_check = await self.read_popup(self.client)
                    if (await self._npc_dialogue_visible(self.client)
                            or getattr(self.client, '_quest_dialogue_update_pending', False)):
                        await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
                        return
                    if await self._maybe_photo_giant_vat(self.client, sigil_msg_check):
                        return
                    if is_dungeon_entry_prompt(sigil_msg_check):
                        # Handles entering dungeons
                        entry_clients = await self.prepare_party_dungeon_entry()
                        if not entry_clients:
                            return
                        entry_session = await begin_dungeon_entry(
                            self.client, entry_clients,
                            evidence=await is_visible_by_path(self.client, team_up_button_path),
                        )
                        if portal_kind(await get_popup_title(self.client)) != 'world_gate':
                            if (entry_session is not None and not await remember_dungeon_transition(
                                    self.client, await self.client.body.position())):
                                if not entry_session.confirmed and dungeon_session_for(self.client) is entry_session:
                                    clear_dungeon_session(self.client)
                                return
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
                                    if entry_session is not None:
                                        entry_session.entry_evidence = True
                                        for member in entry_session.members:
                                            member._xuanshu_mainline_dungeon_session = entry_session
                                    await c.send_key(Keycode.ENTER, 0.1)
                                await asyncio.sleep(0.1)
                            if await c.is_loading():
                                loading_clients.append(c)
                            else:
                                logger.warning(
                                    f"Client {c.title} did not enter the dungeon in time."
                                )

                        for c in loading_clients:
                            loading_deadline = time.monotonic() + 15.0
                            while await c.is_loading() and time.monotonic() < loading_deadline:
                                if not c.questing_status:
                                    return
                                await asyncio.sleep(0.1)
                            if await c.is_loading():
                                logger.debug(f"Client {c.title} - dungeon loading still pending; retry next iteration.")
                                return
                        await observe_dungeon_session(self.client)
                        if entry_session is not None and not entry_session.entry_evidence:
                            clear_dungeon_session(self.client)
                        if (
                            getattr(self.client, "quest_party_hitters", [])
                            and await self.client.zone_name()
                            != zone_before_quest_move
                        ):
                            self.client.quest_party_quest_worker_zone = (
                                await self.client.zone_name()
                            )
                            self.client.quest_party_probe_pending = True
                            logger.debug(
                                f"Client {self.client.title} entered a dungeon; pausing for hitter probe."
                            )
                            return
                    elif interaction_kind(sigil_msg_check) == "talk":
                        logger.debug('Talking to NPC')
                        before_progress = await self._dungeon_quest_snapshot(self.client)
                        failed_interaction = await self._quest_interaction_signature(self.client, sigil_msg_check)
                        if not await self.quest_interaction_ready(self.client, quest_xyz, task_before=quest_move_task):
                            return
                        await self.client.send_key(Keycode.X, 0.1)
                        quest_updated = await self.handle_npc_talking_quests(
                            self.client, [self.client], before_progress=before_progress,
                            failed_interaction=failed_interaction
                        )
                        if not quest_updated:
                            await asyncio.sleep(2.0)
                            return

                    else:
                        search_progress = await self._dungeon_quest_snapshot(self.client)
                        search_zone = await self.client.zone_name()
                        failed_interaction = await self._quest_interaction_signature(self.client, sigil_msg_check)
                        input_members = [self.client]
                        if (dungeon_member_inside(self.client)
                                and portal_kind(await get_popup_title(self.client)) != 'world_gate'):
                            input_members = await self.prepare_party_dungeon_entry()
                            if not input_members:
                                return
                            if not await remember_dungeon_transition(self.client, await self.client.body.position()):
                                return
                        if not await self.quest_interaction_ready(self.client, quest_xyz, task_before=quest_move_task):
                            return
                        await gather_owned(*[c.send_key(Keycode.X, .1) for c in input_members])

                        await asyncio.sleep(0.75)
                        if await is_spiral_door_open(self.client):
                            # Handles spiral door navigation
                            if await self.new_world_doors(self.client) == False:
                                await spiral_door_with_quest(self.client)
                        else:
                            if await self._recover_door_approach(self.client, quest_xyz, x_attempted=True):
                                return
                            if await self._recover_quest_x_direction(
                                    self.client, search_progress, quest_xyz, search_zone, [self.client],
                                    failed_interaction=failed_interaction):
                                return
                            retry = getattr(self.client, '_quest_interaction_retry', None)
                            if (isinstance(retry, dict)
                                    and not await self._npc_dialogue_visible(self.client)
                                    and not getattr(self.client, '_quest_dialogue_update_pending', False)
                                    and await self._dungeon_quest_snapshot(self.client) == search_progress
                                    and _same_movement_task(retry['task'], await _movement_task_snapshot(self.client))):
                                retry['failed'].add(failed_interaction)

                if (await self._npc_dialogue_visible(self.client)
                        or getattr(self.client, '_quest_dialogue_update_pending', False)):
                    await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
                    return
                quest_objective = await get_quest_name(self.client)

                if quest_has_action(quest_objective, "photomance"):
                    # Photomancy quests (WC, KM, LM)
                    await self.take_photomancy_photo(self.client, quest_objective)

                if await is_visible_by_path(self.client, missing_area_path):
                    # Handles when an area hasn't been downloaded yet
                    while not await is_visible_by_path(self.client, missing_area_retry_path):
                        if await closed_dungeon_popup(self.client, dismiss=True):
                            return
                        await asyncio.sleep(0.1)
                    await click_window_by_path(self.client, missing_area_retry_path, True)

                if not await is_visible_by_path(self.client, npc_range_path):
                    await self._recover_door_approach(self.client, quest_xyz)

            else:
                # Double check - sometimes wiz lies about quest position - a simple sleep and re-grabbing of the quest xyz seems to solve the issue
                await asyncio.sleep(3.0)
                quest_xyz = await self.client.quest_position.position()
                distance = calc_Distance(quest_xyz, XYZ(0.0, 0.0, 0.0))

                if distance < 1:
                    if (await self._npc_dialogue_visible(self.client)
                            or getattr(self.client, '_quest_dialogue_update_pending', False)):
                        await self.handle_npc_talking_quests(self.client, [self.client], direction_search=False)
                        return
                    await self.auto_collect_rewrite(self.client)

    async def auto_quest(self, ignore_pet_level_up: bool, play_dance_game: bool):
        try:
            while self.client.questing_status:
                await asyncio.sleep(1)
                await self.auto_quest_solo(ignore_pet_level_up=ignore_pet_level_up, play_dance_game=play_dance_game)
        finally:
            from src.mainline_recovery import cancel_mainline_recovery
            cancel_mainline_recovery(self.client)
            if not self.client.questing_status:
                clear_dungeon_session(self.client)



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
