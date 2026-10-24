"""Pending followers must get the same passive dialogue settling as questers."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import questing
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.paths import advance_dialog_path
from src.questing import Quester
from tests import test_dungeon_local_tp as fixtures


REAL_HANDLER = Quester.handle_npc_talking_quests
REAL_SLEEP = asyncio.sleep


class FollowerDialoguePendingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.DungeonFixture.setUp(self)
        self.h.title = 'p3'
        self.h._quest_dialogue_update_pending = True
        self.q._quest_dialogue_update_pending = True  # Must never clear another client.
        self.clock = 0.0
        self.dialogue = False
        self.calls = []
        self.finished = 0
        self.done = asyncio.Event()
        self.until = lambda: bool(self.finished)
        self.on_sleep = lambda delay: None

        async def tick(delay):
            self.clock += delay
            self.on_sleep(delay)
            if self.until() or self.clock >= 65:
                self.done.set()
            await REAL_SLEEP(0)

        self.api = SimpleNamespace(sleep=tick,
            get_running_loop=lambda: SimpleNamespace(time=lambda: self.clock),
            wait_for=asyncio.wait_for, timeout=asyncio.timeout,
            current_task=asyncio.current_task, CancelledError=asyncio.CancelledError,
            TimeoutError=TimeoutError)
        self.stack.enter_context(patch.object(questing, 'asyncio', self.api))
        self.stack.enter_context(patch.object(questing, 'time',
                                             SimpleNamespace(monotonic=lambda: self.clock)))
        self.stack.enter_context(patch.object(Quester, 'read_quest_txt',
            AsyncMock(side_effect=lambda c: 'Follower unchanged quest' if c is self.h else 'Quester task')))
        self.stack.enter_context(patch.object(questing, 'is_visible_by_path',
            AsyncMock(side_effect=lambda c, path: c is self.h and self.dialogue and path == advance_dialog_path)))
        self.close_menu = self.stack.enter_context(patch.object(
            questing, 'close_npc_quest_menu', AsyncMock()))
        self.exit_menus = self.stack.enter_context(patch.object(questing, 'exit_menus', AsyncMock()))
        self.move = self.stack.enter_context(patch.object(Quester, 'move_dungeon_member', AsyncMock()))

        async def handle(reader, client, members, **kwargs):
            self.calls.append((reader.client, client, list(members), kwargs))
            try:
                return await REAL_HANDLER(reader, client, members, **kwargs)
            finally:
                self.finished += 1
        self.stack.enter_context(patch.object(Quester, 'handle_npc_talking_quests', handle))

    async def follow(self):
        await fixtures.FollowerTests.follow_until(self, self.done, primary=True,
                                                  extra=dict(asyncio=self.api))

    def assert_passive_and_local(self):
        self.assertTrue(self.calls, 'The follower must reach the actual dialogue handler')
        for reader, client, members, kwargs in self.calls:
            self.assertIs(reader, self.h)
            self.assertIs(client, self.h)
            self.assertEqual(members, [self.h])
            self.assertEqual(kwargs, dict(direction_search=False))
        self.assertTrue(self.q._quest_dialogue_update_pending)
        self.h.teleport.assert_not_awaited()
        self.h.send_key.assert_not_awaited()
        self.q.teleport.assert_not_awaited()
        self.q.send_key.assert_not_awaited()
        self.move.assert_not_awaited()
        self.friend.assert_not_awaited()

    async def test_closed_dialogue_and_unchanged_own_quest_clears_pending(self):
        self.q.goal_id.return_value = 99  # The two clients need not track the same task.
        await self.follow()
        self.assertFalse(self.h._quest_dialogue_update_pending)
        self.assertGreaterEqual(self.clock, 4.25)
        self.h.goal_id.assert_awaited()
        self.assert_passive_and_local()

    async def test_quester_entry_preparation_does_not_starve_follower_settling(self):
        self.q.quest_party_entry_active = True
        await self.follow()
        self.assertFalse(self.h._quest_dialogue_update_pending)
        self.assertTrue(self.q.quest_party_entry_active)
        self.assert_passive_and_local()

    async def test_visible_dialogue_keeps_pending_until_it_really_closes(self):
        self.dialogue = True
        def close_later(delay):
            if self.clock < 2:
                self.assertTrue(self.h._quest_dialogue_update_pending)
            else:
                self.dialogue = False
        self.on_sleep = close_later
        await self.follow()
        self.assertFalse(self.h._quest_dialogue_update_pending)
        self.assertGreaterEqual(self.clock, 6.25)
        self.assert_passive_and_local()

    async def test_dialogue_that_stays_open_or_incomplete_quest_keeps_pending(self):
        for kind in ('visible', 'incomplete'):
            with self.subTest(kind=kind):
                self.setUp()
                self.dialogue = kind == 'visible'
                if kind == 'incomplete':
                    self.h.goal_id.return_value = 0
                await self.follow()
                self.assertTrue(self.h._quest_dialogue_update_pending)
                self.assert_passive_and_local()

    async def test_cancelled_settling_preserves_pending_and_next_session_resumes(self):
        def cancel(delay):
            if self.calls:
                raise asyncio.CancelledError()
        self.on_sleep = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.follow()
        self.assertTrue(self.h._quest_dialogue_update_pending)
        self.assertFalse(get_client_automation_ownership(self.h).locked)
        self.on_sleep = lambda delay: None
        self.done = asyncio.Event()
        self.until = lambda: self.finished >= 2
        await self.follow()
        self.assertFalse(self.h._quest_dialogue_update_pending)
        self.assert_passive_and_local()

    async def test_following_resumes_after_successful_settling(self):
        await fixtures.DungeonFixture.enter(self)
        self.q.quest_party_observed_zone = self.q.zone
        self.q._quest_dialogue_update_pending = False
        self.until = lambda: False

        async def local():
            self.assertFalse(self.h._quest_dialogue_update_pending)
            self.done.set()
            return False
        self.move.side_effect = local
        await self.follow()
        self.assertEqual(self.finished, 1)
        self.move.assert_awaited()
        self.friend.assert_not_awaited()
        self.h.teleport.assert_not_awaited()
        self.h.send_key.assert_not_awaited()

    async def test_one_followers_dialogue_does_not_stop_another_followers_probe(self):
        self.stack.close()
        from tests import test_per_follower_probe as pair
        pair.PerFollowerProbeTests.setUp(self)
        self.p2._quest_dialogue_update_pending = True
        started = asyncio.Event()

        async def blocked(reader, client, members, **kwargs):
            self.assertIs(client, self.p2)
            started.set()
            await asyncio.Future()
        self.stack.enter_context(patch.object(Quester, 'handle_npc_talking_quests', blocked))
        await pair.PerFollowerProbeTests.run_pair(self)
        self.assertTrue(started.is_set())
        self.assertEqual(self.counts[id(self.p2)], 0)
        self.assertEqual(self.counts[id(self.p3)], 1)
        self.assertTrue(self.p2._quest_dialogue_update_pending)
        self.assertFalse(get_client_automation_ownership(self.p2).locked)

    async def test_busy_follower_defers_without_consuming_or_clearing_pending(self):
        for kind in ('loading', 'battle', 'recovery', 'refill', 'entity_combat',
                     'rescue', 'post_combat', 'safe_return', 'pet', 'entry', 'owner'):
            with self.subTest(kind=kind):
                self.setUp()
                self.until = lambda: self.clock >= 2
                flags = dict(recovery='quest_recovery_owner', refill='refilling_potions',
                    entity_combat='entity_detect_combat_status', rescue='quest_party_battle_rescue_active',
                    post_combat='post_combat_movement_active', safe_return='quest_party_safe_return_active',
                    pet='feeding_pet_status', entry='quest_party_entry_active')
                if kind == 'loading':
                    self.h.is_loading.return_value = True
                elif kind == 'battle':
                    self.h.in_battle.return_value = True
                elif kind in flags:
                    setattr(self.h, flags[kind], True)
                if kind == 'owner':
                    async with automation_owner(self.h, 'unrelated-input'):
                        await self.follow()
                else:
                    await self.follow()
                self.assertEqual(self.calls, [])
                self.assertTrue(self.h._quest_dialogue_update_pending)
                self.close_menu.assert_not_awaited()
                self.h.teleport.assert_not_awaited()
                self.h.send_key.assert_not_awaited()

