"""Party probe deadlines and publication of current, whole-party arrival."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src import quest_party as party
from src.questing import Quester
import test_dungeon_local_tp as fixtures
from test_full_chain_415_restore import entry

REAL_SLEEP = asyncio.sleep
REAL_WAIT_FOR = asyncio.wait_for


class PartyProbeTests(fixtures.DungeonFixture, unittest.IsolatedAsyncioTestCase):
    follow_until = fixtures.FollowerTests.follow_until

    async def test_confirmed_dungeon_session_behavior_unchanged(self):
        session = await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room2'
        self.q.quest_party_probe_pending = True
        gate = Quester(self.q, [self.q], None)
        self.assertTrue(await gate._quest_party_probe_blocks_movement())
        self.assertTrue(session.confirmed)

    async def test_confirmed_dungeon_friend_failures_stop_after_two_attempts(self):
        session = await self.enter()
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.ready_probe(present=False)
        self.proof.return_value = False
        friend = AsyncMock(side_effect=RuntimeError('friend teleport unavailable'))
        done = asyncio.Event()
        def status(_h, _q, text):
            if '已达上限' in text:
                done.set()
        with patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=True, when_status=status,
                extra=self.probe_namespace(asyncio=self.clock_api(done=done, stop_after=100),
                    teleport_to_friend_from_list=friend))
        self.assertEqual(friend.await_count, 2)
        self.assertEqual(session.follow_attempts[id(self.h)], 2)
        self.assertTrue(self.q.quest_party_probe_pending)

    async def test_dungeon_follow_limit_survives_follower_restart(self):
        session = await self.enter()
        session.follow_attempts[id(self.h)] = 2
        self.q.zone, self.q.area_id = 'Dungeon/Room2', 201
        self.ready_probe(present=False)
        self.proof.return_value = False
        done = asyncio.Event()
        def status(_h, _q, text):
            if '已达上限' in text:
                done.set()
        await self.follow_until(done, primary=True, when_status=status,
            extra=self.probe_namespace(asyncio=self.clock_api(done=done, stop_after=20)))
        self.friend.assert_not_awaited()
        self.assertEqual(session.follow_attempts[id(self.h)], 2)

    async def test_proven_local_reunion_clears_dungeon_follow_budget(self):
        session = await self.enter()
        session.follow_attempts[id(self.h)] = 2
        self.ready_probe()
        done = asyncio.Event()
        async def local(*_args, **_kwargs):
            done.set()
            return False
        with patch.object(Quester, 'move_dungeon_member', AsyncMock(side_effect=local)):
            await self.follow_until(done, primary=True, extra=self.probe_namespace())
        self.assertNotIn(id(self.h), session.follow_attempts)
        self.friend.assert_not_awaited()

    def ready_probe(self, *, present=True):
        self.q.quest_party_observed_zone = self.q.zone
        self.q.quest_party_quest_worker_zone = 'Dungeon/PreviousRoom'
        self.q.quest_party_probe_pending = True
        self.h.get_base_entity_list.return_value = [self.q.client_object] if present else []

    def clock_api(self, *, done=None, stop_after=None, accelerated=False, after_sleep=None):
        self.clock = 0.0
        self.polls = 0

        async def tick(delay):
            self.clock += delay
            self.polls += 1
            if after_sleep is not None:
                after_sleep()
            if stop_after is not None and self.polls >= stop_after:
                done.set()
            await REAL_SLEEP(0)

        async def bounded(awaitable, timeout):
            # Cancel the actual pending operation. The test's own deadline uses
            # REAL_WAIT_FOR and cannot be shortened by this injected clock.
            return await REAL_WAIT_FOR(awaitable, min(timeout, .02))

        return SimpleNamespace(sleep=tick,
            get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
            wait_for=bounded if accelerated else REAL_WAIT_FOR,
            CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError,
            timeout=asyncio.timeout, current_task=asyncio.current_task)

    def probe_namespace(self, **extra):
        return dict(clients_share_live_area=self.proof, **extra)

    async def three_member_session(self):
        third = fixtures.client(3, XYZ(1200, 1300, 0))
        third.quest_party_status_session = self.owner
        self.q.quest_party_hitters = [self.h, third]
        session = await party.begin_dungeon_entry(self.q, [self.q, self.h, third], evidence=True)
        for member in session.members:
            member.zone, member.area_id = 'Dungeon/Room1', 200
        await party.observe_dungeon_session(self.q)
        self.assertTrue(session.confirmed)
        return session, third

    async def test_already_arrived_party_releases_probe_without_friend_ui(self):
        await self.enter()
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe()
        done = asyncio.Event()
        async def local(*_):
            done.set()
            return True
        with patch.object(Quester, 'move_dungeon_member', AsyncMock(side_effect=local)):
            await self.follow_until(done, primary=True, extra=self.probe_namespace())
        self.assertFalse(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/Room2')
        self.friend.assert_not_awaited()
        self.h.mouse_handler.__aenter__.assert_not_awaited()

    async def test_mouse_acquisition_timeout_preserves_gate_and_cancels_wait(self):
        await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.ready_probe(present=False)
        self.proof.return_value = False
        done = asyncio.Event()
        cancelled = asyncio.Event()
        async def acquire():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        self.h.mouse_handler.__aenter__.side_effect = acquire
        def status(_h, _q, text):
            if '重试' in text:
                done.set()
        with patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=True, when_status=status,
                extra=self.probe_namespace(asyncio=self.clock_api(accelerated=True)))
        self.assertTrue(cancelled.is_set(), 'The expired mouse acquisition must be cancelled')
        self.assertTrue(self.q.quest_party_probe_pending)
        self.friend.assert_not_awaited()

    async def test_same_zone_without_instance_proof_does_not_publish_success(self):
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe(present=False)
        self.proof.return_value = False
        done = asyncio.Event()
        def status(_h, _q, text):
            if '重试' in text:
                done.set()
        async def local(*_):
            done.set()
            return True
        with patch.object(Quester, 'move_dungeon_member', AsyncMock(side_effect=local)), patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=True, when_status=status,
                extra=self.probe_namespace(asyncio=self.clock_api(done=done, stop_after=12)))
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')
        self.assertFalse(self.q.in_solo_zone)

    async def test_cancelled_friend_action_does_not_hang_in_mouse_exit(self):
        await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.ready_probe(present=False)
        self.proof.return_value = False
        done, released = asyncio.Event(), asyncio.Event()
        async def release(*_):
            try:
                await asyncio.Future()
            finally:
                released.set()
        self.h.mouse_handler.__aexit__.side_effect = release
        async def click(*_, **__):
            await asyncio.Future()
        def status(_h, _q, text):
            if '重试' in text: done.set()
        with patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=True, when_status=status,
                extra=self.probe_namespace(asyncio=self.clock_api(accelerated=True),
                    teleport_to_friend_from_list=AsyncMock(side_effect=click)))
        self.assertTrue(released.is_set())
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_mouse_exit_timeout_preserves_caller_cancellation(self):
        from contextlib import asynccontextmanager
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def exit_mouse(*_):
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        self.h.mouse_handler.__aexit__.side_effect = exit_mouse
        raw = entry('probe_mouse', dict(asyncio=self.clock_api(accelerated=True)), hitter=self.h)
        scope = asynccontextmanager(raw)
        async def cancelled_probe():
            async with scope(self.h):
                started.set()
                await asyncio.Future()
        task = asyncio.create_task(cancelled_probe())
        try:
            await REAL_WAIT_FOR(started.wait(), .2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await REAL_WAIT_FOR(asyncio.shield(task), .2)
            self.assertTrue(cleaned.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_unavailable_objective_read_is_cancelled_and_probe_can_retry(self):
        await self.enter()
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.ready_probe(present=False)
        self.proof.return_value = False
        done = asyncio.Event()
        cancelled = asyncio.Event()
        async def objective(*_):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        def status(_h, _q, text):
            if '重试' in text:
                done.set()
        friend = AsyncMock(side_effect=RuntimeError('friend UI temporarily unavailable'))
        with patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(side_effect=objective)):
            await self.follow_until(done, primary=True, when_status=status,
                extra=self.probe_namespace(asyncio=self.clock_api(accelerated=True),
                    teleport_to_friend_from_list=friend))
        self.assertTrue(cancelled.is_set(), 'An unavailable objective must not block the probe indefinitely')
        friend.assert_awaited()
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_one_arrived_hitter_cannot_release_missing_third_member(self):
        session, third = await self.three_member_session()
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe()
        self.proof.side_effect = lambda a, b: a.zone == b.zone
        done = asyncio.Event()
        with patch.object(Quester, 'move_dungeon_member', AsyncMock(return_value=False)):
            await self.follow_until(done, primary=True, extra=self.probe_namespace(
                walker=SimpleNamespace(clients=list(session.members)),
                asyncio=self.clock_api(done=done, stop_after=8)))
        self.assertEqual(third.zone, 'Dungeon/Room1')
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_non_primary_hitter_can_join_while_party_probe_is_pending(self):
        session, third = await self.three_member_session()
        primary = self.h
        self.q.zone = primary.zone = 'Dungeon/Room2'
        self.h = third
        self.ready_probe(present=False)
        self.proof.side_effect = lambda a, b: a.zone == b.zone
        done = asyncio.Event()
        async def joined(*_, **__):
            third.zone = 'Dungeon/Room2'
            third.get_base_entity_list.return_value = [self.q.client_object]
        async def local(*_):
            if third.zone == 'Dungeon/Room2':
                done.set()
            return False
        with patch.object(Quester, 'move_dungeon_member', AsyncMock(side_effect=local)), patch.object(
                Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.follow_until(done, primary=False, extra=self.probe_namespace(
                walker=SimpleNamespace(clients=list(session.members)),
                teleport_to_friend_from_list=AsyncMock(side_effect=joined),
                asyncio=self.clock_api(done=done, stop_after=12)))
        self.assertEqual(third.zone, 'Dungeon/Room2')
        self.assertFalse(self.q.in_solo_zone)

    async def direct_probe(self, done, change_equipment, *, clock=None, prepare_name=None, when_status=None):
        namespace = dict(walker=SimpleNamespace(clients=[self.q, self.h]), questing_status=True,
            gear_switching_in_solo_zones=True, quest_friend_icons={}, use_potions=False,
            buy_potions=False, close_automation_popup=AsyncMock(return_value=False),
            is_free=AsyncMock(return_value=True), is_visible_by_path=AsyncMock(return_value=False),
            is_friend_teleport_error=AsyncMock(return_value=False), SprintyClient=lambda c: c,
            clients_share_live_area=self.proof, teleport_to_friend_from_list=AsyncMock(),
            asyncio=clock or self.clock_api())
        follow = entry('_follow_quester_session', namespace, members=[self.q, self.h],
            change_party_equipment=change_equipment,
            prepare_quester_name=prepare_name if prepare_name is not None else AsyncMock(),
            remove_party_status=Mock(), restart_quest_worker_after_probe=Mock(),
            update_party_status=Mock(side_effect=when_status))
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), patch.object(
                Quester, 'move_dungeon_member', AsyncMock(return_value=False)):
            task = asyncio.create_task(follow(self.h, self.q, True))
            try:
                await REAL_WAIT_FOR(done.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_unavailable_quester_name_is_cancelled_without_releasing_probe(self):
        self.q.zone, self.h.zone = 'Dungeon/Room2', 'Dungeon/Room1'
        self.q.wizard_name = None
        self.ready_probe(present=False)
        self.proof.return_value = False
        done = asyncio.Event()
        cancelled = asyncio.Event()
        async def prepare_name(*_):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        def status(*_):
            if cancelled.is_set():
                done.set()
        with patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use switch')):
            await self.direct_probe(done, AsyncMock(return_value=True),
                clock=self.clock_api(accelerated=True),
                prepare_name=AsyncMock(side_effect=prepare_name), when_status=status)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_cancelled_gear_switch_keeps_probe_gate(self):
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe()
        self.q.quest_party_solo_gear_active = True
        started = asyncio.Event()
        async def gear(*_):
            started.set()
            await asyncio.Future()
        await self.direct_probe(started, AsyncMock(side_effect=gear))
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_gear_switch_result_cannot_release_new_zone_probe(self):
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe()
        self.q.quest_party_solo_gear_active = True
        done = asyncio.Event()
        changed = False
        async def gear(*_):
            nonlocal changed
            self.q.zone = 'Dungeon/Room3'
            self.q.is_loading.return_value = True
            changed = True
            return True
        def after_sleep():
            if changed:
                done.set()
        await self.direct_probe(done, AsyncMock(side_effect=gear),
            clock=self.clock_api(after_sleep=after_sleep))
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')

    async def test_gear_switch_result_cannot_release_changed_task_probe(self):
        self.q.zone = self.h.zone = 'Dungeon/Room2'
        self.ready_probe()
        self.q.quest_party_solo_gear_active = True
        done = asyncio.Event()
        changed = False
        async def gear(*_):
            nonlocal changed
            self.q.quest_id_value = 99
            self.q.is_loading.return_value = True
            changed = True
            return True
        def after_sleep():
            if changed:
                done.set()
        await self.direct_probe(done, AsyncMock(side_effect=gear),
            clock=self.clock_api(after_sleep=after_sleep))
        self.assertTrue(self.q.quest_party_probe_pending)
        self.assertEqual(self.q.quest_party_quest_worker_zone, 'Dungeon/PreviousRoom')


class ProbeHelperCleanupTests(fixtures.DungeonFixture, unittest.IsolatedAsyncioTestCase):
    def deadline_api(self):
        async def bounded(awaitable, timeout):
            return await REAL_WAIT_FOR(awaitable, min(timeout, .01))
        async def wait(awaitables, *, timeout=None, **kwargs):
            return await asyncio.wait(awaitables, timeout=min(timeout, .01), **kwargs)
        return SimpleNamespace(wait_for=bounded, wait=wait, create_task=asyncio.create_task,
            gather=asyncio.gather,
            CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError)

    async def drain(self, task):
        if not task.done():
            task.cancel()
        await REAL_WAIT_FOR(asyncio.gather(task, return_exceptions=True), .2)

    async def test_real_name_helper_cancellation_bounds_character_cleanup(self):
        self.q.wizard_name = None
        opened = asyncio.Event()
        closing = asyncio.Event()
        cleanup_cancelled = asyncio.Event()
        async def open_screen(*_):
            opened.set()
            await asyncio.Future()
        async def close_screen(*_):
            closing.set()
            try:
                await asyncio.Future()
            finally:
                cleanup_cancelled.set()
        prepare = entry('prepare_quester_name', dict(asyncio=self.deadline_api(),
            set_wizard_name_from_character_screen=AsyncMock()))
        with patch.object(Quester, 'open_character_screen', AsyncMock(side_effect=open_screen)), patch.object(
                Quester, 'close_character_screen', AsyncMock(side_effect=close_screen)):
            task = asyncio.create_task(prepare(self.q))
            try:
                await REAL_WAIT_FOR(opened.wait(), .2)
                task.cancel()
                await REAL_WAIT_FOR(closing.wait(), .2)
                with self.assertRaises(asyncio.CancelledError):
                    await REAL_WAIT_FOR(asyncio.shield(task), .1)
                self.assertTrue(cleanup_cancelled.is_set())
                self.assertTrue(task.done())
            finally:
                await self.drain(task)

    def blocked_native_equipment(self):
        from wizwalker.mouse_handler import MouseHandler
        mouse = MouseHandler.__new__(MouseHandler)
        mouse.client = self.q
        mouse._ref_lock = None
        mouse._ref_count = 0
        started = asyncio.Event()
        exit_started = asyncio.Event()
        exit_cancelled = asyncio.Event()
        tasks = []
        async def deactivate():
            exit_started.set()
            try:
                await asyncio.Future()
            finally:
                exit_cancelled.set()
        self.q.hook_handler = SimpleNamespace(_check_if_hook_active=lambda *_: True,
            deactivate_mouseless_cursor_hook=AsyncMock(side_effect=deactivate))
        async def equipment(*_):
            tasks.append(asyncio.current_task())
            async with mouse:
                started.set()
                await asyncio.Future()
        return equipment, mouse, started, exit_started, exit_cancelled, tasks

    async def test_real_equipment_timeout_drains_native_mouse_exit(self):
        equipment, mouse, _, exit_started, exit_cancelled, tasks = self.blocked_native_equipment()
        change = entry('change_party_equipment', dict(asyncio=self.deadline_api(),
            change_equipment_set=equipment))
        task = asyncio.create_task(change(self.q, 0))
        try:
            self.assertFalse(await REAL_WAIT_FOR(asyncio.shield(task), .2))
            self.assertTrue(exit_started.is_set())
            self.assertTrue(exit_cancelled.is_set())
            self.assertTrue(all(t.done() for t in tasks))
            self.assertEqual(mouse._ref_count, 0)
            self.assertFalse(mouse._ref_lock.locked())
        finally:
            await self.drain(task)
            for child in tasks:
                await self.drain(child)

    async def test_real_equipment_caller_cancellation_drains_native_mouse_exit(self):
        equipment, mouse, started, exit_started, exit_cancelled, tasks = self.blocked_native_equipment()
        change = entry('change_party_equipment', dict(asyncio=self.deadline_api(),
            change_equipment_set=equipment))
        task = asyncio.create_task(change(self.q, 0))
        try:
            await REAL_WAIT_FOR(started.wait(), .2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await REAL_WAIT_FOR(asyncio.shield(task), .2)
            self.assertTrue(exit_started.is_set())
            self.assertTrue(exit_cancelled.is_set())
            self.assertTrue(all(t.done() for t in tasks))
            self.assertEqual(mouse._ref_count, 0)
            self.assertFalse(mouse._ref_lock.locked())
        finally:
            await self.drain(task)
            for child in tasks:
                await self.drain(child)

    async def test_real_equipment_timeout_bounds_backpack_close_key(self):
        cancelled = asyncio.Event()
        async def equipment(*_):
            await asyncio.Future()
        async def close_key(*_):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        self.q.send_key.side_effect = close_key
        change = entry('change_party_equipment', dict(asyncio=self.deadline_api(),
            change_equipment_set=equipment))
        task = asyncio.create_task(change(self.q, 0))
        try:
            self.assertFalse(await REAL_WAIT_FOR(asyncio.shield(task), .2))
            self.assertTrue(cancelled.is_set())
        finally:
            await self.drain(task)


if __name__ == '__main__':
    unittest.main()
