"""Dialogue and target updates must settle before releasing a dungeon group."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import quest_party as party
from src.questing import Quester
from src.paths import advance_dialog_path
from test_dungeon_party_transition import PartyTransitionFixture


class SharedReleaseTests(PartyTransitionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        for name in ('src.mainline_recovery.maybe_recover_mainline',
                     'src.questing.close_npc_quest_menu', 'src.questing.close_automation_popup',
                     'src.questing.is_spiral_door_open', 'src.questing.is_potion_needed'):
            self.stack.enter_context(patch(name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(Quester, 'handle_pending_dungeon_confirmation',
                                             AsyncMock(return_value=False)))
        self.q.quest_position.position.return_value = XYZ(500, 600, 0)
        self.caller = Quester(self.q, [self.q], None)
        self.caller.teleport_to_quest_target = AsyncMock(side_effect=self.land)
        self.caller._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.caller.read_popup = AsyncMock(return_value='Press X to Use')
        self.caller._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Use Lever'))
        self.caller._quest_interaction_signature = AsyncMock(return_value=('entry',))
        self.caller._recover_quest_x_direction = AsyncMock(return_value=True)

    async def capture(self):
        session = await self.enter()
        self.assertTrue(await party.remember_dungeon_transition(self.q, XYZ(500, 600, 0)))
        return session
    async def test_pending_dialogue_update_blocks_hitter_without_visible_dialogue(self):
        await self.enter()
        self.q._quest_dialogue_update_pending = True
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_visible_quester_dialogue_blocks_hitter(self):
        await self.enter()
        self.visible.side_effect = lambda c, path: c is self.q and path == advance_dialog_path
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_new_target_must_survive_spaced_confirmation(self):
        await self.enter()
        old, new = XYZ(6464, -3998, 0), XYZ(6309, -1207, 0)
        self.q.quest_position.position.side_effect = [old, new, new, new, new]
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_target_change_between_frames_is_not_released(self):
        await self.enter()
        # Keep the real yield: the module-wide asyncio.sleep patch is shared.
        from test_dungeon_party_transition import REAL_SLEEP
        async def update(delay):
            self.clock += delay
            if delay == .75:
                self.q.quest_position.position.return_value = XYZ(6309, -1207, 0)
            await REAL_SLEEP(0)
        with patch('src.questing.asyncio.sleep', update):
            self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_closed_hitter_dialogue_does_not_require_a_hitter_quest_worker(self):
        await self.enter()
        self.h._quest_dialogue_update_pending = True
        self.assertTrue(await self.reader.move_dungeon_member())

    async def test_quester_goal_change_during_confirmation_is_not_released(self):
        await self.enter()
        self.q.goal_id.side_effect = [7, 8, 8, 8]
        self.assertFalse(await self.reader.move_dungeon_member())
        self.reader.teleport_to_quest_target.assert_not_awaited()

    async def test_quester_refreshes_old_iteration_target_to_confirmed_group_target(self):
        session = await self.enter()
        old, new = XYZ(6464, -3998, 0), XYZ(6309, -1207, 0)
        reads = 0
        async def position():
            nonlocal reads
            reads += 1
            return old if reads == 1 else new
        self.q.quest_position.position.side_effect = position
        await self.caller.auto_quest_solo()
        move = self.caller.teleport_to_quest_target.await_args
        self.assertIsNotNone(move)
        self.assertEqual((move.args[1].x, move.args[1].y), (new.x, new.y))
        self.assertEqual((session.transition['target'].x, session.transition['target'].y), (new.x, new.y))

    async def test_post_movement_handling_keeps_the_snapshot_actually_moved_to(self):
        session = await self.enter()
        async def newer_release():
            session.transition = dict(session.transition, target=XYZ(20000, 30000, 0))
        self.caller.handle_questing_zone_change = AsyncMock(side_effect=newer_release)
        await self.caller.auto_quest_solo()
        self.caller._maybe_photo_giant_vat.assert_awaited_once()
        target = self.caller.teleport_to_quest_target.await_args.args[1]
        self.assertEqual((target.x, target.y), (500, 600))

    async def test_both_workers_keep_one_release_and_same_target(self):
        session = await self.enter()
        await asyncio.gather(self.caller.auto_quest_solo(), self.reader.move_dungeon_member())
        state = session.transition
        self.assertIsNotNone(state)
        for worker in (self.reader, self.caller):
            target = worker.teleport_to_quest_target.await_args.args[1]
            self.assertEqual((target.x, target.y, target.z),
                             (state['target'].x, state['target'].y, state['target'].z))
        await self.reader.move_dungeon_member()
        self.assertIs(session.transition, state)

    async def test_peer_crossing_does_not_cancel_valid_source_movement(self):
        session = await self.enter()
        entered, finish, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def moving(*_args, **_kwargs):
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        self.reader.teleport_to_quest_target.side_effect = moving
        worker = asyncio.create_task(self.reader.move_dungeon_member())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
            self.q.quest_id_value += 1
            self.q.quest_position.position.return_value = XYZ(19000, 29000, 0)
            await party.observe_dungeon_session(self.q)
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertFalse(cancelled.is_set())
            self.assertFalse(worker.done())
            finish.set()
            await asyncio.wait_for(worker, 1)
            self.assertNotIn(id(self.h), session.transition['arrivals'])
            self.assertIn(id(self.q), session.transition['arrivals'])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_valid_old_entry_recovers_without_friend_tp(self):
        session = await self.capture()
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.q.quest_id_value += 1
        await party.observe_dungeon_session(self.q)
        self.assertTrue(await self.reader.recover_dungeon_transition())
        self.h.send_key.assert_awaited_once()
        self.h.mouse_handler.assert_not_awaited()
        self.assertEqual(session.transition['attempts'][id(self.h)], 1)


if __name__ == '__main__':
    unittest.main()
