import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from wizwalker import XYZ, Keycode
from wizwalker.memory import DuelPhase
from src.combat_targeting import TargetingSprintyCombat, UpstreamSprintyCombat


class PartyEntryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hitter = SimpleNamespace(
            title='p2', process_id=2, questing_status=True,
            in_battle=AsyncMock(return_value=False), is_loading=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Room'),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(1, 2, 3))),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = SimpleNamespace(
            title='p1', in_solo_zone=False, in_battle=AsyncMock(return_value=True),
            quest_party_battle_started_at=asyncio.get_running_loop().time() - 1.5,
            zone_name=AsyncMock(return_value='Room'), helper_clients=[],
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(10, 20, 30))))
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        helpers = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                   and n.name in ('clear_post_combat_phase', 'rescue_missing_party_hitters')]
        self.release = Mock()
        self.ns = dict(Client=object, asyncio=asyncio, Keycode=Keycode, XYZ=XYZ,
            time=__import__('time'), logger=Mock(), quest_party_enabled=True,
            original_client_locations={}, walker=SimpleNamespace(clients=[self.quester, self.hitter]),
            current_quest_party=lambda *_: SimpleNamespace(hitter_assignments=[(self.hitter, self.quester)]),
            claim_quest_recovery=lambda *_: True, release_quest_recovery=self.release)
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'XuanShu.py', 'exec'), self.ns)

    async def test_auto_quest_post_combat_uses_a_then_d(self):
        await self.ns['clear_post_combat_phase'](self.hitter)
        self.assertEqual([c.kwargs['key'] for c in self.hitter.send_key.await_args_list], [Keycode.A, Keycode.D])
        self.assertFalse(self.hitter.post_combat_movement_active)

    async def test_recovery_starts_after_one_second_and_returns_from_high_point(self):
        async def arrive(position):
            if position.z == 30:
                self.hitter.in_battle.return_value = True
        self.hitter.teleport.side_effect = arrive
        await self.ns['rescue_missing_party_hitters'](self.quester)
        positions = [c.args[0] for c in self.hitter.teleport.await_args_list]
        self.assertEqual([(p.x, p.y, p.z) for p in positions], [(0, 0, 10000), (10, 20, 30)])
        self.assertFalse(self.hitter.quest_party_battle_rescue_active)
        self.release.assert_called_once_with(self.hitter, 'party_battle')

    async def test_cancelled_movement_returns_from_high_point(self):
        self.hitter.send_key.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.ns['rescue_missing_party_hitters'](self.quester)
        self.assertEqual(self.hitter.teleport.await_args.args[0].z, 30)
        self.assertFalse(self.hitter.quest_party_battle_rescue_active)


class PartyRoundGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_hitter_does_not_execute_priorities(self):
        combat = object.__new__(TargetingSprintyCombat)
        combat.client = SimpleNamespace(
            questing_status=True, quest_party_hitters=[SimpleNamespace(
                client_object=SimpleNamespace(global_id_full=AsyncMock(return_value=2)))],
            in_battle=AsyncMock(return_value=True),
            duel=SimpleNamespace(duel_phase=AsyncMock(side_effect=[DuelPhase.planning, DuelPhase.ended])))
        combat.get_members = AsyncMock(return_value=[SimpleNamespace(owner_id=AsyncMock(return_value=1))])
        with patch.object(UpstreamSprintyCombat, 'handle_round', new_callable=AsyncMock) as execute, patch(
            'src.combat_targeting.asyncio.sleep', new_callable=AsyncMock
        ):
            await combat.handle_round()
        execute.assert_not_awaited()
        combat.client.duel.duel_phase.side_effect = None
        combat.client.duel.duel_phase.return_value = DuelPhase.planning
        combat.get_members.return_value.append(SimpleNamespace(owner_id=AsyncMock(return_value=2)))
        with patch.object(UpstreamSprintyCombat, 'handle_round', new_callable=AsyncMock) as execute:
            await combat.handle_round()
        execute.assert_awaited_once()
