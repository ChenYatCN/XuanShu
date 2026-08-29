import unittest
from types import SimpleNamespace

from src.deimoslang.types import PlayerSelector
from src.deimoslang.vm import VM


class DeimosVMTargetingTests(unittest.TestCase):
    @staticmethod
    def make_vm(*, group_any_as_mass: bool):
        vm = object.__new__(VM)
        vm._clients = [SimpleNamespace(title=f"p{i}") for i in range(1, 4)]
        vm._any_player_client = []
        vm._group_any_as_mass = group_any_as_mass
        return vm

    @staticmethod
    def any_selector():
        selector = PlayerSelector()
        selector.any_player = True
        return selector

    def test_targeted_group_any_action_selects_entire_group(self):
        vm = self.make_vm(group_any_as_mass=True)

        self.assertEqual(
            vm._select_action_players(self.any_selector()),
            vm._clients,
        )

    def test_legacy_any_action_keeps_original_first_client_fallback(self):
        vm = self.make_vm(group_any_as_mass=False)

        self.assertEqual(
            vm._select_action_players(self.any_selector()),
            vm._clients[:1],
        )

    def test_legacy_any_action_keeps_condition_matches(self):
        vm = self.make_vm(group_any_as_mass=False)
        vm._any_player_client = [vm._clients[1]]

        self.assertEqual(
            vm._select_action_players(self.any_selector()),
            [vm._clients[1]],
        )


if __name__ == "__main__":
    unittest.main()
