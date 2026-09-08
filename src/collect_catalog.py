# Modified 2026-09-09: XuanShu branding and path compatibility; see NOTICE.md.
"""Build collect aliases from the installed language resources, with a versioned cache.

No name-table allowlist: names can move to a new table after a game update.
Short bilingual text is indexed; long dialogue and substitution templates are excluded.
Game files are opened read-only. Work runs off the game automation event loop.
"""
import asyncio
import hashlib
import json
import os
import re
import struct
import tempfile
import time
import zlib
from pathlib import Path
from src.branding import appdata_dir

from loguru import logger

from src.collect_matching import CollectNames, collect_names


_VERSION = 1
_CJK = re.compile(r'[\u3400-\u9fff]')
_SERVICES = {}


def cache_directory():
    base = appdata_dir()
    return base / 'collect_cache'


def resource_files(game_data):
    """Include archive overlays named .wad.d and expanded .wad.d directories."""
    sources = []
    for path in game_data.iterdir():
        name = path.name.casefold()
        if name not in ('root.wad', 'root.wad.d') and not (
                name.startswith('locale_') and name.endswith(('.wad', '.wad.d'))):
            continue
        if path.is_dir():
            sources.extend(p for p in path.rglob('*.lang') if p.is_file())
        elif path.is_file():
            sources.append(path)
    return sorted(sources, key=lambda p: str(p).casefold())


def signature(paths):
    return [[str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns] for p in paths]


def source_signature(paths, bundled):
    # A one-file build extracts bundled files into a different temporary directory
    # on each launch. Use their content, so those launches can reuse the cache.
    return signature(paths) + [['bundled', hashlib.sha256(bundled.read_bytes()).hexdigest()]]


def language_files(path):
    """Stream only language entries; honor compressed size (not original size)."""
    if path.suffix.casefold() == '.lang':
        yield str(path), path.read_bytes()
        return
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if stream.read(5) != b'KIWAD':
            raise ValueError(f'{path.name}: invalid WAD header')
        version, count = struct.unpack('<II', stream.read(8))
        if count > 1_000_000:
            raise ValueError(f'{path.name}: invalid entry count')
        if version >= 2:
            stream.read(1)
        entries = []
        for _ in range(count):
            offset, raw_size, compressed_size, compressed, _, name_size = struct.unpack('<III?II', stream.read(21))
            if not 1 <= name_size <= 8192:
                raise ValueError(f'{path.name}: invalid entry name')
            name = stream.read(name_size).rstrip(b'\0').decode('utf-8')
            if not name.casefold().endswith('.lang'):
                continue
            length = compressed_size if compressed else raw_size
            if offset + length > size or max(length, raw_size) > 32 * 1024 * 1024:
                raise ValueError(f'{path.name}: invalid language entry size')
            entries.append((name, offset, length, compressed))
        for name, offset, length, compressed in entries:
            stream.seek(offset)
            data = stream.read(length)
            if compressed:
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    # A broken entry must not hide the other language tables.
                    continue
            yield name, data


def parse_language(data):
    try:
        lines = data.decode('utf-16').split('\r\n')
    except UnicodeError:
        return
    if not lines or ':' not in lines[0]:
        return
    table = lines[0].split(':', 1)[1].strip()
    if not table:
        return
    for index in range(1, len(lines) - 2, 3):
        key, english, translated = lines[index:index + 3]
        if key:
            yield f'{table}_{key}', english, translated


def is_label(value):
    return bool(value.strip()) and len(value) <= 160 and not any(
        token in value for token in ('\n', '\r', '\\n', '&', '%s', '%d', '{', '}'))


