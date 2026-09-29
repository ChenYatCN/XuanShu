import ast
import asyncio
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from contextlib import ExitStack
from src import utils


class PotionRecallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p1', zone_name=AsyncMock(return_value='Original'),
            stats=SimpleNamespace(reference_level=AsyncMock(return_value=50),
                                  potion_charge=AsyncMock(return_value=0), potion_max=AsyncMock(return_value=4)))
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.mocks = {}
        for name in ('ensure_teleport_mark','navigate_to_ravenwood', 'navigate_to_commons_from_ravenwood',
                     'navigate_to_potions', 'buy_potions', 'is_potion_needed'):
            self.mocks[name] = stack.enter_context(patch.object(utils, name, new_callable=AsyncMock))
        self.mocks['ensure_teleport_mark'].return_value = True
        self.mocks['buy_potions'].return_value = True
        self.mocks['is_potion_needed'].return_value = False

    async def test_default_mark_false_still_marks_before_departure(self):
        events = []
        async def mark(c):
            events.append('mark')
            return True
        async def travel(c):
            events.append('travel')
        self.mocks['ensure_teleport_mark'].side_effect = mark
        self.mocks['navigate_to_ravenwood'].side_effect = travel
        self.assertTrue(await utils.refill_potions(self.client))
        self.assertEqual(events, ['mark', 'travel'])
        self.mocks['buy_potions'].assert_awaited_once_with(
            self.client, True, original_zone='Original', dungeon_return=False)
        self.assertFalse(self.client.refilling_potions)

    async def test_failed_mark_prevents_both_departure_paths(self):
        self.mocks['ensure_teleport_mark'].return_value = False
        self.assertFalse(await utils.refill_potions(self.client))
        self.assertFalse(await utils.auto_potions_force_buy(self.client))
        self.mocks['navigate_to_ravenwood'].assert_not_awaited()
        self.mocks['buy_potions'].assert_not_awaited()

    async def test_wrapper_propagates_recall_failure(self):
        self.mocks['buy_potions'].return_value = False
        self.assertFalse(await utils.refill_potions_if_needed(self.client))
        self.assertFalse(await utils.auto_potions(self.client))

    async def test_no_recall_preserves_unmarked_trip(self):
        self.assertTrue(await utils.refill_potions(self.client, recall=False))
        self.mocks['ensure_teleport_mark'].assert_not_awaited()

    async def test_mismatched_original_zone_prevents_mark_and_travel(self):
        self.assertFalse(await utils.refill_potions(self.client, original_zone='Elsewhere'))
        self.mocks['ensure_teleport_mark'].assert_not_awaited()
        self.mocks['navigate_to_ravenwood'].assert_not_awaited()

    async def test_zone_changes_during_mark_prevents_departure(self):
        self.client.zone_name.side_effect = ['Original', 'Unexpected']
        self.assertFalse(await utils.refill_potions(self.client))
        self.mocks['navigate_to_ravenwood'].assert_not_awaited()

    async def test_force_buy_passes_original_zone_and_failure(self):
        self.mocks['buy_potions'].return_value = False
        self.assertFalse(await utils.auto_potions_force_buy(self.client))
        self.mocks['buy_potions'].assert_awaited_once_with(
            self.client, recall=True, original_zone='Original', dungeon_return=False)

    async def test_each_dungeon_client_refills_independently_without_mark(self):
        other = SimpleNamespace(title='p2', zone_name=AsyncMock(return_value='Original'),
            questing_status=True, stats=self.client.stats)
        self.client.title = 'p1'
        self.client.questing_status = True
        self.client.quest_party_group_dungeon_zone = 'Original'
        other.quest_party_group_dungeon_zone = 'Original'
        seen = []

        async def travel(client):
            seen.append((client.title, client.refilling_potions))

        self.mocks['navigate_to_ravenwood'].side_effect = travel
        self.assertTrue(await utils.refill_potions(self.client))
        self.assertTrue(await utils.refill_potions(other))
        self.assertEqual(seen, [('p1', True), ('p2', True)])
        self.assertFalse(self.client.refilling_potions)
        self.assertFalse(other.refilling_potions)
        self.mocks['ensure_teleport_mark'].assert_not_awaited()
        self.assertEqual(self.mocks['buy_potions'].await_count, 2)
        self.assertTrue(all(call.kwargs['dungeon_return'] for call in
                            self.mocks['buy_potions'].await_args_list))

    async def test_failed_dungeon_return_stops_only_refilling_client(self):
        self.client.questing_status = True
        self.client.quest_party_group_dungeon_zone = 'Original'
        self.mocks['buy_potions'].return_value = False
        self.assertFalse(await utils.refill_potions(self.client))
        self.assertFalse(self.client.refilling_potions)
        self.assertFalse(self.client.questing_status)

    async def test_simultaneous_refills_keep_client_states_independent(self):
        other = SimpleNamespace(title='p2', questing_status=True,
            zone_name=AsyncMock(return_value='Original'), stats=self.client.stats,
            quest_party_group_dungeon_zone='Original')
        self.client.quest_party_group_dungeon_zone = 'Original'
        self.client.questing_status = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def travel(client):
            if client is self.client:
                entered.set()
                await release.wait()

        self.mocks['navigate_to_ravenwood'].side_effect = travel
        first = asyncio.create_task(utils.refill_potions(self.client))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertTrue(await utils.refill_potions(other))
            self.assertTrue(self.client.refilling_potions)
            self.assertFalse(other.refilling_potions)
        finally:
            release.set()
            await first
        self.assertFalse(self.client.refilling_potions)

    async def test_dungeon_cancel_clears_state_and_prevents_worker_restart(self):
        for refill in (utils.refill_potions, utils.auto_potions_force_buy):
            with self.subTest(refill=refill.__name__):
                self.client.questing_status = True
                self.client.quest_party_group_dungeon_zone = 'Original'
                self.mocks['navigate_to_ravenwood'].side_effect = asyncio.CancelledError
                with self.assertRaises(asyncio.CancelledError):
                    await refill(self.client)
                self.assertFalse(self.client.refilling_potions)
                self.assertFalse(self.client.questing_status)

    async def test_dungeon_disconnect_does_not_attempt_another_zone_read(self):
        for refill in (utils.refill_potions, utils.auto_potions_force_buy):
            with self.subTest(refill=refill.__name__):
                self.client.questing_status = True
                self.client.quest_party_group_dungeon_zone = 'Original'
                self.client.zone_name.side_effect = ['Original', 'Original', RuntimeError('closed')]
                self.mocks['navigate_to_ravenwood'].side_effect = RuntimeError('closed')
                self.assertFalse(await refill(self.client))
                self.assertFalse(self.client.refilling_potions)
                self.assertFalse(self.client.questing_status)

    async def test_exit_confirmation_alone_does_not_select_dungeon_return(self):
        self.client.quest_party_confirmed_dungeon_transition = ('Dungeon/Room', 'Original')
        self.assertTrue(await utils.refill_potions(self.client))
        self.mocks['ensure_teleport_mark'].assert_awaited_once()
        self.assertFalse(self.mocks['buy_potions'].await_args.kwargs['dungeon_return'])

    async def test_force_buy_uses_confirmed_dungeon_return_without_mark(self):
        self.client.quest_dungeon_recovery = {'zone': 'Original'}
        self.assertTrue(await utils.auto_potions_force_buy(self.client))
        self.mocks['ensure_teleport_mark'].assert_not_awaited()
        self.assertTrue(self.mocks['buy_potions'].await_args.kwargs['dungeon_return'])
        self.assertFalse(self.client.refilling_potions)


class DungeonReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_map_uses_mark_even_with_stale_resume_button(self):
        client = SimpleNamespace(title='p1', questing_status=True,
            zone_name=AsyncMock(return_value='WizardCity/WC_Hub'),
            stats=SimpleNamespace(potion_max=AsyncMock(return_value=0),
                                  potion_charge=AsyncMock(return_value=0)))
        with patch.object(utils.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(utils, 'is_visible_by_path', new=AsyncMock(return_value=True)), \
             patch.object(utils, 'return_to_dungeon_after_potions', new=AsyncMock()) as dungeon_return, \
             patch.object(utils, 'recall_to_teleport_mark', new=AsyncMock(return_value=True)) as mark_return:
            self.assertTrue(await utils.buy_potions(client, original_zone='World/Street'))
        dungeon_return.assert_not_awaited()
        mark_return.assert_awaited_once_with(client, expected_zone='World/Street')

    async def test_dungeon_return_failure_never_falls_back_to_mark_or_friend_tp(self):
        client = SimpleNamespace(title='p2', questing_status=True,
            zone_name=AsyncMock(return_value='WizardCity/WC_Hub'),
            stats=SimpleNamespace(potion_max=AsyncMock(return_value=0),
                                  potion_charge=AsyncMock(return_value=0)))
        with patch.object(utils.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(utils, 'return_to_dungeon_after_potions',
                          new=AsyncMock(return_value=False)) as dungeon_return, \
             patch.object(utils, 'recall_to_teleport_mark',
                          new=AsyncMock()) as mark_return:
            self.assertFalse(await utils.buy_potions(
                client, recall=True, original_zone='Dungeon/Room', dungeon_return=True))
        dungeon_return.assert_awaited_once_with(client, 'Dungeon/Room')
        mark_return.assert_not_awaited()
        self.assertFalse(client.questing_status)

    async def test_red_button_return_waits_for_stable_original_zone(self):
        client = SimpleNamespace(title='p2',
            is_loading=AsyncMock(side_effect=[False, True, False, False, False]),
            zone_name=AsyncMock(side_effect=['WizardCity/WC_Hub', 'Dungeon/Room',
                                             'Dungeon/Room', 'Dungeon/Room']))
        with patch.object(utils, 'is_visible_by_path', new=AsyncMock(return_value=True)) as visible, \
             patch.object(utils, 'click_window_by_path', new=AsyncMock()) as click, \
             patch.object(utils.asyncio, 'sleep', new=AsyncMock()):
            self.assertTrue(await utils.return_to_dungeon_after_potions(client, 'Dungeon/Room'))
        visible.assert_awaited_with(client, utils.dungeon_recall_path)
        click.assert_awaited_once_with(client, utils.dungeon_recall_path)
        self.assertEqual(utils.dungeon_recall_path,
            ['WorldView', 'windowHUD', 'compassAndTeleporterButtons', 'ResumeInstanceButton'])
        self.assertIsNone(client.potion_dungeon_returned)

    async def test_missing_red_button_returns_failure_without_click(self):
        client = SimpleNamespace(title='p2', is_loading=AsyncMock(return_value=False))
        with patch.object(utils, 'is_visible_by_path', new=AsyncMock(return_value=False)), \
             patch.object(utils, 'click_window_by_path', new=AsyncMock()) as click, \
             patch.object(utils.time, 'monotonic', side_effect=[0, 1, 11]), \
             patch.object(utils.asyncio, 'sleep', new=AsyncMock()):
            self.assertFalse(await utils.return_to_dungeon_after_potions(client, 'Dungeon/Room'))
        click.assert_not_awaited()


class PotionSynchronizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_failed_refill_removes_only_failed_client_from_local_roster(self):
        from src.questing import Quester
        leader = SimpleNamespace(process_id=1, questing_status=True,
            stats=SimpleNamespace(potion_charge=AsyncMock(return_value=1)))
        other = SimpleNamespace(process_id=2, questing_status=True,
            stats=SimpleNamespace(potion_charge=AsyncMock(return_value=0),
                                  reference_level=AsyncMock(return_value=10)))
        roster = [leader, other]
        quester = Quester(leader, roster, 1)
        quester.collect_wisps = AsyncMock()
        quester.guarantee_use_potion = AsyncMock()
        with patch('src.questing.refill_potions', new=AsyncMock(return_value=False)):
            self.assertFalse(await quester.heal_and_handle_potions())
        self.assertTrue(leader.questing_status)
        self.assertFalse(other.questing_status)
        self.assertEqual(quester.clients, [leader])
        self.assertEqual(roster, [leader, other])
        quester.collect_wisps.reset_mock()
        self.assertTrue(await quester.heal_and_handle_potions())
        quester.collect_wisps.assert_awaited_once_with(leader)

    async def test_legacy_zone_and_movement_skip_refilling_or_stopped_client(self):
        from src.questing import Quester
        leader = SimpleNamespace(process_id=1, zone_name=AsyncMock(return_value='Dungeon/Room'),
                                 body=SimpleNamespace(position=AsyncMock()))
        other = SimpleNamespace(process_id=2, questing_status=True, refilling_potions=True,
                                zone_name=AsyncMock(return_value='WizardCity/WC_Hub'),
                                teleport=AsyncMock(), send_key=AsyncMock())
        quester = Quester(leader, [leader, other], 1)
        for refilling, active in ((True, True), (False, False)):
            other.refilling_potions = refilling
            other.questing_status = active
            self.assertTrue(await quester.followers_in_correct_zone())
            self.assertEqual(await quester.get_follower_clients(), [])
            await quester.friend_teleport(False)
            await quester.teleport_to_quest_target(other, None)
        other.zone_name.assert_not_awaited()
        other.teleport.assert_not_awaited()
        other.send_key.assert_not_awaited()

    async def test_generic_combat_detection_skips_refilling_client(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        detect = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == 'detect_combat')
        nearest = AsyncMock()
        ns = dict(Client=object, asyncio=asyncio, SprintyClient=Mock(),
                  nearest_duel_circle_distance_and_xyz=nearest,
                  combat_group_for=Mock())
        exec(compile(ast.Module(body=[detect], type_ignores=[]), 'XuanShu.py', 'exec'), ns)
        client = SimpleNamespace(questing_status=True, refilling_potions=True)
        with patch.object(asyncio, 'sleep', new=AsyncMock(side_effect=[None, asyncio.CancelledError])):
            with self.assertRaises(asyncio.CancelledError):
                await ns['detect_combat'](client)
        ns['combat_group_for'].assert_not_called()
        nearest.assert_not_awaited()


class TeleportMarkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            title='p1', zone_name=AsyncMock(return_value='Original'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            send_key=AsyncMock(), stats=SimpleNamespace(current_mana=AsyncMock(return_value=100)))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(utils.asyncio, 'sleep', new_callable=AsyncMock))
        self.timer = stack.enter_context(patch.object(
            utils, 'wait_for_teleport_mark_timer', new_callable=AsyncMock, return_value=True))
        self.available = stack.enter_context(patch.object(
            utils, 'teleport_mark_is_available', new_callable=AsyncMock, return_value=True))

    async def test_new_mark_verified_before_success(self):
        self.available.side_effect = [False, True]
        self.assertTrue(await utils.ensure_teleport_mark(self.client))
        self.client.send_key.assert_awaited_once_with(utils.Keycode.PAGE_DOWN, 0.2)

    async def test_existing_mark_with_no_mana_change_is_not_success(self):
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 100, 3)):
            self.assertFalse(await utils.ensure_teleport_mark(self.client))
        self.assertEqual(
            [call.args for call in self.client.send_key.await_args_list],
            [
                (utils.Keycode.PAGE_DOWN, 0.2),
                (utils.Keycode.S, 3.0),
                (utils.Keycode.PAGE_DOWN, 0.2),
            ],
        )

    async def test_zone_change_while_moving_prevents_second_mark(self):
        self.available.return_value = True
        self.client.zone_name.side_effect = ['Original', 'Other']
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 100, 3)):
            self.assertFalse(await utils.ensure_teleport_mark(self.client))
        self.assertEqual(
            [call.args for call in self.client.send_key.await_args_list],
            [
                (utils.Keycode.PAGE_DOWN, 0.2),
                (utils.Keycode.S, 3.0),
            ],
        )

    async def test_cooldown_failure_prevents_recall_key(self):
        self.timer.return_value = False
        self.assertFalse(await utils.recall_to_teleport_mark(self.client, expected_zone='Destination'))
        self.available.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_recall_ignores_grayed_hud_state(self):
        events = []
        async def timer(c):
            events.append('timer')
            return True
        self.timer.side_effect = timer
        self.available.return_value = False
        self.client.zone_name.return_value = 'Shop'
        with patch.object(utils.time, 'monotonic', side_effect=[0, 0, 0, 13]):
            self.assertFalse(await utils.recall_to_teleport_mark(
                self.client, expected_zone='Destination', attempts=1))
        self.assertEqual(events, ['timer'])
        self.available.assert_not_awaited()
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_recall_confirms_destination(self):
        self.client.zone_name.side_effect = ['Shop', 'Destination', 'Destination']
        self.assertTrue(await utils.recall_to_teleport_mark(self.client, expected_zone='Destination'))
        self.assertEqual(
            [call.args for call in self.client.send_key.await_args_list],
            [(utils.Keycode.PAGE_UP, 0.1), (utils.Keycode.PAGE_UP, 0.1)],
        )

    async def test_recall_success_does_not_probe_hud_availability(self):
        self.available.return_value = False
        self.client.zone_name.side_effect = ['Shop', 'Destination', 'Destination']
        self.assertTrue(await utils.recall_to_teleport_mark(self.client, expected_zone='Destination'))
        self.available.assert_not_awaited()
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_wrong_destination_is_failure(self):
        self.client.zone_name.side_effect = ['Shop', 'Wrong', 'Wrong']
        self.assertFalse(await utils.recall_to_teleport_mark(
            self.client, expected_zone='Destination', attempts=1))
