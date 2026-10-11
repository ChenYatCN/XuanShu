"""Exercise the basic movement order with real Quester/navmap entry points."""
import ast
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src import questing as q, teleport_math as tm
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests.test_quest_x_turn_recovery import REAL_SLEEP


class BasicTaskOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.events = 0, []
        self.target, self.position = XYZ(3000, 100, 0), XYZ(10, 100, 0)
        self.zone, self.quest, self.goal = 'World/NewArea', 42, 7
        self.progress, self.visible = 'Use object (0/1)', False
        self.free, self.loading, self.battle, self.selector = True, False, False, False
        self.client = SimpleNamespace(title='p1', process_id=1, questing_status=True,
            quest_party_hitters=[], quest_party_probe_pending=False,
            use_potions=False, auto_pet_status=False, entity_detect_combat_status=False,
            is_loading=AsyncMock(side_effect=lambda: self.loading),
            in_battle=AsyncMock(side_effect=lambda: self.battle),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.quest),
            goal_id=AsyncMock(side_effect=lambda: self.goal),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            teleport=AsyncMock(), goto=AsyncMock(), send_key=AsyncMock())
        self.runner = q.Quester(self.client, [self.client], None)
        self.runner._dungeon_quest_snapshot = AsyncMock(side_effect=lambda _: (
            self.quest, self.goal, self.progress))
        self.runner._quest_dialogue_blocks_movement = AsyncMock(return_value=False)
        self.runner._quest_party_probe_blocks_movement = AsyncMock(return_value=False)
        self.runner.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.runner.handle_questing_zone_change = AsyncMock()
        self.runner._note_quest_x_blocked = AsyncMock()
        self.runner._note_quest_x_lock_wait = AsyncMock()
        self.runner._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.runner.read_popup = AsyncMock(return_value='Press X to Use')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(q, 'logger'))
        self.stack.enter_context(patch.object(tm, 'logger'))
        self.stack.enter_context(patch.object(q, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        async def tick(seconds):
            self.now += seconds
            await REAL_SLEEP(0)
        self.stack.enter_context(patch.object(q.asyncio, 'sleep', tick))
        self.stack.enter_context(patch.object(q, 'is_free_leader_questing', AsyncMock(
            side_effect=lambda _: self.free and not self.loading and not self.battle)))
        self.stack.enter_context(patch.object(q, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(tm, 'is_free', AsyncMock(return_value=True)))
        self.stack.enter_context(patch.object(q, 'is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: path == q.npc_range_path and self.visible)))
        self.stack.enter_context(patch.object(q, 'get_popup_title', AsyncMock(return_value='World Gate')))
        self.stack.enter_context(patch.object(q, 'is_spiral_door_open', AsyncMock(
            side_effect=lambda _: self.selector)))
        self.stack.enter_context(patch.object(q, 'get_quest_name', AsyncMock(return_value='Use object')))
        for name in ('is_potion_needed', 'close_npc_quest_menu', 'close_automation_popup'):
            self.stack.enter_context(patch.object(q, name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.nav = self.stack.enter_context(patch.object(q, 'navmap_tp', AsyncMock()))

    def add_hitter(self, *, zone=None, same_area=True):
        state = {'position': XYZ(10, 100, 0), 'zone': zone or self.zone}
        peer = SimpleNamespace(title='p2', questing_status=True,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(side_effect=lambda: state['zone']),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: state['position'])))
        async def follow(dest):
            self.events.append(('follow', dest))
            state['position'] = dest
        peer.teleport = AsyncMock(side_effect=follow)
        self.client.quest_party_hitters = [peer]
        self.proof = self.stack.enter_context(patch.object(q, 'clients_share_live_area',
                                                      AsyncMock(return_value=same_area)))
        return peer, state

    async def test_navmap_return_at_origin_is_failure_and_retries_without_toggle(self):
        self.assertFalse(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.now = 1
        self.assertFalse(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.nav.assert_awaited_once_with(self.client, self.target)
        self.now = 6
        async def arrive(*args, **kwargs):
            self.position = self.target
        self.nav.side_effect = arrive
        self.assertTrue(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.nav.await_count, 2)
        self.assertTrue(all(call.kwargs == {} for call in self.nav.await_args_list))
        self.assertTrue(self.client.questing_status)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_native_direct_nav_nav_spiral_path_matches_manual_and_is_not_cut_at_30s(self):
        import wizwalker
        origin = self.position
        events, phase = [], ['']
        async def teleport(dest):
            events.append(('teleport', (dest.x, dest.y, dest.z)))
            phase[0] = 'direct' if len(events) == 1 else 'nav'
            self.position = self.target if phase[0] == 'direct' else origin
        self.client.teleport.side_effect = teleport
        async def tick(seconds):
            self.now += seconds
            if seconds == 1 and phase[0] == 'direct':
                for _ in range(5):
                    await REAL_SLEEP(0)
                self.position = origin  # Reject a transient landing before native check.
            await REAL_SLEEP(0)
        async def spiral(client, dest):
            events.append(('spiral', (dest.x, dest.y, dest.z)))
            await tick(35)  # Native fallback outlives the former outer 30s cutoff.
            self.position = dest
        wad = SimpleNamespace(get_file=AsyncMock(return_value=b'nav'))
        vertices = [XYZ(2000, 100, 0)]
        with (patch.object(q.asyncio, 'sleep', tick),
              patch.object(tm, 'load_wad', AsyncMock(return_value=wad)),
              patch.object(tm, 'parse_nav_data', Mock(return_value=(vertices, []))),
              patch.object(tm, 'get_neighbors', Mock(return_value=[])),
              patch.object(tm, 'fallback_spiral_tp', AsyncMock(side_effect=spiral))):
            self.visible = True
            self.nav.side_effect = tm.navmap_tp
            self.assertTrue(await self.runner.teleport_to_quest_target(self.client, self.target))
            auto_events = list(events)
            self.assertEqual([e[0] for e in auto_events], ['teleport'] * 3 + ['spiral'])
            self.assertGreater(self.now, 30)
            events.clear()
            self.position, self.now = origin, 0
            tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
            node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                        and n.name == 'navmap_teleport')
            env = dict(wizwalker=wizwalker, Client=wizwalker.Client, XYZ=XYZ, asyncio=asyncio,
                       navmap_tp=tm.navmap_tp, logger=Mock(),
                       get_client_automation_ownership=get_client_automation_ownership)
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'XuanShu.py', 'exec'), env)
            await env['navmap_teleport'](self.client, [])
            self.assertEqual(events, auto_events)

    async def test_native_nav_walk_finishes_even_with_a_nearby_x(self):
        async def path(client, dest):
            self.position, self.visible = XYZ(dest.x - 400, dest.y, dest.z), True
            self.events.append('nav')
            for _ in range(15):
                await q.asyncio.sleep(.1)
            self.events.append('walk')
            self.position = dest
        self.nav.side_effect = path
        self.assertTrue(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.events, ['nav', 'walk'])
        self.client.send_key.assert_not_awaited()

    async def test_transient_progress_read_does_not_cancel_native_path(self):
        async def path(client, dest):
            self.runner._dungeon_quest_snapshot.return_value = None
            self.runner._dungeon_quest_snapshot.side_effect = None
            await q.asyncio.sleep(1)
            self.position = dest
        self.nav.side_effect = path
        self.assertTrue(await self.runner.teleport_to_quest_target(self.client, self.target))

    async def test_watcher_body_read_error_does_not_cancel_native_fallback(self):
        async def path(client, dest):
            self.position, self.visible = XYZ(dest.x - 400, dest.y, dest.z), True
            self.client.body.position.side_effect = OSError('transient memory read')
            for _ in range(15):
                await q.asyncio.sleep(.1)
            self.client.body.position.side_effect = lambda: self.position
            self.position = dest
            self.events.append('native-finished')
        self.nav.side_effect = path
        self.assertTrue(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.events, ['native-finished'])

    async def test_zero_guidance_during_native_fallback_yields_only_after_native_finishes(self):
        original = self.target
        async def path(client, dest):
            self.target = XYZ(0, 0, 0)
            for _ in range(15):
                await q.asyncio.sleep(.1)
            self.position = dest
            self.events.append('native-finished')
        self.nav.side_effect = path
        self.assertFalse(await self.runner.teleport_to_quest_target(self.client, original))
        self.assertEqual(self.events, ['native-finished'])
        self.client.send_key.assert_not_awaited()

    async def test_world_arrival_with_birth_gate_x_moves_before_interaction(self):
        self.visible = True  # The spawn gate remains visible, far from current target.
        async def move(client, target):
            self.events.append('move')
            self.position = target
        self.nav.side_effect = move
        async def interact(client, target):
            self.events.append('X')
            self.assertLess(q.calc_Distance(self.position, self.target), 60)
            return True
        self.runner.handle_quest_interaction = AsyncMock(side_effect=interact)
        await self.runner.auto_quest_solo()
        self.assertEqual(self.events, ['move', 'X'])

    async def test_failed_world_arrival_does_not_interact_with_birth_gate(self):
        self.visible = True
        self.runner.handle_quest_interaction = AsyncMock()
        await self.runner.auto_quest_solo()
        self.runner.handle_quest_interaction.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_auto_iterations_retry_same_target_without_manual_intervention(self):
        self.visible = True
        self.runner.handle_quest_interaction = AsyncMock(return_value=True)
        await self.runner.auto_quest_solo()
        self.runner.handle_quest_interaction.assert_not_awaited()
        self.now = 6
        async def arrive(client, dest):
            self.position = dest
        self.nav.side_effect = arrive
        await self.runner.auto_quest_solo()
        self.assertEqual(self.nav.await_count, 2)
        self.runner.handle_quest_interaction.assert_awaited_once_with(self.client, self.target)
        self.assertTrue(self.client.questing_status)

    async def test_real_x_world_selection_then_new_world_moves_before_next_x(self):
        self.visible = True
        first = self.target
        async def arrive(client, dest):
            self.events.append(('move', self.zone, dest))
            self.position = dest
        async def key(code, seconds):
            self.events.append(('X', self.zone, self.position))
            if self.target is first:
                self.selector = True
        async def select(client):
            self.events.append(('select', self.zone))
            self.selector = False
            self.zone = 'NextWorld/BirthArea'
            self.goal += 1
            self.target, self.position = XYZ(9000, 100, 0), XYZ(100, 100, 0)
            return True
        self.nav.side_effect = arrive
        self.client.send_key.side_effect = key
        self.runner.new_world_doors = AsyncMock(side_effect=select)
        await self.runner.auto_quest_solo()
        await self.runner.auto_quest_solo()
        self.assertEqual([event[0] for event in self.events], ['move', 'X', 'select', 'move', 'X'])
        self.runner.new_world_doors.assert_awaited_once()
        self.assertIs(self.events[-1][2], self.target)

    async def test_existing_selector_finishes_before_new_target_move(self):
        self.selector = True
        async def finish(client):
            self.selector = False
            self.events.append('finish-selector')
            return True
        self.runner.new_world_doors = AsyncMock(side_effect=finish)
        await self.runner.auto_quest_solo()
        self.nav.assert_not_awaited()
        async def move(client, target):
            self.events.append('move')
            self.position = target
        self.nav.side_effect = move
        await self.runner.auto_quest_solo()
        self.assertEqual(self.events, ['finish-selector', 'move'])
        self.runner.new_world_doors.assert_awaited_once()

    async def test_gate_itself_as_target_still_moves_then_interacts(self):
        self.visible = True
        async def move(client, target):
            self.position = target
            self.events.append('move')
        self.nav.side_effect = move
        self.client.send_key.side_effect = lambda key, seconds: self.events.append(key)
        self.runner.new_world_doors = AsyncMock(return_value=True)
        await self.runner.auto_quest_solo()
        self.assertEqual(self.events, ['move', Keycode.X])

    async def test_unarrived_quester_does_not_move_hitter_or_publish_candidate(self):
        peer, _ = self.add_hitter()
        self.assertFalse(await self.runner.teleport_party_to_quest_target(self.target))
        self.nav.assert_awaited_once_with(self.client, self.target)
        peer.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, 'quest_party_shared_target', None))
        self.assertFalse(self.client.quest_party_target_sync_active)

    async def test_arrival_then_same_area_follow_then_x(self):
        peer, _ = self.add_hitter()
        self.visible = True
        async def move(client, target):
            self.assertTrue(self.client.quest_party_target_sync_active)
            peer.teleport.assert_not_awaited()
            self.events.append(('move', target))
            self.position = target
        self.nav.side_effect = move
        async def interact(client, target):
            self.events.append(('X', target))
            return True
        self.runner.handle_quest_interaction = AsyncMock(side_effect=interact)
        await self.runner.auto_quest_solo()
        self.assertEqual([e[0] for e in self.events], ['move', 'follow', 'X'])
        self.assertIs(peer.teleport.await_args.args[0], self.position)

    async def test_different_area_hitter_never_receives_quester_coordinates(self):
        peer, _ = self.add_hitter(zone='World/OldArea', same_area=False)
        async def move(client, target):
            self.position = target
        self.nav.side_effect = move
        self.assertFalse(await self.runner.teleport_party_to_quest_target(self.target))
        peer.teleport.assert_not_awaited()
        self.assertFalse(self.client.quest_party_target_sync_active)

    async def test_real_zone_transition_releases_existing_friend_probe_without_old_x(self):
        peer, _ = self.add_hitter()
        self.visible = True
        async def move(client, target):
            self.zone = 'World/NextArea'
        self.nav.side_effect = move
        self.runner.handle_quest_interaction = AsyncMock()
        await self.runner.auto_quest_solo()
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertFalse(self.client.quest_party_target_sync_active)
        peer.teleport.assert_not_awaited()
        self.runner.handle_quest_interaction.assert_not_awaited()

    async def test_old_goal_after_movement_cannot_authorize_x(self):
        async def move(client, target):
            self.goal += 1
            self.position, self.visible = target, True
        self.nav.side_effect = move
        self.runner.handle_quest_interaction = AsyncMock()
        await self.runner.auto_quest_solo()
        self.runner.handle_quest_interaction.assert_not_awaited()

    async def test_real_dialogue_is_handled_before_any_movement(self):
        self.runner._quest_dialogue_blocks_movement.return_value = True
        await self.runner.auto_quest_solo()
        self.nav.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_zero_coordinate_enters_existing_collector(self):
        self.target = XYZ(0, 0, 0)
        self.runner.auto_collect_rewrite = AsyncMock()
        await self.runner.auto_quest_solo()
        self.runner.auto_collect_rewrite.assert_awaited_once_with(self.client)
        self.nav.assert_not_awaited()

    async def test_target_changed_while_waiting_for_input_lock_skips_old_then_reads_new(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def owner():
            async with automation_owner(self.client, 'other-input'):
                started.set()
                await release.wait()
        owned = asyncio.create_task(owner())
        await started.wait()
        task = asyncio.create_task(self.runner.auto_quest_solo())
        for _ in range(5):
            await REAL_SLEEP(0)
        self.target = XYZ(4000, 100, 0)
        release.set()
        await owned
        await task
        self.nav.assert_not_awaited()
        async def move(client, target):
            self.position = target
        self.nav.side_effect = move
        await self.runner.auto_quest_solo()
        self.nav.assert_awaited_once_with(self.client, self.target)

    async def test_user_stop_cancels_native_movement_and_releases_its_input(self):
        entered, drained = asyncio.Event(), asyncio.Event()
        async def path(*args):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
        self.nav.side_effect = path
        task = asyncio.create_task(self.runner.teleport_to_quest_target(self.client, self.target))
        await entered.wait()
        self.client.questing_status = False
        self.assertFalse(await task)
        self.assertTrue(drained.is_set())
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(self.client.questing_status)

    async def test_existing_recovery_rearms_same_target_instead_of_permanent_failure_ban(self):
        self.runner.handle_repeated_normal_quest_failures = AsyncMock()
        for self.now in (0, 6, 12, 43, 49):
            self.assertFalse(await self.runner.teleport_to_quest_target(self.client, self.target))
        self.assertEqual(self.nav.await_count, 5)
        self.runner.handle_repeated_normal_quest_failures.assert_awaited_once()
        self.assertTrue(self.client.questing_status)


class ProductionFollowOrderTests(unittest.IsolatedAsyncioTestCase):
    """Run the background production loop, including actual friend-follow calls."""
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        cls.function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                            and n.name == '_follow_quester_session')

    def setUp(self):
        from tests.test_quest_party_probe_confirmation import ProbeConfirmationTests
        ProbeConfirmationTests.setUp(self)
        self.run_follow = ProbeConfirmationTests.run_follow.__get__(self)
        self.quester.quest_party_probe_pending = False
        self.limit = 8
        self.target, self.position = XYZ(3000, 0, 0), XYZ(1000, 0, 0)
        self.quester.quest_position = SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target))
        self.quester.body.position.side_effect = lambda: self.position
        self.hitter.body.position.return_value = XYZ(-2000, 0, 0)
        self.reader._valid_quest_target = q.Quester._valid_quest_target
        self.namespace['is_free_leader_questing'] = AsyncMock(return_value=True)
        self.namespace['calc_Distance'] = q.calc_Distance
        self.namespace['Quester'].overgrown_estate_paused = q.Quester.overgrown_estate_paused
        self.hitter.teleport.side_effect = lambda pos: setattr(self.hitter.body.position, 'return_value', pos)

    async def test_partial_failed_landing_does_not_trigger_background_coordinate_follow(self):
        self.same_area = True
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow()
        self.hitter.teleport.assert_not_awaited()
        self.friend.assert_not_awaited()
        self.assertIn('等待任务端到达当前任务点', [c.args[2] for c in self.status.call_args_list])

    async def test_partial_failed_landing_does_not_trigger_background_friend_follow(self):
        await self.run_follow()
        self.friend.assert_not_awaited()
        self.hitter.teleport.assert_not_awaited()

    async def test_confirmed_arrival_releases_original_coordinate_follow(self):
        self.position = self.target
        self.same_area = True
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow()
        self.hitter.teleport.assert_awaited_once_with(self.position)
        self.friend.assert_not_awaited()

    async def test_confirmed_arrival_releases_original_friend_follow(self):
        self.position = self.target
        self.limit = 20
        async def arrive(*args, **kwargs):
            self.hitter.zone_name.return_value = 'World/New'
            self.same_area = True
        self.friend.side_effect = arrive
        await self.run_follow()
        self.friend.assert_awaited_once()
        self.equip.assert_not_awaited()

    async def test_confirmed_interaction_range_arrival_releases_follow_without_exact_xyz(self):
        self.position = XYZ(self.target.x - 400, 0, 0)
        self.quester.quest_party_shared_target = dict(zone='World/New', identity=(42, 7),
            xyz=self.target, actual=self.position, source_tokens={})
        self.same_area = True
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow()
        self.hitter.teleport.assert_awaited_once_with(self.position)

    async def test_changed_goal_invalidates_old_arrival_for_background_follow(self):
        self.position = XYZ(self.target.x - 400, 0, 0)
        self.quester.quest_party_shared_target = dict(zone='World/New', identity=(41, 7),
            xyz=self.target, actual=self.position, source_tokens={})
        await self.run_follow()
        self.friend.assert_not_awaited()
        self.hitter.teleport.assert_not_awaited()

    async def test_native_battle_follow_keeps_existing_policy(self):
        self.namespace['is_free_leader_questing'].return_value = False
        self.same_area = True
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow()
        self.hitter.teleport.assert_awaited_once_with(self.position)

    async def test_single_player_probe_keeps_existing_two_busy_results_and_gear_switch(self):
        self.quester.quest_party_probe_pending = True
        self.limit = 30
        await self.run_follow()
        self.assertEqual(self.friend.await_count, 2)
        self.equip.assert_awaited_once_with(self.quester, 1)
        self.assertTrue(self.quester.in_solo_zone)

