import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.mainline_progress import match_quest, quest_rows, log_mainline_progress
from src.questing import Quester


class MainlineAlignmentTests(unittest.IsolatedAsyncioTestCase):
    def test_every_verified_key_matches_its_row_with_world_context(self):
        rows = quest_rows()
        for row in rows:
            for code in row['keys']:
                with self.subTest(world=row['world'], number=row['number'], code=code):
                    self.assertIs(match_quest(rows, None, code, '', world=row['world']), row)

    def test_source_numbering_and_only_unresolved_tutorial_are_preserved(self):
        worlds = defaultdict(list)
        for row in quest_rows():
            worlds[row['world']].append(row)
        for world, rows in worlds.items():
            self.assertEqual(sorted(row['number'] for row in rows), list(range(1, len(rows) + 1)), world)
            self.assertEqual({row['total'] for row in rows}, {len(rows)}, world)
        self.assertEqual([(row['world'], row['number']) for row in quest_rows() if not row['keys']],
                         [('wizard city', 1)])

    def test_reviewed_spelling_aliases_match_without_replacing_workbook_titles(self):
        rows = quest_rows()
        for world, number, title in (
            ('marleybone', 7, 'Springing the Snitch'),
            ('polaris', 16, 'Storming the Basstille'),
            ('empyrea', 51, 'Tunnel Visions'),
            ('mirage', 117, 'Grandfather Spider'),
            ('zafariA', 130, "Knockin on Kallah's Door"),
            ('azteca', 182, 'Strong Smooth Words'),
            ('Khrysalis', 159, 'Thy Glittering Eye'),
        ):
            row = match_quest(rows, None, '', title, world=world)
            self.assertEqual((row['world'], row['number']), (world, number))
            self.assertIn(title, row['aliases'])

    def test_shared_mission_impossible_requires_known_world(self):
        rows = quest_rows()
        self.assertIsNone(match_quest(rows, None, 'QuestTitle_80B56', '棘手的任务'))
        self.assertIsNone(match_quest(rows, None, 'QuestTitle_80B56', '棘手的任务', world='Arcanum'))
        for world, number in (('zafariA', 106), ('mirage', 64)):
            row = match_quest(rows, None, 'QuestTitle_80B56', '棘手的任务', world=world)
            self.assertEqual((row['world'], row['number']), (world, number))

    def test_world_never_overrides_verified_id_or_unique_key(self):
        rows = quest_rows()
        row = match_quest(rows, 147211413153260846, 'QuestTitle_AB2D5', 'Monkey Business', world='azteca')
        self.assertEqual((row['world'], row['number']), ('novus', 56))
        row = match_quest(rows, None, 'QuestTitle_80B3E', '大象游行', world='mirage')
        self.assertEqual((row['world'], row['number']), ('zafariA', 84))
        duplicate_ids = [dict(rows[0], quest_ids=[42]), dict(rows[1], quest_ids=[42])]
        self.assertIsNone(match_quest(duplicate_ids, 42, '', '', world=rows[0]['world']))

    async def test_current_waterfront_mission_is_not_sent_back_to_finder(self):
        quest_id = 42  # Mock identity; no production Quest ID is inferred from the image.
        quest = SimpleNamespace(name_lang_key=AsyncMock(return_value='QuestTitle_80B56'),
                                mainline=AsyncMock(return_value=True))
        client = SimpleNamespace(title='p1', quest_id=AsyncMock(return_value=quest_id),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={quest_id: quest}))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='棘手的任务')),
            zone_name=AsyncMock(return_value='Zafaria/ZF_Z08_Waterfront'),
            questing_status=True, mainline_finder_enabled=True, quest_recovery_owner=None,
            root_window=SimpleNamespace(children=AsyncMock(return_value=[])))
        quester = Quester(client, [client], None)
        identity = await quester._mainline_identity(client)
        self.assertEqual((identity[3]['world'], identity[3]['number']), ('zafariA', 106))
        quester._run_mainline_finder = AsyncMock()
        with patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)):
            self.assertFalse(await quester._maybe_recover_mainline(client))
        quester._run_mainline_finder.assert_not_awaited()
        with patch('src.mainline_progress.logger'):
            await log_mainline_progress(client)
        self.assertEqual(client._xuanshu_mainline_progress, 'p1 · zafariA 主线 106/148')


if __name__ == '__main__':
    unittest.main()
