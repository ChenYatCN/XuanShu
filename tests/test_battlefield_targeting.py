import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker.combat import CombatMember
from wizwalker.memory import DuelPhase
from wizwalker.utils import XYZ

from src.combat_targeting import BattlefieldFallbackCombatCard
from src.automation_ownership import automation_owner


class BattlefieldFallbackCombatCardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.accepted = False
        self.spell_window = SimpleNamespace(
            is_visible=AsyncMock(side_effect=lambda: not self.accepted)
        )
        self.health_window = SimpleNamespace(
            debug_paint=AsyncMock(),
            is_visible=AsyncMock(return_value=True),
            name=AsyncMock(return_value="Health"),
            scale_to_client=AsyncMock(
                return_value=SimpleNamespace(center=lambda: (175, 58))
            ),
        )
        self.mouse = SimpleNamespace(
            click_window=AsyncMock(),
            click=AsyncMock(),
            set_mouse_position_to_window=AsyncMock(),
        )
        self.client = SimpleNamespace(
            title="p1",
            mouse_handler=self.mouse,
            duel=SimpleNamespace(
                duel_phase=AsyncMock(return_value=DuelPhase.planning)
            ),
        )
        self.handler = SimpleNamespace(client=self.client)
        self.card = BattlefieldFallbackCombatCard(self.handler, self.spell_window)
        self.target = CombatMember(None, None)
        self.target.get_health_text_window = AsyncMock(
            return_value=self.health_window
        )
        self.target.owner_id = AsyncMock(return_value=42)
        self.sleep_patch = patch(
            "src.combat_targeting.asyncio.sleep", new_callable=AsyncMock
        )
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    async def test_keeps_health_click_when_game_accepts_it(self):
        async def accept_health(window):
            if window is self.health_window:
                self.accepted = True

        self.mouse.click_window.side_effect = accept_health

        await self.card.cast(self.target, sleep_time=0.2)

        self.assertEqual(self.mouse.click_window.await_count, 2)
        self.mouse.click.assert_not_awaited()

    async def test_clicks_projected_monster_when_health_click_is_ignored(self):
        async def accept_model(x, y, **kwargs):
            self.accepted = True

        self.mouse.click.side_effect = accept_model
        with patch(
            "src.combat_targeting._member_screen_points",
            new_callable=AsyncMock,
            return_value=[(640, 320)],
        ):
            await self.card.cast(self.target, sleep_time=0.2)

        self.mouse.click.assert_awaited_once_with(640, 320, sleep_duration=0.08)

    async def test_hover_retry_accepts_target_without_model_click(self):
        self.target._combatant_control = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            get_windows_with_name=AsyncMock(
                side_effect=lambda name: [] if name == "Name" else [self.health_window]
            ),
        )

        async def accept_delayed(window, **kwargs):
            if window is self.health_window and kwargs.get("sleep_duration"):
                self.accepted = True
        self.mouse.click_window.side_effect = accept_delayed
        await self.card.cast(self.target, sleep_time=0)
        self.mouse.set_mouse_position_to_window.assert_awaited_once()
        self.mouse.click.assert_not_awaited()

    async def test_original_health_click_precedes_name_fallback(self):
        name_window = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            name=AsyncMock(return_value="Name"),
            scale_to_client=AsyncMock(
                return_value=SimpleNamespace(center=lambda: (172, 28))
            ),
        )
        self.target._combatant_control = SimpleNamespace(
            is_visible=AsyncMock(return_value=True),
            get_windows_with_name=AsyncMock(
                side_effect=lambda name: (
                    [name_window] if name == "Name" else [self.health_window]
                )
            ),
        )

        async def accept_name(window, **kwargs):
            if window is name_window:
                self.accepted = True

        self.mouse.click_window.side_effect = accept_name
        await self.card.cast(self.target, sleep_time=0)

        attempted = [call.args[0] for call in self.mouse.click_window.await_args_list]
        self.assertEqual(
            attempted,
            [self.spell_window, self.health_window, name_window],
        )
        self.mouse.click.assert_not_awaited()

    async def test_fails_boundedly_when_entity_cannot_be_projected(self):
        with patch(
            "src.combat_targeting._member_screen_points",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with self.assertRaisesRegex(ValueError, "无法投影战斗盘实体"):
                await self.card.cast(self.target, sleep_time=0.2)
        self.mouse.click.assert_not_awaited()

    async def test_script_cannot_enter_between_card_and_target_click(self):
        card_selected = asyncio.Event()
        allow_targeting = asyncio.Event()
        script_acquired = asyncio.Event()

        async def block_after_card_click(window, **kwargs):
            if window is self.spell_window:
                card_selected.set()
                await allow_targeting.wait()
            elif window is self.health_window:
                self.accepted = True

        async def script_action():
            async with automation_owner(self.client, "script-vm"):
                script_acquired.set()

        self.mouse.click_window.side_effect = block_after_card_click
        cast_task = asyncio.create_task(
            self.card.cast(self.target, sleep_time=0)
        )
        await card_selected.wait()
        script_task = asyncio.create_task(script_action())

        done, _ = await asyncio.wait({script_task}, timeout=0.05)
        self.assertFalse(done)
        self.assertFalse(script_acquired.is_set())

        allow_targeting.set()
        await asyncio.wait_for(cast_task, 1)
        await asyncio.wait_for(script_task, 1)
        self.assertTrue(script_acquired.is_set())


class MemberProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_entity_position_and_scaled_model_height(self):
        from src.combat_targeting import _member_screen_points

        body = SimpleNamespace(
            position=AsyncMock(return_value=XYZ(10, 20, 30)),
            height=AsyncMock(return_value=200),
            scale=AsyncMock(return_value=1.5),
        )
        entity = SimpleNamespace(
            location=AsyncMock(return_value=XYZ(1, 2, 3)),
            actor_body=AsyncMock(return_value=body),
        )
        participant = SimpleNamespace(fetch_entity=AsyncMock(return_value=entity))
        target = SimpleNamespace(
            get_participant=AsyncMock(return_value=participant)
        )
        camera = {"client_w": 1152, "client_h": 864}

        with patch(
            "src.combat_targeting.get_camera_state",
            new_callable=AsyncMock,
            return_value=camera,
        ), patch(
            "src.combat_targeting.project_point",
            side_effect=lambda _cam, x, y, z: (int(x + z), int(y + z)),
        ):
            points = await _member_screen_points(SimpleNamespace(), target)

        self.assertEqual(points[0], (190, 200))
        self.assertEqual(len(points), 3)
