import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from loguru import logger
from tests.test_nightmare_krok_recovery import NightmareKrokRecoveryTests
from tests.test_darkmoor_castle_route import DarkmoorCastleRouteTests
from tests.test_power_core_recovery import PowerCoreRecoveryTests
logger.remove()
old_nightmare = NightmareKrokRecoveryTests.setUp
def nightmare_setup(self):
    old_nightmare(self)
    self.client.refilling_potions = False
NightmareKrokRecoveryTests.setUp = nightmare_setup
old_castle = DarkmoorCastleRouteTests.setUp
def castle_setup(self):
    old_castle(self)
    self.client.quest_id = AsyncMock(return_value=123)
    self.client.goal_id = AsyncMock(side_effect=lambda: self.goal)
DarkmoorCastleRouteTests.setUp = castle_setup
names = [
 'test_cancellation_releases_lock', 'test_failed_point_retries_twice_without_x_or_looping',
 'test_failed_x_is_retried_once_at_the_same_point',
 'test_final_tp_error_after_zone_change_still_waits_for_stability',
 'test_final_zone_timeout_releases_lock', 'test_loading_must_finish_and_new_zone_stabilize',
 'test_loading_without_zone_change_times_out', 'test_quest_progress_resets_stall_window',
 'test_strict_twelve_point_order_then_exit_and_stable_zone'
]
suite = unittest.TestSuite(NightmareKrokRecoveryTests(n) for n in names)
suite.addTest(DarkmoorCastleRouteTests('test_dialogue_worker_records_short_page_between_route_ticks'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('No production code changed; only concrete missing client fields supplied.')
raise SystemExit(not result.wasSuccessful())
