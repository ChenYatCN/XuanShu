import unittest
from types import SimpleNamespace

from src.bot_targeting import (
    bot_group_key,
    normalize_client_titles,
    overlapping_bot_groups,
    resolve_bot_clients,
    unpack_bot_command,
)


class BotTargetingTests(unittest.TestCase):
    def setUp(self):
        self.clients = [SimpleNamespace(title=f"p{i}") for i in range(1, 5)]

    def test_legacy_payload_targets_all_clients(self):
        text, requested = unpack_bot_command("mass sendkey X, 0.1")
        self.assertEqual(text, "mass sendkey X, 0.1")
        self.assertIsNone(requested)
        self.assertEqual(resolve_bot_clients(self.clients, requested), self.clients)

    def test_targeted_payload_preserves_live_client_order(self):
        text, requested = unpack_bot_command(
            {"text": "sleep 1", "clients": ["p3", "p2", "p3"]}
        )
        self.assertEqual(text, "sleep 1")
        self.assertEqual(requested, ("p3", "p2"))
        self.assertEqual(
            [client.title for client in resolve_bot_clients(self.clients, requested)],
            ["p2", "p3"],
        )

    def test_empty_selection_targets_nobody(self):
        self.assertEqual(normalize_client_titles([]), ())
        self.assertEqual(resolve_bot_clients(self.clients, ()), [])

    def test_group_overlap_controls_replacement_and_stop(self):
        groups = [("p1",), ("p2", "p3")]
        self.assertEqual(overlapping_bot_groups(groups, ["p2"]), [("p2", "p3")])
        self.assertEqual(overlapping_bot_groups(groups, ["p4"]), [])
        self.assertEqual(overlapping_bot_groups(groups, None), groups)
        self.assertEqual(bot_group_key(self.clients[1:3]), ("p2", "p3"))


if __name__ == "__main__":
    unittest.main()
