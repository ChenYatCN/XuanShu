"""Read the supplied workbook and game title table; update only the runtime index."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.collect_catalog import language_files, parse_language


def main():
    workbook = ROOT / 'W101 Mainline Quests.xlsx'
    output = ROOT / 'src/data/mainline_quests.json'
    before = json.loads(output.read_text(encoding='utf-8'))
    workbook_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    archive = Path('D:/Steam/steamapps/common/Wizard101/Data/GameData/Locale_en-US-root.wad.d')
    tables = [(name, data) for name, data in language_files(archive)
              if name.rsplit('/', 1)[-1].casefold() == 'questtitle.lang']
    if len(tables) != 1:
        raise ValueError('Expected one verified QuestTitle table in the installed overlay')
    translations = {code: translated for code, _, translated in parse_language(tables[0][1])}
    root_tables = [(name, data) for name, data in language_files(archive.with_name('Root.wad'))
                   if name.casefold() == 'locale/en-us/questtitle.lang']
    if len(root_tables) != 1:
        raise ValueError('Expected one current English QuestTitle table in Root.wad')
    title_lines = ['1:QuestTitle']
    for code, _, english in parse_language(root_tables[0][1]):
        title_lines.extend((code.removeprefix('QuestTitle_'), english, translations.get(code, english)))
    python = Path('C:/Users/ChenYat/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
    with tempfile.TemporaryDirectory(prefix='mainline-update-', dir=ROOT / 'artifacts') as folder:
        folder = Path(folder)
        lang = folder / 'QuestTitle.lang'
        generated = folder / 'mainline_quests.json'
        lang.write_bytes(('\r\n'.join(title_lines) + '\r\n').encode('utf-16'))
        subprocess.run([
            str(python), '-X', 'utf8', str(ROOT / 'scripts/build_mainline_index.py'),
            '--quest-title-lang', str(lang), '--workbook', str(workbook),
            '--output', str(generated), '--previous-index', str(output),
            '--title-source', 'Root.wad current English + Locale_en-US-root.wad.d display titles, joined by language key; existing verified mappings retained',
        ], cwd=ROOT, check=True)
        after = json.loads(generated.read_text(encoding='utf-8'))
        identity = lambda row: (row['world'].casefold(), row['english'])
        indexed = {identity(row): row for row in after['rows']}
        if len(indexed) != len(after['rows']):
            raise ValueError('Duplicate world/title rows in the updated index')
        for row in before['rows']:
            if indexed.get(identity(row)) != row:
                raise ValueError(f"Unexpected change to existing row: {row['world']} {row['english']}")
        darkmoor = [row for row in after['rows'] if row['world'].casefold() == 'darkmoor']
        if [row['number'] for row in darkmoor] != list(range(1, 80)):
            raise ValueError('Expected the supplied 79 numbered Darkmoor quests')
        if any(row['total'] != 79 or not row['keys'] for row in darkmoor):
            raise ValueError('Missing verified key: ' + repr([
                (row['number'], row['english'], row['keys'], row['chinese']) for row in darkmoor
                if not row['keys']]))
        if workbook_hash != hashlib.sha256(workbook.read_bytes()).hexdigest():
            raise ValueError('The source workbook was unexpectedly modified')
        shutil.copyfile(generated, output)
        print(f"Preserved {len(before['rows'])} existing rows unchanged; added {len(darkmoor)} Darkmoor rows.")
        print(f"All 79 new rows have language keys; {sum(bool(r['chinese']) for r in darkmoor)} have Chinese titles; source workbook unchanged.")
        duplicate_keys = {key: [row['world'] + '/' + row['english'] for row in after['rows']
                                if key in row['keys']]
                          for row in darkmoor for key in row['keys']}
        print('New keys shared across rows:', {key: rows for key, rows in duplicate_keys.items() if len(rows) > 1})


if __name__ == '__main__':
    main()
