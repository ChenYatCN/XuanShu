import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class SoloFollowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.function = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.AsyncFunctionDef)
                            and node.name == '_follow_quester_session')

    async def run_session(self, *, zone='Solo', primary=True, group_zone=None,
                          solo=True, recovery_owner='lemuria_dungeon', navigation_holding=False):
        self.hitter = SimpleNamespace(
            title='p2', questing_status=True,
            quest_party_battle_sync_state='failed',
            is_loading=AsyncMock(return_value=False))
        self.quester = SimpleNamespace(
            title='p1', questing_status=True, in_solo_zone=solo,
            quest_party_observed_zone='Solo',
            quest_party_quest_worker_zone='Solo',
            quest_party_probe_pending=False,
            quest_party_group_dungeon_zone=group_zone,
            quest_dungeon_recovery={'zone': 'Solo'},
            quest_recovery_owner=recovery_owner,
            quest_lemuria_navigation_recovery={'holding': navigation_holding},
            in_battle=AsyncMock(return_value=True),
            is_loading=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value=zone))
        status = Mock()
        clock = Mock()
        clock.time.side_effect = [100.0, 200.0, 300.0]
        reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False))
        namespace = dict(
            Client=object, asyncio=asyncio, logger=Mock(),
            members=None, questing_status=True,
            walker=SimpleNamespace(clients=[self.hitter, self.quester]),
            Quester=Mock(return_value=reader), update_party_status=status,
            remove_party_status=Mock(),
            close_automation_popup=AsyncMock(return_value=False))
        namespace['observe_party_area'] = AsyncMock()
        exec(compile(ast.Module(body=[self.function], type_ignores=[]),
                     'XuanShu.py', 'exec'), namespace)
        ticks = 0

        async def tick(_):
            nonlocal ticks
            ticks += 1
            if ticks > 3:
                self.hitter.questing_status = False

        with patch.object(asyncio, 'sleep', tick), patch.object(
                asyncio, 'get_running_loop', return_value=clock):
            await namespace['_follow_quester_session'](self.hitter, self.quester, primary)
        return [call.args[2] for call in status.call_args_list]

    async def test_solo_battle_waits_even_after_sixty_seconds(self):
        statuses = await self.run_session()
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)
        self.assertIsNone(self.hitter.quest_party_battle_sync_state)
        self.assertTrue(self.quester.in_solo_zone)

    async def test_secondary_hitter_also_waits(self):
        statuses = await self.run_session(primary=False)
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_existing_solo_state_survives_session_restart(self):
        # Each run starts with the persistent completed probe, no local block.
        for _ in range(2):
            statuses = await self.run_session()
            self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_solo_has_priority_over_stale_group_marker(self):
        statuses = await self.run_session(group_zone='Solo')
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_zone_change_releases_solo_wait_and_requests_probe(self):
        statuses = await self.run_session(zone='Outside')
        self.assertNotIn('单人区域，任务端独立执行', statuses)
        self.assertTrue(self.quester.quest_party_probe_pending)
        self.assertEqual(self.quester.quest_party_observed_zone, 'Outside')

    async def test_missing_zone_does_not_clear_completed_solo_probe(self):
        statuses = await self.run_session(zone=None)
        self.assertEqual(statuses[1:], ['等待任务端区域稳定'] * 3)
        self.assertEqual(self.quester.quest_party_observed_zone, 'Solo')
        self.assertFalse(self.quester.quest_party_probe_pending)

    async def test_non_solo_is_not_blocked(self):
        statuses = await self.run_session(solo=False)
        self.assertNotIn('单人区域，任务端独立执行', statuses)

    async def test_sacred_yarn_recovery_pauses_assigned_followers(self):
        statuses = await self.run_session(solo=False, recovery_owner='sacred_yarn')
        self.assertEqual(statuses[1:], ['等待 SacredYarnTemple 区域切换'] * 3)

    async def test_solo_policy_keeps_priority_during_sacred_yarn_recovery(self):
        statuses = await self.run_session(recovery_owner='sacred_yarn')
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_tamarin_house_flow_pauses_followers(self):
        statuses = await self.run_session(solo=False, recovery_owner='tamarin_house')
        self.assertEqual(statuses[1:], ['等待 TamarinHouse 两段 TP'] * 3)

    async def test_solo_policy_keeps_priority_during_tamarin_house_flow(self):
        statuses = await self.run_session(recovery_owner='tamarin_house')
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_bumbles_recovery_pauses_followers(self):
        statuses = await self.run_session(solo=False, recovery_owner='bumbles_mind')
        self.assertEqual(statuses[1:], ['等待 BumblesMind 任务端战斗恢复'] * 3)

    async def test_solo_keeps_priority_during_bumbles_recovery(self):
        statuses = await self.run_session(recovery_owner='bumbles_mind')
        self.assertEqual(statuses[1:], ['单人区域，任务端独立执行'] * 3)

    async def test_navigation_recovery_pauses_follow_before_reading_new_zone(self):
        statuses = await self.run_session(solo=False, recovery_owner='lemuria_navigation', zone='Hub')
        self.assertEqual(statuses[1:], ['等待 Lemuria 任务助手导航恢复'] * 3)
        self.quester.zone_name.assert_not_awaited()
        self.assertFalse(self.quester.quest_party_probe_pending)

    async def test_failed_navigation_wait_also_pauses_follow_without_lock(self):
        statuses = await self.run_session(solo=False, recovery_owner=None, navigation_holding=True, zone='Hub')
        self.assertEqual(statuses[1:], ['等待 Lemuria 任务助手导航恢复'] * 3)
        self.quester.zone_name.assert_not_awaited()

    def test_recovery_marker_alone_does_not_enable_battle_sync(self):
        assignment = next(node for node in ast.walk(self.function)
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name)
                                  and target.id == 'in_confirmed_dungeon'
                                  for target in node.targets))
        for group_ready in (False, True):
            with self.subTest(group_ready=group_ready):
                namespace = dict(group_dungeon_ready=group_ready,
                                 dungeon_state={'zone': 'Solo'}, quester_zone='Solo')
                exec(compile(ast.Module(body=[assignment], type_ignores=[]),
                             'XuanShu.py', 'exec'), namespace)
                self.assertEqual(namespace['in_confirmed_dungeon'], group_ready)


if __name__ == '__main__':
    unittest.main()
