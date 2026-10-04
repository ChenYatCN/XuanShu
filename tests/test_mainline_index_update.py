import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.mainline_progress import match_quest, quest_rows
from src.questing import Quester


class MainlineIndexUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = quest_rows()
        self.darkmoor = [row for row in self.rows if row['world'].casefold() == 'darkmoor']

    def test_new_world_has_all_79_verified_key_identities(self):
        self.assertEqual(len(self.rows), 2094)
        self.assertEqual([row['number'] for row in self.darkmoor], list(range(1, 80)))
        for row in self.darkmoor:
            with self.subTest(number=row['number']):
                self.assertEqual(row['total'], 79)
                self.assertEqual(row['quest_ids'], [])
                self.assertTrue(row['keys'])
                for key in row['keys']:
                    self.assertIs(match_quest(self.rows, 0, key, ''), row)
                    self.assertIs(match_quest(self.rows, 0, key.upper(), 'unavailable'), row)

    def test_all_new_titles_resolve_with_world_context(self):
        for row in self.darkmoor:
            with self.subTest(number=row['number']):
                self.assertIs(match_quest(self.darkmoor, None, '', row['english']), row)
                for title in row['chinese']:
                    self.assertIs(match_quest(self.darkmoor, None, '', title), row)
        self.assertEqual(sum(bool(row['chinese']) for row in self.darkmoor), 77)

    def test_untranslated_titles_still_match_exact_game_keys_and_english(self):
        for number, code, title in (
                (5, 'QuestTitle_00002171', 'Panopt-outicon'),
                (53, 'QuestTitle_00002170', 'Were-Dunnit')):
            row = self.darkmoor[number - 1]
            self.assertEqual(row['chinese'], [])
            self.assertIs(match_quest(self.rows, None, code, ''), row)
            self.assertIs(match_quest(self.rows, None, '', title), row)

    def test_parallel_branches_are_distinct_and_keep_source_notes(self):
        for number, code in ((30, 'QuestTitle_19BE7D'), (31, 'QuestTitle_19BE81'),
                             (32, 'QuestTitle_19BE84')):
            row = self.darkmoor[number - 1]
            self.assertIn('interchangeable', row['note'])
            self.assertIs(match_quest(self.rows, 0, code, ''), row)
        self.assertIsNone(match_quest(self.rows, 0, '', 'The Survivors'))
        self.assertIsNone(match_quest(self.rows, 0, '', '幸存者'))
        self.assertIsNone(match_quest(self.rows, 0, 'UnknownTitle', 'Unindexed sidequest'))

    def make_offer(self):
        row = self.darkmoor[31]
        other = next(item for item in self.rows if item['world'].casefold() != 'darkmoor'
                     and match_quest([item], None, '', 'The Survivors') is not None)
        quests = {
            42: SimpleNamespace(mainline=AsyncMock(return_value=True),
                                name_lang_key=AsyncMock(return_value=row['keys'][0])),
            99: SimpleNamespace(mainline=AsyncMock(return_value=True),
                                name_lang_key=AsyncMock(return_value=other['keys'][0])),
        }
        client = SimpleNamespace(
            title='p1', zone_name=AsyncMock(return_value='Darkmoor/DM_Z02_MortalPlain'),
            root_window=SimpleNamespace(),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value=quests))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='幸存者')),
        )
        quester = Quester(client, [client], None)
        dialog = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        quester._visible_window_nodes = AsyncMock(return_value=[
            (object(), ('WorldView', 'wndDialogMain', 'txtQuestTitle'))])
        quester._window_text = AsyncMock(return_value='幸存者')
        return client, quester, dialog, row, quests

    async def test_same_title_offer_uses_world_then_verified_live_key(self):
        client, quester, dialog, row, _ = self.make_offer()
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=dialog)):
            self.assertEqual(await quester._mainline_offer_candidate(client), (42, row))

    async def test_same_title_outside_both_worlds_remains_ambiguous(self):
        client, quester, dialog, _, _ = self.make_offer()
        client.zone_name.return_value = 'Arcanum/Area'
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=dialog)):
            self.assertIsNone(await quester._mainline_offer_candidate(client, allow_unowned=True))

    async def test_same_title_unowned_darkmoor_offer_requires_explicit_permission(self):
        client, quester, dialog, row, quests = self.make_offer()
        quests.clear()
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=dialog)):
            self.assertIsNone(await quester._mainline_offer_candidate(client))
            self.assertEqual(await quester._mainline_offer_candidate(client, allow_unowned=True), (None, row))

    async def test_unreadable_world_keeps_same_title_offer_unresolved(self):
        client, quester, dialog, _, _ = self.make_offer()
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=dialog)):
            client.zone_name.return_value = None
            self.assertIsNone(await quester._mainline_offer_candidate(client, allow_unowned=True))
            client.zone_name.side_effect = RuntimeError('transition memory unavailable')
            self.assertIsNone(await quester._mainline_offer_candidate(client, allow_unowned=True))


if __name__ == '__main__':
    unittest.main()
