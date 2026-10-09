import ast
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import Keycode, XYZ
from src.paths import advance_dialog_path, decline_quest_path, npc_range_path, cancel_multiple_quest_menu_path
from src.questing import Quester
from src.automation_ownership import automation_owner
from src.task_lifecycle import gather_owned
from src.utils import close_npc_quest_menu

REAL_SLEEP = asyncio.sleep


def app_function(name):
    tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


class OrdinaryQuest415Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.target = XYZ(1000, 2000, 0)
        self.position = XYZ(10, 20, 0)
        self.now = 0.0
        self.prompt = 'Press X to Use'
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_party_hitters=[],
            mainline_finder_enabled=False, quest_recovery_owner=None, refilling_potions=False,
            use_potions=False, auto_pet_status=False, entity_detect_combat_status=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/Area'),
            quest_id=AsyncMock(return_value=999001), goal_id=AsyncMock(return_value=7),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            root_window=object(), send_key=AsyncMock(),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.quester._maybe_recover_mainline = AsyncMock(side_effect=AssertionError('ordinary mode entered finder'))
        self.quester._mainline_sync_blocks_movement = AsyncMock(side_effect=AssertionError('ordinary mode entered index gate'))
        self.quester._quest_dialogue_blocks_movement = AsyncMock(side_effect=AssertionError('ordinary mode entered failed menu hold'))
        self.objective = 'Use Lever in Forest'
        self.range_visible = True
        async def move(client, target, leader_client=None):
            self.position = target
        async def tick(delay):
            self.now += delay
            await REAL_SLEEP(0)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.move = stack.enter_context(patch('src.questing.collision_tp', AsyncMock(side_effect=move)))
        self.progress = stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        self.free = stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)))
        stack.enter_context(patch('src.questing.is_potion_needed', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.close_automation_popup', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda _, p: p == npc_range_path and self.range_visible)))
        stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(return_value='Lever')))
        stack.enter_context(patch('src.questing.get_quest_name', AsyncMock(side_effect=lambda _: self.objective)))
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', tick))

    async def test_unindexed_and_side_quest_current_targets_continue_without_mainline_gate(self):
        for index, objective in enumerate(('Use Lever in Forest', 'Collect Mushrooms in Forest')):
            self.objective = objective
            self.target = XYZ(1000 + index * 100, 2000, 0)
            await self.quester.auto_quest_solo()
        self.assertEqual(self.client.send_key.await_count, 2)
        self.assertEqual(self.position, self.target)
        self.quester._maybe_recover_mainline.assert_not_awaited()
        self.quester._mainline_sync_blocks_movement.assert_not_awaited()
        self.quester._quest_dialogue_blocks_movement.assert_not_awaited()

    async def test_unknown_visible_nearby_prompt_uses_the_old_x_rule(self):
        self.prompt = '按 X 未知动作'
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_without_visible_interaction_window_no_x_is_sent(self):
        self.range_visible = False
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_not_awaited()
        self.move.assert_awaited_once_with(self.client, self.target, leader_client=None)

    async def test_npc_current_goal_change_is_confirmed_without_index_lookup(self):
        self.prompt = 'Press X to Talk'
        self.objective = 'Talk To Merle in Forest'
        reads = 0
        async def state(_):
            nonlocal reads
            reads += 1
            if reads > 1:
                self.client.goal_id.return_value = 8
                return 'Go To The Next Room'
            return 'Talk To Merle'
        self.quester.read_quest_txt = AsyncMock(side_effect=state)
        await self.quester.auto_quest_solo()
        self.assertEqual(self.client.goal_id.return_value, 8)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.quester._maybe_recover_mainline.assert_not_awaited()

    async def test_legacy_world_gate_opens_and_selects_only_on_task_client(self):
        hitter = SimpleNamespace(title='p2', questing_status=True, refilling_potions=False,
            is_loading=AsyncMock(return_value=False), send_key=AsyncMock())
        self.quester.clients = [self.client, hitter]
        self.position = self.target
        self.quester.leader_wait_for_free = AsyncMock()
        self.quester.handle_spiral_navigation = AsyncMock()
        opened = False
        async def press(*args, **kwargs):
            nonlocal opened
            opened = True
        self.client.send_key.side_effect = press
        with (patch('src.questing.clients_share_live_area', AsyncMock(return_value=True)),
              patch('src.questing.safe_click_window', AsyncMock()),
              patch('src.questing.get_popup_title', AsyncMock(return_value='世界之门')),
              patch('src.questing.is_spiral_door_open', AsyncMock(side_effect=lambda c: c is self.client and opened))):
            await self.quester.handle_normal_quests([], True)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        hitter.send_key.assert_not_awaited()
        self.quester.handle_spiral_navigation.assert_awaited_once()

    async def test_npc_goal_id_change_confirms_progress_even_when_label_and_position_stay_same(self):
        self.prompt = 'Press X to Talk'
        reads = 0
        async def same_text(_):
            nonlocal reads
            reads += 1
            if reads > 1:
                self.client.goal_id.return_value = 8
            return 'Talk To Merle'
        self.quester.read_quest_txt = AsyncMock(side_effect=same_text)
        self.assertTrue(await self.quester.handle_npc_talking_quests(self.client, [self.client]))
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.client.goal_id.return_value, 8)

    async def test_slow_hitter_uses_retained_source_point_only_with_valid_source_token(self):
        hitter = SimpleNamespace(title='p2', questing_status=True, refilling_potions=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='World/Area'),
            body=SimpleNamespace(position=AsyncMock(return_value=self.position)))
        self.client.zone_name.return_value = 'World/NextRoom'
        self.client.goal_id.return_value = 8
        self.client.quest_party_shared_target = {'identity': (999001, 7), 'zone': 'World/Area',
            'xyz': self.target, 'source_tokens': {id(hitter): 'source-token'}}
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        with patch('src.questing._party_area_token', AsyncMock(return_value='source-token')):
            await self.quester.move_until_quest_interaction(hitter, self.target, self.client)
        self.move.assert_awaited_once_with(hitter, self.target, leader_client=self.client)
        self.move.reset_mock()
        with patch('src.questing._party_area_token', AsyncMock(return_value='different-instance')):
            await self.quester.move_until_quest_interaction(hitter, self.target, self.client)
        self.move.assert_not_awaited()

    async def test_ordinary_quester_advances_with_loading_refilling_or_stopped_hitter(self):
        hitter = SimpleNamespace(title='p2', questing_status=False, refilling_potions=True,
            is_loading=AsyncMock(return_value=True), zone_name=AsyncMock(return_value='Elsewhere'),
            quest_position=SimpleNamespace(position=AsyncMock()), send_key=AsyncMock())
        self.client.quest_party_hitters = [hitter]
        self.client.quest_party_probe_pending = True
        await self.quester.auto_quest_solo()
        self.move.assert_awaited_once_with(self.client, self.target, leader_client=None)
        self.client.send_key.assert_awaited_once()
        hitter.quest_position.position.assert_not_awaited()
        hitter.send_key.assert_not_awaited()
        hitter.is_loading.assert_not_awaited()

    async def test_explicit_finder_can_pause_but_disabling_it_releases_current_goal(self):
        self.client.mainline_finder_enabled = True
        self.quester._maybe_recover_mainline = AsyncMock(return_value=True)
        await self.quester.auto_quest_solo()
        self.move.assert_not_awaited()
        self.quester._maybe_recover_mainline.assert_awaited_once_with(self.client)
        self.client.mainline_finder_enabled = False
        self.client.mainline_finder_offer_guard = True
        self.client.mainline_chain_retry_active = True
        await self.quester.auto_quest_solo()
        self.move.assert_awaited_once()
        self.client.send_key.assert_awaited_once()
        self.assertFalse(self.client.mainline_finder_offer_guard)
        self.assertFalse(self.client.mainline_chain_retry_active)

    async def test_stop_during_loop_sleep_does_not_start_another_iteration(self):
        async def stop(delay):
            self.client.questing_status = False
        self.quester.auto_quest_solo = AsyncMock()
        with patch('src.questing.asyncio.sleep', stop):
            await self.quester.auto_quest(False, False)
        self.quester.auto_quest_solo.assert_not_awaited()

    async def test_target_change_during_move_does_not_interact_with_old_target(self):
        async def move(client, target, leader_client=None):
            self.position = target
            self.client.goal_id.return_value = 8
        self.move.side_effect = move
        await self.quester.auto_quest_solo()
        self.client.send_key.assert_not_awaited()

    async def test_stop_after_waiting_for_input_lock_does_not_move_or_press_x(self):
        acquired = asyncio.Event()
        async def stop():
            async with automation_owner(self.client, 'manual'):
                task = asyncio.create_task(self.quester.auto_quest_solo())
                await REAL_SLEEP(.03)
                self.client.questing_status = False
            await asyncio.wait_for(task, 1)
        await stop()
        self.move.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_stopped_loading_and_battle_clients_do_not_interact(self):
        for state in ('stopped', 'loading', 'battle'):
            self.client.questing_status = state != 'stopped'
            self.client.is_loading.return_value = state == 'loading'
            self.client.in_battle.return_value = state == 'battle'
            await self.quester.auto_quest_solo()
        self.move.assert_not_awaited()
        self.client.send_key.assert_not_awaited()


