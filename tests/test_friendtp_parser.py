import unittest

from src.deimoslang.parser import Parser, ParserError
from src.deimoslang.tokenizer import Tokenizer
from src.deimoslang.types import CommandKind, IdentExpression, TeleportKind


def parse_friendtp(source):
    statements = Parser(Tokenizer().tokenize(source)).parse()
    return statements[0].command


class FriendTeleportParserTests(unittest.TestCase):
    def test_quoted_names_preserve_text_without_quotes(self):
        for name in ("伊恩", "Malorn ThunderTail", "icon"):
            for quote in ('"', "'"):
                with self.subTest(name=name, quote=quote):
                    command = parse_friendtp(f"mass friendtp {quote}{name}{quote}")
                    self.assertEqual(command.kind, CommandKind.teleport)
                    self.assertEqual(command.data, [TeleportKind.friend_name, name])
                    self.assertTrue(command.player_selector.mass)

    def test_quoted_name_keeps_selected_client(self):
        command = parse_friendtp('p2 friendtp "伊恩"')
        self.assertEqual(command.player_selector.player_nums, [2])
        self.assertEqual(command.data, [TeleportKind.friend_name, "伊恩"])

    def test_icon_keyword_keeps_original_fish_icon_mode(self):
        command = parse_friendtp("mass friendtp icon")
        self.assertEqual(command.data, [TeleportKind.friend_icon])

    def test_legacy_unquoted_name_remains_compatible(self):
        command = parse_friendtp("mass friendtp Malorn ThunderTail")
        self.assertEqual(
            command.data, [TeleportKind.friend_name, "Malorn ThunderTail"]
        )

    def test_legacy_identifier_can_still_resolve_a_constant(self):
        command = parse_friendtp("mass friendtp target")
        self.assertIsInstance(command.data[1], IdentExpression)
        self.assertEqual(command.data[1].ident, "target")

    def test_trailing_arguments_after_quoted_name_are_rejected(self):
        with self.assertRaises(ParserError):
            parse_friendtp('mass friendtp "伊恩" extra')


if __name__ == "__main__":
    unittest.main()
