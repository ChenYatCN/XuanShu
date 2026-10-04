import asyncio
import html
import re
from collections import Counter
from typing import List, Optional, Union

import wizwalker
from loguru import logger
from wizwalker.combat import CombatMember
from wizwalker.combat.card import CombatCard
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    SprintyCombat as UpstreamSprintyCombat,
)
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import Move, MoveConfig, NamedSpell, TargetType
from wizwalker.memory import DuelPhase

from src.automation_ownership import automation_owner
from src.paths import willcast_path
from src.world_to_screen import get_camera_state, project_point


class FusionSelectionIncomplete(RuntimeError):
    """Stop this round without replaying material clicks after a fusion choice."""


def _is_fusion_result_prompt(text: str) -> bool:
    # Root.wad GUI3.lang and the installed Chinese overlay, verified 2026-10-02.
    # The earlier FusionSelectHelp prompt selects MATERIALS, not result cards.
    if 'GUI3_SelectTieredFusionHelp' in text:
        return True
    plain = html.unescape(re.sub(r'<[^>]*>', '', text))
    return ''.join(plain.split()).casefold() in ('selectfusionresult', '选择融合结果')


async def _selection_was_accepted(card: CombatCard) -> bool:
    """A targeted spell leaves the hand after the game accepts its target."""
    try:
        return not await card._spell_window.is_visible()
    except wizwalker.WizWalkerMemoryError:
        # An unreadable window does not prove that the game accepted a target.
        return False


async def _wait_for_selection(card: CombatCard, samples: int = 5) -> bool:
    for _ in range(samples):
        if await _selection_was_accepted(card):
            return True
        await asyncio.sleep(0.1)
    return await _selection_was_accepted(card)


async def _visible_target_windows(target: CombatMember):
    """Only inspect children of this member; never borrow another enemy's UI."""
    control = target._combatant_control
    if control is None or not await control.is_visible():
        return []
    windows = []
    # Expanded damage details can cover or invalidate the Health hit area.
    # Live logs show the same member's Name widget remains clickable, so use
    # that first and retain Health as the bounded fallback.
    for name in ("Name", "Health"):
        for window in await control.get_windows_with_name(name):
            if await window.is_visible():
                windows.append(window)
    return windows


async def _member_screen_points(
    client, target: CombatMember
) -> list[tuple[int, int]]:
    """Project several points inside the selected member's visible model."""
    participant = await target.get_participant()
    entity = await participant.fetch_entity()
    if entity is None:
        return []

    position = await entity.location()
    height = 170.0
    try:
        body = await entity.actor_body()
        if body is not None:
            body_position = await body.position()
            body_height = await body.height()
            body_scale = await body.scale()
            if body_position is not None:
                position = body_position
            scaled_height = body_height * body_scale
            if 20.0 <= scaled_height <= 2000.0:
                height = scaled_height
    except wizwalker.WizWalkerMemoryError:
        pass

    camera = await get_camera_state(client)
    if camera is None:
        return []

    logger.debug(f"Target projection: position={position}, height={height}, camera={camera}")

    points: list[tuple[int, int]] = []
    # Center mass first, then lower and upper torso. Different creatures use
    # different clickable meshes, so bounded alternatives are more reliable
    # than one hard-coded vertical offset.
    for height_fraction in (0.5, 0.35, 0.65):
        point = project_point(
            camera,
            position.x,
            position.y,
            position.z + height * height_fraction,
        )
        if point is None:
            continue
        x, y = point
        if not (0 <= x < camera["client_w"] and 0 <= y < camera["client_h"]):
            continue
        if point not in points:
            points.append(point)
    return points


