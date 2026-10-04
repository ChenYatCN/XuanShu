import asyncio
import ast
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.automation_ownership import get_client_automation_ownership
from src.paths import exit_dungeon_path
from src.questing import Quester


class PowerCoreRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.modal = False
        self.tick = None
        self.text = 'Defeat 0/3'
        self.client = SimpleNamespace(
            title='p1', questing_status=True, refilling_potions=False,
            quest_recovery_owner=None, quest_dungeon_recovery=None,
            quest_party_hitters=[], mainline_finder_enabled=True,
            zone_name=AsyncMock(return_value=Quester.POWER_CORE_ZONE),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.text)
        self.quester._refresh_dungeon_quest = AsyncMock(return_value=True)

        async def sleep(seconds):
            self.now += seconds
            if self.tick:
                self.tick()

        async def click(client, path):
            self.assertIs(client, self.client)
            self.assertEqual(path, exit_dungeon_path)
            self.modal = False
            client.zone_name.return_value = 'Arcanum/AR_Z02_LowerDecks'

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)))
        stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: self.modal and p == exit_dungeon_path)))
        self.click = stack.enter_context(patch('src.questing.click_window_by_path', new=AsyncMock(side_effect=click)))
        self.log = stack.enter_context(patch('src.questing.logger'))

    async def handle(self):
        return await self.quester._maybe_refresh_stalled_dungeon_quest(self.client)

    async def refreshed(self):
        self.assertFalse(await self.handle())
        self.now = 180.0
        self.assertTrue(await self.handle())
        self.quester._refresh_dungeon_quest.assert_awaited_once_with(self.client, (42, 7, self.text))
        self.client.teleport.assert_not_awaited()
        self.assertTrue(self.client.quest_dungeon_recovery['card_refreshed'])

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        state = self.client.quest_dungeon_recovery
        if state:
            self.assertFalse(state['active'])

    def assert_changed(self):
        self.client.teleport.assert_awaited_once_with(Quester.POWER_CORE_EXIT)
        point = self.client.teleport.await_args.args[0]
        self.assertEqual((point.x, point.y, point.z), (22.596, -9478.560, 9.790))
        self.assertIsNone(self.client.quest_dungeon_recovery)
        self.assertEqual(self.client.quest_party_confirmed_dungeon_transition,
                         (Quester.POWER_CORE_ZONE, 'Arcanum/AR_Z02_LowerDecks'))
        self.assert_released()

    async def test_three_minutes_refresh_then_three_minutes_direct_exit(self):
        self.assertFalse(await self.handle())
        self.now = 179.9
        self.assertFalse(await self.handle())
        self.quester._refresh_dungeon_quest.assert_not_awaited()
        self.now = 180
        self.assertTrue(await self.handle())
        self.now = 359.9
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.now = 360
        self.client.teleport.side_effect = lambda p: setattr(
            self.client.zone_name, 'return_value', 'Arcanum/AR_Z02_LowerDecks')
        self.assertTrue(await self.handle())
        self.assert_changed()
        self.click.assert_not_awaited()
        self.quester._refresh_dungeon_quest.assert_awaited_once()

    async def test_exit_confirmation_and_party_transition(self):
        await self.refreshed()
        self.client.quest_party_hitters = [SimpleNamespace()]
        self.client.quest_party_group_dungeon_zone = Quester.POWER_CORE_ZONE
        self.client.teleport.side_effect = lambda p: setattr(self, 'modal', True)
        self.now = 360
        self.assertTrue(await self.handle())
        self.assert_changed()
        self.click.assert_awaited_once_with(self.client, exit_dungeon_path)
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, 'Arcanum/AR_Z02_LowerDecks')

    async def test_delayed_confirmation(self):
        await self.refreshed()
        self.tick = lambda: setattr(self, 'modal', self.now >= 360.4)
        self.now = 360
        self.assertTrue(await self.handle())
        self.assert_changed()
        self.click.assert_awaited_once()

    async def test_loading_then_direct_exit(self):
        await self.refreshed()
        self.client.teleport.side_effect = lambda p: setattr(self.client.is_loading, 'return_value', True)

        def finish_loading():
            if self.now >= 361:
                self.client.is_loading.return_value = False
                self.client.zone_name.return_value = 'Arcanum/AR_Z02_LowerDecks'

        self.tick = finish_loading
        self.now = 360
        self.assertTrue(await self.handle())
        self.assert_changed()
        self.click.assert_not_awaited()

    async def test_failed_exit_is_bounded_and_cools_down(self):
        await self.refreshed()
        self.now = 360
        self.assertTrue(await self.handle())
        self.assertGreaterEqual(self.now, 380)
        self.assertLess(self.now, 381)
        self.client.teleport.assert_awaited_once()
        self.click.assert_not_awaited()
        self.assertIsNotNone(self.client.quest_dungeon_recovery)
        self.assertTrue(self.client.quest_dungeon_recovery['card_refreshed'])
        self.assertFalse(await self.handle())
        self.assert_released()

    async def test_loading_alone_is_not_zone_change(self):
        await self.refreshed()
        self.client.teleport.side_effect = lambda p: setattr(self.client.is_loading, 'return_value', True)
        self.now = 360
        self.assertTrue(await self.handle())
        self.assertIsNotNone(self.client.quest_dungeon_recovery)
        self.assertFalse(hasattr(self.client, 'quest_party_confirmed_dungeon_transition'))
        self.assert_released()

    async def test_failed_refresh_does_not_arm_exit(self):
        self.quester._refresh_dungeon_quest.return_value = False
        self.assertFalse(await self.handle())
        self.now = 180
        self.assertTrue(await self.handle())
        self.now = 360
        self.assertTrue(await self.handle())
        self.assertEqual(self.quester._refresh_dungeon_quest.await_count, 2)
        self.client.teleport.assert_not_awaited()

    async def test_progress_resets_both_timer_and_refresh_stage(self):
        await self.refreshed()
        self.now = 359
        self.text = 'Defeat 1/3'
        self.assertFalse(await self.handle())
        self.assertFalse(self.client.quest_dungeon_recovery['card_refreshed'])
        self.now = 539
        self.assertTrue(await self.handle())
        self.assertEqual(self.quester._refresh_dungeon_quest.await_count, 2)
        self.client.teleport.assert_not_awaited()

    async def test_recovery_state_survives_worker_recreation(self):
        await self.refreshed()
        previous = self.quester
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = previous.read_quest_txt
        self.quester._refresh_dungeon_quest = previous._refresh_dungeon_quest
        self.client.teleport.side_effect = lambda p: setattr(
            self.client.zone_name, 'return_value', 'Arcanum/AR_Z02_LowerDecks')
        self.now = 360
        self.assertTrue(await self.handle())
        self.assert_changed()

    async def test_exact_zone_only(self):
        self.client.zone_name.return_value = Quester.POWER_CORE_ZONE + '_Other'
        self.assertFalse(await self.handle())
        self.assertIsNone(self.client.quest_dungeon_recovery)
        self.client.teleport.assert_not_awaited()

    async def test_power_core_preempts_finder_even_without_entry_record(self):
        self.quester._mainline_identity = AsyncMock()
        self.assertFalse(await self.quester._maybe_recover_mainline(self.client))
        self.assertEqual(self.client.quest_dungeon_recovery['zone'], Quester.POWER_CORE_ZONE)
        self.quester._mainline_identity.assert_not_awaited()

    async def test_priority_states_defer_exit(self):
        await self.refreshed()
        self.now = 360
        for attribute in ('refilling_potions', 'quest_party_probe_pending',
                          'quest_party_battle_rescue_active', 'post_combat_movement_active'):
            with self.subTest(attribute=attribute):
                setattr(self.client, attribute, True)
                self.assertFalse(await self.handle())
                setattr(self.client, attribute, False)
        for method in (self.client.is_loading, self.client.in_battle):
            method.return_value = True
            with patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=False)):
                self.assertFalse(await self.handle())
            method.return_value = False
        self.client.teleport.assert_not_awaited()

    async def test_progress_during_exit_tp_does_not_click_or_repeat(self):
        await self.refreshed()

        def teleport(point):
            self.text = 'Defeat 1/3'
            self.modal = True

        self.client.teleport.side_effect = teleport
        self.now = 360
        self.assertTrue(await self.handle())
        self.click.assert_not_awaited()
        self.assertFalse(await self.handle())
        self.client.teleport.assert_awaited_once()
        self.assertFalse(self.client.quest_dungeon_recovery['card_refreshed'])
        self.assert_released()

    async def test_refill_during_last_snapshot_read_prevents_tp(self):
        await self.refreshed()
        original = self.quester._recover_power_core_exit

        async def recover(client, snapshot):
            self.quester.read_quest_txt.side_effect = lambda c: (
                setattr(c, 'refilling_potions', True) or self.text)
            await original(client, snapshot)

        self.quester._recover_power_core_exit = recover
        self.now = 360
        self.assertTrue(await self.handle())
        self.client.teleport.assert_not_awaited()
        self.assert_released()

    async def test_cancellation_releases_ownership(self):
        await self.refreshed()
        self.client.teleport.side_effect = asyncio.CancelledError
        self.now = 360
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()

    async def test_confirmation_uses_recovering_client_not_constructor_client(self):
        await self.refreshed()
        self.quester.client = SimpleNamespace()
        self.client.teleport.side_effect = lambda p: setattr(self, 'modal', True)
        self.now = 360
        self.assertTrue(await self.handle())
        self.assert_changed()
        self.click.assert_awaited_once_with(self.client, exit_dungeon_path)

    async def test_stale_modal_without_change_is_not_success(self):
        await self.refreshed()
        self.client.teleport.side_effect = lambda p: setattr(self, 'modal', True)
        self.click.side_effect = lambda c, p: setattr(self, 'modal', False)
        self.now = 360
        self.assertTrue(await self.handle())
        self.click.assert_awaited_once()
        self.assertIsNotNone(self.client.quest_dungeon_recovery)
        self.assertFalse(hasattr(self.client, 'quest_party_confirmed_dungeon_transition'))
        self.assert_released()

    async def test_battle_start_after_tp_does_not_confirm(self):
        await self.refreshed()

        def teleport(point):
            self.client.in_battle.return_value = True
            self.modal = True

        self.client.teleport.side_effect = teleport
        self.now = 360
        self.assertTrue(await self.handle())
        self.click.assert_not_awaited()
        self.assert_released()

    def test_dialogue_follower_and_watchdogs_pause_for_exit(self):
        source = Path('XuanShu.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
        self.assertEqual(literals.count('power_core'), 4)


if __name__ == '__main__':
    unittest.main()
