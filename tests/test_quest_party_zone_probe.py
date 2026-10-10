"""A transition probe owns the quester's pause through equipment completion."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src.questing import Quester
from src.automation_ownership import automation_owner


class ZoneProbeWaitTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.complete = next(n for n in ast.walk(tree)
                            if isinstance(n, ast.AsyncFunctionDef)
                            and n.name == 'complete_zone_probe')
        cls.change_equipment = next(n for n in ast.walk(tree)
                                    if isinstance(n, ast.AsyncFunctionDef)
                                    and n.name == 'change_party_equipment')

    def setUp(self):
        self.now = 0.0
        self.zone = 'World/New'
        self.quest = 42
        self.goal = 7
        self.session = object()
        self.hitter = SimpleNamespace(title='p2', questing_status=True,
                                      is_loading=AsyncMock(return_value=False),
                                      quest_party_status_session=self.session)
        self.client = SimpleNamespace(title='p1', questing_status=True,
            in_solo_zone=False, quest_party_solo_gear_active=False,
            quest_party_hitters=[self.hitter], quest_party_probe_pending=True,
            quest_party_group_dungeon_zone=None, quest_party_dungeon_interaction=None,
            quest_party_quest_worker_zone='World/Old', quest_party_probe_wait=None,
            quest_id=AsyncMock(side_effect=lambda: self.quest),
            goal_id=AsyncMock(side_effect=lambda: self.goal),
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            send_key=AsyncMock())
        self.client.quest_position = SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0)))
        self.hitter.quest_party_quester = self.client
        self.quester = Quester(self.client, [self.client], None)
        self.equipment = AsyncMock(return_value=True)
        self.restart = Mock()
        self.roster = [self.client, self.hitter]
        self.namespace = dict(asyncio=asyncio, automation_owner=automation_owner, is_probe_hitter=True,
            members=None, questing_status=True, hitter=self.hitter, quester=self.client,
            walker=SimpleNamespace(clients=self.roster),
            gear_switching_in_solo_zones=True, change_party_equipment=self.equipment,
            restart_quest_worker_after_probe=self.restart, logger=Mock(),
            update_party_status=Mock(), clients_share_live_area=AsyncMock(return_value=True))
        exec(compile(ast.Module(body=[self.complete], type_ignores=[]),
                     'XuanShu.py', 'exec'), self.namespace)
        patcher = patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def context(self):
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        return (self.zone, (self.quest, self.goal), self.client.quest_party_probe_wait,
                self.session, True)

    async def finish(self, solo, context):
        await self.namespace['complete_zone_probe'](solo, context)

    async def blocked_equipment(self, solo=True):
        started, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def equip(*args):
            started.set()
            try:
                await release.wait()
                return True
            finally:
                drained.set()
        self.equipment.side_effect = equip
        context = await self.context()
        task = asyncio.create_task(self.finish(solo, context))
        await asyncio.wait_for(started.wait(), 1)
        self.addAsyncCleanup(self.drain, task)
        return task, release, drained, context

    async def drain(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_worker_detects_transition_before_follower_poll(self):
        self.client.quest_party_probe_pending = False
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_probe_wait['zone'], self.zone)

    async def test_completed_ordinary_area_never_waits_for_busy_hitter(self):
        self.client.quest_party_probe_pending = False
        self.client.quest_party_quest_worker_zone = self.zone
        self.hitter.questing_status = False
        self.assertFalse(await self.quester._quest_party_probe_blocks_movement())
        self.assertIsNone(self.client.quest_party_probe_wait)

    async def test_solo_equipment_finishes_before_worker_can_advance(self):
        task, release, drained, _ = await self.blocked_equipment()
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertFalse(self.client.in_solo_zone)
        self.restart.assert_not_called()
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester._maybe_recover_mainline = AsyncMock()
        self.client.mainline_finder_enabled = True
        self.quester.teleport_to_quest_target = AsyncMock()
        self.quester._quest_local_interaction_ready = AsyncMock(return_value=True)
        with patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)):
            await self.quester.auto_quest_solo()
        self.quester.teleport_to_quest_target.assert_not_awaited()
        self.quester._maybe_recover_mainline.assert_not_awaited()
        self.quester._quest_local_interaction_ready.assert_not_awaited()
        # The overall probe deadline must not release a still-running switch.
        self.now = 46.0
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        release.set()
        await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())
        self.assertTrue(self.client.in_solo_zone)
        self.assertTrue(self.client.quest_party_solo_gear_active)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.assertIsNone(self.client.quest_party_probe_wait)
        self.restart.assert_called_once_with(self.client)
        self.assertFalse(await self.quester._quest_party_probe_blocks_movement())

    async def test_non_solo_result_releases_without_second_equipment(self):
        await self.finish(False, await self.context())
        self.equipment.assert_not_awaited()
        self.restart.assert_not_called()
        self.assertFalse(self.client.in_solo_zone)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.assertFalse(await self.quester._quest_party_probe_blocks_movement())

    async def test_leaving_solo_preserves_existing_first_equipment_restore(self):
        self.client.in_solo_zone = self.client.quest_party_solo_gear_active = True
        await self.finish(False, await self.context())
        self.equipment.assert_awaited_once_with(self.client, 0)
        self.assertFalse(self.client.quest_party_solo_gear_active)

    async def test_disabled_gear_switch_keeps_existing_solo_policy(self):
        self.namespace['gear_switching_in_solo_zones'] = False
        await self.finish(True, await self.context())
        self.equipment.assert_not_awaited()
        self.assertTrue(self.client.in_solo_zone)
        self.assertFalse(self.client.quest_party_probe_pending)

    async def test_timeout_releases_once_without_inventing_a_solo_result(self):
        context = await self.context()
        self.now = 45.0
        with patch('src.questing.logger.warning') as warning:
            self.assertFalse(await self.quester._quest_party_probe_blocks_movement())
            self.assertFalse(await self.quester._quest_party_probe_blocks_movement())
            warning.assert_called_once()
        await self.finish(True, context)  # A late busy response is obsolete.
        self.equipment.assert_not_awaited()
        self.assertFalse(self.client.in_solo_zone)

    async def test_retry_samples_do_not_restart_the_wait_deadline(self):
        context = await self.context()
        self.now = 44.0
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        self.assertIs(self.client.quest_party_probe_wait, context[2])
        self.assertEqual(context[2]['since'], 0)

    async def test_old_result_cannot_clear_a_new_zone_wait(self):
        old = await self.context()
        self.zone = 'World/Next'
        new = await self.context()
        await self.finish(True, old)
        self.assertIs(self.client.quest_party_probe_wait, new[2])
        self.assertTrue(self.client.quest_party_probe_pending)
        self.equipment.assert_not_awaited()
        await self.finish(False, new)
        self.assertFalse(self.client.quest_party_probe_pending)

    async def test_task_replacement_discards_old_result(self):
        old = await self.context()
        self.quest = 43
        await self.finish(True, old)
        self.assertFalse(await self.quester._quest_party_probe_blocks_movement())
        self.equipment.assert_not_awaited()
        self.assertFalse(self.client.in_solo_zone)

    async def test_returning_to_same_zone_rejects_previous_visit_result(self):
        old = await self.context()
        self.zone = 'World/Next'
        await self.context()
        self.zone = 'World/New'
        new = await self.context()
        await self.finish(True, old)
        self.assertIs(self.client.quest_party_probe_wait, new[2])
        self.assertTrue(self.client.quest_party_probe_pending)
        self.equipment.assert_not_awaited()

    async def test_stale_equipment_is_cancelled_and_drained(self):
        for event in ('zone', 'task', 'stop', 'disconnect', 'role', 'session', 'character'):
            with self.subTest(event=event):
                self.setUp()
                task, _, drained, old = await self.blocked_equipment()
                if event == 'zone':
                    self.zone = 'World/Next'
                    new = await self.context()
                elif event == 'task': self.quest = 43
                elif event == 'stop': self.client.questing_status = False
                elif event == 'disconnect': self.roster.remove(self.client)
                elif event == 'role': self.client.quest_party_hitters = []
                elif event == 'session': self.hitter.quest_party_status_session = object()
                else: self.client._character_selection_active = True
                await asyncio.wait_for(task, 1)
                self.assertTrue(drained.is_set())
                self.assertFalse(old[2]['equipping'])
                self.assertFalse(self.client.in_solo_zone)
                self.assertFalse(self.client.quest_party_solo_gear_active)
                self.restart.assert_not_called()
                if event == 'zone':
                    self.assertIs(self.client.quest_party_probe_wait, new[2])
                    self.assertTrue(self.client.quest_party_probe_pending)
                else:
                    self.assertIsNone(self.client.quest_party_probe_wait)
                    self.assertFalse(self.client.quest_party_probe_pending)

    async def test_equipment_error_keeps_existing_failure_policy(self):
        self.equipment.side_effect = RuntimeError('equipment UI unavailable')
        await self.finish(True, await self.context())
        self.assertTrue(self.client.in_solo_zone)
        self.assertFalse(self.client.quest_party_solo_gear_active)
        self.assertFalse(self.client.quest_party_probe_pending)

    async def test_explore_goal_settling_never_releases_before_fresh_probe(self):
        old = await self.context()
        self.goal = 8
        self.now = 10
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        new = self.client.quest_party_probe_wait
        self.assertIsNot(new, old[2])
        self.assertEqual(new['since'], old[2]['since'])
        await self.finish(True, old)
        self.assertTrue(self.client.quest_party_probe_pending)
        self.equipment.assert_not_awaited()
        await self.finish(False, (self.zone, (self.quest, self.goal), new, self.session, True))
        self.assertFalse(self.client.quest_party_probe_pending)

    async def test_goal_settling_during_equipment_cancels_then_reprobes(self):
        task, _, drained, _ = await self.blocked_equipment()
        self.goal = 8
        await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        self.restart.assert_not_called()

    async def test_pending_probe_blocks_direct_task_tp_and_special_exit(self):
        for zone in ('World/New', 'Krokotopia/KT_WorldTeleporter'):
            self.zone = zone
            with patch('src.questing.collision_tp', AsyncMock()) as tp:
                await self.quester.teleport_to_quest_target(self.client, XYZ(1, 2, 3))
                tp.assert_not_awaited()

    async def test_existing_equipment_timeout_completes_before_release(self):
        equipment = AsyncMock()
        async def timeout(coro, *, timeout):
            self.assertEqual(timeout, 15.0)
            coro.close()
            raise asyncio.TimeoutError
        env = dict(asyncio=SimpleNamespace(wait_for=timeout, TimeoutError=asyncio.TimeoutError),
                   Client=object, change_equipment_set=equipment, logger=Mock(),
                   Keycode=SimpleNamespace(B='B'))
        exec(compile(ast.Module(body=[self.change_equipment], type_ignores=[]),
                     'XuanShu.py', 'exec'), env)
        self.namespace['change_party_equipment'] = env['change_party_equipment']
        await self.finish(True, await self.context())
        self.client.send_key.assert_awaited_once_with('B', .1)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.assertFalse(self.client.quest_party_solo_gear_active)

    async def test_cancelling_probe_does_not_publish_solo_or_restart_worker(self):
        task, _, drained, _ = await self.blocked_equipment()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(drained.is_set())
        self.assertIsNone(self.client.quest_party_probe_wait)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.restart.assert_not_called()
        self.assertFalse(self.client.in_solo_zone)

    async def test_rejoin_restore_is_cancelled_when_live_area_proof_is_lost(self):
        self.client.in_solo_zone = self.client.quest_party_solo_gear_active = True
        task, _, drained, _ = await self.blocked_equipment(solo=False)
        self.namespace['clients_share_live_area'].return_value = False
        await asyncio.wait_for(task, 1)
        self.assertTrue(drained.is_set())
        self.assertTrue(self.client.in_solo_zone)
        self.assertTrue(self.client.quest_party_solo_gear_active)
        self.restart.assert_not_called()

    async def test_hitter_refill_or_loading_cancels_probe_equipment_without_solo_result(self):
        for state in ('refill', 'loading'):
            with self.subTest(state=state):
                self.setUp()
                task, _, drained, _ = await self.blocked_equipment()
                if state == 'refill': self.hitter.refilling_potions = True
                else: self.hitter.is_loading.return_value = True
                await asyncio.wait_for(task, 1)
                self.assertTrue(drained.is_set())
                self.assertFalse(self.client.in_solo_zone)
                self.restart.assert_not_called()

    async def test_pending_probe_prevents_assigned_quester_exploration_reentry(self):
        self.quester._dungeon_quest_snapshot = AsyncMock(return_value=(42, 7, 'Go to the door'))
        self.assertTrue(await self.quester._trigger_reentry_blocked(self.client, target=XYZ(1, 2, 3)))
        self.quester._dungeon_quest_snapshot.assert_not_awaited()
