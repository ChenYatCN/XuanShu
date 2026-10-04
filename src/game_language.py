"""Switch installed Subata language overlays without deleting their sources.

Subata's chatonly/debug/release suffixes are .c/.d/.r. Enabling a patch
copies it to the .wad name; disabling that overlay exposes Root.wad's English.
No downloads, archive rewriting, or changes to Root.wad are performed here.
"""
import filecmp
import os
import re
import shutil
import tempfile
from pathlib import Path

from src.collect_catalog import language_files, parse_language


ARCHIVES = ('Locale_en-US-root.wad', 'Locale_English-Root.wad')
PATCHES = tuple(name + suffix for name in ARCHIVES for suffix in ('.d', '.r'))


class GameLanguageError(ValueError):
    """The message is a UI translation key."""


def installed_language_patches(game_path):
    data = Path(game_path) / 'Data' / 'GameData'
    return [name for name in PATCHES if (data / name).is_file()]


def _resource(data, name):
    path = data / name
    # Never follow an overlay pointing outside the selected game's resources.
    if path.is_symlink() or path.resolve().parent != data.resolve():
        raise GameLanguageError('game_language_invalid_resource')
    return path


def _check_wad(path):
    if not path.is_file():
        raise GameLanguageError('game_language_missing_patch')
    with path.open('rb') as stream:
        if stream.read(5) != b'KIWAD':
            raise GameLanguageError('game_language_invalid_resource')


def _check_chinese(path):
    _check_wad(path)
    try:
        for _, content in language_files(path):
            if any(re.search(r'[\u3400-\u9fff]', translated)
                   for _, _, translated in parse_language(content)):
                return
    except (ValueError, UnicodeError, OSError) as error:
        raise GameLanguageError('game_language_invalid_resource') from error
    raise GameLanguageError('game_language_not_chinese')


def apply_game_language(game_path, language, patch_name, running_clients):
    """Blocking; call on a worker. Returns a preserved backup path, or None.

    running_clients must be a fresh check of ALL Wizard windows, including
    unhooked clients. No-op reapplication is allowed during multi-account launch.
    """
    if language not in ('en', 'zh') or patch_name not in PATCHES:
        raise GameLanguageError('game_language_invalid_resource')
    data = (Path(game_path) / 'Data' / 'GameData').resolve()
    if not data.is_dir() or not (data / 'Root.wad').is_file():
        raise GameLanguageError('game_language_invalid_path')
    target = _resource(data, patch_name[:-2])
    source = _resource(data, patch_name)
    if language == 'en' and not target.exists():
        return None
    if language == 'zh':
        _check_chinese(source)
        if target.is_file() and filecmp.cmp(source, target, shallow=False):
            return None
    if running_clients():
        raise GameLanguageError('game_language_close_clients')
    if target.exists():
        _check_wad(target)

    temporary = None
    backup = None
    try:
        if language == 'zh':
            fd, name = tempfile.mkstemp(prefix=target.name + '.xuanshu-', suffix='.tmp', dir=data)
            os.close(fd)
            temporary = Path(name)
            shutil.copy2(source, temporary)
            _check_chinese(temporary)
        if target.exists():
            fd, name = tempfile.mkstemp(prefix=target.name + '.xuanshu-backup-', suffix='.bak', dir=data)
            os.close(fd)
            backup = Path(name)
            if language == 'en':
                if running_clients():
                    backup.unlink()
                    raise GameLanguageError('game_language_close_clients')
                os.replace(target, backup)
            else:
                shutil.copy2(target, backup)
        if temporary is not None:
            # Recheck immediately before committing, after the potentially slow copy.
            if running_clients():
                raise GameLanguageError('game_language_close_clients')
            os.replace(temporary, target)
        return str(backup) if backup else None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
