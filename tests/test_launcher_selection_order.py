import unittest

from src.gui.tab_launcher import update_account_selection_order


class LauncherSelectionOrderTests(unittest.TestCase):
    def test_tracks_click_order_instead_of_row_order(self):
        order = []
        update_account_selection_order(order, "third-row", True)
        update_account_selection_order(order, "first-row", True)
        update_account_selection_order(order, "second-row", True)
        self.assertEqual(order, ["third-row", "first-row", "second-row"])

    def test_uncheck_removes_and_recheck_moves_to_end(self):
        order = ["a", "b", "c"]
        update_account_selection_order(order, "b", False)
        update_account_selection_order(order, "b", True)
        self.assertEqual(order, ["a", "c", "b"])

    def test_duplicate_checked_signal_does_not_duplicate_account(self):
        order = ["a", "b"]
        update_account_selection_order(order, "a", True)
        self.assertEqual(order, ["b", "a"])


if __name__ == "__main__":
    unittest.main()
