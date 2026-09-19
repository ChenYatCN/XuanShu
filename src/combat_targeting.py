import asyncio
from typing import List, Optional, Union

import wizwalker
from loguru import logger
from wizwalker.combat import CombatMember
from wizwalker.combat.card import CombatCard
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    SprintyCombat as UpstreamSprintyCombat,
)
from wizwalker.memory import DuelPhase

from src.automation_ownership import automation_owner
from src.world_to_screen import get_camera_state, project_point


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

        owner_id = await target.owner_id()
        try:
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


class TargetingSprintyCombat(UpstreamSprintyCombat):
    """Upstream SprintyCombat with a narrow 3D-model targeting fallback."""

    async def handle_round(self):
        # Own the whole planning transaction, including enchant/discard/pass and
        # the card -> target sequence.  Card.cast claims the same task-reentrant
        # lock, so direct calls are protected without deadlocking this wrapper.
        async with automation_owner(self.client, "auto-combat-round"):
            return await super().handle_round()

    async def get_cards(self) -> list[CombatCard]:
        cards = await super().get_cards()
        return [
            card
            if isinstance(card, BattlefieldFallbackCombatCard)
            else BattlefieldFallbackCombatCard(self, card._spell_window)
            for card in cards
        ]