def build_records(paths):
    english_by_id = {}
    bilingual = {}
    for path in paths:
        for name, data in language_files(path):
            for code, english, translated in parse_language(data):
                # Root contains the current English text in the third column.
                if path.name.casefold() == 'root.wad' and name.casefold().startswith('locale/en-us/'):
                    english_by_id[code] = translated
                if is_label(english) and is_label(translated) and _CJK.search(translated):
                    bilingual[code] = [code, english, translated]
    bundled = json.loads((Path(__file__).with_name('data') / 'collect_names.json').read_text(encoding='utf-8'))['records']
    result = {}
    for row in bundled:
        code, english, _ = row
        if code not in english_by_id or english_by_id[code] == english:
            result[code] = row
    for code, row in bilingual.items():
        # The bilingual pair is useful even when language IDs changed. A stale
        # ID must not become authoritative for an entity with a missing label.
        result.pop(code, None)
        if code in english_by_id and english_by_id[code] != row[1]:
            row = ['', row[1], row[2]]
        result[code] = row
    return list(result.values()), len(bilingual)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class InstalledCollectCatalog:
    def __init__(self, install, cache_dir=None):
        self.game_data = Path(install) / 'Data' / 'GameData'
        self.cache_dir = Path(cache_dir) if cache_dir is not None else cache_directory()
        key = hashlib.sha256(str(self.game_data.resolve()).casefold().encode()).hexdigest()[:16]
        self.cache_path = self.cache_dir / f'names-{key}.json'
        self.names = collect_names()
        self.fingerprint = None
        self.next_check = 0
        self.lock = asyncio.Lock()

    def refresh(self):
        paths = resource_files(self.game_data)
        # Include bundled data so an application update invalidates the cache too.
        bundled = Path(__file__).with_name('data') / 'collect_names.json'
        fingerprint = source_signature(paths, bundled)
        if fingerprint == self.fingerprint:
            return
        cached = None
        try:
            cached = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if isinstance(cached, dict) and cached.get('version') == _VERSION and cached.get('sources') == fingerprint:
            rows = cached.get('records')
            if isinstance(rows, list) and all(isinstance(r, list) and len(r) == 3
                                             and all(isinstance(v, str) for v in r) for r in rows):
                self.names = CollectNames(rows)
                self.fingerprint = fingerprint
                return
        rows, count = build_records(paths)
        if source_signature(resource_files(self.game_data), bundled) != fingerprint:
            raise ValueError('game language resources changed while reading')
        self.names = CollectNames(rows)
        self.fingerprint = fingerprint
        try:
            atomic_json(self.cache_path, {'version': _VERSION, 'sources': fingerprint, 'records': rows})
        except OSError as exc:
            logger.warning(f'采集名称缓存无法保存，本次仍使用已读取的名称：{exc}')
        logger.info(f'采集名称对照已更新：从本机语言资源读取 {count} 条双语名称。')

    async def get(self):
        async with self.lock:
            if time.monotonic() >= self.next_check:
                # Shield the read job, retaining ownership until it exits. Stopping
                # questing may cancel the await, but must not start a second writer.
                job = asyncio.create_task(asyncio.to_thread(self.refresh))
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    try:
                        await job
                    except Exception:
                        pass
                    raise
                except Exception as exc:
                    logger.warning(f'本机采集名称读取失败，暂用已有对照：{exc}')
                finally:
                    self.next_check = time.monotonic() + 30
            return self.names

    def report_unresolved(self, zone, goal, candidates, popup=''):
        key = hashlib.sha256(f'{zone}|{goal.target}|{goal.location}'.encode()).hexdigest()[:16]
        path = self.cache_dir / f'unresolved-{key}.json'
        atomic_json(path, {'zone': zone, 'target': goal.target, 'location': goal.location,
                           'progress': [goal.current, goal.total], 'popup': popup,
                           'candidate_sample': candidates})
        return path


def installed_catalog(client):
    try:
        install = getattr(client.cache_handler, 'install_location', None)
    except (OSError, ValueError) as exc:
        logger.debug(f'无法定位本机语言资源，使用内置采集名称：{exc}')
        return None
    if not isinstance(install, (str, os.PathLike)):
        return None
    key = str(Path(install).resolve()).casefold()
    if key not in _SERVICES:
        _SERVICES[key] = InstalledCollectCatalog(install)
    return _SERVICES[key]
