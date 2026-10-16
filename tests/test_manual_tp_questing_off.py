"""Manual quest TP bypasses only the automatic-quest switch, throughout fallback."""
import ast
import asyncio
import statistics
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from loguru import logger
from wizwalker import XYZ
from src import teleport_math as movement
from src.automation_ownership import get_client_automation_ownership
import tests.test_walk_navmap_fallback as fixtures


class ManualTPQuestingOffTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.WalkNavmapFallbackTests.setUp
    sleep = fixtures.WalkNavmapFallbackTests.sleep
    solve = fixtures.WalkNavmapFallbackTests.solve
    plan = fixtures.WalkNavmapFallbackTests.plan
    teleport = fixtures.WalkNavmapFallbackTests.teleport
    goto = fixtures.WalkNavmapFallbackTests.goto

    def manual_entry(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'navmap_teleport')
        namespace = dict(asyncio=asyncio, statistics=statistics, logger=logger,
            navmap_tp=movement.navmap_tp, _movement_position=movement._movement_position,
            get_client_automation_ownership=get_client_automation_ownership)
        exec(compile('from __future__ import annotations\n' + ast.unparse(function),
                     'XuanShu.py', 'exec'), namespace)
        return namespace['navmap_teleport']

    async def test_actual_manual_entry_teleports_with_auto_quest_off(self):
        self.client.questing_status = False
        self.reject_first = False
        await self.manual_entry()(self.client, [])
        self.assertEqual(self.events, [('tp', self.target)])
        self.assertLessEqual(movement.calc_Distance(self.position, self.target), 1)

    async def test_manual_off_runs_nav_candidate_and_actual_final_walk(self):
        self.client.questing_status = False
        self.avoid = []
        midpoint = XYZ((self.safe.x + self.target.x) / 2, (self.safe.y + self.target.y) / 2,
                       self.safe.z + (self.safe.z - self.target.z) / 2)
        with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(
                get_file=AsyncMock(return_value=b'nav')))) as wad, \
             patch('src.teleport_math.parse_nav_data', return_value=([self.safe], [])):
            await self.manual_entry()(self.client, [])
        wad.assert_awaited_once()
        teleports = [point for kind, point in self.events if kind == 'tp']
        self.assertEqual(len(teleports), 2)
        self.assertLessEqual(movement.calc_Distance(teleports[1], midpoint), 1)
        self.assertTrue(any(kind == 'walk' for kind, _ in self.events))
        self.assertLessEqual(movement.calc_Distance(self.position, self.target), movement._WALK_GAP_MIN)

    async def test_manual_off_runs_existing_spiral_when_nav_unavailable(self):
        self.client.questing_status = False
        self.client.teleport = AsyncMock()  # Every attempted landing bounces.
        with patch('src.teleport_math.load_wad', AsyncMock(side_effect=RuntimeError('no nav'))):
            await self.manual_entry()(self.client, [])
        self.assertEqual(self.client.teleport.await_count, 6)  # direct + existing five offsets

    async def test_automatic_off_still_blocks_before_any_input_and_logs_reason(self):
        self.client.questing_status = False
        await movement.navmap_tp(self.client, self.target)
        self.assertFalse(self.events)
        self.assertTrue(any('quest movement stopped' in line for line in self.messages))

    async def test_running_auto_tp_stops_after_switch_off_before_fallback(self):
        self.client.questing_status = True
        original = self.client.teleport
        async def teleport(dest):
            await original(dest)
            self.client.questing_status = False
        self.client.teleport = teleport
        with patch('src.teleport_math.load_wad', AsyncMock()) as wad:
            await movement.navmap_tp(self.client, self.target)
        self.assertEqual(self.events, [('tp', self.target)])
        wad.assert_not_awaited()
        self.assertTrue(any('quest movement stopped' in line for line in self.messages))

    async def test_running_auto_walk_stops_after_switch_off(self):
        self.client.questing_status = True
        self.position = self.safe
        self.avoid = []
        self.on_walk = lambda: setattr(self.client, 'questing_status', False)
        self.assertFalse(await movement._walk_remaining_to_target(
            self.client, self.target, object(), self.zone, 55))
        self.assertEqual(sum(kind == 'walk' for kind, _ in self.events), 1)

    async def test_manual_off_still_blocks_loading_battle_and_dialogue_before_input(self):
        self.client.questing_status = False
        for cause in ('loading', 'battle', 'forced_dialogue', 'dialogue'):
            with self.subTest(cause=cause):
                self.events.clear()
                setattr(self, cause, True)
                await self.manual_entry()(self.client, [])
                self.assertFalse(self.events)
                setattr(self, cause, False)

    async def test_manual_off_state_changes_stop_nav_search_before_next_tp(self):
        self.client.questing_status = False
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle', 'dialogue', 'control'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.messages.clear()
                self.position = XYZ(1000.157, -16635.018, -1176.275)
                self.quest_target = self.target
                self.zone = 'Polaris/PL_Z04_UrvilleStation'
                self.quest_id, self.goal_id = 12, 34
                self.loading = self.battle = self.forced_dialogue = False
                changed_control = False
                actual = movement._movement_control_changed
                async def control(client, expected):
                    return changed_control or await actual(client, expected)
                async def read(_):
                    nonlocal changed_control
                    if cause == 'quest':
                        self.quest_id += 1
                    elif cause == 'goal':
                        self.goal_id += 1
                    elif cause == 'target':
                        self.quest_target = XYZ(2500, -16000, -1000)
                    elif cause == 'zone':
                        self.zone = 'Other/Zone'
                    elif cause == 'dialogue':
                        self.forced_dialogue = True
                    elif cause == 'control':
                        changed_control = True
                    else:
                        setattr(self, cause, True)
                    return b'nav'
                with patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=read))), \
                     patch('src.teleport_math.parse_nav_data', return_value=([self.safe], [])), \
                     patch('src.teleport_math._movement_control_changed', side_effect=control):
                    await self.manual_entry()(self.client, [])
                self.assertEqual(self.events, [('tp', self.target)])
                self.assertTrue(any('stopped:' in line for line in self.messages))

    async def test_manual_off_walk_state_changes_still_stop_after_one_step(self):
        self.client.questing_status = False
        self.avoid = []
        for cause in ('quest', 'goal', 'target', 'zone', 'loading', 'battle', 'dialogue', 'control'):
            with self.subTest(cause=cause):
                self.events.clear()
                self.position = self.safe
                self.quest_target = self.target
                self.zone = 'Polaris/PL_Z04_UrvilleStation'
                self.quest_id, self.goal_id = 12, 34
                self.loading = self.battle = self.forced_dialogue = False
                changed_control = False
                actual = movement._movement_control_changed
                async def control(client, expected):
                    return changed_control or await actual(client, expected)
                def changed():
                    nonlocal changed_control
                    if cause == 'quest':
                        self.quest_id += 1
                    elif cause == 'goal':
                        self.goal_id += 1
                    elif cause == 'target':
                        self.quest_target = XYZ(2500, -16000, -1000)
                    elif cause == 'zone':
                        self.zone = 'Other/Zone'
                    elif cause == 'dialogue':
                        self.forced_dialogue = True
                    elif cause == 'control':
                        changed_control = True
                    else:
                        setattr(self, cause, True)
                self.on_walk = changed
                with patch('src.teleport_math._movement_control_changed', side_effect=control):
                    self.assertFalse(await movement._walk_remaining_to_target(self.client, self.target,
                        object(), self.zone, 55, require_questing=False))
                self.assertEqual(sum(kind == 'walk' for kind, _ in self.events), 1)

    async def test_manual_switch_is_preserved_in_recursive_interaction_recheck(self):
        self.client.questing_status = False
        task = await movement._movement_task_snapshot(self.client)
        with patch('src.teleport_math._movement_interaction_handoff', AsyncMock(return_value=True)):
            reason = await movement._movement_stop_reason(self.client, self.target, self.zone, task,
                                                          require_questing=False)
        self.assertEqual(reason, 'target interaction ready; returning to existing handler')


if __name__ == '__main__':
    unittest.main()
