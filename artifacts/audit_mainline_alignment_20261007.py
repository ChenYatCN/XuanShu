"""Read-only alignment plan from the current installed QuestTitle resources."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.collect_catalog import language_files, parse_language
from src.mainline_progress import title_aliases
from src.collect_matching import normalize_name

# Confirmed against the installed English table AND the respective quest-line
# guides. This is an explicit review list, not automatic fuzzy matching.
# Keep workbook spelling, numbering and historical aliases unchanged.
VERIFIED_SPELLINGS = {
    ('marleybone', 7): ('Springing the Stitch', 'Springing the Snitch'),
    ('marleybone', 23): ('Purloin the Plains', 'Purloin the Plans'),
    ('polaris', 16): ('Storming the Bastille', 'Storming the Basstille'),
    ('polaris', 19): ('Vive Le Penguinonia', 'Vive la Penguinonia'),
    ('polaris', 46): ('Nostradominus', 'Nostradonimus'),
    ('polaris', 61): ('Borealis Marjoris', 'Borealis Majoris'),
    ('polaris', 83): ('Back to Walkruskberg', 'Back to Walruskberg'),
    ('empyrea', 51): ('Tunnel of Visions', 'Tunnel Visions'),
    ('empyrea', 107): ("'Til Wizard Voices Wake Us", "'Till Wizard Voices Wake Us"),
    ('mirage', 55): ('The Purzzian Vassals', 'The Purrzian Vassals'),
    ('mirage', 66): ('Into Instanboa', 'Into Istanboa'),
    ('mirage', 117): ('Granfather Spider', 'Grandfather Spider'),
    ('dragonspyre', 68): ('The Den of Dean', 'The Den of the Dean'),
    ('zafaria', 130): ("Knocking on Kallah's Door", "Knockin on Kallah's Door"),
    ('azteca', 18): ("The Black Sun's Tower", "The Black Sun's Wake"),
    ('azteca', 134): ('Sign of Capactli', 'Sign of Cipactli'),
    ('azteca', 138): ('Showing the Teeth', 'Showing Teeth'),
    ('azteca', 182): ('Strong Smooth Swords', 'Strong Smooth Words'),
    ('khrysalis', 159): ('The Glittering Eye', 'Thy Glittering Eye'),
}


def title_table(path):
    found = [list(parse_language(data)) for name, data in language_files(path)
             if name.casefold() == 'locale/en-us/questtitle.lang']
    if len(found) != 1:
        raise ValueError(f'Expected one English QuestTitle table: {path}')
    return {code: (english, translated) for code, english, translated in found[0]}


def plan():
    index_path = ROOT / 'src/data/mainline_quests.json'
    before = json.loads(index_path.read_text(encoding='utf-8'))
    after = json.loads(json.dumps(before))
    game_data = Path('D:/Steam/steamapps/common/Wizard101/Data/GameData')
    english = {code: text[1].strip() for code, text in title_table(game_data / 'Root.wad').items()}
    display = title_table(game_data / 'Locale_en-US-root.wad.d')
    by_title = defaultdict(set)
    old_key_owners = defaultdict(set)
    for index, row in enumerate(before['rows']):
        reviewed = VERIFIED_SPELLINGS.get((row['world'].casefold(), row['number']))
        if reviewed:
            old, current = reviewed
            if row['english'] != old or normalize_name(current) not in {
                    normalize_name(title) for title in english.values()}:
                raise ValueError(f'Reviewed spelling no longer matches source: {reviewed}')
            if current not in after['rows'][index]['aliases']:
                after['rows'][index]['aliases'].append(current)
        for title in [row['english'], *after['rows'][index]['aliases']]:
            for alias in title_aliases(title):
                by_title[alias].add(index)
        for code in row['keys']:
            old_key_owners[code].add(index)
    changes, conflicts = [], []
    for code, title in english.items():
        candidates = by_title.get(normalize_name(title), set())
        if len(candidates) == 1:
            index = next(iter(candidates))
            row = after['rows'][index]
            if code not in row['keys']:
                row['keys'].append(code)
        elif len(candidates) > 1:
            conflicts.append({'code': code, 'title': title,
                'rows': [(before['rows'][i]['world'], before['rows'][i]['number']) for i in sorted(candidates)]})
        # Existing uniquely assigned keys can establish current renamed titles.
        owners = old_key_owners.get(code, set())
        if len(owners) == 1 and not candidates:
            row = after['rows'][next(iter(owners))]
            if title and normalize_name(title) not in {normalize_name(t) for t in [row['english'], *row['aliases']]}:
                row['aliases'].append(title)
    for index, row in enumerate(after['rows']):
        # Preserve old variants; add only the overlay translations for this row's keys.
        for code in row['keys']:
            translation = display.get(code, ('', ''))[1].strip()
            if any('\u3400' <= ch <= '\u9fff' for ch in translation) and translation not in row['chinese']:
                row['chinese'].append(translation)
        old = before['rows'][index]
        if row != old:
            changes.append({'world': row['world'], 'number': row['number'], 'english': row['english'],
                'keys_added': [k for k in row['keys'] if k not in old['keys']],
                'aliases_added': [t for t in row['aliases'] if t not in old['aliases']],
                'chinese_added': [t for t in row['chinese'] if t not in old['chinese']]})
    missing = [{'world': row['world'], 'number': row['number'], 'english': row['english']}
               for row in after['rows'] if not row['keys']]
    mismatched = [{'code': code, 'current_title': english.get(code), 'owners':
        [(before['rows'][i]['world'], before['rows'][i]['number'], before['rows'][i]['english']) for i in sorted(owners)]}
        for code, owners in old_key_owners.items()
        if code not in english or all(normalize_name(english[code]) not in {
            alias for title in [before['rows'][i]['english'], *before['rows'][i]['aliases']]
            for alias in title_aliases(title)} for i in owners)]
    patch = ['*** Begin Patch', '*** Update File: ' + index_path.as_posix()]
    for old, new in zip(before['rows'], after['rows']):
        if old == new:
            continue
        old_line = '    ' + json.dumps(old, ensure_ascii=False, separators=(',', ':'))
        # Locate the original line verbatim; do not reformat untouched rows.
        original_lines = index_path.read_text(encoding='utf-8').splitlines()
        matching = [line for line in original_lines if line.strip().rstrip(',') == old_line.strip()]
        if len(matching) != 1:
            raise ValueError(f'Cannot uniquely patch row {old["world"]}/{old["number"]}')
        old_line = matching[0]
        new_line = '    ' + json.dumps(new, ensure_ascii=False, separators=(',', ':')) + (',' if old_line.endswith(',') else '')
        patch.extend(['@@', '-' + old_line, '+' + new_line])
    patch.append('*** End Patch')
    return {'summary': {'rows': len(before['rows']), 'changed': len(changes),
        'keys_added': sum(len(c['keys_added']) for c in changes),
        'aliases_added': sum(len(c['aliases_added']) for c in changes),
        'chinese_added': sum(len(c['chinese_added']) for c in changes),
        'missing_keys': len(missing), 'ambiguous_codes': len(conflicts), 'mismatched_keys': len(mismatched)},
        'changes': changes, 'missing': missing, 'conflicts': conflicts, 'mismatched': mismatched,
        'patch': '\n'.join(patch)}


if __name__ == '__main__':
    print(json.dumps(plan(), ensure_ascii=False))
