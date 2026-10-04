import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build_mainline_index as builder


class BuildMainlineIndexTests(unittest.TestCase):
    def test_default_uses_updated_workbook_and_closes_reader(self):
        workbook = SimpleNamespace(
            active=SimpleNamespace(iter_rows=Mock(return_value=[
                ('Arc5',), ('darkmoor(2)',), ('1. First Quest',),
                ('2. Next Quest (returns to previous)',)])), close=Mock())
        with patch.object(builder, 'load_workbook', return_value=workbook) as load:
            rows, mismatches = builder.workbook_rows()
        load.assert_called_once_with(builder.ROOT / 'W101 Mainline Quests.xlsx',
                                     read_only=True, data_only=True)
        workbook.close.assert_called_once()
        self.assertEqual([row['number'] for row in rows], [1, 2])
        self.assertEqual(rows[1]['note'], 'returns to previous')
        self.assertEqual(mismatches, [])

    def test_regeneration_preserves_verified_overrides_and_enriches_only_new_rows(self):
        old = dict(world='novus', number=56, total=88, english='Monkey Business',
                   quest_ids=[147211413153260846], keys=['QuestTitle_17D615'],
                   chinese=['猴子生意'], aliases=['Known old name'], note='Verified live identity.')
        new = dict(world='darkmoor', number=1, total=79, english='A Rough Start',
                   quest_ids=[], keys=[], chinese=[], aliases=[], note='')
        fresh_old = dict(old, number=57, total=89, quest_ids=[], keys=[], chinese=[], aliases=[], note='')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            previous, output, lang = directory / 'previous.json', directory / 'output.json', directory / 'QuestTitle.lang'
            previous.write_text(json.dumps({'rows': [old]}, ensure_ascii=False), encoding='utf-8')
            lang.write_bytes(('1:QuestTitle\r\n17D615\r\nMonkey Business\r\n猴子生意\r\n'
                              '155196\r\nMonkey Business\r\n胡闹\r\n'
                              '00002154\r\nA Rough Start\r\n艰难的开始\r\n').encode('utf-16'))
            with patch.object(builder, 'workbook_rows', return_value=([fresh_old, new], [])):
                data = builder.build(lang, Path('Updated.xlsx'), output, previous)
            persisted = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(persisted, data)
        self.assertEqual(data['source'], 'Updated.xlsx')
        for field in ('quest_ids', 'keys', 'chinese', 'aliases', 'note'):
            self.assertEqual(data['rows'][0][field], old[field])
        self.assertEqual((data['rows'][0]['number'], data['rows'][0]['total']), (57, 89))
        self.assertEqual(data['rows'][1]['keys'], ['QuestTitle_00002154'])
        self.assertEqual(data['rows'][1]['chinese'], ['艰难的开始'])
        self.assertEqual(data['rows'][1]['quest_ids'], [])

    def test_duplicate_existing_title_is_rejected_before_writing(self):
        old = dict(world='darkmoor', number=1, english='Example', keys=[], chinese=[], aliases=[])
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            previous, output, lang = directory / 'previous.json', directory / 'output.json', directory / 'QuestTitle.lang'
            previous.write_text(json.dumps({'rows': [old, dict(old, number=2)]}), encoding='utf-8')
            lang.write_bytes('1:QuestTitle\r\n'.encode('utf-16'))
            with patch.object(builder, 'workbook_rows', return_value=([dict(old)], [])):
                with self.assertRaisesRegex(ValueError, 'ambiguous existing title'):
                    builder.build(lang, Path('Updated.xlsx'), output, previous)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
