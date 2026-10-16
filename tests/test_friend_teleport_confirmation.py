"""A post-request missing modal must reach real arrival verification, not alias replay."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import utils
from src.questing import Quester
from tests.test_dungeon_local_tp import FollowerTests as Fixture


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_confirmation_after_request_is_pending_not_success_or_lookup_failure(self):
        client = SimpleNamespace(title='p4')
        with patch.object(utils, '_teleport_to_friend', AsyncMock(
                side_effect=ValueError('No child window named MessageBoxModalWindow'))) as request:
            self.assertFalse(await utils._request_friend_teleport(client, object()))
        request.assert_awaited_once()

    async def test_disappeared_confirmation_and_profile_are_also_pending(self):
        for name in ('centerButton', 'btnCharacterClose'):
            with self.subTest(name=name), patch.object(utils, '_teleport_to_friend', AsyncMock(
                    side_effect=ValueError(f'No child window named {name}'))):
                self.assertFalse(await utils._request_friend_teleport(SimpleNamespace(title='p4'), object()))

    async def test_missing_go_button_and_other_errors_are_not_swallowed(self):
        for message in ('No child window named btnGoToFriend', 'unrelated read failure'):
            with self.subTest(message=message), patch.object(utils, '_teleport_to_friend', AsyncMock(
                    side_effect=ValueError(message))):
                with self.assertRaises(ValueError):
                    await utils._request_friend_teleport(SimpleNamespace(title='p4'), object())

    async def test_cancel_propagates_without_repeating_input(self):
        import asyncio
        with patch.object(utils, '_teleport_to_friend', AsyncMock(side_effect=asyncio.CancelledError)) as request:
            with self.assertRaises(asyncio.CancelledError):
                await utils._request_friend_teleport(SimpleNamespace(title='p4'), object())
        request.assert_awaited_once()


class ActualArrivalTests(unittest.IsolatedAsyncioTestCase):
    setUp = Fixture.setUp
    enter = Fixture.enter
    follow_until = Fixture.follow_until

    async def scenario(self, arrived, reverse=False):
        import asyncio
        from src import quest_party as party
        from wizwalker import XYZ
        session = await self.enter()
        self.q.wizard_name = 'Mason Flame'
        if reverse:
            await party.remember_dungeon_transition(self.q, XYZ(600,700,0))
            session.transition['entry_sent'] = True
            self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
            session.transition['arrivals'][id(self.h)] = await utils._party_area_token(self.h)
        else:
            self.q.zone = 'Dungeon/Room2'
        self.q.quest_party_observed_zone = self.q.zone
        target, reference = (self.q,self.h) if reverse else (self.h,self.q)
        done = asyncio.Event()
        async def request(c, **kwargs):
            self.assertIs(c, target)
            return await utils._request_friend_teleport(c, object())
        async def sdk(c, _profile):
            if arrived:
                target.zone, target.area_id = reference.zone, reference.area_id
            raise ValueError('No child window named MessageBoxModalWindow')
        def status(_h, _q, text):
            if '归队尝试已达上限' in text: done.set()
        async def local(_self, **_kwargs):
            self.assertEqual(self.h.zone, self.q.zone)
            done.set()
            return True
        with patch.object(utils, '_teleport_to_friend', AsyncMock(side_effect=sdk)) as click, \
             patch.object(Quester, 'move_dungeon_member', local):
            calls = AsyncMock(side_effect=request)
            await self.follow_until(done, extra={'teleport_to_friend_from_list':calls}, when_status=status)
        self.assertTrue(all(call.args[0] is target for call in calls.await_args_list))
        self.assertEqual(calls.await_count, 1 if arrived else 2)
        self.assertEqual(click.await_count, calls.await_count)
        self.h.send_key.assert_not_awaited()

    async def test_modal_missing_but_actual_same_instance_arrival_can_finish(self):
        await self.scenario(True)

    async def test_modal_missing_without_arrival_exhausts_two_attempts_without_alias_replay(self):
        await self.scenario(False)

    async def test_quester_missed_entry_never_pulls_entered_hitter_back_outside(self):
        await self.scenario(True,reverse=True)

    async def test_reverse_friend_recovery_uses_same_two_attempt_limit(self):
        await self.scenario(False,reverse=True)

    async def test_old_source_task_or_instance_cannot_drive_reverse_friend_input(self):
        import asyncio
        from src import quest_party as party
        from wizwalker import XYZ
        for change in ('task','area'):
            with self.subTest(change=change):
                self.setUp()
                session = await self.enter()
                await party.remember_dungeon_transition(self.q, XYZ(600,700,0))
                session.transition['entry_sent'] = True
                self.h.zone, self.h.area_id = 'Dungeon/Room2', 201
                session.transition['arrivals'][id(self.h)] = await utils._party_area_token(self.h)
                if change == 'task': self.q.goal_id.return_value = 8
                else: self.q.area_id += 1
                done = asyncio.Event()
                def status(_h,_q,text):
                    if '旧入口上下文' in text: done.set()
                request = AsyncMock()
                await self.follow_until(done, extra={'teleport_to_friend_from_list':request}, when_status=status)
                request.assert_not_awaited()
                self.h.send_key.assert_not_awaited()
                self.q.send_key.assert_not_awaited()


del Fixture

