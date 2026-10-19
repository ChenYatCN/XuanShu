"""Run the actual continuous follower after instance-arrival verification."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.questing import Quester
from src import quest_party as party
from tests.test_dungeon_local_tp import client, REAL_SLEEP
from tests.test_full_chain_415_restore import entry


class SameZonePositionalFollowTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, gap=5000, later_move=False, pause=None, cross_zone=False,
                       landing_moves=True, source_session=False):
        q, h = client(1, XYZ(10, 20, 0)), client(2, XYZ(10, 20, 0))
        q.quest_party_hitters = [h]
        q.zone = h.zone = 'Test/Area'
        q.quest_party_observed_zone = q.zone
        q.position = XYZ(gap, 0, 0)
        h.position = XYZ(0, 0, 0)
        h.quest_party_quester = q
        if cross_zone:
            h.zone = 'Test/OtherArea'
        if source_session:
            session = await party.begin_dungeon_entry(q, [q, h], evidence=True)
            self.assertIsNotNone(session)
            self.assertFalse(session.confirmed)
            self.assertIsNone(session.entry_wait)
        self.moves, self.friend_requests = [], []
        clock, iteration = 0.0, 0
        flags = {'entry': pause == 'entry'}
        release, ready = asyncio.Event(), asyncio.Event()
        if pause in ('loading', 'battle'):
            getattr(h, 'is_loading' if pause == 'loading' else 'in_battle').return_value = True
        if pause == 'post-combat':
            h.post_combat_movement_active = True

        async def cleanup_owner():
            async with automation_owner(h, 'friend-ui-cleanup'):
                ready.set()
                await release.wait()
        holder = asyncio.create_task(cleanup_owner()) if pause == 'cleanup' else None
        if holder:
            await ready.wait()

        async def tick(delay):
            nonlocal clock, iteration
            clock += delay
            if delay == .5:
                iteration += 1
                if iteration == 3:
                    if later_move:
                        q.position = XYZ(5000, 0, 0)
                    h.is_loading.return_value = h.in_battle.return_value = False
                    h.post_combat_movement_active = False
                    flags['entry'] = False
                    release.set()
                if iteration == 6 and later_move:
                    q.position = XYZ(10000, 0, 0)
                if iteration >= 10:
                    h.questing_status = False
            await REAL_SLEEP(0)

        async def teleport(point):
            owner = get_client_automation_ownership(h)
            self.moves.append((iteration, tuple(point), owner.owner_label))
            if landing_moves:
                h.position = XYZ(*point)
        h.teleport.side_effect = teleport

        async def friend(moving, **_kwargs):
            self.friend_requests.append((iteration, moving.zone))
            moving.zone, moving.area_id, moving.position = q.zone, q.area_id, XYZ(*q.position)

        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[q, h]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False, buy_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False), is_friend_teleport_error=AsyncMock(return_value=False),
            dungeon_entry_waiting=AsyncMock(side_effect=lambda _: flags['entry']),
            teleport_to_friend_from_list=friend,
            clients_share_live_area=AsyncMock(side_effect=lambda a, b: a.zone == b.zone),
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=lambda: SimpleNamespace(time=lambda: clock),
                wait_for=asyncio.wait_for, CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError,
                timeout=asyncio.timeout, current_task=asyncio.current_task)),
            members=[q, h], change_party_equipment=AsyncMock(return_value=True),
            prepare_quester_name=AsyncMock(), remove_party_status=Mock(),
            restart_quest_worker_after_probe=Mock(), update_party_status=Mock())
        task = None
        try:
            with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)), \
                 patch.object(Quester, 'get_truncated_quest_objectives', AsyncMock(return_value='Use task target')):
                task = asyncio.create_task(follow(h, q, True))
                await asyncio.wait_for(task, 3)
        finally:
            release.set()
            if task and not task.done():
                task.cancel()
            if holder:
                await asyncio.gather(holder, return_exceptions=True)
            if task:
                await asyncio.gather(task, return_exceptions=True)

    async def test_b1_same_instance_far_uses_local_follow_without_friend_probe(self):
        await self.scenario()
        self.assertEqual(len(self.moves), 1)
        self.assertEqual(self.moves[0][1], (5000, 0, 0))
        self.assertIsNotNone(self.moves[0][2], 'Local follow must own client input')
        self.assertEqual(self.friend_requests, [])

    async def test_b2_close_follower_does_not_teleport_repeatedly(self):
        await self.scenario(gap=10)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.friend_requests, [])

    async def test_b3_verified_near_then_quester_moves_far_can_follow_again(self):
        await self.scenario(gap=10, later_move=True)
        self.assertEqual([move[1] for move in self.moves], [(5000, 0, 0), (10000, 0, 0)])
        self.assertEqual(self.friend_requests, [])

    async def test_b4_loading_and_battle_temporarily_pause_then_resume(self):
        for pause in ('loading', 'battle'):
            with self.subTest(pause=pause):
                await self.scenario(pause=pause)
                self.assertEqual(len(self.moves), 1)
                self.assertGreaterEqual(self.moves[0][0], 3)
                self.assertEqual(self.friend_requests, [])

    async def test_b5_cross_zone_stays_with_existing_friend_follow(self):
        await self.scenario(cross_zone=True)
        self.assertEqual(len(self.friend_requests), 1)
        self.assertEqual(self.moves, [])

    async def test_b6_entry_wait_gate_pauses_ordinary_position_follow(self):
        await self.scenario(pause='entry')
        self.assertEqual(len(self.moves), 1)
        self.assertGreaterEqual(self.moves[0][0], 3)

    async def test_b7_friend_cleanup_owner_is_respected_then_follow_resumes(self):
        await self.scenario(pause='cleanup')
        self.assertEqual(len(self.moves), 1)
        self.assertGreaterEqual(self.moves[0][0], 3)
        self.assertNotEqual(self.moves[0][2], 'friend-ui-cleanup')

    async def test_b8_post_combat_coordinator_pauses_ordinary_follow(self):
        await self.scenario(pause='post-combat')
        self.assertEqual(len(self.moves), 1)
        self.assertGreaterEqual(self.moves[0][0], 3)

    async def test_reported_437u_is_inside_the_existing_follow_threshold(self):
        await self.scenario(gap=437)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.friend_requests, [])

    async def test_no_actual_landing_reuses_existing_backoff_without_tp_spam(self):
        await self.scenario(landing_moves=False)
        self.assertLessEqual(len(self.moves), 3)
        self.assertGreater(len(self.moves), 1, 'A temporary failed landing must remain retryable')

    async def test_unconfirmed_source_session_without_wait_keeps_existing_local_follow(self):
        await self.scenario(source_session=True)
        self.assertEqual([move[1] for move in self.moves], [(5000, 0, 0)])
        self.assertEqual(self.friend_requests, [])
