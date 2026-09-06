import unittest

from src.quest_party import (
    friend_follow_retry_delay,
    resolve_quest_party,
    resolve_quester_friend_icon,
)


class Client:
    def __init__(self, title, nickname=None, gid=None):
        self.title = title
        self.account_nick = nickname
        self.player_gid = gid


class QuestPartyTests(unittest.TestCase):
    def test_friend_icon_fallback_uses_persistent_identity(self):
        quester = Client("p1", nickname="main")
        self.assertEqual(
            resolve_quester_friend_icon(
                quester,
                {"account:main": {"icon_list": 2, "icon_index": 17}},
            ),
            (2, 17),
        )

    def test_invalid_friend_icon_is_ignored(self):
        quester = Client("p1")
        self.assertIsNone(
            resolve_quester_friend_icon(
                quester,
                {"p1": {"icon_list": 3, "icon_index": -1}},
            )
        )

    def setUp(self):
        self.clients = [Client("p1"), Client("p2"), Client("p3"), Client("p4")]

    def test_legacy_mode_keeps_every_client_questing(self):
        party = resolve_quest_party(
            self.clients,
            enabled=False,
            quester_titles=[],
            hitter_titles=[],
        )
        self.assertEqual([c.title for c in party.questers], ["p1", "p2", "p3", "p4"])
        self.assertEqual(party.hitters, [])

    def test_party_mode_filters_roles_and_leaves_unselected_idle(self):
        party = resolve_quest_party(
            self.clients,
            enabled=True,
            quester_titles=["p1", "p3"],
            hitter_titles=["p2"],
        )
        self.assertEqual([c.title for c in party.questers], ["p1", "p3"])
        self.assertEqual([c.title for c in party.hitters], ["p2"])
        self.assertEqual([c.title for c in party.idle], ["p4"])

    def test_hitters_are_assigned_round_robin(self):
        party = resolve_quest_party(
            self.clients,
            enabled=True,
            quester_titles=["p1", "p2"],
            hitter_titles=["p3", "p4"],
        )
        self.assertEqual(
            [(h.title, q.title) for h, q in party.hitter_assignments],
            [("p3", "p1"), ("p4", "p2")],
        )

    def test_quester_role_wins_if_both_are_selected(self):
        party = resolve_quest_party(
            self.clients,
            enabled=True,
            quester_titles=["p1"],
            hitter_titles=["p1", "p2"],
        )
        self.assertEqual([c.title for c in party.questers], ["p1"])
        self.assertEqual([c.title for c in party.hitters], ["p2"])

    def test_missing_saved_clients_are_not_reassigned(self):
        party = resolve_quest_party(
            self.clients[:2],
            enabled=True,
            quester_titles=["p4"],
            hitter_titles=["p2"],
        )
        self.assertEqual(party.questers, [])
        self.assertEqual([c.title for c in party.hitters], ["p2"])
        self.assertEqual(party.hitter_assignments, [])

    def test_account_identity_survives_p_title_reordering(self):
        clients = [
            Client("p1", "second", 202),
            Client("p2", "main", 101),
        ]
        party = resolve_quest_party(
            clients,
            enabled=True,
            quester_titles=["gid:101"],
            hitter_titles=["account:second"],
        )
        self.assertEqual([c.title for c in party.questers], ["p2"])
        self.assertEqual([c.title for c in party.hitters], ["p1"])

    def test_manual_assignment_uses_persistent_identities(self):
        clients = [
            Client("p1", "quest-a", 11),
            Client("p2", "quest-b", 22),
            Client("p3", "hitter", 33),
        ]
        party = resolve_quest_party(
            clients,
            enabled=True,
            quester_titles=["gid:11", "gid:22"],
            hitter_titles=["gid:33"],
            assignment_mode="manual",
            manual_assignments={"gid:33": "gid:22"},
        )
        self.assertEqual(
            [(h.title, q.title) for h, q in party.hitter_assignments],
            [("p3", "p2")],
        )

    def test_invalid_manual_assignment_falls_back_to_round_robin(self):
        party = resolve_quest_party(
            self.clients,
            enabled=True,
            quester_titles=["p1", "p2"],
            hitter_titles=["p3"],
            assignment_mode="manual",
            manual_assignments={"p3": "missing"},
        )
        self.assertEqual(
            [(h.title, q.title) for h, q in party.hitter_assignments],
            [("p3", "p1")],
        )

    def test_friend_follow_retry_delay_is_bounded(self):
        self.assertEqual(
            [friend_follow_retry_delay(i) for i in range(1, 7)],
            [2.0, 5.0, 10.0, 20.0, 30.0, 30.0],
        )


if __name__ == "__main__":
    unittest.main()
