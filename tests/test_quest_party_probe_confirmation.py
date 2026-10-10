"""Transient friend errors need settled confirmation; live rejoin reverses solo."""
import ast
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.utils import FriendBusyOrInstanceClosed

REAL_SLEEP = asyncio.sleep


class State(SimpleNamespace):
    __eq__ = object.__eq__
    __ne__ = object.__ne__


class ProbeConfirmationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                            and n.name == '_follow_quester_session')

    def setUp(self):
        self.now, self.ticks, self.limit = 0.0, 0, 30
        self.same_area = False
        self.session = object()
        self.hitter = State(title='p2', questing_status=True, refilling_potions=False,
            quest_party_status_session=self.session, entity_detect_combat_status=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/Old'), mouse_handler=AsyncMock(),
            body=SimpleNamespace(position=AsyncMock(return_value=0)), teleport=AsyncMock())
        self.quester = State(title='p1', questing_status=True, refilling_potions=False,
            in_solo_zone=False, quest_party_solo_gear_active=False, quest_party_probe_pending=True,
            quest_party_probe_wait=None, quest_party_hitters=[self.hitter],
            quest_party_observed_zone='World/New', quest_party_quest_worker_zone='World/New',
            quest_party_group_dungeon_zone=None, wizard_name='Wizard',
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/New'), quest_id=AsyncMock(return_value=42),
            goal_id=AsyncMock(return_value=7), body=SimpleNamespace(position=AsyncMock(return_value=0)))
        self.hitter.quest_party_quester = self.quester
        self.peer = State(title='p3', questing_status=True, in_solo_zone=True,
                          quest_party_solo_gear_active=True, quest_party_probe_pending=True)
        self.roster = [self.quester, self.hitter, self.peer]
        self.friend = AsyncMock(side_effect=FriendBusyOrInstanceClosed())
        self.equip = AsyncMock(return_value=True)
        self.restart, self.status = Mock(), Mock()
        self.reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
            _resume_party_dungeon_interaction=AsyncMock(return_value=False),
            get_truncated_quest_objectives=AsyncMock(return_value='Defeat Boss'))
        self.namespace = dict(asyncio=asyncio, Client=object, logger=Mock(),
            members=[self.quester, self.hitter], questing_status=True,
            walker=SimpleNamespace(clients=self.roster), Quester=Mock(return_value=self.reader),
            use_potions=False, buy_potions=True, auto_potions=AsyncMock(), refill_potions=AsyncMock(),
            automation_owner=automation_owner, update_party_status=self.status,
            get_client_automation_ownership=get_client_automation_ownership,
            remove_party_status=Mock(), gear_switching_in_solo_zones=True,
            change_party_equipment=self.equip, restart_quest_worker_after_probe=self.restart,
            is_free=AsyncMock(side_effect=lambda c: not c.is_loading.return_value),
            clients_share_live_area=AsyncMock(side_effect=lambda *a: self.same_area),
            close_automation_popup=AsyncMock(return_value=False),
            is_spiral_door_open=AsyncMock(return_value=False),
            is_visible_by_path=AsyncMock(return_value=False), spiral_door_teleport_path=[],
            resolve_quester_friend_icon=lambda *a: None, quest_friend_icons={},
            teleport_to_friend_from_list=self.friend, close_friend_windows=AsyncMock(),
            is_friend_teleport_error=AsyncMock(return_value=False),
            click_window_by_path=AsyncMock(), friend_is_busy_and_dungeon_reset_path=[],
            FriendBusyOrInstanceClosed=FriendBusyOrInstanceClosed,
            friend_follow_retry_delay=lambda n: 5, calc_Distance=lambda a, b: abs(a - b))
        exec(compile(ast.Module(body=[self.function], type_ignores=[]), 'XuanShu.py', 'exec'), self.namespace)
        self.on_tick = None

    async def run_follow(self, primary=True, expect_enabled=True):
        async def tick(delay):
            self.now += delay
            if delay == .5:
                self.ticks += 1
                if self.on_tick: self.on_tick()
                if self.ticks > self.limit: self.hitter.questing_status = False
            await REAL_SLEEP(0)
        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio, 'sleep', tick))
            stack.enter_context(patch.object(asyncio, 'get_running_loop',
                return_value=SimpleNamespace(time=lambda: self.now)))
            await self.namespace['_follow_quester_session'](self.hitter, self.quester, primary)
        self.assertEqual(self.quester.questing_status, expect_enabled)
        self.assertFalse(get_client_automation_ownership(self.quester).locked)
        self.assertTrue(self.peer.in_solo_zone)
        self.assertTrue(self.peer.quest_party_solo_gear_active)
        self.assertTrue(self.peer.quest_party_probe_pending)

    async def test_first_busy_error_cannot_publish_solo_or_switch_equipment(self):
        async def first(*args, **kwargs):
            self.limit = self.ticks
            raise FriendBusyOrInstanceClosed()
        self.friend.side_effect = first
        await self.run_follow()
        self.friend.assert_awaited_once()
        self.equip.assert_not_awaited()
        self.restart.assert_not_called()
        self.assertFalse(self.quester.in_solo_zone)
        self.assertTrue(self.quester.quest_party_probe_pending)

    async def test_temporary_error_then_success_never_switches_second_equipment(self):
        async def friend(*args, **kwargs):
            if self.friend.await_count == 1: raise FriendBusyOrInstanceClosed()
            self.hitter.zone_name.return_value = 'World/New'
            self.same_area = True
        self.friend.side_effect = friend
        await self.run_follow()
        self.assertEqual(self.friend.await_count, 2)
        self.assertFalse(self.quester.in_solo_zone)
        self.equip.assert_not_awaited()
        self.restart.assert_not_called()
        self.assertFalse(self.quester.quest_party_probe_pending)

    async def test_two_settled_errors_confirm_solo_then_stop_frequent_tp(self):
        await self.run_follow()
        self.assertEqual(self.friend.await_count, 2)
        self.equip.assert_awaited_once_with(self.quester, 1)
        self.assertTrue(self.quester.in_solo_zone)
        self.assertTrue(self.quester.quest_party_solo_gear_active)
        self.assertFalse(self.quester.quest_party_probe_pending)
        self.restart.assert_called_once_with(self.quester)

    async def test_refill_time_is_not_evidence_and_return_requires_two_fresh_probes(self):
        self.namespace['use_potions'] = True
        charge = [0]
        self.hitter.stats = SimpleNamespace(potion_charge=AsyncMock(side_effect=lambda: charge[0]),
                                           reference_level=AsyncMock(return_value=100))
        returned = []
        async def refill(*args, **kwargs):
            self.hitter.refilling_potions = True
            self.now += 70  # More than the quester's existing 45s wait.
            self.assertFalse(self.quester.in_solo_zone)
            self.equip.assert_not_awaited()
            charge[0] = 3
            self.hitter.refilling_potions = False
            returned.append(self.now)
        self.namespace['refill_potions'].side_effect = refill
        async def friend(*args, **kwargs):
            self.assertGreaterEqual(self.now - returned[0], 3)
            self.equip.assert_not_awaited()
            raise FriendBusyOrInstanceClosed()
        self.friend.side_effect = friend
        await self.run_follow()
        self.namespace['refill_potions'].assert_awaited_once()
        self.assertEqual(self.friend.await_count, 2)
        self.equip.assert_awaited_once_with(self.quester, 1)

    async def test_loading_between_errors_invalidates_first_evidence(self):
        calls = []
        async def friend(*args, **kwargs):
            calls.append(self.now)
            if len(calls) == 1: self.hitter.is_loading.return_value = True
            if len(calls) == 2: self.limit = self.ticks
            raise FriendBusyOrInstanceClosed()
        self.friend.side_effect = friend
        def tick():
            if self.hitter.is_loading.return_value: self.hitter.is_loading.return_value = False
        self.on_tick = tick
        await self.run_follow()
        self.assertEqual(len(calls), 2)
        self.equip.assert_not_awaited()
        self.assertFalse(self.quester.in_solo_zone)

    async def test_completed_solo_is_reversed_by_later_same_instance_rejoin(self):
        def tick():
            if self.quester.in_solo_zone:
                self.same_area = True
                self.hitter.zone_name.return_value = 'World/New'
        self.on_tick = tick
        await self.run_follow()
        self.assertEqual([c.args[1] for c in self.equip.await_args_list], [1, 0])
        self.assertFalse(self.quester.in_solo_zone)
        self.assertFalse(self.quester.quest_party_solo_gear_active)
        self.assertFalse(self.quester.quest_party_probe_pending)
        self.restart.assert_called_once()  # Rejoin does not restart the worker.
        self.assertIn('已归队', [c.args[2] for c in self.status.call_args_list])

    async def test_secondary_hitter_can_reverse_completed_solo_with_live_proof(self):
        self.quester.in_solo_zone = self.quester.quest_party_solo_gear_active = True
        self.quester.quest_party_probe_pending = False
        self.same_area = True
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow(primary=False)
        self.friend.assert_not_awaited()
        self.equip.assert_awaited_once_with(self.quester, 0)
        self.assertFalse(self.quester.in_solo_zone)
        self.restart.assert_not_called()

    async def test_same_zone_without_live_proof_cannot_reverse_solo(self):
        self.quester.in_solo_zone = self.quester.quest_party_solo_gear_active = True
        self.quester.quest_party_probe_pending = False
        self.hitter.zone_name.return_value = 'World/New'
        await self.run_follow()
        self.equip.assert_not_awaited()
        self.assertTrue(self.quester.in_solo_zone)

    async def test_goal_change_between_errors_requires_new_confirmation(self):
        async def friend(*args, **kwargs):
            if self.friend.await_count == 1:
                self.quester.goal_id.return_value = 8
            else: self.limit = self.ticks
            raise FriendBusyOrInstanceClosed()
        self.friend.side_effect = friend
        await self.run_follow()
        self.equip.assert_not_awaited()
        self.assertFalse(self.quester.in_solo_zone)

    async def test_stopped_quester_discards_busy_response_without_inputs_or_restart(self):
        async def friend(*args, **kwargs):
            self.quester.questing_status = False
            raise FriendBusyOrInstanceClosed()
        self.friend.side_effect = friend
        # This scenario intentionally stops the quester.
        await self.run_follow(expect_enabled=False)
        self.assertEqual(self.friend.await_count, 1)
        self.equip.assert_not_awaited()
        self.restart.assert_not_called()
        self.assertFalse(self.quester.in_solo_zone)
        self.namespace['close_friend_windows'].assert_awaited_once()
        self.namespace['click_window_by_path'].assert_not_awaited()
