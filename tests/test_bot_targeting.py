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

    def test_targeted_payload_uses_numeric_client_order(self):
        text, requested = unpack_bot_command(
            {"text": "sleep 1", "clients": ["p3", "p2", "p3"]}
        )
        self.assertEqual(text, "sleep 1")
        self.assertEqual(requested, ("p3", "p2"))
        self.assertEqual(
            [client.title for client in resolve_bot_clients(self.clients, requested)],
            ["p2", "p3"],
        )

    def test_shuffled_discovery_preserves_script_and_playstyle_roles(self):
        from src.config_combat import delegate_combat_configs
        shuffled = [self.clients[i] for i in (1, 3, 0, 2)]
        configs = delegate_combat_configs('### p1\none\n### p2\ntwo\n### p3\nthree\n### p4\nfour')
        for requested in (None, ('p2', 'p4', 'p1', 'p3')):
            ordered = resolve_bot_clients(shuffled, requested)
            self.assertEqual(ordered, self.clients)
            self.assertEqual({c.title: configs[i] for i, c in enumerate(ordered)},
                             dict(p1='one', p2='two', p3='three', p4='four'))
        self.assertEqual([c.title for c in shuffled], ['p2', 'p4', 'p1', 'p3'])

    def test_p10_sorts_after_p2(self):
        clients = [SimpleNamespace(title=t) for t in ('p10', 'p2', 'p1')]
        self.assertEqual([c.title for c in resolve_bot_clients(clients, None)],
                         ['p1', 'p2', 'p10'])

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
