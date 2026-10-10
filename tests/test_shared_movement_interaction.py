"""Regressions for local collection and an incomplete shared-room final walk."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src import questing as q, teleport_math as tm
from src.automation_ownership import get_client_automation_ownership
from tests import test_quest_final_approach as approach_tests


class SharedMovementInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        approach_tests.QuestFinalApproachTests.setUp(self)
        self.client.use_potions = False
        self.client.auto_pet_status = False
        self.client.entity_detect_combat_status = False
        self.peer_position = XYZ(0, 0, 0)
        self.peer = SimpleNamespace(title='p2', questing_status=True,
            refilling_potions=False, quest_recovery_owner=None,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.peer_position)),
            goto=AsyncMock(), send_key=AsyncMock())
        self.client.quest_party_hitters = [self.peer]
        self.client.quest_party_group_dungeon_zone = self.zone
        self.visible = False
        self.popup_title = 'Quest-local object'
        self.prompt = 'Press X to Collect'
        del self.quester.quest_interaction_ready
        self.quester.read_popup = AsyncMock(side_effect=lambda member: self.prompt if member is self.client else '')
        self.quester.party_dungeon_entry_visible = AsyncMock(return_value=False)
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.stack.enter_context(patch.object(q, 'get_popup_title', AsyncMock(side_effect=lambda member: self.popup_title if member is self.client else '')))
        self.stack.enter_context(patch.object(q, 'get_quest_name', AsyncMock(side_effect=lambda _: self.objective)))
        self.stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(side_effect=lambda member, path: self.visible and member is self.client and path == q.npc_range_path)))
        self.proof = self.stack.enter_context(patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(q, 'is_free', AsyncMock(return_value=True)))
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'):
            self.stack.enter_context(patch.object(q, name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.probe = self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=False)

    async def land(self, client, dest, *args):
        if dest is self.target:
            return False
        if client is self.client:
            self.position = dest
        else:
            self.peer_position = dest
        return True

    def west_time_dune(self):
        self.zone = 'Mirage/Interiors/MR_Z10_WestTimeDune'
        self.client.title, self.peer.title = 'p3', 'p4'
        self.client.quest_party_group_dungeon_zone = self.zone
        self.target = XYZ(12781.5595703125, 600.5938110351562, 143.0408935546875)
        self.position = XYZ(12714.2109375, 581.511474609375, 130.707763671875)
        self.visible = True
        self.objective = 'Collect an hourglass in the West Time Dune'
        self.popup_title = '沙漏'
        self.prompt = '按 X 收集'

    def broken_tower(self):
        self.zone = 'Khrysalis/Interiors/KR_Z00_I05_Bastion_BrokenTower'
        self.client.quest_party_group_dungeon_zone = self.zone
        self.target = XYZ(-472.9403076171875, -1489.35302734375, -6.103516352595761e-05)
        self.strict_position = XYZ(-450, -1299.038105676658, 0)
        self.blockers.return_value = [approach_tests.box(-500, -1550, -440, -1450)]

    def assert_drained(self):
        for member in (self.client, self.peer):
            self.assertFalse(getattr(member, 'quest_party_target_sync_active', False))
            self.assertFalse(get_client_automation_ownership(member).locked)

    async def test_west_time_dune_ready_collection_precedes_pending_probe_and_busy_hitter(self):
        self.west_time_dune()
        self.client.quest_party_probe_pending = True
        self.peer.is_loading.return_value = True
        self.probe.return_value = True
        with patch.object(self.quester, 'teleport_party_to_quest_target', AsyncMock()) as group:
            await self.quester.auto_quest_solo()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.peer.send_key.assert_not_awaited()
        self.peer.zone_name.assert_not_awaited()
        self.probe.assert_not_awaited()
        group.assert_not_awaited()
        self.tp.assert_not_awaited()
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.proof.assert_not_awaited()
        self.assert_drained()

    async def test_ready_collect_in_shared_interaction_does_not_require_peer_prompt(self):
        self.west_time_dune()
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.client.send_key.assert_awaited_once()
        self.peer.send_key.assert_not_awaited()
        self.peer.body.position.assert_not_awaited()
        self.proof.assert_not_awaited()

    async def test_ordinary_local_objects_precede_probe_for_both_pairs(self):
        for leader, hitter in (('p1', 'p2'), ('p3', 'p4')):
            for prompt, objective, title in (
                    ('Press X to Collect', 'Collect Hourglass in Area', 'Hourglass'),
                    ('Press X to Use', 'Use Lever in Area', 'Lever'),
                    ('Press X to Open', 'Open Chest in Area', 'Chest')):
                with self.subTest(pair=leader, prompt=prompt):
                    self.west_time_dune()
                    self.client.title, self.peer.title = leader, hitter
                    self.client.quest_party_group_dungeon_zone = None
                    self.client.quest_party_probe_pending = True
                    self.client.quest_interaction_attempt = None
                    self.client._quest_x_turn_failed = None
                    self.prompt, self.objective, self.popup_title = prompt, objective, title
                    self.peer.is_loading.return_value = True
                    self.probe.return_value = True
                    self.probe.reset_mock()
                    self.client.send_key.reset_mock()
                    await self.quester.auto_quest_solo()
                    self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
                    self.peer.send_key.assert_not_awaited()
                    self.probe.assert_not_awaited()
                    self.tp.assert_not_awaited()
                    self.proof.assert_not_awaited()

    async def test_ordinary_unmatched_mechanism_still_waits_for_probe(self):
        self.west_time_dune()
        self.client.quest_party_group_dungeon_zone = None
        self.client.quest_party_probe_pending = True
        self.prompt, self.popup_title = 'Press X to Use', 'Unrelated Lever'
        self.probe.return_value = True
        await self.quester.auto_quest_solo()
        self.probe.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_local_collection_pending_feedback_is_not_owned_by_hitter_probe(self):
        self.west_time_dune()
        self.client.quest_party_group_dungeon_zone = None
        self.client.quest_party_probe_pending = True
        self.probe.return_value = True
        await self.quester.auto_quest_solo()
        state = self.client.quest_interaction_attempt
        self.assertTrue(state['local_only'])
        with patch.object(self.quester, '_recover_quest_x_direction', AsyncMock(return_value=True)) as retry:
            state['next_at'] = 0
            await self.quester.auto_quest_solo()
        retry.assert_awaited_once_with(self.client, state)
        self.probe.assert_not_awaited()
        self.peer.send_key.assert_not_awaited()

    async def test_unknown_empty_hidden_far_or_loading_prompt_never_skips_probe_or_sends_x(self):
        for event in ('unknown', 'empty', 'hidden', 'far', 'loading', 'battle'):
            with self.subTest(event=event):
                self.west_time_dune()
                self.loading = self.battle = False
                if event == 'unknown': self.prompt = 'Press X to Something'
                elif event == 'empty': self.popup_title = ''
                elif event == 'hidden': self.visible = False
                elif event == 'far': self.position = XYZ(self.target.x - 800, self.target.y, self.target.z)
                elif event == 'loading': self.loading = True
                elif event == 'battle': self.battle = True
                self.probe.return_value = True
                self.probe.reset_mock()
                await self.quester.auto_quest_solo()
                if event not in ('loading', 'battle'):
                    self.probe.assert_awaited_once()
                self.client.send_key.assert_not_awaited()
        self.tp.assert_not_awaited()

    async def test_unreadable_prompt_does_not_authorize_local_input(self):
        self.west_time_dune()
        self.quester.read_popup.side_effect = RuntimeError('UI rebuilding')
        self.probe.return_value = True
        await self.quester.auto_quest_solo()
        self.probe.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_ready_prompt_keeps_identity_owner_and_fresh_target_guards(self):
        for event in ('unreadable_quest', 'unreadable_goal', 'target_changed', 'owner'):
            with self.subTest(event=event):
                self.west_time_dune()
                self.quest, self.goal = 42, 7
                self.client.quest_recovery_owner = None
                self.client.quest_position.position.side_effect = lambda: self.target
                if event == 'unreadable_quest': self.quest = None
                elif event == 'unreadable_goal': self.goal = None
                elif event == 'target_changed': self.client.quest_position.position.side_effect = lambda: XYZ(0, 0, 0)
                elif event == 'owner': self.client.quest_recovery_owner = 'potion_refill'
                self.assertTrue(await self.quester.handle_quest_interaction(self.client, self.target))
                self.client.send_key.assert_not_awaited()
        self.assert_drained()

    async def test_ready_collect_keeps_existing_x_cooldown(self):
        self.west_time_dune()
        await self.quester.auto_quest_solo()
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_awaited_once()

    async def test_door_with_missing_hitter_prompt_still_waits_for_group(self):
        self.west_time_dune()
        self.prompt = 'Press X to Open'
        self.assertFalse(await self.quester._quest_local_interaction_ready(self.client, self.target))
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.client.send_key.assert_not_awaited()
        self.peer.send_key.assert_not_awaited()

    async def test_broken_tower_real_shared_collision_zero_nodes_gets_bounded_final_walk(self):
        self.broken_tower()
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        reports = self.client.quest_party_shared_target['move_results']
        for member in (self.client, self.peer):
            self.assertEqual(reports[id(member)]['waypoints'], 0)
            self.assertTrue(reports[id(member)]['truncated'])
            self.assertFalse(reports[id(member)]['walk_completed'])
        self.assertEqual(self.tp.await_count, 4)
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)
        self.peer.goto.assert_not_awaited()
        self.assertLess(q.calc_Distance(self.position, self.target), 5)
        self.navmap.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assert_drained()

    async def test_broken_tower_failed_final_walk_returns_false_and_does_not_repeat_tp(self):
        self.broken_tower()
        self.client.goto.side_effect = None
        for _ in range(3):
            self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.assertEqual(self.tp.await_count, 4)
        self.client.goto.assert_awaited_once()
        self.navmap.assert_awaited_once()
        self.log.warning.assert_called_once()
        self.assert_drained()

    async def test_truncated_report_does_not_reject_a_client_already_at_exact_target(self):
        self.broken_tower()
        async def completed(member, xyz, **kwargs):
            self.client.quest_party_shared_target['move_results'][id(member)].update(
                landed=True, walk_attempted=True, walk_completed=False, truncated=True, waypoints=0)
            if member is self.client:
                self.position = XYZ(xyz.x, xyz.y, xyz.z)
        with patch.object(self.quester, 'teleport_to_quest_target', AsyncMock(side_effect=completed)):
            self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.assert_drained()

    async def test_broken_tower_progress_change_permits_new_task_movement(self):
        self.broken_tower()
        self.client.goto.side_effect = None
        self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
        self.objective = 'Go to the tower (1/2)'
        async def walk(x, y): self.position = XYZ(x, y, 0)
        self.client.goto.side_effect = walk
        self.assertTrue(await self.quester.teleport_party_to_quest_target(self.target))
        self.assertEqual(self.tp.await_count, 8)
        self.assertEqual(self.quester._quest_approach_failed, {})

    async def test_missing_group_proof_or_busy_hitter_never_authorizes_fallback(self):
        self.broken_tower()
        for event in ('proof', 'loading', 'zone', 'stopped'):
            with self.subTest(event=event):
                self.proof.return_value = True
                self.peer.is_loading.return_value = False
                self.peer.zone_name.side_effect = lambda: self.zone
                self.peer.questing_status = True
                if event == 'proof': self.proof.return_value = False
                elif event == 'loading': self.peer.is_loading.return_value = True
                elif event == 'zone': self.peer.zone_name.side_effect = lambda: 'Other'
                elif event == 'stopped': self.peer.questing_status = False
                self.assertFalse(await self.quester.teleport_party_to_quest_target(self.target))
                self.tp.assert_not_awaited()
                self.client.goto.assert_not_awaited()
                self.navmap.assert_not_awaited()
        self.assert_drained()

    async def test_late_hitter_zone_change_blocks_final_walk(self):
        self.broken_tower()
        original = self.land
        async def land(member, dest, *args):
            landed = await original(member, dest, *args)
            if landed and member is self.peer:
                self.peer.zone_name.side_effect = lambda: 'Other'
            return landed
        self.tp.side_effect = land
        await self.quester.teleport_party_to_quest_target(self.target)
        self.client.goto.assert_not_awaited()
        self.navmap.assert_not_awaited()
        self.assert_drained()

    async def test_collection_appearing_during_group_move_cancels_peers_then_runs_checked_x(self):
        self.west_time_dune()
        self.visible = False
        cancelled = []
        async def move(member, *args, **kwargs):
            if member is self.client:
                self.visible = True
            try:
                await asyncio.Future()
            finally:
                cancelled.append(member.title)
        with patch.object(self.quester, 'teleport_to_quest_target', AsyncMock(side_effect=move)):
            await self.quester.auto_quest_solo()
        self.assertCountEqual(cancelled, ['p3', 'p4'])
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.peer.send_key.assert_not_awaited()
        self.assert_drained()


if __name__ == '__main__':
    unittest.main()
