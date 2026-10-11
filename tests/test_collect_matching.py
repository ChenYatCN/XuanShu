import unittest

from src.collect_matching import collect_names, CollectNames, parse_collect_goal, count_increased
from src.teleport_math import calc_chunks, calc_Distance
from wizwalker import XYZ


class CollectMatchingTests(unittest.TestCase):
    def setUp(self):
        self.names = collect_names()

    def test_screenshot_target_matches_english_without_same_code(self):
        self.assertEqual(self.names.score('海洋泡沫水晶', 'Sea Foam Crystal', 'Different_999'), 100)

    def test_english_plural_and_internal_name(self):
        self.assertEqual(self.names.score('Sea Foam Crystals', '海洋泡沫水晶'), 100)
        self.assertGreaterEqual(self.names.score('海洋泡沫水晶', internal='CL-SeaFoam-Crystal_03'), 85)

    def test_leyden_jar_screenshot_names_and_goal(self):
        goal = parse_collect_goal('聚集 莱顿瓶 地点：科学中心 (2 of 3)')
        self.assertEqual((goal.target, goal.location, goal.current, goal.total),
                         ('莱顿瓶', '科学中心', 2, 3))
        for title in ('LEYDEN JAR', 'Leyden Jars'):
            self.assertEqual(self.names.score(goal.target, title, 'DifferentLanguageId_123'), 100)
        self.assertGreaterEqual(self.names.score(goal.target, internal='CL-LeydenJars'), 85)
        for code in ('WCNameplates_00000061', 'WizItems_00001647'):
            self.assertEqual(self.names.score(goal.target, code=code), 100)
        self.assertEqual(self.names.score(goal.target, 'Sea Foam Crystal', internal='CL-SeaFoam-Crystal'), 0)

    def test_reused_language_id_cannot_override_conflicting_live_text(self):
        self.assertEqual(self.names.score('海洋泡沫水晶', 'Stone Door', 'WizardGameObjects_00000255'), 0)

    def test_missing_label_can_use_known_id_but_unknown_does_not_guess(self):
        self.assertEqual(self.names.score('海洋泡沫水晶', code='WizardGameObjects_00000255'), 100)
        self.assertEqual(self.names.score('海洋泡沫水晶', code='unknown', internal='CL-Guard'), 0)

    def test_different_tables_can_share_translated_name(self):
        names = CollectNames([['Object_1', 'Crystal', '水晶'], ['Item_2', 'Crystal', '晶石']])
        self.assertEqual(names.score('晶石', 'Crystal', 'Object_999'), 100)
        self.assertEqual(names.score('水晶', '晶石', 'Item_2'), 100)

    def test_counts_and_location_are_separate_from_target(self):
        for count in ('(0 of 4)', '(0/4)', '（0／4）'):
            goal = parse_collect_goal('收集 海洋泡沫水晶 地点：漂浮大陆 ' + count)
            self.assertEqual((goal.target, goal.location, goal.current, goal.total), ('海洋泡沫水晶', '漂浮大陆', 0, 4))

    def test_find_without_counter_remains_supported(self):
        self.assertEqual(parse_collect_goal('Find Submarine Parts in The Floating Land').target, 'Submarine Parts')
        self.assertEqual(parse_collect_goal('寻找 潜艇零件 地点：漂浮大陆').target, '潜艇零件')

    def test_gather_translation_preserves_target_and_progress(self):
        goal = parse_collect_goal('聚集 水晶 地点：科学中心 (0 of 3)')
        self.assertEqual((goal.target, goal.location, goal.current, goal.total), ('水晶', '科学中心', 0, 3))
        self.assertTrue(count_increased(goal, parse_collect_goal('聚集 水晶 地点：科学中心 (1 of 3)')))
        self.assertIsNone(parse_collect_goal('聚焦 水晶 地点：科学中心 (0 of 3)'))

    def test_additional_verified_language_table_actions(self):
        # WizardQuestGoals: 8, 9, 56, 174, 196, 198, 300, 416, 564, 590, 614, 767.
        for action in ('搜索', '获得', '恢复', '捕获', '拿取', '毁坏', '打破', '偷', '取', '得到', '检索', '抓'):
            with self.subTest(action=action):
                goal = parse_collect_goal(f'{action} 水晶 地点：海滩 (0 of 4)')
                self.assertEqual((goal.target, goal.current, goal.total), ('水晶', 0, 4))
        self.assertEqual(parse_collect_goal('Retrieve the Crystal in Beach (0/4)').target, 'the Crystal')
        self.assertIsNone(parse_collect_goal('取悦 守卫 地点：海滩 (0/4)'))

    def test_combat_and_dialogue_are_not_ground_collects(self):
        for text in ('Defeat and Collect Crystals in Beach (0 of 4)', '击败并收集 水晶 地点：海滩 (0 of 4)', 'Talk to Guard in Beach', '收集 水晶 地点：海滩 (5/4)'):
            self.assertIsNone(parse_collect_goal(text), text)

    def test_counter_change_requires_same_goal_and_total(self):
        before = parse_collect_goal('Collect Crystal in Beach (0/4)')
        self.assertTrue(count_increased(before, parse_collect_goal('Collect Crystal in Beach (1/4)')))
        for text in ('Collect Crystal in Beach (0/4)', 'Collect Crystal in Cave (1/4)', 'Collect Stone in Beach (1/4)', 'Collect Crystal in Beach (1/5)'):
            self.assertFalse(count_increased(before, parse_collect_goal(text)))


class CollectRegionTests(unittest.TestCase):
    def test_small_map_produces_search_region_at_map_height(self):
        points = [XYZ(100, -200, 1300), XYZ(200, -100, 1300)]
        chunks = calc_chunks(points)
        self.assertEqual(len(chunks), 1)
        self.assertEqual((chunks[0].x, chunks[0].y, chunks[0].z), (150, -150, 1300))

    def test_irregular_maps_and_separate_floors_are_covered(self):
        points = [XYZ(x, y, z) for x, y in [(-20000, 15000), (-500, -17000), (600, -2100), (12000, 8500)] for z in (-1500, 0, 6000)]
        chunks = calc_chunks(points)
        for point in points:
            self.assertLessEqual(min(calc_Distance(point, XYZ(c.x, c.y, c.z - 550)) for c in chunks), 3147)

    def test_empty_map_and_invalid_radius(self):
        self.assertEqual(calc_chunks([]), [])
        with self.assertRaises(ValueError):
            calc_chunks([XYZ(0, 0, 0)], 0)
