"""Historical 180s snapshot timeout, adapted to current sessions and quest book."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import quest_party as party
from src.questing import Quester
from src.mainline_progress import log_mainline_progress
from tests.test_dungeon_local_tp import client, REAL_SLEEP
from tests.test_mainline_recovery import MainlineClient, MainlineRecoveryTests


class DungeonQuestRefreshTests(unittest.IsolatedAsyncioTestCase):
    runtime = MainlineRecoveryTests.runtime
    tick = MainlineRecoveryTests.tick

    def setUp(self):
        MainlineRecoveryTests.setUp(self)
        self.runtime_loaded = False
        self.q = self.client
        defaults = client(1, XYZ(42, 100, 0))
        for name, value in vars(defaults).items():
            if not hasattr(self.q, name):
                setattr(self.q, name, value)
        self.h = client(2, XYZ(42, 100, 0))
        self.q.quest_party_hitters = [self.h]
        self.h.quest_party_status_session = object()
        self.h.quest_party_quester = self.q
        self.q.zone = self.h.zone = 'Outside'
        self.q.zone_name = AsyncMock(side_effect=lambda: self.q.zone)
        self.objective = 'Defeat 0/1'
        self.moves = []
        self.reader = Quester(self.q, [self.q], None)
        self.reader.read_quest_txt = AsyncMock(side_effect=lambda _: self.objective)
        for name, value in (('_npc_dialogue_visible', False), ('_ensure_quest_pet_mode', True),
                ('_wait_internal_quest_failure', False), ('handle_pending_dungeon_confirmation', False),
                ('_quest_party_probe_blocks_movement', False), ('_try_deimos_interaction', False),
                ('_hold_arrived_interaction_rejection', False)):
            setattr(self.reader, name, AsyncMock(return_value=value))
        self.reader.handle_npc_talking_quests = AsyncMock()
        async def confirmed(_):
            return await self.snapshot()
        self.reader._confirmed_dungeon_target = AsyncMock(side_effect=confirmed)
        async def moved(**kwargs):
            self.moves.append(await self.reader._dungeon_quest_snapshot(self.q))
            return False  # End the ordinary tick at its real movement boundary.
        self.reader.move_dungeon_member = AsyncMock(side_effect=moved)
        self.stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch('src.utils.clients_share_live_area', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.questing.is_potion_needed', AsyncMock(return_value=False)))
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open'):
            self.stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
        self.runtime()

    async def snapshot(self):
        return (await self.q.quest_id(), await self.q.goal_id(),
                await self.q.quest_position.position(), await self.q.zone_name())

    async def asyncSetUp(self):
        await log_mainline_progress(self.q)
        self.assertIsNotNone(self.q._xuanshu_mainline_last_success)
        self.session = await party.begin_dungeon_entry(self.q, [self.q, self.h], evidence=True)
        self.q.zone = self.h.zone = 'Dungeon/Room1'
        self.q.area_id = self.h.area_id = 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(self.session.confirmed)

    async def poll(self, at):
        self.clock = at
        await self.reader.auto_quest_solo()

    def selected(self):
        return self.q.events.count(('click', 'txtGoal'))

    async def test_179_seconds_waits_and_180_seconds_selects_once_and_closes_book(self):
        await self.poll(0)
        await self.poll(179)
        self.assertEqual(self.selected(), 0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertEqual(self.q.events.count(('key', Keycode.Q)), 2)
        self.assertFalse(self.q.book_open)
        self.q.teleport.assert_not_awaited()
        self.assertIs(party.dungeon_session_for(self.q), self.session)

    async def test_snapshot_quest_goal_and_text_changes_restart_the_full_timer(self):
        for changed in ('quest', 'goal', 'text'):
            with self.subTest(changed=changed):
                if changed != 'quest':
                    self.stack.close()
                    self.setUp()
                    await self.asyncSetUp()
                await self.poll(0)
                if changed == 'quest': self.q.tracked = 43
                elif changed == 'goal': self.q.goal_id.return_value = 8
                else: self.objective = 'Defeat 1/2'
                await self.poll(179)
                await self.poll(180)
                self.assertEqual(self.selected(), 0)
                await self.poll(359)
                self.assertEqual(self.selected(), 1)

    async def test_movement_and_transition_recreation_do_not_refresh_the_timer(self):
        await self.poll(0)
        for at in (30, 90, 179):
            self.q.position = XYZ(at, at, 0)
            self.session.transition = dict(recreated=at)
            await self.poll(at)
        self.assertGreaterEqual(len(self.moves), 4)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)

    async def test_busy_timeout_remains_due_until_safe(self):
        for busy in ('battle', 'loading', 'dialogue', 'potions', 'recovery', 'mainline'):
            with self.subTest(busy=busy):
                if busy != 'battle':
                    self.stack.close()
                    self.setUp()
                    await self.asyncSetUp()
                await self.poll(0)
                if busy == 'battle': self.q.battle = True
                elif busy == 'loading': self.q.loading = True
                elif busy == 'dialogue': self.q._quest_dialogue_update_pending = True
                elif busy == 'potions': self.q.refilling_potions = True
                elif busy == 'recovery': self.q.quest_recovery_owner = 'other-recovery'
                else: self.q._xuanshu_mainline_recovery = dict(phase='observe')
                await self.poll(180)
                self.assertEqual(self.selected(), 0)
                self.assertNotIn(('key', Keycode.Q), self.q.events)
                self.q.battle = self.q.loading = self.q.refilling_potions = False
                self.q._quest_dialogue_update_pending = False
                self.q.quest_recovery_owner = None
                self.q._xuanshu_mainline_recovery = None
                await self.poll(181)
                self.assertEqual(self.selected(), 1)

    async def test_follower_never_refreshes_mainline(self):
        self.reader.client = self.h
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 0)
        self.h.send_key.assert_not_awaited()

    async def test_ordinary_mainline_recovery_stays_forbidden_inside_dungeon(self):
        recovery = self.runtime()
        self.assertFalse(await recovery.maybe_recover_mainline(self.q))
        with self.assertRaises(RuntimeError):
            await recovery.select_from_book(self.q, self.q._xuanshu_mainline_last_success)
        self.assertNotIn(('key', Keycode.Q), self.q.events)

    async def test_unchanged_refresh_waits_another_full_180_seconds(self):
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        finished = self.clock
        await self.poll(finished + 1)
        await self.poll(finished + 179)
        self.assertEqual(self.selected(), 1)
        await self.poll(finished + 180)
        self.assertEqual(self.selected(), 2)

    async def test_changed_goal_is_left_for_the_next_existing_quest_tick(self):
        original = self.q.mouse_handler.click_window
        async def click(window, **kwargs):
            await original(window, **kwargs)
            if window.label == 'txtGoal': self.q.goal_id.return_value = 8
        self.q.mouse_handler.click_window = click
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertEqual(self.moves[-1][1], 7)  # No old/new movement in recovery tick.
        await self.poll(self.clock + 1)
        self.assertEqual(self.moves[-1][1], 8)

    async def test_no_session_or_unconfirmed_entry_does_not_refresh(self):
        self.session.confirmed = False
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 0)

    async def test_current_selector_can_find_the_confirmed_card_on_a_later_page(self):
        self.q.pages = [['任务搜寻'], ['原主线']]
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertEqual(self.q.tracked, 42)
        self.assertFalse(self.q.book_open)

    async def test_missing_owned_card_never_clicks_finder_and_waits_full_cycle(self):
        self.q.pages = [['任务搜寻']]
        await self.poll(0)
        await self.poll(180)
        self.assertIn(('key', Keycode.Q), self.q.events)
        self.assertEqual(self.selected(), 0)
        self.assertEqual(self.q.tracked, 42)
        self.assertFalse(self.q.book_open)
        count = len(self.q.events)
        await self.poll(self.clock + 1)
        self.assertEqual(len(self.q.events), count)

    async def test_restricted_entry_rejects_calls_without_active_timeout(self):
        from src.mainline_recovery import refresh_stalled_dungeon_quest
        self.assertFalse(await refresh_stalled_dungeon_quest(self.q, self.session))
        self.assertNotIn(('key', Keycode.Q), self.q.events)

    async def test_owned_card_refresh_does_not_enable_normal_finder(self):
        self.q.mainline_finder_enabled = False
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertFalse(await self.runtime().maybe_recover_mainline(self.q))

    async def test_retired_card_cannot_match_the_previous_title_alias(self):
        last = self.q._xuanshu_mainline_last_success
        last['retired_mainlines'] = frozenset({('Mirage', 5)})
        self.q.pages = [['原主线'], ['下一主线']]
        await self.poll(0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertEqual(self.q.tracked, 43)

    async def test_busy_book_pass_is_not_cancelled_by_a_due_timeout_tick(self):
        await self.poll(0)
        running = asyncio.create_task(asyncio.Event().wait())
        self.q._xuanshu_mainline_recovery = dict(phase='book')
        self.q._xuanshu_mainline_book_task = running
        self.q.quest_recovery_owner = self.runtime().OWNER
        try:
            await self.poll(180)
            await REAL_SLEEP(0)
            self.assertFalse(running.done())
            self.assertEqual(self.selected(), 0)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_old_shared_movement_releases_input_before_q(self):
        from src.automation_ownership import get_client_automation_ownership
        await self.poll(0)
        started, drained = asyncio.Event(), asyncio.Event()
        hitter = Quester(self.h, [self.h], None)
        async def moving(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await REAL_SLEEP(.01)
                drained.set()
        hitter.teleport_to_quest_target = moving
        task = asyncio.create_task(hitter.move_dungeon_member())
        original = self.q.send_key
        async def key(code, *args):
            if code == Keycode.Q:
                self.assertTrue(drained.is_set())
                self.assertFalse(get_client_automation_ownership(self.h).locked)
            await original(code, *args)
        self.q.send_key = key
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.session.transition['deadline'] = 1000
            await self.poll(180)
            self.assertEqual(self.selected(), 1)
            self.assertTrue(task.done())
            self.assertIs(party.dungeon_session_for(self.h), self.session)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_other_recovery_starting_during_preflight_is_not_overwritten(self):
        from src.paths import quest_buttons_parent_path
        recovery = self.runtime()
        original = recovery.is_visible_by_path
        fired = False
        async def visible(member, path):
            nonlocal fired
            result = await original(member, path)
            if not fired and self.clock >= 180 and path == quest_buttons_parent_path:
                fired = True
                self.q.quest_recovery_owner = 'other-recovery'
            return result
        await self.poll(0)
        with patch.object(recovery, 'is_visible_by_path', visible):
            await self.poll(180)
        self.assertEqual(self.q.quest_recovery_owner, 'other-recovery')
        self.assertEqual(self.selected(), 0)
        self.assertNotIn(('key', Keycode.Q), self.q.events)

    async def test_cancelling_the_book_pass_does_not_cancel_the_quest_worker(self):
        recovery = self.runtime()
        entered = asyncio.Event()
        async def waiting(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        await self.poll(0)
        with patch.object(recovery, 'stable_page', waiting):
            task = asyncio.create_task(self.poll(180))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.q.mainline_finder_enabled = False
                recovery.cancel_mainline_recovery(self.q)
                result = await asyncio.gather(task, return_exceptions=True)
                self.assertFalse(isinstance(result[0], BaseException), repr(result[0]))
                self.assertTrue(self.q.questing_status)
                self.assertFalse(self.q.book_open)
                self.assertIsNone(self.q.quest_recovery_owner)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_goal_progress_during_preflight_skips_the_expired_q_pass(self):
        from src.paths import quest_buttons_parent_path
        recovery = self.runtime()
        original = recovery.is_visible_by_path
        fired = False
        async def visible(member, path):
            nonlocal fired
            result = await original(member, path)
            if not fired and self.clock >= 180 and path == quest_buttons_parent_path:
                fired = True
                self.q.goal_id.return_value = 8
            return result
        await self.poll(0)
        with patch.object(recovery, 'is_visible_by_path', visible):
            await self.poll(180)
        self.assertEqual(self.selected(), 0)
        self.assertNotIn(('key', Keycode.Q), self.q.events)
        self.assertEqual(self.moves[-1][1], 8)

    async def test_exhausted_movement_wait_still_allows_the_180_second_book_refresh(self):
        self.reader._wait_internal_quest_failure = Quester._wait_internal_quest_failure.__get__(self.reader)
        await self.reader._defer_internal_quest_failure(
            self.q, 'exhausted movement', kind='exhausted', delay=2)
        waiting = self.q._quest_internal_failure_wait
        await self.poll(0)
        await self.poll(179)
        self.assertEqual(self.selected(), 0)
        await self.poll(180)
        self.assertEqual(self.selected(), 1)
        self.assertIs(self.q._quest_internal_failure_wait, waiting)
        self.assertFalse(self.moves)
        self.assertFalse(self.q.book_open)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DungeonQuestRefreshTests(name)
        for name in DungeonQuestRefreshTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
