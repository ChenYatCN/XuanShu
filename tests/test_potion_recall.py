import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
        self.mocks['buy_potions'].assert_awaited_once_with(self.client, True, original_zone='Original')

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
            self.client, recall=True, original_zone='Original')


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