class BattlefieldFallbackCombatCard(CombatCard):
    """Keep upstream targeting and click the 3D model only if it is ignored."""

    async def cast(
        self,
        target: Union[CombatCard, CombatMember, List, None],
        *,
        sleep_time: Optional[float] = 1.0,
        debug_paint: bool = False,
    ):
        client = self.combat_handler.client
        async with automation_owner(client, "auto-combat-cast"):
            self.combat_handler._round_input_started = True
            return await self._cast_owned(
                target, sleep_time=sleep_time, debug_paint=debug_paint
            )

    async def _cast_owned(
        self,
        target: Union[CombatCard, CombatMember, List, None],
        *,
        sleep_time: Optional[float],
        debug_paint: bool,
    ):
        if not isinstance(target, CombatMember):
            return await super().cast(
                target, sleep_time=sleep_time, debug_paint=debug_paint
            )

        client = self.combat_handler.client
        owner_id = await target.owner_id()
        target = await self.combat_handler.refresh_member(owner_id)
        original_error = None
        try:
            # Run wizwalker's original card and Health-window click first.
            await super().cast(
                target, sleep_time=sleep_time, debug_paint=debug_paint
            )
        except (ValueError, wizwalker.WizWalkerMemoryError) as exc:
            original_error = exc

        if await _wait_for_selection(self):
            return

        try:
            if await client.duel.duel_phase() != DuelPhase.planning:
                return
        except wizwalker.WizWalkerMemoryError:
            return

        try:
            # A member control may have been replaced during the selection wait.
            # Resolve the same owner again; never substitute a different target.
            target = await self.combat_handler.refresh_member(owner_id)
            windows = await _visible_target_windows(target)
            if not windows:
                windows = []
            for window in windows[:4]:
                if await client.duel.duel_phase() != DuelPhase.planning:
                    return
                await client.mouse_handler.set_mouse_position_to_window(window)
                await asyncio.sleep(0.12)
                rect = await window.scale_to_client()
                logger.debug(
                    f"Client {client.title} - 原版目标点击未确认，补救点击："
                    f"owner_id={owner_id}, "
                    f"widget={await window.name()}, center={rect.center()}"
                )
                if debug_paint:
                    await window.debug_paint()
                await client.mouse_handler.click_window(window, sleep_duration=0.08)
                if await _wait_for_selection(self):
                    logger.debug(f"Client {client.title} - 可见目标控件点击已确认：owner_id={owner_id}")
                    return
        except (ValueError, wizwalker.WizWalkerMemoryError) as exc:
            original_error = exc

        target = await self.combat_handler.refresh_member(owner_id)
        points = await _member_screen_points(client, target)
        if not points:
            detail = f"；原版点击错误：{original_error}" if original_error else ""
            raise ValueError(
                f"目标 {owner_id} 的血条点击未生效，且无法投影战斗盘实体{detail}"
            )

        logger.warning(
            f"Client {client.title} - 血条点击未被接受，改点战斗盘上的目标 "
            f"owner_id={owner_id}，候选坐标={points}"
        )
        for x, y in points:
            try:
                if await client.duel.duel_phase() != DuelPhase.planning:
                    return
            except wizwalker.WizWalkerMemoryError:
                return
            await client.mouse_handler.click(x, y, sleep_duration=0.08)
            if await _wait_for_selection(self, samples=4):
                logger.debug(
                    f"Client {client.title} - 战斗盘目标点击已确认："
                    f"owner_id={owner_id}, point=({x}, {y})"
                )
                return
            logger.warning(f"Client {client.title} - 战斗盘点击未确认：owner_id={owner_id}, point=({x}, {y})")

        logger.error(f"Client {client.title} - 血条和实体点击均未确认：owner_id={owner_id}")
        raise ValueError(
            f"目标 {owner_id} 的血条和战斗盘实体点击均未被游戏接受"
        )

    async def discard(self, **kwargs):
        self.combat_handler._round_input_started = True
        return await super().discard(**kwargs)


