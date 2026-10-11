"""Behaviour regressions for the complete 4.1.5 task path, including callers."""
import asyncio
from contextlib import ExitStack
import dis
import inspect
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace, FunctionType, CodeType
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src.paths import advance_dialog_path, npc_range_path
from src.questing import Quester
from src.teleport_math import navmap_tp

# Import the actual app definitions with isolated settings; never call main().
with patch.dict(os.environ, {'APPDATA': tempfile.mkdtemp(prefix='xuanshu-415-tests-')}):
    import XuanShu as APP

REAL_SLEEP = asyncio.sleep

def codes(code):
    yield code
    for item in code.co_consts:
        if isinstance(item, CodeType):
            yield from codes(item)

def entry(name, namespace=None, **closed):
    matches = [code for code in codes(inspect.unwrap(APP.main).__code__) if code.co_name == name]
    assert len(matches) == 1, (name, len(matches))
    code = matches[0]
    assert set(closed) == set(code.co_freevars), (name, code.co_freevars)
    def cell(value):
        return (lambda: value).__closure__[0]
    fn = FunctionType(code, dict(APP.__dict__, **(namespace or {})), name,
                      closure=tuple(cell(closed[n]) for n in code.co_freevars) or None)
    if name in ('dialogue_loop', 'questing_loop'):
        fn.__defaults__ = (None,)
    return fn

class FullTaskPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000, 2000, 300)
        self.position = XYZ(0, 0, 0)
        self.objective = 'Use Lever in Forest'
        self.prompt = 'Press X to Use'
        self.range_visible = True
        self.clock = 0.0
        self.actions = []
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_party_hitters=[],
            use_potions=False, auto_pet_status=False, entity_detect_combat_status=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/Area'),
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=7),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            send_key=AsyncMock(side_effect=lambda key=None, seconds=None: self.actions.append(('key', key))),
            teleport=AsyncMock(), root_window=object(),
            # All of these were new task gates, and must be ignored by 4.1.5.
            quest_recovery_owner='new-recovery', refilling_potions=True,
            post_combat_cleanup_active=True, quest_party_target_sync_active=True,
            potion_dungeon_returned=('old-zone', 'new-zone'),
            mainline_finder_enabled=True, _quest_movement_failures={'failed': object()},
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        async def tick(delay):
            self.clock += delay
            await REAL_SLEEP(0)
        self.tick = tick
        async def move(client, target, leader_client=None):
            self.actions.append(('move', target))
            self.position = target
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.asyncio.sleep', tick))
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.clock)))
        stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_potion_needed', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.close_automation_popup', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(
            side_effect=lambda _, path: path == npc_range_path and self.range_visible)))
        stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(return_value='Lever')))
        stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(side_effect=lambda _: self.objective)))
        self.movement = stack.enter_context(patch('src.questing.collision_tp', AsyncMock(side_effect=move)))

    async def test_consecutive_targets_move_then_x_without_new_state_qualification(self):
        for i in range(3):
            self.target = XYZ(1000 + i * 1200, 2000, 300)
            await self.quester.auto_quest_solo()
        self.assertEqual([action[0] for action in self.actions], ['move', 'key'] * 3)
        self.assertEqual([action[1] for action in self.actions if action[0] == 'key'], [Keycode.X] * 3)
        self.assertEqual(self.movement.await_count, 3)
        self.client.goal_id.assert_not_awaited()
        self.assertTrue(self.client.questing_status)

    async def test_rejected_ordinary_target_is_retried_each_iteration_without_failure_cache(self):
        self.range_visible = False
        self.movement.side_effect = None  # a rejected server TP returns without progress
        for _ in range(4):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.movement.await_count, 4)
        self.client.send_key.assert_not_awaited()
        self.assertTrue(self.client.questing_status)

    async def test_log_photomancy_objective_sends_s_then_two_z_after_movement(self):
        self.range_visible = False
        self.target = XYZ(1432.211, -1316.835, 346.554)
        self.objective = '<center>按下 Z 来拍照 废物排放管道 地点：尼布尔海姆</center>'
        with patch('src.questing.discard_photo_popup', AsyncMock(return_value=True)):
            await self.quester.auto_quest_solo()
        self.assertEqual(self.actions, [('move', self.target), ('key', Keycode.S), ('key', Keycode.Z), ('key', Keycode.Z)])

    async def test_zero_coordinates_go_directly_to_collect_one_after_baseline_reread(self):
        self.target = XYZ(0, 0, 0)
        self.quester.auto_collect_rewrite = AsyncMock(return_value=True)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.client.quest_position.position.await_count, 2)
        self.quester.auto_collect_rewrite.assert_awaited_once_with(self.client)
        self.movement.assert_not_awaited()
        self.client.goal_id.assert_not_awaited()

    async def test_party_dungeon_entry_uses_baseline_same_zone_then_coordinate_tp(self):
        hitter = SimpleNamespace(title='p2', questing_status=True,
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='World/Area'),
            teleport=AsyncMock(), quest_party_target_sync_active=True, refilling_potions=True)
        self.client.quest_party_hitters = [hitter]
        self.position = self.target
        self.assertEqual(await self.quester.prepare_party_dungeon_entry(), [self.client, hitter])
        hitter.teleport.assert_awaited_once_with(self.target)

    async def test_npc_missing_x_retries_three_times_without_new_npc_cache(self):
        self.quester.read_quest_txt = AsyncMock(return_value='Talk to NPC')
        with patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)):
            self.assertFalse(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.assertEqual(self.client.send_key.await_count, 2)  # first X is sent by caller
        self.assertTrue(all(call.args == (Keycode.X, .15) for call in self.client.send_key.await_args_list))
        self.assertTrue(self.client.questing_status)

class CallerAndDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_quest_worker_globals_resolve_against_current_app(self):
        for name in ('dialogue_loop', 'run_questing_worker', '_follow_quester_session'):
            code = next(c for c in codes(inspect.unwrap(APP.main).__code__) if c.co_name == name)
            names = {ins.argval for sub in codes(code) for ins in dis.get_instructions(sub)
                     if ins.opname == 'LOAD_GLOBAL'}
            import builtins
            self.assertFalse(names - APP.__dict__.keys() - vars(builtins).keys() - {'walker'}, name)

    async def test_real_auto_dialogue_advances_even_when_removed_recovery_state_is_set(self):
        done = asyncio.Event()
        client = SimpleNamespace(refilling_potions=True, post_combat_cleanup_active=True,
                                 quest_recovery_owner='obsolete', hotkey_accept_sidequests=False)
        async def send(**kwargs):
            self.assertEqual(kwargs, {'key': Keycode.SPACEBAR})
            done.set()
        client.send_key = AsyncMock(side_effect=send)
        async def tick(_):
            await REAL_SLEEP(0)
        loop = entry('dialogue_loop', dict(freecam_status=False,
            close_npc_quest_menu=AsyncMock(return_value=False), side_quest_status=False,
            is_visible_by_path=AsyncMock(side_effect=lambda c, path: path == advance_dialog_path),
            asyncio=SimpleNamespace(sleep=tick)))
        task = asyncio.create_task(loop([client]))
        try:
            await asyncio.wait_for(done.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertGreater(client.send_key.await_count, 0)

    async def test_baseline_post_combat_nudge_has_no_deep_teleport(self):
        client = SimpleNamespace(title='p1', questing_status=True,
            post_combat_movement_active=False, post_combat_movement_at=-100,
            post_combat_cleanup_active=True, is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False), send_key=AsyncMock(), teleport=AsyncMock())
        await entry('clear_post_combat_phase')(client)
        client.send_key.assert_awaited_once_with(key=Keycode.S, seconds=.3)
        client.teleport.assert_not_awaited()
        self.assertFalse(client.post_combat_movement_active)

    async def test_real_follower_moves_to_quester_without_landing_or_shared_target_approval(self):
        from src.task_lifecycle import gather_owned
        destination = XYZ(2000, 2000, 0)
        done = asyncio.Event()
        quester = SimpleNamespace(title='p1', questing_status=True, in_solo_zone=False,
            quest_party_probe_pending=False, quest_party_observed_zone='World/Area',
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='World/Area'),
            body=SimpleNamespace(position=AsyncMock(return_value=destination)),
            quest_party_target_sync_active=True, quest_party_shared_target={'arrived': False},
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[])))
        async def arrive(target):
            self.assertEqual(target, destination)
            done.set()
        hitter = SimpleNamespace(title='p2', questing_status=True, use_potions=False,
            entity_detect_combat_status=False, in_solo_zone=False,
            in_battle=AsyncMock(return_value=False),
            is_loading=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='World/Area'),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))),
            teleport=AsyncMock(side_effect=arrive), quest_party_status_session=object(),
            quest_party_target_sync_active=True, post_combat_cleanup_active=True)
        async def tick(_):
            await REAL_SLEEP(0)
        follow = entry('_follow_quester_session', dict(
            walker=SimpleNamespace(clients=[quester, hitter]), questing_status=True,
            gear_switching_in_solo_zones=False, quest_friend_icons={}, use_potions=False,
            close_automation_popup=AsyncMock(return_value=False), is_free=AsyncMock(return_value=True),
            is_visible_by_path=AsyncMock(return_value=False),
            asyncio=SimpleNamespace(sleep=tick, get_running_loop=asyncio.get_running_loop,
                CancelledError=asyncio.CancelledError, TimeoutError=TimeoutError)),
            members=[quester, hitter], change_party_equipment=AsyncMock(), prepare_quester_name=AsyncMock(),
            remove_party_status=Mock(), restart_quest_worker_after_probe=Mock(), update_party_status=Mock())
        with patch.object(Quester, 'handle_pending_dungeon_confirmation', AsyncMock(return_value=False)):
            task = asyncio.create_task(follow(hitter, quester, False))
            try:
                await asyncio.wait_for(done.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertGreater(hitter.teleport.await_count, 0)

    async def test_task_friend_matching_keeps_old_full_name_rule(self):
        from src.utils import _cycle_friends_list
        row = ('<Y;0><X;0><indent;0><Color;FFFFFF><left>'
               '<icon;FriendsList/Friend_Icon_List_01.dds;0;0;0></left>'
               '<Y;0><X;0><indent;0><Color;FFFFFF><left><COLOR;FFFFFF>Mason Stone<br>')
        client = SimpleNamespace(questing_status=True)
        match, _ = await _cycle_friends_list(client, object(), SimpleNamespace(maybe_text=AsyncMock(return_value=row)),
                                              None, None, 'Mason', 1)
        self.assertIsNone(match)
        match, _ = await _cycle_friends_list(client, object(), SimpleNamespace(maybe_text=AsyncMock(return_value=row)),
                                              None, None, 'Mason Stone', 1)
        self.assertIsNotNone(match)

    async def test_navmap_fallback_retries_full_path_and_walks_after_average_landing(self):
        pos = XYZ(0, 0, 0)
        target = XYZ(1000, 0, 0)
        calls = []
        async def teleport(dest):
            nonlocal pos
            calls.append(dest)
            if len(calls) % 3 == 0:
                pos = dest
        client = SimpleNamespace(title='p1', questing_status=True,
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: pos)),
            zone_name=AsyncMock(return_value='World/Area'), teleport=AsyncMock(side_effect=teleport), goto=AsyncMock())
        vertices = [XYZ(100, 0, 0), XYZ(300, 0, 0), XYZ(500, 0, 0)]
        with (patch('src.teleport_math.is_free', AsyncMock(return_value=True)),
              patch('src.teleport_math.asyncio.sleep', AsyncMock()),
              patch('src.teleport_math.load_wad', AsyncMock(return_value=SimpleNamespace(get_file=AsyncMock(return_value=b'nav')))),
              patch('src.teleport_math.parse_nav_data', return_value=(vertices, [(0, 1), (1, 0), (1, 2), (2, 1)])),
              patch('src.teleport_math.fallback_spiral_tp', AsyncMock()) as spiral):
            for _ in range(2):
                pos = XYZ(0, 0, 0)
                await navmap_tp(client, target)
        self.assertEqual(len(calls), 6)
        self.assertEqual([p.x for p in calls], [1000, 650, 300] * 2)
        self.assertEqual(client.goto.await_count, 2)
        spiral.assert_not_awaited()

if __name__ == '__main__':
    unittest.main()
