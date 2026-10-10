"""Replay small first displacement, then compare real native retry effects."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src import questing as q, teleport_math as tm
from src.automation_ownership import get_client_automation_ownership
from tests import test_quest_final_approach as approach_fixture

REAL_SLEEP = asyncio.sleep


class ManualAutoEffectTests(unittest.IsolatedAsyncioTestCase):
    land = approach_fixture.QuestFinalApproachTests.land

    def setUp(self):
        approach_fixture.QuestFinalApproachTests.setUp(self)
        self.now = 0.0
        self.origin = XYZ(194.50379943847656, -631.9130249023438, -205.89109802246094)
        self.rounded = XYZ(192, -628, -204)
        self.target = XYZ(877.8347778320312, -1832.6519775390625, -205.89100646972656)
        self.position = self.origin
        self.stack.enter_context(patch.object(q, 'time', SimpleNamespace(monotonic=lambda: self.now)))
        self.inputs = []
        async def teleport(point):
            self.inputs.append(('tp', point.x, point.y, point.z))
            self.position = self.rounded if q.calc_Distance(point, self.target) < .01 else point
        async def walk(x, y):
            self.inputs.append(('walk', x, y))
            self.position = XYZ(x, y, self.target.z)
        self.client.teleport.side_effect = teleport
        self.client.goto.side_effect = walk
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'navmap_teleport')
        env = dict(q.__dict__, wizwalker=__import__('wizwalker'))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), env)
        self.manual = env['navmap_teleport']

    async def test_automatic_second_attempt_matches_manual_tp_inputs_and_actual_arrival(self):
        self.assertGreater(q.calc_Distance(self.origin, self.rounded), 5)
        self.assertFalse(await self.quester.teleport_to_quest_target(self.client, self.target))
        self.assertAlmostEqual(q.calc_Distance(self.position, self.target), 1386.2, delta=.1)
        self.wad.assert_not_awaited()  # Native direct-feedback exit; fallback never ran.
        self.inputs.clear()
        self.now = 6
        self.assertTrue(await self.quester.teleport_to_quest_target(self.client, self.target))
        automatic = list(self.inputs)
        self.assertLess(q.calc_Distance(self.position, self.target), .01)
        self.assertTrue(self.client.questing_status)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.position = self.rounded  # Same client, target and starting state as retry.
        self.inputs.clear()
        await self.manual(self.client, [])
        self.assertEqual(automatic, self.inputs)
        self.assertEqual([step[0] for step in automatic], ['tp', 'tp', 'walk'])
        self.assertLess(q.calc_Distance(self.position, self.target), .01)

    def prepare_worker(self, change_target=False):
        self.client.use_potions = self.client.auto_pet_status = self.client.entity_detect_combat_status = False
        for name in ('is_free',):
            self.stack.enter_context(patch.object(q, name, AsyncMock(return_value=True)))
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open',
                     'is_potion_needed', 'is_visible_by_path'):
            self.stack.enter_context(patch.object(q, name, AsyncMock(return_value=False)))
        self.stack.enter_context(patch.object(q, 'get_quest_name', AsyncMock(side_effect=lambda _: self.objective)))
        self.stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.turns = 0
        async def tick(seconds):
            self.now += seconds
            if seconds == 1:
                self.turns += 1
                self.assertLess(self.turns, 25, 'worker never completed an automatic retry')
                if q.calc_Distance(self.position, self.target) < .01:
                    self.client.questing_status = False  # Test ends only after actual arrival.
                elif change_target and self.quester._quest_movement_waiting:
                    self.target = XYZ(1500, -2200, -205.891)
                    self.goal = 8
            await REAL_SLEEP(0)
        self.stack.enter_context(patch.object(asyncio, 'sleep', tick))

    async def test_real_auto_worker_retries_and_reaches_without_manual_tp_or_restart(self):
        self.prepare_worker()
        await self.quester.auto_quest(False, False)
        self.assertLess(q.calc_Distance(self.position, self.target), .01)
        self.assertEqual(self.navmap.await_count, 2)
        self.assertEqual([s[0] for s in self.inputs], ['tp', 'tp', 'tp', 'walk'])
        self.client.send_key.assert_not_awaited()

    async def test_real_auto_worker_rereads_goal_and_target_after_failure(self):
        self.prepare_worker(change_target=True)
        await self.quester.auto_quest(False, False)
        self.assertEqual(self.goal, 8)
        self.assertEqual(self.navmap.await_count, 2)
        self.assertIs(self.navmap.await_args_list[-1].args[1], self.target)
        self.assertLess(q.calc_Distance(self.position, self.target), .01)

    async def test_stopping_auto_during_native_walk_cancels_only_that_action(self):
        await self.quester.teleport_to_quest_target(self.client, self.target)
        self.now = 6
        started, drained = asyncio.Event(), asyncio.Event()
        async def walk(*args):
            started.set()
            try: await asyncio.Future()
            finally: drained.set()
        self.client.goto.side_effect = walk
        task = asyncio.create_task(self.quester.teleport_to_quest_target(self.client, self.target))
        await asyncio.wait_for(started.wait(), 1)
        self.client.questing_status = False
        self.assertFalse(await asyncio.wait_for(task, 1))
        self.assertTrue(drained.is_set())
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertFalse(self.client.questing_status)

    async def test_two_shared_groups_retry_native_paths_with_separate_current_targets(self):
        groups = []
        positions, rounded, targets = {}, {}, {}
        for offset, titles in ((0, ('p1', 'p2')), (10000, ('p3', 'p4'))):
            target = XYZ(self.target.x + offset, self.target.y, self.target.z)
            members = []
            for title in titles:
                member = SimpleNamespace(title=title, questing_status=True,
                    refilling_potions=False, quest_recovery_owner=None,
                    quest_party_status_session=object(),
                    zone_name=AsyncMock(return_value='Dungeon/' + titles[0]),
                    quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
                    is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
                    quest_position=SimpleNamespace(position=AsyncMock(return_value=target)),
                    body=SimpleNamespace(position=AsyncMock()), teleport=AsyncMock(), goto=AsyncMock())
                key = id(member)
                positions[key] = XYZ(self.origin.x + offset, self.origin.y, self.origin.z)
                rounded[key] = XYZ(self.rounded.x + offset, self.rounded.y, self.rounded.z)
                targets[key] = target
                member.body.position.side_effect = lambda key=key: positions[key]
                async def tp(point, key=key):
                    positions[key] = rounded[key] if q.calc_Distance(point, targets[key]) < .01 else point
                async def goto(x, y, key=key):
                    positions[key] = XYZ(x, y, targets[key].z)
                member.teleport.side_effect, member.goto.side_effect = tp, goto
                members.append(member)
            leader, hitter = members
            leader.quest_party_hitters = [hitter]
            hitter.quest_party_quester = leader
            leader.quest_party_group_dungeon_zone = 'Dungeon/' + titles[0]
            runner = q.Quester(leader, [leader], None)
            runner.quest_interaction_ready = AsyncMock(return_value=False)
            runner._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Go to Door'))
            runner._note_quest_x_blocked = AsyncMock()
            runner._note_quest_x_lock_wait = AsyncMock()
            groups.append((runner, target, members))
        with (patch.object(q, 'is_free', AsyncMock(return_value=True)),
              patch.object(q, 'clients_share_live_area', AsyncMock(return_value=True))):
            first = await asyncio.gather(*[r.teleport_party_to_quest_target(t) for r, t, _ in groups])
            self.assertEqual(first, [False, False])
            self.now = 6
            second = await asyncio.gather(*[r.teleport_party_to_quest_target(t) for r, t, _ in groups])
            self.assertEqual(second, [True, True])
        for runner, target, members in groups:
            self.assertEqual(set(runner._quest_movement_waiting), {id(m) for m in members})
            for member in members:
                self.assertLess(q.calc_Distance(positions[id(member)], target), .01)
                self.assertTrue(member.questing_status)
                self.assertFalse(member.quest_party_target_sync_active)
                self.assertFalse(get_client_automation_ownership(member).locked)
                member.teleport.assert_any_await(target)
            members[1].quest_position.position.assert_not_awaited()
