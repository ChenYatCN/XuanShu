"""Release/rescue goals through the existing parser and collection workflow."""
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.collect_matching import collect_names, parse_collect_goal
from src.collecting import collect_one
import test_collecting as collect_fixtures


class FreeGoalParsingTests(unittest.TestCase):
    def test_localized_free_goal_preserves_miner_location_and_zero_of_five(self):
        goal = parse_collect_goal('自由 矿工 地点：寒冷的哨 (0/5)')
        self.assertIsNotNone(goal)
        self.assertEqual((goal.target, goal.location, goal.current, goal.total),
                         ('矿工', '寒冷的哨', 0, 5))

    def test_verified_free_release_rescue_action_records(self):
        for action in ('Free', 'Release', 'Rescue'):
            with self.subTest(action=action):
                goal = parse_collect_goal(f'{action} Miners in Frigid Maw (0 of 5)')
                self.assertIsNotNone(goal)
                self.assertEqual((goal.target, goal.location, goal.current, goal.total),
                                 ('Miners', 'Frigid Maw', 0, 5))
        for action in ('自由', '释放', '釋放', '解救', '救援'):
            with self.subTest(action=action):
                goal = parse_collect_goal(f'{action} 矿工 地点：寒冷的哨 （0／5）')
                self.assertIsNotNone(goal)
                self.assertEqual((goal.target, goal.current, goal.total), ('矿工', 0, 5))

    def test_free_does_not_parse_freeze_or_combat(self):
        for text in ('Freeze Miner in Frigid Maw (0/5)', 'Freezer Parts in Frigid Maw (0/5)',
                     'Defeat and Free Miner in Frigid Maw (0/5)'):
            self.assertIsNone(parse_collect_goal(text))

    def test_actual_template_language_code_matches_without_internal_alias_rule(self):
        # Root.wad ObjectData/PL/PL_CharmedMiner.xml references Items_00016351;
        # the existing language table resolves this to Miner / 矿工.
        names = collect_names()
        self.assertEqual(names.score('矿工', 'Miner', 'Items_00016351', 'PL_CharmedMiner'), 100)
        self.assertEqual(names.score('矿工', '', 'Items_00016351', 'PL_CharmedMiner'), 100)
        self.assertEqual(names.score('矿工', 'Stone', 'Items_00016351', 'PL_CharmedMiner'), 0)


class FreeEntityWorkflowTests(unittest.IsolatedAsyncioTestCase):
    entity = collect_fixtures.CollectWorkflowTests.entity

    def setUp(self):
        collect_fixtures.CollectWorkflowTests.setUp(self)
        self.target_title = 'Miner'
        self.position = XYZ(4000, 3494.322, -4763.765)
        self.client.title = 'p3'
        self.client.zone_name.return_value = 'Polaris/PL_Z05_FrigidMine'
        self.client.cache_handler.get_langcode_name.return_value = 'Miner'
        self.quester.read_quest_txt.side_effect = lambda _: f'自由 矿工 地点：寒冷的哨 ({self.count}/5)'
        self.miners = []
        self.released = []
        self.moves = []
        for i in range(5):
            entity = self.entity(template_id=812, x=6059.338 + i * 2000)
            entity.object_template.return_value.object_name.return_value = 'PL_CharmedMiner'
            entity.object_template.return_value.display_name.return_value = 'Items_00016351'
            entity.location.return_value = XYZ(6059.338 + i * 2000, 3494.322, -4763.765)
            self.miners.append(entity)
        self.client.get_base_entity_list.return_value = self.miners
        async def approach(client, target, **kwargs):
            self.moves.append(target)
            self.position = target
        self.stack.enter_context(patch('src.collecting.collision_tp', approach))
        async def release(key, seconds):
            self.assertEqual((key, seconds), (Keycode.X, .1))
            distances = [(abs(self.position.x - e.location.return_value.x), i)
                         for i, e in enumerate(self.miners)]
            _, chosen = min(distances)
            self.assertNotIn(chosen, self.released)
            self.released.append(chosen)
            self.count += 1
        self.client.send_key.side_effect = release

    async def test_selects_actual_miner_approaches_presses_x_and_confirms_one_of_five(self):
        self.assertTrue(await collect_one(self.quester, self.client))
        self.assertEqual(self.count, 1)
        self.assertEqual(self.released, [0])
        self.assertEqual(self.moves, [self.miners[0].location.return_value])
        self.other.send_key.assert_not_awaited()

    async def test_progress_rescans_other_entities_and_does_not_repeat_finished_one(self):
        # Finished miners deliberately remain in the loaded entity list, so
        # the existing recent-object state must carry across collect_one calls.
        for expected in range(1, 6):
            self.assertTrue(await collect_one(self.quester, self.client))
            self.assertEqual(self.count, expected)
        self.assertEqual(self.released, [0, 1, 2, 3, 4])
        self.assertFalse(await collect_one(self.quester, self.client))
        self.assertEqual(self.client.send_key.await_count, 5)

    async def test_known_display_code_handles_missing_live_label(self):
        self.client.cache_handler.get_langcode_name.return_value = ''
        self.assertTrue(await collect_one(self.quester, self.client))
        self.assertEqual(self.released, [0])

    async def test_unrelated_popup_does_not_release_entity(self):
        self.target_title = 'Stone Door'
        self.assertFalse(await collect_one(self.quester, self.client))
        self.assertEqual(self.released, [])
        self.client.send_key.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
