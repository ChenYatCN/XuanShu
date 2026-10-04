"""Read only the installed strings/templates relevant to this task."""
import asyncio
from wizwalker.file_readers.wad import Wad


async def main():
    wad = Wad('D:/Steam/steamapps/common/Wizard101/Data/GameData/Root.wad')
    names = await wad.names()
    for name in ('Locale/en-US/Cantrips.lang', 'Locale/en-US/WizardQuestGoals.lang'):
        if name not in names:
            continue
        lines = (await wad.get_file(name)).decode('utf-16').splitlines()
        for index, line in enumerate(lines):
            if any(term in line.casefold() for term in (
                    'calescent', 'ritual object', 'broken stick', 'magic touch', 'click on the target')):
                print(name, lines[max(0, index - 3):index + 2])
    for name in names:
        if name.startswith('ObjectData/') and 'darkmoor' in name.casefold() and any(
                term in name.casefold() for term in ('stick', 'cantrip', 'ritual', 'tracker')):
            print(name)


if __name__ == '__main__':
    asyncio.run(main())
