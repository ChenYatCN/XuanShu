"""The quester's existing worker must retain final-exit movement and handoff."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import quest_party as party
from src.game_text_catalog import TEXT_RECORDS
from src.questing import Quester
from tests.test_dungeon_exit_confirmation import Window
from tests.test_dungeon_local_tp import client
from tests.test_dungeon_party_transition import PartyTransitionFixture


class DungeonExitOwnerTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.third = client(3, XYZ(100, 200, 0))
        self.third.quest_party_quester = self.q
        self.third.quest_party_status_session = self.owner
        self.q.quest_party_hitters.append(self.third)
        self.target = XYZ(500, 600, 0)  # Initially outside the ordinary X range.
        self.q.quest_position.position.return_value = self.target
        self.q.quest_party_quest_worker_zone = 'Dungeon/Room1'
        goal = SimpleNamespace(goal_destination_zone=AsyncMock(return_value='Outside'))
        self.q.quest_manager = AsyncMock(return_value=SimpleNamespace(
            quest_data=AsyncMock(return_value={42: SimpleNamespace(
                goal_data=AsyncMock(return_value={7: goal, 8: goal}))})))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        for name in ('src.mainline_recovery.maybe_recover_mainline',
                     'src.questing.close_npc_quest_menu', 'src.questing.close_automation_popup',
                     'src.questing.is_spiral_door_open', 'src.questing.is_potion_needed'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.caller = Quester(self.q, [self.q], None)
        self.caller.teleport_to_quest_target = AsyncMock(side_effect=self.land)
        self.caller.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.caller.handle_questing_zone_change = AsyncMock()
        self.caller.read_popup = AsyncMock(return_value='Press X to Use')
        self.caller._maybe_photo_giant_vat = AsyncMock(return_value=False)

    async def enter(self):
        members = [self.q, *self.q.quest_party_hitters]
        session = await party.begin_dungeon_entry(self.q, members, evidence=True)
        for member in members:
            member.zone, member.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        return session

    async def capture(self):
        session = await self.enter()
        self.assertTrue(await party.remember_dungeon_transition(self.q, self.target))
        return session

    def assert_quester_attempt_only(self):
        self.caller.teleport_to_quest_target.assert_awaited_once()
        self.assertIs(self.caller.teleport_to_quest_target.await_args.args[0], self.q)
        for member in (self.h, self.third):
            member.teleport.assert_not_awaited()
            member.send_key.assert_not_awaited()

    async def test_b1_all_inside_external_goal_starts_existing_quester_worker(self):
        session = await self.enter()
        await self.caller.auto_quest_solo()
        self.assert_quester_attempt_only()
        self.assertFalse(session.quester_out, 'An exit attempt is not an observed exit')
        self.assertEqual(session.exited, set())

    async def test_b2_confirmed_outside_followers_do_not_gate_last_quester(self):
        session = await self.enter()
        for member in (self.h, self.third):
            member.zone, member.area_id = 'Outside', 100
            await party.observe_dungeon_session(member)
        self.q.quest_party_probe_pending = True
        await self.caller.auto_quest_solo()
        self.assert_quester_attempt_only()
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(session.exited, {id(self.h), id(self.third)})

    async def test_stale_probe_without_transition_or_after_expiry_releases_exit_tp(self):
        for index, mode in enumerate(('missing-transition', 'expired-transition')):
            with self.subTest(mode=mode):
                if index:
                    self.stack.close()
                    self.setUp()
                session = await self.capture()
                self.q.quest_party_probe_pending = True
                if mode == 'missing-transition':
                    session.transition = None
                else:
                    # A sent old operation suppresses the earlier stale-bit release;
                    # recover_dungeon_transition must expire it before this new goal.
                    session.transition['entry_sent'] = True
                    self.clock += 31
                    self.assertFalse(await self.caller.recover_dungeon_transition())
                    self.assertIsNone(session.transition)
                self.assertTrue(await party.quest_party_members_ready(self.q))
                for _ in range(2):
                    await self.caller.auto_quest_solo()
                self.assertGreater(self.caller.teleport_to_quest_target.await_count, 0,
                    'An unowned probe bit must not starve the quester exit target')
                self.assertFalse(self.q.quest_party_probe_pending)
                self.assertFalse(session.quester_out)

    async def test_b3_actual_source_departure_observed_by_next_quester_tick(self):
        session = await self.enter()
        snapshot = await self.caller._confirmed_dungeon_target(session)
        self.assertIsNotNone(snapshot)

        async def depart(moving, target, **_kwargs):
            moving.position = target
            moving.zone, moving.area_id = 'Outside', 100

        self.caller.teleport_to_quest_target.side_effect = depart
        await self.caller.move_dungeon_member(_quester_snapshot=snapshot)
        # The real persistent worker observes departure on its next tick. A
        # raw move returning False is not proof that this owner was lost.
        await self.caller.auto_quest_solo()
        self.assertTrue(session.quester_out)
        self.assertIn(id(self.q), session.exited)
        self.assertIsNone(session.transition)
        self.assertTrue(party.dungeon_member_inside(self.h))
        self.assertTrue(party.dungeon_member_inside(self.third))

    async def test_verified_exit_modal_hands_off_to_existing_confirmation(self):
        session = await self.enter()
        caption = Window('CaptionText', text=TEXT_RECORDS['GUI_InstanceExit'][1])
        yes = Window('leftButton')
        modal = Window('MessageBoxModalWindow', Window('messageBoxBG',
            Window('messageBoxLayout', Window('TextArea', caption),
                Window('AdjustmentWindow', Window('Layout',
                    Window('centerButton', visible=False), yes, Window('rightButton'))))),
            visible=False)
        self.q.root_window = Window('root', modal)

        async def show_exit(moving, target, **_kwargs):
            moving.position = target
            modal.visible = True

        async def click(button):
            self.assertIs(button, yes)
            modal.visible = False
            self.q.zone, self.q.area_id = 'Outside', 100

        async def confirm_exit():
            # Exercise the real scoped Yes reader, not a fabricated exit API.
            await Quester.handle_pending_dungeon_confirmation(self.caller)

        self.q.mouse_handler.click_window.side_effect = click
        self.caller.teleport_to_quest_target.side_effect = show_exit
        self.caller.handle_pending_dungeon_confirmation.side_effect = confirm_exit
        with patch('src.questing.read_control_text', AsyncMock(side_effect=lambda w: w.text)), \
                patch('src.questing.is_free', AsyncMock(
                    side_effect=lambda c: c is not self.q or not modal.visible)):
            snapshot = await self.caller._confirmed_dungeon_target(session)
            self.assertIsNotNone(snapshot)
            await self.caller.move_dungeon_member(_quester_snapshot=snapshot)
            await self.caller.auto_quest_solo()
        self.q.mouse_handler.click_window.assert_awaited_once_with(yes)
        self.assertTrue(session.quester_out)
        self.assertIsNone(session.transition)
        self.assertNotIn(id(self.h), session.exited)

    async def test_b4_loading_and_battle_defer_without_losing_current_exit(self):
        for index, safety in enumerate(('loading', 'battle')):
            with self.subTest(safety=safety):
                if index:
                    self.stack.close()
                    self.setUp()
                session = await self.capture()
                session.transition = None
                self.q.quest_party_probe_pending = True
                blocked = self.q.is_loading if safety == 'loading' else self.q.in_battle
                blocked.return_value = True
                await self.caller.auto_quest_solo()
                self.caller.teleport_to_quest_target.assert_not_awaited()
                self.q.send_key.assert_not_awaited()
                self.assertFalse(session.quester_out)
                blocked.return_value = False
                await self.caller.auto_quest_solo()
                self.assert_quester_attempt_only()
                self.assertFalse(self.q.quest_party_probe_pending)

    async def test_b5_task_change_rejects_old_snapshot_and_revalidates_next_goal(self):
        session = await self.enter()
        old = await self.caller._confirmed_dungeon_target(session)
        self.assertIsNotNone(old)
        self.q.goal_id.return_value = 8
        self.assertFalse(await self.caller.move_dungeon_member(_quester_snapshot=old))
        self.caller.teleport_to_quest_target.assert_not_awaited()
        self.q.send_key.assert_not_awaited()
        fresh = await self.caller._confirmed_dungeon_target(session)
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh[:2], (42, 8))
        await self.caller.move_dungeon_member(_quester_snapshot=fresh)
        self.assert_quester_attempt_only()
        self.assertFalse(session.quester_out)

    async def test_b6_retirement_disables_old_replay_and_releases_ordinary_worker(self):
        session = await self.capture()
        for member in session.members:
            member.zone, member.area_id = 'Outside', 100
            await party.observe_dungeon_session(member)
        self.assertEqual(session.phase, 'retired')

        self.assertIsNone(session.transition)
        self.assertFalse(await self.caller.recover_dungeon_transition())
        self.caller.teleport_to_quest_target.assert_not_awaited()
        self.q.send_key.assert_not_awaited()
        self.q.quest_party_quest_worker_zone = 'Outside'
        self.q.quest_party_probe_pending = False
        await self.caller.auto_quest_solo()
        self.assert_quester_attempt_only()
        self.assertIsNone(party.dungeon_session_for(self.q))
        self.assertEqual(session.phase, 'retired')

    async def test_initial_or_actually_pending_follower_probe_remains_owned(self):
        await self.enter()
        self.q.quest_party_probe_pending = True
        self.q.quest_party_quest_worker_zone = None
        self.assertTrue(await self.caller._quest_party_probe_blocks_movement())
        self.assertTrue(self.q.quest_party_probe_pending)
        self.h._quest_follower_probe = dict(quester=self.q, pending=True)
        self.assertTrue(await self.caller._quest_party_probe_blocks_movement())
        self.assertTrue(self.q.quest_party_probe_pending)


if __name__ == '__main__':
    unittest.main()
