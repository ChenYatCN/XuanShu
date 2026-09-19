import unittest

from src.ibao_locations import DEFAULT_LOCATIONS, LOCATION_ALIASES, expand_location_aliases
from src.ibao_runtime import parse_locations


class LocationAliasTests(unittest.TestCase):
    def test_defaults_share_coordinates_and_type(self):
        locations, names = parse_locations(DEFAULT_LOCATIONS)
        for english, chinese in LOCATION_ALIASES:
            self.assertEqual(locations[names.index(english)][1:],
                             locations[names.index(chinese)][1:])

    def test_saved_configs_work_both_ways(self):
        for name in ('The Commons', '广场'):
            locations, names = parse_locations(f'{name}:|Location|1,2,3')
            self.assertIn('The Commons', names)
            self.assertIn('广场', names)
            self.assertEqual(locations[0][2], locations[1][2])

    def test_explicit_override_and_unknown_name_are_preserved(self):
        text = 'The Commons|Location|1,2,3\n广场|Location|4,5,6\n自定义|Dungeon|'
        self.assertEqual(expand_location_aliases(text), text)

    def test_idempotent_and_preserves_points(self):
        text = 'Kembaalung Village:Hollow Mountain|Dungeon|1,2,3:4,5,6'
        expanded = expand_location_aliases(text)
        self.assertEqual(expand_location_aliases(expanded), expanded)
        self.assertEqual(expanded.split('|')[1:], text.split('|')[1:])
        self.assertIn('肯巴隆村', expanded)
        self.assertIn('中空的山', expanded)
