"""Transient target X must not erase a rejected-landing retry chain."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import questing as q
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests import test_quest_movement_failure_lifecycle as lifecycle


class QuestTeleportRollbackTests(unittest.IsolatedAsyncioTestCase):
    move = lifecycle.MovementFailureLifecycleTests.move

    def setUp(self):
        lifecycle.MovementFailureLifecycleTests.setUp(self)
        self.origin = XYZ(-852, 8, 120)
        self.target = XYZ(-6.113241195678711, -70.63642883300781, 133.8686065673828)
        self.position = self.origin
        self.zone = 'Khrysalis/KR_Z07_RuinedAlcazar'
        self.quest, self.goal = 155655662430208256, 2559431
        self.battle, self.loading = False, False
        self.client.in_battle = AsyncMock(side_effect=lambda: self.battle)
        self.client.is_loading = AsyncMock(side_effect=lambda: self.loading)
        self.drained, self.started = asyncio.Event(), asyncio.Event()
        self.ticks, self.on_tick = 0, None
        self.nav.side_effect = self.transient_landing
        self.spiral = patch.object(q.teleport_math, 'fallback_spiral_tp', AsyncMock())
        self.spiral_mock = self.spiral.start()
        self.addCleanup(self.spiral.stop)

        async def tick(_):
            await lifecycle.REAL_SLEEP(0)
            if self.started.is_set():
                self.ticks += 1
                if self.on_tick:
                    self.on_tick()
        self.tick_patch = patch.object(q.asyncio, 'sleep', tick)
        self.tick_patch.start()
        self.addCleanup(self.tick_patch.stop)

    async def transient_landing(self, *args, **kwargs):
        self.position, self.interaction = self.target, True
        self.started.set()
        try:
            for _ in range(15):
                await q.asyncio.sleep(.1)  # Native path completes; X cannot cancel it.
        finally:
            self.drained.set()

    def rollback(self):
        if self.ticks >= 2:
            self.position = self.origin

    async def rejected(self):
        self.on_tick = self.rollback
        self.assertFalse(await self.move())
        record = self.runner._quest_movement_waiting[id(self.client)]
        self.assertEqual(record['reason'], 'landing_rejected')
        self.assertTrue(self.drained.is_set())
        return record

    async def test_stable_npc_or_collectible_candidate_is_confirmed_after_nav_drains(self):
        self.assertTrue(await self.move())
        self.assertGreaterEqual(self.ticks, 10)
        self.assertTrue(self.drained.is_set())
        self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_transient_target_then_server_rollback_retains_signature(self):
        record = await self.rejected()
        self.assertEqual(record['signature'][0], (self.quest, self.goal, self.zone))
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(record['rollback_count'], 1)
        self.assertEqual(record['next_at'], 5)

    async def test_origin_npc_cannot_clear_failure_or_bypass_cooldown(self):
        record = await self.rejected()
        self.assertTrue(self.interaction)  # Visible unrelated NPC remains visible.
        self.assertFalse(await self.runner._quest_local_interaction_ready(self.client, self.target))
        self.assertIs(self.runner._quest_movement_waiting[id(self.client)], record)
        self.now = 1
        self.assertFalse(await self.move())
        self.nav.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_real_readiness_gate_blocks_origin_even_inside_old_750_radius(self):
        await self.rejected()
        self.position = XYZ(self.target.x - 740, self.target.y, self.target.z)
        record = self.runner._quest_movement_waiting[id(self.client)]
        record['pre_move'] = self.position
        self.runner.quest_interaction_ready = q.Quester.quest_interaction_ready.__get__(self.runner)
        with patch.object(q, 'is_visible_by_path', AsyncMock(return_value=True)):
            self.assertFalse(await self.runner.quest_interaction_ready(self.client, self.target))

    async def test_repeated_rollback_retries_full_path_and_runs_existing_recovery(self):
        await self.rejected()
        self.runner.handle_repeated_normal_quest_failures = AsyncMock()
        for when, calls in ((1, 1), (5, 2), (6, 2), (10, 3), (11, 3), (39, 3), (40, 4)):
            self.now = when
            self.drained.clear()
            self.ticks = 0
            self.assertFalse(await self.move())
            self.assertEqual(self.nav.await_count, calls)
        self.assertNotIn('reenter', self.nav.await_args_list[0].kwargs)
        self.assertTrue(all(call.kwargs == {} for call in self.nav.await_args_list))
        self.runner.handle_repeated_normal_quest_failures.assert_awaited_once()
        self.assertEqual(self.runner._quest_movement_waiting[id(self.client)]['attempts'], 1)

    async def test_context_changes_invalidate_origin_suppression(self):
        for event in ('quest', 'goal', 'zone', 'target', 'progress', 'group'):
            with self.subTest(event=event):
                self.runner._quest_movement_waiting.clear()
                self.position, self.interaction = self.origin, False
                self.drained.clear()
                self.ticks = 0
                await self.rejected()
                if event == 'quest': self.quest += 1
                elif event == 'goal': self.goal += 1
                elif event == 'zone': self.zone += '/Next'
                elif event == 'target': self.target = XYZ(2000, 0, 0)
                elif event == 'progress': self.progress += ' 1/2'
                else: self.client.quest_party_hitters = [object()]
                self.assertFalse(await self.runner._quest_rollback_origin_interaction(self.client, self.target))
                self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_actual_progress_or_zone_during_confirmation_hands_off_immediately(self):
        for event in ('quest', 'goal', 'progress', 'zone'):
            with self.subTest(event=event):
                self.position, self.interaction = self.origin, False
                self.drained.clear()
                self.ticks = 0
                def advance():
                    if self.ticks == 1:
                        if event == 'quest': self.quest += 1
                        elif event == 'goal': self.goal += 1
                        elif event == 'progress': self.progress += ' 1/2'
                        else: self.zone += '/Next'
                self.on_tick = advance
                self.assertEqual(await self.move(), event == 'zone')
                self.assertLess(self.ticks, 10)
                self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_real_dialogue_or_battle_hands_off_without_full_window(self):
        for event in ('dialogue', 'battle', 'loading'):
            with self.subTest(event=event):
                self.position, self.interaction, self.free = self.origin, False, True
                self.battle, self.loading = False, False
                self.drained.clear()
                self.ticks = 0
                def native():
                    if self.ticks == 1:
                        self.free = False
                        self.battle = event == 'battle'
                        self.loading = event == 'loading'
                self.on_tick = native
                self.assertFalse(await self.move())
                self.assertLess(self.ticks, 10)
                self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_stop_or_cancel_during_confirmation_releases_owned_tasks(self):
        self.on_tick = lambda: setattr(self.client, 'questing_status', False)
        self.assertFalse(await self.runner.move_until_quest_interaction(self.client, self.target))
        self.assertEqual(self.runner._quest_movement_waiting, {})
        self.client.questing_status = True
        self.position, self.interaction = self.origin, False
        self.drained.clear()
        task = asyncio.create_task(self.move())
        self.on_tick = lambda: task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertTrue(self.drained.is_set())

    async def test_two_group_failure_records_and_foreign_owner_are_isolated(self):
        peer = SimpleNamespace(title='p3')
        sentinel = {'attempts': 2}
        self.runner._quest_movement_waiting[id(peer)] = sentinel
        async with automation_owner(peer, 'npc-dialogue'):
            await self.rejected()
            self.assertIs(self.runner._quest_movement_waiting[id(peer)], sentinel)
            self.assertTrue(get_client_automation_ownership(peer).locked)

    async def test_normal_shared_member_uses_retained_target_and_preserves_sync_flags(self):
        leader = self.client
        member = SimpleNamespace(**vars(self.client))
        member.title = 'p2'
        member.quest_party_quester = leader
        member.quest_party_target_sync_active = True
        leader.quest_party_hitters = [member]
        leader.quest_party_shared_target = dict(identity=(self.quest, self.goal), zone=self.zone,
            xyz=self.target, progress=(self.quest, self.goal, self.progress), source_tokens={})
        with patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True)):
            self.assertTrue(await self.runner.move_until_quest_interaction(member, self.target, leader))
        self.assertTrue(member.quest_party_target_sync_active)
        self.assertFalse(get_client_automation_ownership(member).locked)

    async def test_shared_parent_does_not_cancel_member_stability_for_transient_x(self):
        member = SimpleNamespace(**vars(self.client))
        member.title, member.quest_party_quester = 'p2', self.client
        self.client.quest_party_hitters = [member]
        self.client.quest_party_group_dungeon_zone = self.zone
        peer_position = self.origin
        member.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: peer_position))
        self.runner.quest_interaction_ready.side_effect = lambda c, *a: (
            self.interaction and q.calc_Distance(self.position if c is self.client else peer_position, self.target) < 60)
        async def land(c, *a, **kw):
            nonlocal peer_position
            if c is self.client:
                self.position = self.target
            else:
                peer_position = self.target
            self.interaction = True
            self.started.set()
            try:
                for _ in range(15):
                    await q.asyncio.sleep(.1)
            finally:
                self.drained.set()
        self.nav.side_effect = land
        self.runner._note_quest_entry_wait = lambda *a, **kw: None
        def recoil():
            nonlocal peer_position
            self.rollback()
            if self.ticks >= 2:
                peer_position = self.origin
        self.on_tick = recoil
        with (patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True)),
              patch.object(q, '_party_area_token', AsyncMock(return_value=('room', 1))),
              patch.object(q, 'is_free', AsyncMock(return_value=True))):
            async with asyncio.timeout(2):
                self.assertFalse(await self.runner.teleport_party_to_quest_target(self.target))
        self.assertIn(id(self.client), self.runner._quest_movement_waiting)
        self.assertEqual(self.runner._quest_movement_waiting[id(self.client)]['reason'], 'landing_rejected')
        self.assertNotIn(id(member), self.runner._quest_movement_waiting)
        self.assertEqual(self.nav.await_count, 1)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(self.client.quest_party_target_sync_active)

    async def test_rollback_retry_executes_real_nav_landing_and_existing_walk(self):
        await self.rejected()
        self.now, self.on_tick = 5, None
        self.nav.side_effect = q.teleport_math.navmap_tp
        async def teleport(point):
            self.position = self.origin if point is self.target else point
            self.interaction = q.calc_Distance(point, self.target) <= 60
        async def walk(x, y):
            self.position = XYZ(x, y, self.target.z)
            self.interaction = True
        self.client.teleport.side_effect = teleport
        self.client.goto = AsyncMock(side_effect=walk)
        tm = q.teleport_math
        vertices = [XYZ(self.target.x - 300, self.target.y - 100, self.target.z),
                    XYZ(self.target.x - 400, self.target.y, self.target.z)]
        with (patch.object(tm, 'is_free', AsyncMock(return_value=True)),
              patch.object(tm, 'load_wad', AsyncMock(return_value=SimpleNamespace(get_file=AsyncMock(return_value=b'nav')))),
              patch.object(tm, 'parse_nav_data', return_value=(vertices, [(0, 1)]))):
            self.assertTrue(await self.move())
        self.nav.assert_awaited_with(self.client, self.target)
        self.assertIs(self.client.teleport.await_args_list[0].args[0], self.target)
        self.client.goto.assert_awaited_once_with(self.target.x, self.target.y)
        self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_missing_nav_data_is_handled_by_native_path_on_fresh_retry(self):
        await self.rejected()
        self.now, self.on_tick = 5, None
        self.nav.side_effect = q.teleport_math.navmap_tp
        async def spiral(*args):
            self.position = self.target
        self.spiral_mock.side_effect = spiral
        with (patch.object(q.teleport_math, 'is_free', AsyncMock(return_value=True)),
              patch.object(q.teleport_math, 'load_wad', AsyncMock(side_effect=ValueError('no nav')))):
            self.assertTrue(await self.move())
        self.spiral_mock.assert_awaited_once_with(self.client, self.target)

    async def test_origin_dialogue_without_progress_keeps_rejected_landing(self):
        record = await self.rejected()
        self.free = False
        self.assertFalse(await self.move())
        self.assertIs(self.runner._quest_movement_waiting[id(self.client)], record)
        self.nav.assert_awaited_once()

    async def test_origin_dialogue_that_advances_goal_removes_suppression(self):
        await self.rejected()
        self.free = False
        self.goal += 1
        self.assertFalse(await self.runner._quest_rollback_origin_interaction(self.client, self.target))
        self.assertEqual(self.runner._quest_movement_waiting, {})

    async def test_native_nav_keeps_running_until_candidate_is_stably_confirmed(self):
        observed = []
        def sample():
            if self.ticks < 10:
                observed.append(self.drained.is_set())
        self.on_tick = sample
        self.assertTrue(await self.move())
        self.assertTrue(observed)
        self.assertFalse(any(observed))
        self.assertTrue(self.drained.is_set())

    async def test_late_server_rollback_revokes_accepted_landing_before_next_x(self):
        self.assertTrue(await self.move())
        self.assertIn(id(self.client), self.runner._quest_movement_landings)
        self.position = self.origin
        self.now = 1
        self.assertTrue(await self.runner._quest_rollback_origin_interaction(self.client, self.target))
        self.assertNotIn(id(self.client), self.runner._quest_movement_landings)
        failure = self.runner._quest_movement_waiting[id(self.client)]
        self.assertEqual((failure['reason'], failure['attempts']), ('landing_rejected', 1))
        self.assertFalse(await self.runner._quest_local_interaction_ready(self.client, self.target))
        self.client.send_key.assert_not_awaited()

    async def test_small_target_and_html_jitter_preserve_retry_and_origin_suppression(self):
        record = await self.rejected()
        self.target = XYZ(self.target.x + 5, self.target.y, self.target.z)
        self.progress = '<center>' + self.progress + '</center>'
        self.assertTrue(await self.runner._quest_rollback_origin_interaction(self.client, self.target))
        self.assertIs(self.runner._quest_movement_waiting[id(self.client)], record)
        self.now = 1
        self.assertFalse(await self.move())
        self.nav.assert_awaited_once()

    async def test_guidance_disappearance_releases_coordinate_without_claiming_quest_success(self):
        self.on_tick = lambda: setattr(self, 'target', XYZ(0, 0, 0))
        old_target = self.target
        self.assertFalse(await self.runner.move_until_quest_interaction(self.client, old_target))
        self.assertTrue(self.drained.is_set())
        self.client.send_key.assert_not_awaited()
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_auto_loop_does_not_handoff_origin_npc_while_rollback_cools_down(self):
        record = await self.rejected()
        self.now = 1
        self.client.quest_party_hitters = [SimpleNamespace(title='p2')]
        signature = record['signature']
        group = (id(self.client), tuple((id(p), id(getattr(p, 'quest_party_status_session', None)))
                                       for p in self.client.quest_party_hitters), id(self.client))
        record['signature'] = (*signature[:3], group)
        self.runner._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.runner._unguided_collect_context = AsyncMock(return_value=None)
        self.runner._quest_party_probe_blocks_movement = AsyncMock(return_value=True)
        self.runner.handle_quest_interaction = AsyncMock()
        self.runner.handle_npc_talking_quests = AsyncMock()
        await self.runner.auto_quest_solo()
        self.runner.handle_quest_interaction.assert_not_awaited()
        self.runner.handle_npc_talking_quests.assert_not_awaited()
        self.client.send_key.assert_not_awaited()
        self.assertIs(self.runner._quest_movement_waiting[id(self.client)], record)

