import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src.mainline_progress import match_quest, log_mainline_progress
from src.collect_matching import CollectNames


class MainlineTests(unittest.IsolatedAsyncioTestCase):
    def test_id_key_bilingual_notes_and_ambiguity(self):
        row = dict(world='celestia(100)', number=27, english='Example Quest (returns to previous)',
                   keys=['Quest_123'], quest_ids=[42], chinese=[], aliases=['Old Example'])
        self.assertIs(match_quest([row], 42, '', ''), row)
        self.assertIs(match_quest([row], 0, 'Quest_123', ''), row)
        names = CollectNames([['Quest_123', 'Example Quest', '示例任务']])
        self.assertIs(match_quest([row], 0, '', '示例任务', names), row)
        self.assertIs(match_quest([row], 0, '', 'Old Example'), row)
        self.assertIsNone(match_quest([row, dict(row, number=28)], 0, '', 'Example Quest'))
        self.assertIsNone(match_quest([row], 99, '', 'Collect Example Quest'))

    async def test_logs_once_per_identity_not_objective(self):
        quest = SimpleNamespace(mainline=AsyncMock(return_value=True), name_lang_key=AsyncMock(return_value='Quest_123'))
        manager = SimpleNamespace(quest_data=AsyncMock(return_value={42: quest, 99: quest}))
        client = SimpleNamespace(title='p2', quest_id=AsyncMock(return_value=42),
            quest_manager=AsyncMock(return_value=manager),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(return_value='示例任务')))
        with patch('src.mainline_progress.installed_catalog', return_value=None), \
             patch('src.mainline_progress.quest_rows', return_value=[]), \
             patch('src.mainline_progress.logger') as log:
            await log_mainline_progress(client)
            await log_mainline_progress(client)
            self.assertEqual(log.info.call_count, 1)
            self.assertIn('未匹配', log.info.call_args.args[0])
            client.quest_id.return_value = 99
            await log_mainline_progress(client)
            self.assertEqual(log.info.call_count, 2)
            client.quest_id.side_effect = RuntimeError('unavailable')
            await log_mainline_progress(client)
