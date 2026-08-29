import unittest

from src.config_combat import delegate_selected_combat_configs


class CombatTargetingTests(unittest.TestCase):
    def test_plain_style_applies_to_every_selected_client(self):
        assignments = delegate_selected_combat_configs(
            "any<damage> @ enemy",
            [1, 2],
            4,
        )
        self.assertEqual(assignments[1], "any<damage> @ enemy")
        self.assertEqual(assignments[2], "any<damage> @ enemy")

    def test_full_file_uses_global_client_numbers(self):
        assignments = delegate_selected_combat_configs(
            "### p1\nStyle One\n### p2\nStyle Two\n### p3\nStyle Three",
            [1, 2],
            3,
        )
        self.assertEqual(assignments[1], "Style Two")
        self.assertEqual(assignments[2], "Style Three")

    def test_small_file_uses_selected_group_order(self):
        assignments = delegate_selected_combat_configs(
            "### p1\nStyle One\n### p2\nStyle Two",
            [1, 2],
            4,
        )
        self.assertEqual(assignments[1], "Style One")
        self.assertEqual(assignments[2], "Style Two")

    def test_single_selected_client_accepts_p1_file(self):
        assignments = delegate_selected_combat_configs(
            "### p1\nSolo Style",
            [1],
            4,
        )
        self.assertEqual(assignments[1], "Solo Style")


if __name__ == "__main__":
    unittest.main()