class OrdinaryDialogue415Tests(unittest.IsolatedAsyncioTestCase):
    async def run_dialogue(self, *, accepts, client_flag='missing', mode='dialogue'):
        client = SimpleNamespace(title='p1', questing_status=True, is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False), quest_id=AsyncMock(return_value=101),
            mainline_finder_offer_guard=True, npc_mainline_menu_selection={'failed': True},
            quest_invitation_state={'failed': True})
        if client_flag != 'missing':
            client.hotkey_accept_sidequests = client_flag
        done = asyncio.Event()
        async def key(key=None, **kwargs):
            if key == Keycode.SPACEBAR:
                client.quest_id.return_value = 202  # Server published the accepted quest.
            done.set()
        client.send_key = AsyncMock(side_effect=key)
        ns = dict(asyncio=asyncio, Client=object, walker=SimpleNamespace(clients=[client]),
            automation_owner=automation_owner, freecam_status=False, side_quest_status=accepts, gather_owned=gather_owned, Keycode=Keycode,
            close_npc_quest_menu=AsyncMock(return_value=False),
            is_visible_by_path=AsyncMock(return_value=True),
            advance_dialog_path=advance_dialog_path, decline_quest_path=decline_quest_path)
        fn = app_function('dialogue_loop')
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), ns)
        tasks = []
        if mode in ('dialogue', 'both'):
            tasks.append(asyncio.create_task(ns['dialogue_loop']([client])))
        if mode in ('questing', 'both'):
            tasks.append(asyncio.create_task(ns['dialogue_loop']([client], questing=True)))
        try:
            await asyncio.wait_for(done.wait(), 1)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return client

    async def test_task_only_dialogue_only_and_combined_share_old_page_rules(self):
        for mode in ('questing', 'dialogue', 'both'):
            for accepts in (True, False):
                with self.subTest(mode=mode, accepts=accepts):
                    client = await self.run_dialogue(accepts=accepts, mode=mode)
                    self.assertEqual(client.quest_id.return_value, 202 if accepts else 101)
                    self.assertEqual(client.send_key.await_count, 1)
                    self.assertFalse(getattr(client, 'auto_dialogue_running', False))

    async def test_global_side_accept_default_reaches_actual_acceptance(self):
        client = await self.run_dialogue(accepts=True)
        self.assertEqual(client.quest_id.return_value, 202)
        self.assertEqual(client.send_key.await_args_list[0].kwargs['key'], Keycode.SPACEBAR)

    async def test_side_accept_off_declines_without_changing_owned_quest(self):
        client = await self.run_dialogue(accepts=False)
        self.assertEqual(client.quest_id.return_value, 101)
        self.assertEqual(client.send_key.await_args_list[0].kwargs['key'], Keycode.ESC)

    async def test_client_group_flag_overrides_global_default(self):
        client = await self.run_dialogue(accepts=False, client_flag=True)
        self.assertEqual(client.quest_id.return_value, 202)
        client = await self.run_dialogue(accepts=True, client_flag=False)
        self.assertEqual(client.quest_id.return_value, 101)

    async def test_one_or_many_npc_choices_exit_without_choosing_indexed_quest(self):
        for option_count in (1, 3):
            client = SimpleNamespace(is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
                quest_id=AsyncMock(return_value=101), options=list(range(option_count)),
                mainline_finder_enabled=False, npc_mainline_menu_selection={'failed': True})
            with (patch('src.utils.is_visible_by_path', AsyncMock(side_effect=lambda c, p: p == cancel_multiple_quest_menu_path)),
                  patch('src.utils.safe_click_window', AsyncMock()) as exit_button):
                self.assertTrue(await close_npc_quest_menu(client))
            exit_button.assert_awaited_once_with(client, cancel_multiple_quest_menu_path)
            self.assertEqual(client.quest_id.return_value, 101)


if __name__ == '__main__':
    unittest.main()
