"""Recorded entry, local movement, old probe ordering and per-member exits."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ, Keycode
from src import quest_party as party, utils
from src.questing import Quester
from src.paths import npc_range_path, team_up_button_path

REAL_SLEEP = asyncio.sleep
EXIT_TEXT = ('如果你现在离开地下城，你在地下城的进度将会消失，你需要从头开始。'
             '<br>你确定你要现在离开吗？')


class ClientStub(SimpleNamespace):
    __hash__ = object.__hash__


def client(gid, target):
    c = ClientStub(title=f'p{gid}', process_id=gid, zone='Outside', area_id=100,
        position=XYZ(0, 0, 0), questing_status=True, quest_party_hitters=[],
        quest_party_probe_pending=False, quest_party_observed_zone=None,
        quest_party_quest_worker_zone=None, entity_detect_combat_status=False,
        auto_pet_status=False, feeding_pet_status=False, refilling_potions=False,
        quest_recovery_owner=None, use_potions=False, buy_potions=False, in_solo_zone=False,
        wizard_name='Wizard', character_level=50, running=True, quest_id_value=42,
        root_window=SimpleNamespace(children=AsyncMock(return_value=[]),
                                    get_windows_with_name=AsyncMock(return_value=[])),
        mouse_handler=AsyncMock(), is_loading=AsyncMock(return_value=False),
        in_battle=AsyncMock(return_value=False), is_running=lambda: c.running,
        quest_id=AsyncMock(side_effect=lambda: c.quest_id_value),
        goal_id=AsyncMock(return_value=7), quest_position=SimpleNamespace(position=AsyncMock(return_value=target)))
    c.zone_name = AsyncMock(side_effect=lambda: c.zone)
    c.body = SimpleNamespace(position=AsyncMock(side_effect=lambda: c.position))
    area = SimpleNamespace(read_base_address=AsyncMock(side_effect=lambda: gid * 10000 + c.area_id),
                           zone_id=AsyncMock(side_effect=lambda: c.area_id))
    c.client_object = SimpleNamespace(client_zone=AsyncMock(return_value=area),
                                     global_id_full=AsyncMock(return_value=gid))
    c.stats = SimpleNamespace(reference_level=AsyncMock(return_value=50),
        potion_charge=AsyncMock(return_value=0), potion_max=AsyncMock(return_value=4))
    c.send_key = AsyncMock()
    c.teleport = AsyncMock(side_effect=lambda point: setattr(c, 'position', point))
    c.get_base_entity_list = AsyncMock(return_value=[])
    return c


class DungeonFixture:
    def setUp(self):
        self.q = client(1, XYZ(9000, 9000, 0))
        self.h = client(2, XYZ(700, 800, 0))
        self.q.quest_party_hitters = [self.h]
        self.owner = object()
        self.h.quest_party_status_session = self.owner
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.proof = self.stack.enter_context(patch.object(utils, 'clients_share_live_area', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.questing.clients_share_live_area', self.proof))
        self.stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))

    async def enter(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        for c in (self.q, self.h): c.zone, c.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        return session


class DungeonTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def test_x_intent_alone_never_enables_local_tp(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        await self.q.send_key(Keycode.X, .1)
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.assertFalse(party.dungeon_member_inside(self.h))

    async def test_all_members_must_arrive_with_live_instance_proof(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone, self.q.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.h.zone, self.h.area_id = 'Dungeon/Room1', 200
        self.proof.return_value = False
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        self.proof.return_value = True
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        self.assertEqual(session.source_zones, {id(self.q): 'Outside', id(self.h): 'Outside'})
        self.assertEqual(session.entry_zones, {id(self.q): 'Dungeon/Room1', id(self.h): 'Dungeon/Room1'})
        self.assertEqual(session.entry_areas[id(self.h)][2], 200)
        self.assertEqual(session.members, (self.q, self.h))

    async def test_missing_member_or_potion_trip_cannot_record_entry(self):
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q], evidence=True))
        self.h.refilling_potions = True
        self.assertIsNone(await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True))

    async def test_quester_waits_for_whole_group_after_partial_entry(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone = 'Dungeon/Room1'
        self.assertTrue(await Quester(self.q, [self.q], None)._quest_party_probe_blocks_movement())
        self.assertFalse(session.confirmed)
        self.assertFalse(party.dungeon_member_inside(self.h))

    async def test_aborted_unconfirmed_entry_is_cleared_on_return_to_source(self):
        await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone = 'Dungeon/Room1'
        await party.observe_dungeon_session(self.q)
        self.q.zone = 'Outside'
        await party.observe_dungeon_session(self.q)
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertIsNone(party.dungeon_session_for(self.h))

    async def test_interiors_task_id_and_battle_are_not_entry_evidence(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=False)
        for c in (self.q, self.h):
            c.zone = 'World/Interiors/House'
            c.quest_id_value += 1
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)
        session.entry_evidence = True
        self.h.in_battle.return_value = True
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)

    async def test_membership_or_loading_changes_during_proof_reject_entry(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone = self.h.zone = 'Dungeon/Room1'
        async def change(*_):
            self.h.is_loading.return_value = True
            return True
        self.proof.side_effect = change
        await party.observe_dungeon_session(self.q)
        self.assertFalse(session.confirmed)

    async def test_multi_room_switch_keeps_session_until_shared_target_is_safe(self):
        session = await self.enter()
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        await party.observe_dungeon_session(self.q)
        self.assertIs(party.dungeon_session_for(self.h), session)
        self.assertTrue(party.dungeon_member_inside(self.h))
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock()) as move, patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            self.assertFalse(await Quester(self.h, [self.h], None).move_dungeon_member())
            move.assert_not_awaited()
            self.h.zone, self.h.area_id = self.q.zone, self.q.area_id
            self.assertTrue(await Quester(self.h, [self.h], None).move_dungeon_member())
        self.assertEqual(move.await_count, 1)
        self.assertIs(move.await_args.args[0], self.h)
        self.assertIs(move.await_args.kwargs['leader_client'], self.h)
        actual, expected = move.await_args.args[1], self.q.quest_position.position.return_value
        self.assertEqual((actual.x, actual.y, actual.z), (expected.x, expected.y, expected.z))
        self.q.body.position.assert_not_awaited()
        self.assertEqual(session.exited, set())

    async def test_zero_shared_target_does_not_start_hitters_independent_collect(self):
        await self.enter()
        self.q.quest_position.position.return_value = XYZ(0, 0, 0)
        reader = Quester(self.h, [self.h], None)
        reader.auto_collect_rewrite = AsyncMock()
        reader.teleport_to_quest_target = AsyncMock()
        self.assertFalse(await reader.move_dungeon_member())
        reader.auto_collect_rewrite.assert_not_awaited()
        reader.teleport_to_quest_target.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_battle_recovery_refill_and_stopped_member_never_move(self):
        for kind in ('battle', 'recovery', 'refill', 'stop'):
            with self.subTest(kind=kind):
                self.setUp()
                session = await self.enter()
                if kind == 'battle': self.h.in_battle.return_value = True
                if kind == 'recovery': self.h.quest_recovery_owner = 'existing-recovery'
                if kind == 'refill': self.h.refilling_potions = True
                if kind == 'stop': self.h.questing_status = False
                reader = Quester(self.h, [self.h], None)
                reader.teleport_to_quest_target = AsyncMock()
                with patch('src.questing.is_free', AsyncMock(return_value=kind != 'battle')):
                    self.assertFalse(await reader.move_dungeon_member())
                reader.teleport_to_quest_target.assert_not_awaited()
                if kind == 'battle': self.assertNotIn(id(self.h), session.exited)

    async def test_zone_change_during_shared_target_read_rejects_old_room_tp(self):
        await self.enter()
        async def changed():
            self.h.zone = 'Dungeon/Room2'
            return XYZ(1, 2, 3)
        self.q.quest_position.position.side_effect = changed
        reader = Quester(self.h, [self.h], None)
        reader.teleport_to_quest_target = AsyncMock()
        self.assertFalse(await reader.move_dungeon_member())
        reader.teleport_to_quest_target.assert_not_awaited()

    async def test_quester_room_switch_still_blocks_next_tp_for_existing_probe(self):
        await self.enter()
        self.q.quest_party_quest_worker_zone = 'Dungeon/Room1'
        self.q.zone = 'Dungeon/Room2'
        reader = Quester(self.q, [self.q], None)
        self.assertTrue(await reader._quest_party_probe_blocks_movement())
        self.assertTrue(self.q.quest_party_probe_pending)
        self.q.quest_party_probe_pending = False  # Existing probe completion.
        self.h.zone = self.q.zone
        self.assertFalse(await reader._quest_party_probe_blocks_movement())

    async def test_members_exit_in_either_order_and_last_exit_restores_normal_mode(self):
        for first, second in ((self.q, self.h), (self.h, self.q)):
            with self.subTest(first=first.title):
                session = await self.enter()
                first.zone = 'Outside'
                await party.observe_dungeon_session(first)
                self.assertFalse(party.dungeon_member_inside(first))
                self.assertTrue(party.dungeon_member_inside(second))
                self.assertIs(party.dungeon_session_for(second), session)
                second.zone = 'Outside'
                await party.observe_dungeon_session(second)
                self.assertIsNone(party.dungeon_session_for(first))
                self.assertIsNone(party.dungeon_session_for(second))

    async def test_unmarked_new_region_and_task_change_do_not_guess_exit(self):
        session = await self.enter()
        self.q.quest_id_value += 1
        self.q.zone = 'World/NextArea'
        await party.observe_dungeon_session(self.q)
        self.assertEqual(session.exited, set())
        self.assertTrue(party.dungeon_member_inside(self.q))

    async def test_last_quester_can_finish_exit_after_all_hitters_have_exited(self):
        session = await self.enter()
        self.h.zone = 'Outside'
        await party.observe_dungeon_session(self.h)
        self.q.zone = 'Dungeon/Room2'
        self.q.quest_party_probe_pending = True
        self.assertFalse(await Quester(self.q, [self.q], None)._quest_party_probe_blocks_movement())
        self.assertFalse(self.q.quest_party_probe_pending)
        self.q.zone = 'Outside'
        await party.observe_dungeon_session(self.q)
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(len(session.exited), 2)

    async def confirm(self, caption, after):
        node = SimpleNamespace(is_visible=AsyncMock(return_value=True), value=caption)
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                get_windows_with_name=AsyncMock(return_value=[node]))
        center = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        async def resolve(root, path):
            if root is self.h.root_window and path == ['MessageBoxModalWindow']:
                return modal
            if root is modal and path[-1] == 'centerButton':
                return center
            return False
        reader = Quester(self.h, [self.h], None)
        async def click(*_): self.h.zone = after
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=True)), patch(
                'src.questing.get_window_from_path', AsyncMock(side_effect=resolve)), patch(
                'src.questing.read_control_text', AsyncMock(side_effect=lambda c: c.value)), patch.object(
                self.h.mouse_handler, 'click_window', AsyncMock(side_effect=click)) as clicked, patch(
                'src.questing.asyncio.sleep', AsyncMock()), patch(
                'src.questing.time', SimpleNamespace(monotonic=Mock(side_effect=range(30)))):
            await reader.handle_pending_dungeon_confirmation()
        self.assertGreaterEqual(clicked.await_count, 1)
        self.assertLessEqual(clicked.await_count, 3)
        self.assertTrue(all(call.args == (center,) for call in clicked.await_args_list))

    async def test_verified_exit_modal_plus_real_transition_handles_new_area(self):
        session = await self.enter()
        await self.confirm(EXIT_TEXT, 'World/OtherArea')
        self.assertIn(id(self.h), session.exited)
        self.assertTrue(party.dungeon_member_inside(self.q))
        self.assertEqual(session.exit_evidence[id(self.h)], ('GUI_InstanceExit', 'Dungeon/Room1', 'World/OtherArea'))

    async def test_exit_caption_without_transition_and_generic_modal_do_not_end_session(self):
        session = await self.enter()
        await self.confirm(EXIT_TEXT, 'Dungeon/Room1')
        self.assertEqual(session.exited, set())
        await self.confirm('是否进入下一个房间？', 'Dungeon/Room2')
        self.assertEqual(session.exited, set())

    async def test_stop_regroup_or_client_exit_cleans_only_affected_session(self):
        for reason in ('stop', 'regroup', 'closed'):
            with self.subTest(reason=reason):
                self.setUp()
                await self.enter()
                if reason == 'stop': self.h.questing_status = False
                if reason == 'regroup': self.q.quest_party_hitters = []
                if reason == 'closed': self.h.running = False
                self.assertIsNone(party.dungeon_session_for(self.q))
                self.assertIsNone(self.h.quest_party_dungeon)


class PotionLifecycleTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def potion_mocks(self):
        for name in ('ensure_teleport_mark', 'navigate_to_ravenwood', 'navigate_to_commons_from_ravenwood',
                     'navigate_to_potions', 'buy_potions'):
            yield name, self.stack.enter_context(patch.object(utils, name, AsyncMock(return_value=True)))

    async def test_flag_covers_first_departure_through_recall_then_old_probe_is_required(self):
        session = await self.enter()
        mocks = dict(self.potion_mocks())
        async def depart(c):
            self.assertTrue(c.refilling_potions)
            self.assertIn(id(c), session.potion_areas)
            c.zone, c.area_id = 'Outside', 100  # Potion region can equal entry source.
            self.assertTrue(await party.dungeon_quester_away_for_potions(c))
            await party.observe_dungeon_session(c)
            self.assertNotIn(id(c), session.exited)
            self.assertTrue(party.dungeon_member_inside(self.h))
        async def returned(c, recall, original_zone):
            self.assertTrue(c.refilling_potions)
            self.assertTrue(recall)
            c.zone, c.area_id = original_zone, 200
            return True
        mocks['navigate_to_ravenwood'].side_effect = depart
        mocks['buy_potions'].side_effect = returned
        self.assertTrue(await utils.refill_potions(self.q, recall=True))
        self.assertFalse(self.q.refilling_potions)
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertTrue(await Quester(self.q, [self.q], None)._quest_party_probe_blocks_movement())
        self.q.quest_party_probe_pending = False
        self.assertFalse(await Quester(self.q, [self.q], None)._quest_party_probe_blocks_movement())

    async def test_failed_return_keeps_session_paused_and_never_exits_whole_group(self):
        session = await self.enter()
        mocks = dict(self.potion_mocks())
        async def depart(c): c.zone, c.area_id = 'Outside', 100
        mocks['navigate_to_ravenwood'].side_effect = depart
        mocks['buy_potions'].return_value = False
        self.assertFalse(await utils.refill_potions(self.q, recall=True))
        self.proof.return_value = False  # Different area, as in the real area reader.
        self.assertFalse(self.q.refilling_potions)
        self.assertIn(id(self.q), session.potion_areas)
        self.assertTrue(await party.dungeon_quester_away_for_potions(self.q))
        await party.observe_dungeon_session(self.q)
        self.assertEqual(session.exited, set())
        self.assertTrue(party.dungeon_member_inside(self.h))

    async def test_hitter_refills_and_returns_without_taking_quester_away(self):
        session = await self.enter()
        mocks = dict(self.potion_mocks())
        async def departed(c):
            self.assertIs(c, self.h)
            self.assertTrue(c.refilling_potions)
            self.assertFalse(self.q.refilling_potions)
            c.zone = 'PotionArea'
        async def returned(c, recall, original_zone):
            c.zone = original_zone
            return True
        mocks['navigate_to_ravenwood'].side_effect = departed
        mocks['buy_potions'].side_effect = returned
        self.assertTrue(await utils.refill_potions(self.h, recall=True))
        self.assertTrue(party.dungeon_member_inside(self.h))
        self.assertTrue(party.dungeon_member_inside(self.q))
        self.assertEqual(self.q.zone, 'Dungeon/Room1')
        self.q.teleport.assert_not_awaited()
        self.assertEqual(session.exited, set())

    async def test_same_zone_wrong_instance_return_does_not_resume_local_tp(self):
        session = await self.enter()
        mocks = dict(self.potion_mocks())
        async def returned(c, *_args, **_kw):
            c.area_id = 999
            return True
        mocks['buy_potions'].side_effect = returned
        self.assertTrue(await utils.refill_potions(self.h, recall=True))
        self.assertIn(id(self.h), session.potion_areas)
        self.assertFalse(party.dungeon_member_inside(self.h))

    async def test_refill_cancel_restores_flag_but_cannot_claim_return(self):
        session = await self.enter()
        mocks = dict(self.potion_mocks())
        async def cancelled(c):
            c.zone = 'PotionArea'
            raise asyncio.CancelledError
        mocks['navigate_to_ravenwood'].side_effect = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await utils.refill_potions(self.h)
        self.assertFalse(self.h.refilling_potions)
        self.assertIn(id(self.h), session.potion_areas)
        self.assertFalse(party.dungeon_member_inside(self.h))

    async def test_split_purchase_and_return_keeps_refill_flag_until_final_return(self):
        await self.enter()
        reader = Quester(self.q, [self.q], None)
        reader.collect_wisps = reader.guarantee_use_potion = AsyncMock()
        async def buy(c, **_):
            self.assertTrue(c.refilling_potions)
            c.zone = 'PotionArea'
            return True
        async def recall(c, _zone):
            self.assertTrue(c.refilling_potions)
            c.zone = _zone
            return True
        reader.gather_clients_from_potion_buy = AsyncMock(side_effect=recall)
        with patch('src.questing.ensure_teleport_mark', AsyncMock(return_value=True)), patch(
                'src.questing.refill_potions', AsyncMock(side_effect=buy)):
            await reader.heal_and_handle_potions()
        self.assertFalse(self.q.refilling_potions)
        self.assertTrue(self.q.quest_party_probe_pending)


class FollowerTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def follow_until(self, done, *, primary=False, extra=None, when_status=None):
        from test_full_chain_415_restore import entry
        if not isinstance(Quester.get_truncated_quest_objectives, AsyncMock):
            self.stack.enter_context(patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='')))
        self.clock = 0
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        self.status = Mock(side_effect=when_status)
        self.friend = AsyncMock()
        namespace = dict(walker=SimpleNamespace(clients=[self.q, self.h]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False), is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=self.friend,
            clients_share_live_area=self.proof,
            SprintyClient=lambda c: c,
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError))
        namespace.update(extra or {})
        follow = entry('_follow_quester_session', namespace, members=[self.q, self.h],
            change_party_equipment=AsyncMock(return_value=True), prepare_quester_name=AsyncMock(),
            remove_party_status=Mock(), restart_quest_worker_after_probe=Mock(), update_party_status=self.status)
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)):
            task = asyncio.create_task(follow(self.h, self.q, primary))
            waiter = asyncio.create_task(done.wait())
            try:
                completed, _ = await asyncio.wait((task, waiter), timeout=1, return_when=asyncio.FIRST_COMPLETED)
                if task in completed:
                    await task
                last_status = self.status.call_args.args[2] if self.status.call_args else None
                self.assertIn(waiter, completed, f'last status: {last_status}')
            finally:
                task.cancel()
                waiter.cancel()
                await asyncio.gather(task, waiter, return_exceptions=True)

    async def test_real_follower_replays_retained_entrance_without_new_room_goal(self):
        await self.enter()
        entrance = XYZ(600, 700, 0)
        await party.remember_dungeon_transition(self.q, entrance)
        self.q.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = self.q.zone
        done = asyncio.Event()
        async def local(c, target, **_):
            self.assertIs(c, self.h)
            self.assertEqual(tuple(target), tuple(entrance))
            c.position = target
        async def entered(code, _seconds):
            self.assertEqual(code, Keycode.X)
            self.h.zone = self.q.zone
            done.set()
        self.h.send_key.side_effect = entered
        self.q.quest_position.position.reset_mock()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=local)), patch(
                'src.questing.is_visible_by_path', AsyncMock(side_effect=lambda _c, path: path == npc_range_path)), patch(
                'src.questing.get_popup_title', AsyncMock(return_value='Door')):
            await self.follow_until(done)
        self.q.quest_position.position.assert_not_awaited()
        self.q.body.position.assert_not_awaited()
        self.h.send_key.assert_awaited_once()
        self.friend.assert_not_awaited()

    async def test_real_follower_never_follows_or_probes_quester_potion_region(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.refilling_potions = True
        await party.pause_dungeon_for_potions(self.q)
        self.q.zone = 'PotionArea'
        done = asyncio.Event()
        def status(_h, _q, text):
            if '等待原实例归队' in text: done.set()
        local = AsyncMock()
        with patch.object(Quester, 'teleport_to_quest_target', local), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        local.assert_not_awaited()
        self.assertEqual(self.q.quest_party_observed_zone, 'Dungeon/Room1')
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertNotIn(id(self.q), session.exited)
        self.friend.assert_not_awaited()

    async def test_existing_hitter_recovery_owner_blocks_new_tp_and_potion_trip(self):
        await self.enter()
        self.h.quest_recovery_owner = 'existing-return'
        done = asyncio.Event()
        auto, refill = AsyncMock(), AsyncMock()
        def status(_h, _q, text):
            if text == '等待已有恢复流程完成': done.set()
        with patch.object(Quester, 'move_dungeon_member', AsyncMock()) as move:
            await self.follow_until(done, when_status=status, extra=dict(use_potions=True, buy_potions=True,
                auto_potions=auto, refill_potions=refill))
        move.assert_not_awaited()
        auto.assert_not_awaited()
        refill.assert_not_awaited()
        self.friend.assert_not_awaited()

    async def test_existing_quester_recovery_does_not_pull_hitter_or_start_probe(self):
        await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.quest_recovery_owner = 'existing-return'
        self.q.zone = 'RecoveryArea'
        done = asyncio.Event()
        def status(_h, _q, text):
            if '等待原实例归队' in text: done.set()
        local = AsyncMock()
        with patch.object(Quester, 'teleport_to_quest_target', local), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        local.assert_not_awaited()
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_observed_zone, 'Dungeon/Room1')
        self.friend.assert_not_awaited()

    async def test_real_probe_completion_precedes_returned_quester_and_hitter_local_tp(self):
        session = await self.enter()
        self.q.refilling_potions = True
        await party.pause_dungeon_for_potions(self.q)
        self.q.refilling_potions = False
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.quest_party_probe_pending = True
        self.h.get_base_entity_list.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=1))]
        done = asyncio.Event()
        async def local(*_args, **_kw):
            self.assertFalse(self.q.quest_party_probe_pending)
            self.assertNotIn(id(self.q), session.potion_areas)
            done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=local)), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done, primary=True)
        self.friend.assert_not_awaited()

    async def test_quester_exits_first_hitter_continues_own_exit_then_normal_follow_resumes(self):
        goal = SimpleNamespace(goal_destination_zone=AsyncMock(return_value='Dungeon/Room1'))
        quest = SimpleNamespace(goal_data=AsyncMock(return_value={7: goal}))
        self.h.quest_manager = AsyncMock(return_value=SimpleNamespace(
            quest_data=AsyncMock(return_value={42: quest})))
        session = await self.enter()
        self.q.zone = 'Outside'
        await party.observe_dungeon_session(self.q)
        self.q.quest_party_observed_zone = 'Outside'
        self.q.position = XYZ(3000, 0, 0)
        done = asyncio.Event()
        async def local(c, _target, **_):
            c.zone = 'Outside'
        async def followed(target):
            self.assertEqual(target, self.q.position)
            self.assertIsNone(party.dungeon_session_for(self.h))
            done.set()
        self.h.teleport.side_effect = followed
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=local)) as move, patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done)
        move.assert_awaited_once_with(self.h, self.h.quest_position.position.return_value)
        self.assertEqual(len(session.exited), 2)
        self.friend.assert_not_awaited()

    async def test_hitter_exit_first_waits_instead_of_reentering_quester_room(self):
        await self.enter()
        self.h.zone = 'Outside'
        await party.observe_dungeon_session(self.h)
        done = asyncio.Event()
        from test_full_chain_415_restore import entry
        status = Mock(side_effect=lambda *_: done.set())
        async def tick(_): await REAL_SLEEP(0)
        follow = entry('_follow_quester_session', dict(walker=SimpleNamespace(clients=[self.q, self.h]),
            questing_status=True, asyncio=SimpleNamespace(sleep=tick, get_running_loop=asyncio.get_running_loop,
                CancelledError=asyncio.CancelledError)), members=[self.q, self.h],
            change_party_equipment=AsyncMock(), prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=status)
        task = asyncio.create_task(follow(self.h, self.q, True))
        try:
            # First status is setup; wait for at least one complete follower poll.
            await REAL_SLEEP(.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertIn('已退出地牢', status.call_args.args[2])
        self.h.teleport.assert_not_awaited()

    async def test_hitter_refill_in_real_loop_uses_existing_mark_return_only_for_itself(self):
        await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        done = asyncio.Event()
        async def refill(c, **kwargs):
            self.assertIs(c, self.h)
            self.assertEqual(kwargs, dict(mark=False, recall=True, original_zone='Dungeon/Room1'))
            done.set()
        await self.follow_until(done, extra=dict(use_potions=True, buy_potions=True,
            auto_potions=AsyncMock(return_value=True), refill_potions=AsyncMock(side_effect=refill)))
        self.q.teleport.assert_not_awaited()
        self.friend.assert_not_awaited()


    async def test_original_room_probe_runs_before_next_independent_move(self):
        await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.quest_party_quest_worker_zone = 'Dungeon/Room1'
        self.q.zone = 'Dungeon/Room2'
        self.q.quest_party_probe_pending = True
        done = asyncio.Event()
        async def friend_arrive(*_args, **_kw): self.h.zone = self.q.zone
        friend = AsyncMock(side_effect=friend_arrive)
        async def local(c, target, **_):
            self.assertFalse(self.q.quest_party_probe_pending)
            friend.assert_awaited_once()
            self.assertEqual(tuple(target), tuple(self.q.quest_position.position.return_value))
            done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=local)), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()), patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=True, extra=dict(teleport_to_friend_from_list=friend))
        self.q.body.position.assert_not_awaited()

    async def test_failed_recall_keeps_local_hitter_flow_after_refill_flag_clears(self):
        session = await self.enter()
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.refilling_potions = True
        await party.pause_dungeon_for_potions(self.q)
        self.q.refilling_potions = False
        self.q.zone = 'Outside'
        self.proof.return_value = False
        done = asyncio.Event()
        def status(_h, _q, text):
            if '等待原实例归队' in text: done.set()
        local = AsyncMock()
        with patch.object(Quester, 'teleport_to_quest_target', local), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done, primary=True, when_status=status)
        local.assert_not_awaited()
        self.assertEqual(session.exited, set())
        self.assertEqual(self.q.quest_party_observed_zone, 'Dungeon/Room1')
        self.friend.assert_not_awaited()

    async def test_remaining_hitter_inherits_existing_probe_when_primary_has_exited(self):
        old_hitter = self.h
        remaining = client(3, XYZ(1100, 1200, 0))
        remaining.quest_party_status_session = self.owner
        self.q.quest_party_hitters = [old_hitter, remaining]
        session = await party.begin_dungeon_entry(self.q, [self.q, old_hitter, remaining], evidence=True)
        for c in (self.q, old_hitter, remaining): c.zone, c.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        old_hitter.zone = 'Outside'
        await party.observe_dungeon_session(old_hitter)
        self.q.zone = remaining.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = 'Dungeon/Room1'
        self.q.quest_party_probe_pending = True
        remaining.get_base_entity_list.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=1))]
        self.h = remaining
        done = asyncio.Event()
        async def local(c, target, **_):
            self.assertIs(c, remaining)
            self.assertEqual(tuple(target), tuple(self.q.quest_position.position.return_value))
            self.assertFalse(self.q.quest_party_probe_pending)
            done.set()
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=local)), patch.object(
                Quester, 'handle_questing_zone_change', AsyncMock()):
            await self.follow_until(done, primary=False, extra=dict(walker=SimpleNamespace(
                clients=[self.q, old_hitter, remaining])))
        self.assertIn(id(old_hitter), session.exited)
        old_hitter.teleport.assert_not_awaited()
        self.friend.assert_not_awaited()

    async def test_existing_solo_probe_abandons_unconfirmed_entry_without_claiming_group_exit(self):
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone = 'Dungeon/Room1'
        self.q.quest_party_observed_zone = 'Outside'
        self.q.quest_party_probe_pending = True
        done = asyncio.Event()
        async def solo_restart(_): done.set()
        from test_full_chain_415_restore import entry
        self.clock = 0
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        friend = AsyncMock(side_effect=utils.FriendBusyOrInstanceClosed())
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[self.q, self.h]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False), is_friend_teleport_error=AsyncMock(return_value=False),
            teleport_to_friend_from_list=friend, SprintyClient=lambda c: c,
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError)),
            members=[self.q, self.h], change_party_equipment=AsyncMock(), prepare_quester_name=AsyncMock(),
            remove_party_status=Mock(), restart_quest_worker_after_probe=Mock(side_effect=lambda c: done.set()),
            update_party_status=Mock())
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            task = asyncio.create_task(follow(self.h, self.q, True))
            try:
                await asyncio.wait_for(done.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(session.confirmed)
        self.assertEqual(session.exited, set())
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertTrue(self.q.in_solo_zone)


class WorkerCleanupTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def test_actual_role_application_clears_recorded_group_immediately(self):
        from test_full_chain_415_restore import entry
        await self.enter()
        resolved = party.QuestParty([self.q], [self.h], [], [(self.h, self.q)])
        apply = entry('apply_questing_roles', dict(walker=SimpleNamespace(clients=[self.q, self.h])),
            current_quest_party=lambda _members=None: resolved)
        apply(False, None)
        self.assertIsNone(self.q.quest_party_dungeon)
        self.assertIsNone(self.h.quest_party_dungeon)

    async def test_quest_worker_stop_clears_session(self):
        await self.enter()
        reader = Quester(self.q, [self.q], None)
        async def stop(**_): self.q.questing_status = False
        reader.auto_quest_solo = AsyncMock(side_effect=stop)
        with patch('src.questing.asyncio.sleep', AsyncMock()):
            await reader.auto_quest(False, False)
        self.assertIsNone(self.q.quest_party_dungeon)
        self.assertIsNone(self.h.quest_party_dungeon)

    async def test_solo_probe_worker_restart_preserves_live_session(self):
        session = await self.enter()
        reader = Quester(self.q, [self.q], None)
        started = asyncio.Event()
        async def wait(**_):
            started.set()
            await asyncio.Future()
        reader.auto_quest_solo = AsyncMock(side_effect=wait)
        with patch('src.questing.asyncio.sleep', AsyncMock()):
            task = asyncio.create_task(reader.auto_quest(False, False))
            await started.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertIs(party.dungeon_session_for(self.q), session)


class EntryCallerTests(DungeonFixture, unittest.IsolatedAsyncioTestCase):
    async def run_entry(self, *, reject_hitter=False):
        reader = Quester(self.q, [self.q], None)
        reader.read_popup = AsyncMock(return_value='Press X to Enter')
        self.clock = 0
        async def tick(delay):
            self.clock += delay
            for c in (self.q, self.h): c.is_loading.return_value = False
            await REAL_SLEEP(0)
        async def move(c, target): c.position = target
        async def key(c, key, seconds):
            if key != Keycode.X: return
            session = party.dungeon_session_for(c)
            self.assertIsNotNone(session)
            self.assertFalse(session.confirmed)
            self.assertEqual(session.source_zones[id(c)], 'Outside')
            if c is self.h and reject_hitter: return
            c.zone, c.area_id = 'Dungeon/Room1', 200
            c.is_loading.return_value = True
        async def q_key(k, t): await key(self.q, k, t)
        async def h_key(k, t): await key(self.h, k, t)
        self.q.send_key.side_effect, self.h.send_key.side_effect = q_key, h_key
        with patch.object(Quester, 'teleport_to_quest_target', AsyncMock(side_effect=move)), patch.object(
                Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), patch(
                'src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), patch(
                'src.questing.close_automation_popup', AsyncMock(return_value=False)), patch(
                'src.mainline_progress.log_mainline_progress', AsyncMock()), patch(
                'src.questing.is_spiral_door_open', AsyncMock(return_value=False)), patch(
                'src.questing.is_potion_needed', AsyncMock(return_value=False)), patch(
                'src.questing.is_visible_by_path', AsyncMock(side_effect=lambda c, path: path in (npc_range_path, team_up_button_path))), patch(
                'src.questing.asyncio.sleep', tick), patch(
                'src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)):
            await reader.auto_quest_solo()

    async def test_actual_entry_caller_records_before_x_enables_only_after_both_arrive(self):
        await self.run_entry()
        session = party.dungeon_session_for(self.q)
        self.assertTrue(session.confirmed)
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertTrue(party.dungeon_member_inside(self.h))

    async def test_actual_entry_caller_keeps_local_tp_disabled_if_hitter_never_arrives(self):
        await self.run_entry(reject_hitter=True)
        self.assertFalse(party.dungeon_session_for(self.q).confirmed)
        self.assertFalse(party.dungeon_member_inside(self.h))

    async def test_shared_entrance_preparation_never_copies_new_room_coordinates_to_old_room(self):
        await self.enter()
        self.q.zone = 'Dungeon/Room2'
        reader = Quester(self.q, [self.q], None)
        with patch('src.questing.asyncio.sleep', AsyncMock()), patch(
                'src.questing.time', SimpleNamespace(monotonic=Mock(side_effect=range(10)))):
            self.assertEqual(await reader.prepare_party_dungeon_entry(), [])
        self.h.teleport.assert_not_awaited()

    async def test_returned_potion_call_cannot_start_quest_tp_before_existing_probe(self):
        await self.enter()
        self.q.use_potions = True
        reader = Quester(self.q, [self.q], None)
        reader.teleport_to_quest_target = AsyncMock()
        async def returned(*_args, **_kw):
            self.q.quest_party_probe_pending = True
            return True
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), patch(
                'src.questing.close_npc_quest_menu', AsyncMock(return_value=False)), patch(
                'src.questing.close_automation_popup', AsyncMock(return_value=False)), patch(
                'src.mainline_progress.log_mainline_progress', AsyncMock()), patch(
                'src.questing.is_spiral_door_open', AsyncMock(return_value=False)), patch(
                'src.questing.is_potion_needed', AsyncMock(return_value=False)), patch(
                'src.questing.auto_potions', AsyncMock(side_effect=returned)):
            await reader.auto_quest_solo()
        reader.teleport_to_quest_target.assert_not_awaited()
        self.q.quest_position.position.assert_not_awaited()
