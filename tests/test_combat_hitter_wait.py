import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker.memory import DuelPhase
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
