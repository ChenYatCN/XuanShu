import asyncio
import ast
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.automation_ownership import get_client_automation_ownership
from src.paths import npc_range_path
from src.questing import Quester


class NoBloodHideoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.
        self.snapshot = (Quester.NO_BLOOD_QUEST_ID, 4, '跟随 踪迹 地点：Howling Lands')
        self.identity = (Quester.NO_BLOOD_QUEST_ID, 'QuestTitle_18F18A', '不会有鲜血',
                         {'world': 'darkmoor', 'number': 46}, True)
        self.prompt = True
        self.popup = '点击 X 进入'
        self.client = self.make_client('p1')
        self.hitter = self.make_client('p2')
        self.client.quest_party_hitters = [self.hitter]
        self.quester = Quester(self.client, [self.client], None)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot)
        self.quester._mainline_identity = AsyncMock(side_effect=lambda c: self.identity)
        self.quester.read_popup = AsyncMock(side_effect=lambda c: self.popup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        real_sleep = asyncio.sleep

        async def sleep(seconds):
            self.now += seconds
            await real_sleep(0)

        self.stack.enter_context(patch('src.questing.asyncio.sleep', side_effect=sleep))
        self.visible = self.stack.enter_context(patch('src.questing.is_visible_by_path',
            new=AsyncMock(side_effect=lambda c, p: self.prompt if p == npc_range_path else False)))
        self.free = self.stack.enter_context(patch('src.questing.is_free_leader_questing',
            new=AsyncMock(side_effect=lambda c: not c.in_battle.return_value
                          and not c.is_in_dialog.return_value and not c.entity_detect_combat_status)))
        self.stack.enter_context(patch('src.questing.is_free', new=AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.questing.logger'))

        async def teleport(point):
            self.assertEqual(self.client.quest_recovery_owner, 'no_blood_hideout')
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.client.body.position.return_value = point

        async def hitter_tp(point):
            # Catch-up must remain possible while no quest recovery lock is held.
            self.assert_released()
            self.hitter.body.position.return_value = point

        async def key(c, keycode, duration):
            self.assertEqual((keycode, duration), (Keycode.X, .1))
            c.zone_name.return_value = 'Darkmoor/Interiors/VerifiedHideout'
            loading = iter([True, True])
            c.is_loading.side_effect = lambda: next(loading, False)

        self.client.teleport.side_effect = teleport
        self.hitter.teleport.side_effect = hitter_tp
        async def quester_key(k, d):
            await key(self.client, k, d)
        async def hitter_key(k, d):
            await key(self.hitter, k, d)
        self.client.send_key.side_effect = quester_key
        self.hitter.send_key.side_effect = hitter_key

    def make_client(self, title):
        return SimpleNamespace(title=title, questing_status=True, quest_party_status_session=None,
            quest_recovery_owner=None, quest_party_hitters=[], entity_detect_combat_status=False,
            zone_name=AsyncMock(return_value=Quester.NO_BLOOD_HIDEOUT_ZONE),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            teleport=AsyncMock(), send_key=AsyncMock())

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def tick(self):
        result = await self.quester._maybe_handle_no_blood_hideout(self.client)
        self.assert_released()
        return result

    async def test_zero_hud_target_direct_tp_then_existing_party_entry(self):
        self.assertTrue(await self.tick())
        point = self.client.teleport.await_args.args[0]
        self.assertEqual((point.x, point.y, point.z), (-17056.808, 106.266, -483.393))
        self.client.send_key.assert_not_awaited()
        self.assertTrue(await self.tick())
        self.hitter.teleport.assert_awaited_once_with(Quester.NO_BLOOD_HIDEOUT_POSITION)
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.hitter.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_group_dungeon_zone, 'Darkmoor/Interiors/VerifiedHideout')
        self.assertEqual(self.client.quest_dungeon_recovery['mainline_id'], Quester.NO_BLOOD_QUEST_ID)
        self.assertFalse(await self.tick())

    async def test_missing_hitter_waits_without_x_or_repeated_tp(self):
        await self.tick()
        self.hitter.zone_name.return_value = 'World/OtherArea'
        for _ in range(3):
            await self.tick()
        self.client.send_key.assert_not_awaited()
        self.hitter.send_key.assert_not_awaited()
        self.client.teleport.assert_awaited_once()
        self.hitter.teleport.assert_not_awaited()
        self.hitter.zone_name.return_value = Quester.NO_BLOOD_HIDEOUT_ZONE
        await self.tick()
        self.client.send_key.assert_awaited_once()

    async def test_hitter_tp_without_confirmed_landing_does_not_enter(self):
        await self.tick()
        self.hitter.teleport.side_effect = None
        await self.tick()
        self.hitter.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_disabled_or_refilling_hitter_never_bypassed(self):
        await self.tick()
        self.hitter.questing_status = False
        await self.tick()
        self.hitter.questing_status = True
        self.hitter.refilling_potions = True
        await self.tick()
        self.client.send_key.assert_not_awaited()
        self.hitter.teleport.assert_not_awaited()

    async def test_wrong_zone_task_key_goal_and_location_never_use_point(self):
        self.client.zone_name.return_value = 'Darkmoor/DM_Z02_MortalPlain'
        self.assertFalse(await self.tick())
        self.client.zone_name.return_value = Quester.NO_BLOOD_HIDEOUT_ZONE
        original = self.snapshot
        for snapshot in (None, (1, 4, original[2]),
                         (original[0], 4, '对话 阿克托 地点：Howling Lands'),
                         (original[0], 4, '跟随 踪迹 地点：Graveholm')):
            self.snapshot = snapshot
            self.assertFalse(await self.tick())
        self.snapshot = original
        for identity in (None, (1, 'QuestTitle_18F18A', '', None, True),
                         (original[0], 'QuestTitle_19BE8E', '', None, True)):
            self.identity = identity
            self.assertFalse(await self.tick())
        self.client.teleport.assert_not_awaited()

    async def test_assigned_hitter_and_status_session_excluded(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.tick())
        self.quester.clients.pop()
        self.client.quest_party_status_session = object()
        self.assertFalse(await self.tick())
        self.client.teleport.assert_not_awaited()

    async def test_busy_states_and_stop_do_not_tp(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active',
                     'mainline_chain_retry_active', 'entity_detect_combat_status'):
            setattr(self.client, attr, True)
            self.assertTrue(await self.tick())
            setattr(self.client, attr, False)
        for mock in (self.client.is_loading, self.client.in_battle, self.client.is_in_dialog):
            mock.return_value = True
            self.assertTrue(await self.tick())
            mock.return_value = False
        self.client.questing_status = False
        self.assertFalse(await self.tick())
        self.client.teleport.assert_not_awaited()

    async def test_other_owner_is_not_released_or_overridden(self):
        self.client.quest_recovery_owner = 'other'
        self.assertTrue(await self.quester._maybe_handle_no_blood_hideout(self.client))
        self.assertEqual(self.client.quest_recovery_owner, 'other')
        self.client.teleport.assert_not_awaited()

    async def test_changed_goal_after_claim_prevents_tp(self):
        original = self.snapshot
        self.quester._dungeon_quest_snapshot.side_effect = lambda c: (
            (original[0], 99, original[2]) if c.quest_recovery_owner else original)
        await self.tick()
        self.client.teleport.assert_not_awaited()

    async def test_cancelled_tp_is_not_replayed_after_worker_recreation(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.tick()
        self.assert_released()
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        restarted._mainline_identity = self.quester._mainline_identity
        restarted.read_popup = self.quester.read_popup
        self.quester = restarted
        self.now = 6
        await self.tick()
        await self.tick()
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_unconfirmed_quester_landing_is_bounded(self):
        self.client.teleport.side_effect = None
        await self.tick()
        self.now = 6
        await self.tick()
        await self.tick()
        self.client.teleport.assert_awaited_once()
        self.hitter.teleport.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_absent_or_wrong_popup_does_not_press_x(self):
        await self.tick()
        self.prompt = False
        await self.tick()
        self.prompt = True
        self.popup = '点击 X 对话'
        await self.tick()
        self.client.send_key.assert_not_awaited()
        self.hitter.teleport.assert_not_awaited()

    async def test_goal_change_during_gather_prevents_x(self):
        await self.tick()
        original = self.hitter.teleport.side_effect
        async def changed(point):
            await original(point)
            self.snapshot = (self.snapshot[0], 99, '对话 阿克托 地点：Howling Lands')
        self.hitter.teleport.side_effect = changed
        await self.tick()
        self.client.send_key.assert_not_awaited()

    async def test_priority_change_in_entry_identity_read_prevents_x(self):
        await self.tick()
        async def identity(c):
            if get_client_automation_ownership(c).owner_label == 'no-blood-hideout-entry':
                c.refilling_potions = True
            return self.identity
        self.quester._mainline_identity.side_effect = identity
        await self.tick()
        self.client.send_key.assert_not_awaited()

    async def test_single_client_uses_shared_entry_and_confirms_transition(self):
        self.client.quest_party_hitters = []
        await self.tick()
        await self.tick()
        self.client.send_key.assert_awaited_once()
        self.assertEqual(self.client.quest_dungeon_recovery['zone'], 'Darkmoor/Interiors/VerifiedHideout')

    async def test_catchup_priority_change_prevents_hitter_tp(self):
        await self.tick()
        async def position():
            self.client.refilling_potions = True
            return Quester.NO_BLOOD_HIDEOUT_POSITION
        self.client.body.position.side_effect = position
        await self.tick()
        self.hitter.teleport.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_tp_error_stays_bounded_and_releases_worker_ownership(self):
        self.client.teleport.side_effect = RuntimeError('test rejected TP')
        await self.tick()
        await self.tick()
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_legacy_peer_is_gathered_before_shared_entry(self):
        self.client.quest_party_hitters = []
        self.quester.clients.append(self.hitter)
        await self.tick()
        await self.tick()
        self.hitter.teleport.assert_awaited_once_with(Quester.NO_BLOOD_HIDEOUT_POSITION)
        self.hitter.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_solo_hook_reaches_special_with_zero_target(self):
        self.quester._maybe_handle_darkmoor_castle = AsyncMock(return_value=False)
        self.quester._maybe_handle_outback_story = AsyncMock(return_value=False)
        self.quester._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.quester._maybe_handle_bumbles_pet = AsyncMock(return_value=False)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester._maybe_recover_lemuria_navigation = AsyncMock(return_value=False)
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        self.quester.auto_collect_rewrite = AsyncMock()
        with patch('src.questing.close_npc_quest_menu', new=AsyncMock(return_value=False)), \
             patch('src.questing.close_automation_popup', new=AsyncMock(return_value=False)):
            await self.quester.auto_quest_solo()
        self.client.teleport.assert_awaited_once_with(Quester.NO_BLOOD_HIDEOUT_POSITION)
        self.quester.auto_collect_rewrite.assert_not_awaited()

    async def test_direct_tp_hook_prioritizes_entrance_over_supplied_hud(self):
        await self.quester.teleport_to_quest_target(self.client, XYZ(9000, 0, 0))
        self.client.teleport.assert_awaited_once_with(Quester.NO_BLOOD_HIDEOUT_POSITION)
        self.assert_released()

    async def test_leader_hook_uses_current_leader_not_original_client(self):
        original = self.make_client('old leader')
        self.quester.client = original
        self.quester.current_leader_client = self.client
        await self.quester.auto_quest_leader(False, False, None, False, False)
        self.client.teleport.assert_awaited_once_with(Quester.NO_BLOOD_HIDEOUT_POSITION)
        original.teleport.assert_not_awaited()

    def test_hooks_precede_zero_target_gate_and_finder(self):
        tree = ast.parse(Path('src/questing.py').read_text(encoding='utf-8'))
        for name in ('auto_quest_solo', 'auto_quest_leader'):
            method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            calls = [n for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            special = min(n.lineno for n in calls if n.func.attr == '_maybe_handle_no_blood_hideout')
            finder = min(n.lineno for n in calls if n.func.attr == '_maybe_recover_mainline')
            coordinate = min(n.lineno for n in calls if n.func.attr == 'position'
                             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == 'quest_position')
            self.assertLess(special, finder)
            self.assertLess(special, coordinate)


if __name__ == '__main__':
    unittest.main()
