import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker.memory import DuelPhase
from wizwalker import MemoryInvalidated
from src.combat_targeting import TargetingSprintyCombat, UpstreamSprintyCombat


@asynccontextmanager
async def round_owner(*args):
    yield


class CombatHitterWaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hitter = SimpleNamespace(client_object=SimpleNamespace(
            global_id_full=AsyncMock(return_value=42)))
        self.client = SimpleNamespace(
            questing_status=True, in_solo_zone=False, quest_party_hitters=[hitter],
            in_battle=AsyncMock(return_value=True),
            duel=SimpleNamespace(duel_phase=AsyncMock(return_value=DuelPhase.planning)),
        )
        self.combat = object.__new__(TargetingSprintyCombat)
        self.combat.client = self.client
        self.combat.get_members = AsyncMock(return_value=[])
        self.round = AsyncMock()
        for patcher in (
            patch('src.combat_targeting.automation_owner', round_owner),
            patch.object(UpstreamSprintyCombat, 'handle_round', self.round),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_solo_quester_plays_without_unreachable_hitter(self):
        self.client.in_solo_zone = True
        await self.combat.handle_round()
        self.round.assert_awaited_once()
        self.combat.get_members.assert_not_awaited()

    async def test_group_quester_waits_until_assigned_hitter_arrives(self):
        self.combat.get_members.side_effect = [
            [], [SimpleNamespace(owner_id=AsyncMock(return_value=42))],
        ]
        async def wait(_):
            self.round.assert_not_awaited()
        with patch('src.combat_targeting.asyncio.sleep', new=AsyncMock(side_effect=wait)) as sleep:
            await self.combat.handle_round()
        sleep.assert_awaited_once_with(0.25)
        self.round.assert_awaited_once()

    async def test_solo_detection_during_wait_releases_combat(self):
        async def wait(_):
            self.client.in_solo_zone = True
        with patch('src.combat_targeting.asyncio.sleep', new=AsyncMock(side_effect=wait)):
            await self.combat.handle_round()
        self.round.assert_awaited_once()

    async def test_ended_planning_does_not_cast_without_hitter(self):
        self.client.duel.duel_phase.return_value = DuelPhase.execution
        await self.combat.handle_round()
        self.round.assert_not_awaited()

    async def test_invalid_roster_is_refetched_before_any_input(self):
        stale = SimpleNamespace(owner_id=AsyncMock(side_effect=MemoryInvalidated('old participant')))
        fresh = SimpleNamespace(owner_id=AsyncMock(return_value=42))
        self.combat.get_members.side_effect = [[stale], [fresh]]
        with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            await self.combat.handle_round()
        self.assertEqual(self.combat.get_members.await_count, 2)
        self.round.assert_awaited_once()

    async def test_invalid_reads_are_bounded_and_do_not_cast(self):
        self.combat.get_members.side_effect = MemoryInvalidated('unavailable')
        with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            await self.combat.handle_round()
        self.assertEqual(self.combat.get_members.await_count, 3)
        self.round.assert_not_awaited()

    async def test_input_started_never_replays_round(self):
        self.client.in_solo_zone = True
        async def uncertain_cast():
            self.combat._round_input_started = True
            raise MemoryInvalidated('after click')
        self.round.side_effect = uncertain_cast
        await self.combat.handle_round()
        self.round.assert_awaited_once()

    async def test_safe_retry_restores_relative_round_state(self):
        self.client.in_solo_zone = True
        self.combat.rel_round_offset = 10
        calls = 0
        async def evaluate():
            nonlocal calls
            calls += 1
            self.assertEqual(self.combat.rel_round_offset, 10)
            self.combat.rel_round_offset -= 1
            if calls == 1:
                raise MemoryInvalidated('before input')
        self.round.side_effect = evaluate
        with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            await self.combat.handle_round()
        self.assertEqual(calls, 2)
        self.assertEqual(self.combat.rel_round_offset, 9)

    async def test_member_refresh_never_substitutes_another_owner(self):
        self.combat.get_members.return_value = [SimpleNamespace(owner_id=AsyncMock(return_value=7))]
        with self.assertRaises(MemoryInvalidated):
            await self.combat.refresh_member(42)