class TargetingSprintyCombat(UpstreamSprintyCombat):
    """Upstream combat with targeting recovery and configured fusion selection."""

    async def handle_combat(self):
        self._prepared_fusions = {}
        return await super().handle_combat()

    async def _prepared_fusion_card(self, move):
        prepared = getattr(self, '_prepared_fusions', {})
        key = (repr(move.card), repr(move.enchant))
        name = prepared.get(key)
        if name is None:
            return None
        card = await super().try_get_spell(NamedSpell(name, True), castable=False)
        if card is None:
            prepared.pop(key, None)
        return card

    async def _prepare_round_fusions(self, move_config):
        # The provider returns these exact priority objects. Prepare only the
        # selected round, without recomputing relative offsets or editing config.
        config = getattr(getattr(self, 'config', None), 'config', None)
        if config is None:
            return
        rounds = [*config.infinite_rounds, *config.specific_rounds.values()]
        line = next((line for line in rounds
                     if any(priority is move_config for priority in line.priorities)), None)
        if line is None:
            return
        for priority in line.priorities:
            moves = priority.move if isinstance(priority.move, list) else [priority.move]
            fusion_moves = [move for move in moves
                            if move.enchant is not None and move.second_enchant is not None]
            if not fusion_moves:
                continue
            if priority.condition is not None and not await self.evaluate_condition(priority.condition):
                continue
            for move in fusion_moves:
                if await self._prepared_fusion_card(move) is not None:
                    continue
                # Casting availability is not fusion-target availability.
                # Both base and material may be grey (user confirmed in game).
                # Only a confirmed result counts as successful fusion.
                bases = await super().try_get_spell(move.card, castable=False,
                                                    only_enchantable=True, multi=True)
                prepared_names = set(getattr(self, '_prepared_fusions', {}).values())
                base = None
                for candidate in bases or []:
                    if await candidate.name() not in prepared_names:
                        base = candidate
                        break
                material = await super().try_get_spell(move.enchant, castable=False)
                if base is None or material in (None, 'none') or material is base:
                    continue
                if (not await self.client.in_battle()
                        or await self.client.duel.duel_phase() != DuelPhase.planning):
                    raise FusionSelectionIncomplete('提前融合前已离开选牌阶段')
                before = await self.get_cards()
                names = Counter([await card.name() for card in before])
                self._round_input_started = True
                try:
                    await material.cast(base, sleep_time=self.config.cast_time * 2)
                except ValueError as exc:
                    raise FusionSelectionIncomplete('提前融合点击未确认') from exc
                # One material input only. A late/ignored fusion must not be
                # replayed, nor fall through to casting the original card.
                for _ in range(50):
                    if (not await self.client.in_battle()
                            or await self.client.duel.duel_phase() != DuelPhase.planning):
                        raise FusionSelectionIncomplete('提前融合后已离开选牌阶段')
                    after = await self.get_cards()  # resolves the existing side-selection UI
                    if len(after) != len(before):
                        added = Counter([await card.name() for card in after]) - names
                        if len(after) != len(before) - 1 or sum(added.values()) != 1:
                            raise FusionSelectionIncomplete('无法唯一确认提前融合后的卡牌')
                        if not hasattr(self, '_prepared_fusions'):
                            self._prepared_fusions = {}
                        key = (repr(move.card), repr(move.enchant))
                        self._prepared_fusions[key] = next(iter(added))
                        self.cur_card_count -= 1
                        logger.info('提前融合完成，保留结果等待原出牌优先级。')
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise FusionSelectionIncomplete('提前融合未返回已确认的手牌')

    def _fusion_spells_to_keep(self):
        config = getattr(getattr(self, 'config', None), 'config', None)
        if config is None:
            return []
        spells = []
        rounds = [*config.infinite_rounds, *config.specific_rounds.values()]
        for line in rounds:
            for priority in line.priorities:
                moves = priority.move if isinstance(priority.move, list) else [priority.move]
                for move in moves:
                    if move.enchant is not None and move.second_enchant is not None:
                        spells.extend((move.card, move.enchant))
        return spells

    async def try_get_spell(self, spell, only_enchants=False, only_enchantable=False,
                            castable=True, multi=False):
        keep = self._fusion_spells_to_keep()
        if (not keep or getattr(self, '_fusion_cast_active', False)
                or not castable and not getattr(self, '_protect_spell_target', False)):
            return await super().try_get_spell(spell, only_enchants=only_enchants,
                only_enchantable=only_enchantable, castable=castable, multi=multi)
        candidates = await super().try_get_spell(spell, only_enchants=only_enchants,
            only_enchantable=only_enchantable, castable=castable, multi=True)
        if not isinstance(candidates, list):
            return candidates
        reserved = set()
        named = []
        resource_stems = []
        for spec in keep:
            if not isinstance(spec, NamedSpell):
                matches = await super().try_get_spell(spec, castable=False, multi=True)
                if isinstance(matches, list):
                    reserved.update([await card.name() for card in matches])
                continue
            named.append(spec)
            if spec.is_literal:
                # A resource filename is NOT an executable internal card name.
                # Still keep its base conservatively, rather than letting an
                # invalid fusion clause leak that card into any<damage>.
                stem = re.split(r' - T\d+ - ', spec.name, maxsplit=1)[0]
                if stem != spec.name:
                    resource_stems.append(stem.casefold())
        available = []
        prepared_names = set(getattr(self, '_prepared_fusions', {}).values())
        for card in candidates:
            name = await card.name()
            # Epic may target a confirmed result, but generic damage fallback
            # must not consume it before its configured fusion cast priority.
            if name in prepared_names:
                if getattr(self, '_protect_spell_target', False):
                    available.append(card)
                continue
            matches_name = any(name == spec.name if spec.is_literal
                               else spec.name.casefold() in name.casefold() for spec in named)
            if (name not in reserved and not matches_name
                    and not any(stem in name.casefold() for stem in resource_stems)):
                available.append(card)
        return available if multi else (available[0] if available else None)

    async def try_execute_config(self, move_config, willcasted=False):
        if getattr(self, '_fusion_preparation_pending', False):
            self._fusion_preparation_pending = False
            await self._prepare_round_fusions(move_config)
        move = move_config.move
        fusion = (not isinstance(move, list) and move.enchant is not None
                  and move.second_enchant is not None)
        previous = getattr(self, '_fusion_cast_active', False)
        previous_target = getattr(self, '_protect_spell_target', False)
        self._fusion_cast_active = fusion
        target = move_config.target
        self._protect_spell_target = (not isinstance(target, list) and target is not None
                                     and target.target_type is TargetType.type_spell)
        try:
            if self._protect_spell_target and not fusion and self._fusion_spells_to_keep():
                candidates = await self.try_get_spell(target.extra_data, castable=False,
                                                      only_enchantable=True, multi=True)
                if not candidates:
                    return False
            if fusion:
                prepared = await self._prepared_fusion_card(move)
                if prepared is not None:
                    if not await prepared.is_castable():
                        return False
                    enchanted = await prepared.is_enchanted()
                    if not enchanted and not getattr(move.second_enchant, 'optional', False):
                        if await self.try_get_spell(move.second_enchant) is None:
                            return False
                    # Reuse the normal executor for target/condition checks,
                    # Epic and casting; never mutate the parsed configuration.
                    ready_move = Move(NamedSpell(await prepared.name(), True),
                                      None if enchanted else move.second_enchant)
                    return await super().try_execute_config(
                        MoveConfig(ready_move, move_config.target, move_config.condition),
                        willcasted=willcasted)
                # Named enchants in upstream are optional when absent. Fusion
                # must not fall through to casting its uncombined base card.
                base = await self.try_get_spell(move.card, only_enchantable=True)
                material = await self.try_get_spell(move.enchant, castable=False)
                if base is None or material in (None, 'none') or not await material.is_castable():
                    return False
                if not getattr(move.second_enchant, 'optional', False):
                    second = await self.try_get_spell(move.second_enchant)
                    if second is None:
                        return False
            return await super().try_execute_config(move_config, willcasted=willcasted)
        finally:
            self._fusion_cast_active = previous
            self._protect_spell_target = previous_target

    async def handle_round(self):
        # Retry reads only. Once any cast/enchant/discard/pass may have reached
        # the game, leave this round to the existing next-round waiter.
        state_names = ("turn_adjust", "rel_round_offset", "was_pass",
                       "had_first_round", "cur_card_count", "prev_card_count")
        saved = {name: getattr(self, name) for name in state_names if hasattr(self, name)}
        self._round_input_started = False
        self._fusion_ui = None
        for attempt in range(3):
            try:
                self._fusion_preparation_pending = True
                return await self._handle_fresh_round()
            except FusionSelectionIncomplete as exc:
                self._spell_check_boxes = None
                logger.warning('融合结果尚未确认，本回合停止追加操作：{}', exc)
                return
            except wizwalker.MemoryInvalidated as exc:
                self._spell_check_boxes = None
                self._fusion_ui = None
                logger.warning("战斗成员已失效，刷新当前回合读取（{}/3）：{}", attempt + 1, exc)
                if self._round_input_started:
                    logger.warning("本回合已发出战斗操作，不重放施法；等待下一回合。")
                    return
                for name, value in saved.items():
                    setattr(self, name, value)
                if attempt < 2:
                    await asyncio.sleep(0.2)

    async def refresh_member(self, owner_id):
        for member in await self.get_members():
            if await member.owner_id() == owner_id:
                return member
        raise wizwalker.MemoryInvalidated("Combat member no longer present")

    async def pass_button(self):
        self._round_input_started = True
        return await super().pass_button()

    async def _handle_fresh_round(self):
        # Wait for assigned hitters to appear in this battle's roster before
        # evaluating priorities that could otherwise buff the quester itself.
        while (getattr(self.client, 'questing_status', False)
               and not getattr(self.client, 'in_solo_zone', False)
               and getattr(self.client, 'quest_party_hitters', [])):
            if (not await self.client.in_battle()
                    or await self.client.duel.duel_phase() != DuelPhase.planning):
                return
            members = await self.get_members()
            present = {await member.owner_id() for member in members}
            expected = {
                await hitter.client_object.global_id_full()
                for hitter in self.client.quest_party_hitters
            }
            if expected <= present:
                break
            await asyncio.sleep(0.25)
        # Own the whole planning transaction, including enchant/discard/pass and
        # the card -> target sequence.  Card.cast claims the same task-reentrant
        # lock, so direct calls are protected without deadlocking this wrapper.
        async with automation_owner(self.client, "auto-combat-round"):
            return await super().handle_round()

    async def get_cards(self) -> list[CombatCard]:
        # The game reuses Hand's SpellCheckBox controls for fusion results.
        # Resolve that modal state before the backend compares pre/post hand
        # counts or searches for the new fused card to enchant with Epic.
        await self._complete_fusion_result()
        cards = await super().get_cards()
        return [
            card
            if isinstance(card, BattlefieldFallbackCombatCard)
            else BattlefieldFallbackCombatCard(self, card._spell_window)
            for card in cards
        ]

    async def _complete_fusion_result(self):
        cached = getattr(self, '_fusion_ui', None)
        if cached is None:
            selection = self.client.root_window
            try:
                # Reuse the already-established SpellSelection path. The child
                # names are also present in Root.wad's GUI/PlanningPhase.gui.
                for name in willcast_path[:5]:
                    selection = await selection.get_child_by_name(name)
                prompt = await selection.get_child_by_name('HelpText')
                hand = await selection.get_child_by_name('Hand')
            except ValueError:
                return
            cached = self._fusion_ui = (selection, prompt, hand)
        selection, prompt, hand = cached
        if (not await selection.is_visible() or not await prompt.is_visible()
                or not _is_fusion_result_prompt(await prompt.maybe_text())):
            return

        clicked = False
        previous_geometry = None
        stable_reads = 0
        selection_delay = getattr(getattr(self, 'config', None), 'cast_time', 0.2) * 2
        # Bounded wait: do not loop on material casts or run into the next round
        # if a result fails to register. Wait for both result cards to settle
        # for the same cast_time * 2 interval used by normal auto-combat.
        for _ in range(50):
            if (not await self.client.in_battle()
                    or await self.client.duel.duel_phase() != DuelPhase.planning):
                raise FusionSelectionIncomplete('已离开选牌阶段')
            if (not await selection.is_visible() or not await prompt.is_visible()
                    or not _is_fusion_result_prompt(await prompt.maybe_text())):
                self._spell_check_boxes = None
                await asyncio.sleep(0.1)
                return
            if not clicked and await hand.is_visible():
                options = []
                geometry = []
                for window in await hand.get_windows_with_type('SpellCheckBox'):
                    if (await window.name() != 'PetCard' and await window.is_visible()
                            and await window.maybe_graphical_spell() is not None):
                        rect = await window.scale_to_client()
                        if rect.x2 > rect.x1 and rect.y2 > rect.y1:
                            options.append((rect.center()[0], window))
                            geometry.append((rect.x1, rect.y1, rect.x2, rect.y2))
                if len(options) == 2 and options[0][0] != options[1][0]:
                    geometry = tuple(sorted(geometry))
                    stable_reads = stable_reads + 1 if geometry == previous_geometry else 1
                    previous_geometry = geometry
                    if (stable_reads - 1) * 0.1 >= selection_delay:
                        side = getattr(self.client, 'fusion_result_side', 'right')
                        choose = min if side == 'left' else max
                        result = choose(options, key=lambda item: item[0])[1]
                        self._round_input_started = True
                        await self.client.mouse_handler.click_window(result)
                        clicked = True
                        logger.info('融合结果：已选择{}侧卡牌，等待返回手牌。', '左' if side == 'left' else '右')
                else:
                    previous_geometry = None
                    stable_reads = 0
            elif not clicked:
                previous_geometry = None
                stable_reads = 0
            await asyncio.sleep(0.1)
        raise FusionSelectionIncomplete('融合结果未确认或结果卡牌尚未就绪')
