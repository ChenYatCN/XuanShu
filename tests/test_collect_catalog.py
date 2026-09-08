import asyncio
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from src.collect_catalog import InstalledCollectCatalog, language_files, parse_language, source_signature
from src.collect_matching import parse_collect_goal


def lang(table, rows):
    return ('\r\n'.join([f'1:{table}', *[v for row in rows for v in row], ''])).encode('utf-16')


def wad(path, entries):
    """Small real KIWAD fixture; both compressed and plain entries."""
    offset = 14 + sum(21 + len(name.encode()) + 1 for name, _, _ in entries)
    journal, contents = [], []
    for name, data, compressed in entries:
        payload = zlib.compress(data) if compressed else data
        encoded = name.encode() + b'\0'
        journal.append(struct.pack('<III?II', offset, len(data), len(payload), compressed, 0, len(encoded)) + encoded)
        contents.append(payload)
        offset += len(payload)
    path.write_bytes(b'KIWAD' + struct.pack('<II', 2, len(entries)) + b'\0' + b''.join(journal + contents))


class InstalledCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.install = Path(self.temp.name)
        self.data = self.install / 'Data' / 'GameData'
        self.data.mkdir(parents=True)
        self.cache = self.install / 'cache'
        self.overlay = self.data / 'Locale_en-US-root.wad.d'
        self.root = self.data / 'Root.wad'
        self.table = 'BrandNewCollectTable'
        self.english = 'Amber Test Lantern'
        self.chinese = '琥珀测试灯笼'
        self.write_sources()
        self.catalog = InstalledCollectCatalog(self.install, self.cache)

    def write_sources(self, chinese=None, root_english=None):
        name = f'Locale/en-US/{self.table}.lang'
        wad(self.root, [(name, lang(self.table, [('0001', '', root_english or self.english)]), True)])
        wad(self.overlay, [(name, lang(self.table, [('0001', self.english, chinese or self.chinese)]), True)])
        # The regular archive is fonts-only; the .wad.d file holds translations.
        wad(self.data / 'Locale_en-US-root.wad', [('Fonts/Test.ttf', b'font', False)])

    async def test_unknown_table_is_read_from_compressed_overlay(self):
        names = await self.catalog.get()
        self.assertEqual(names.score(self.chinese, self.english, 'Different_999'), 100)
        self.assertEqual(names.score(self.chinese, code=f'{self.table}_0001'), 100)
        self.assertGreaterEqual(names.score(self.chinese, internal='CL-AmberTestLantern'), 85)

    async def test_restart_uses_cache_without_rereading_archives(self):
        await self.catalog.get()
        restarted = InstalledCollectCatalog(self.install, self.cache)
        with patch('src.collect_catalog.build_records', side_effect=AssertionError('cache should be reused')):
            names = await restarted.get()
        self.assertEqual(names.score(self.chinese, self.english), 100)

    async def test_changed_translation_invalidates_cache_and_removes_old_alias(self):
        await self.catalog.get()
        previous = self.overlay.stat().st_mtime_ns
        self.write_sources(chinese='全新测试灯笼')
        os.utime(self.overlay, ns=(previous + 10_000_000, previous + 10_000_000))
        self.catalog.next_check = 0
        names = await self.catalog.get()
        self.assertEqual(names.score('全新测试灯笼', self.english), 100)
        self.assertLess(names.score(self.chinese, self.english), 85)

    async def test_reused_id_only_contributes_text_aliases(self):
        self.write_sources(root_english='Unrelated Stone Gate')
        names = await self.catalog.get()
        self.assertEqual(names.score(self.chinese, self.english), 100)
        self.assertEqual(names.score(self.chinese, code=f'{self.table}_0001'), 0)
        self.assertEqual(names.score(self.chinese, 'Unrelated Stone Gate', f'{self.table}_0001'), 0)

    async def test_corrupt_cache_is_rebuilt(self):
        self.cache.mkdir()
        self.catalog.cache_path.write_text('{broken', encoding='utf-8')
        names = await self.catalog.get()
        self.assertEqual(names.score(self.chinese, self.english), 100)
        json.loads(self.catalog.cache_path.read_text(encoding='utf-8'))

    async def test_read_only_cache_keeps_names_in_memory(self):
        with patch('src.collect_catalog.atomic_json', side_effect=PermissionError('read only')):
            names = await self.catalog.get()
        self.assertEqual(names.score(self.chinese, self.english), 100)

    async def test_expanded_language_directory_and_noncompressed_file(self):
        expanded = self.data / 'Locale_custom-root.wad.d' / 'Locale' / 'en-US'
        expanded.mkdir(parents=True)
        (expanded / 'NewObjects.lang').write_bytes(lang('NewObjects', [('2', 'Cobalt Beacon', '钴蓝信标')]))
        wad(self.data / 'Locale_extra-root.wad', [
            ('Locale/en-US/Other.lang', lang('Other', [('3', 'Violet Lamp', '紫色油灯')]), False)])
        names = await self.catalog.get()
        self.assertEqual(names.score('钴蓝信标', 'Cobalt Beacon'), 100)
        self.assertEqual(names.score('紫色油灯', 'Violet Lamp'), 100)

    async def test_broken_archive_falls_back_then_recovers_on_next_check(self):
        self.overlay.write_bytes(b'broken')
        names = await self.catalog.get()
        self.assertEqual(names.score('莱顿瓶', 'Leyden Jar'), 100)
        self.write_sources()
        self.catalog.next_check = 0
        self.assertEqual((await self.catalog.get()).score(self.chinese, self.english), 100)

    async def test_concurrent_clients_share_one_build(self):
        from src.collect_catalog import build_records
        with patch('src.collect_catalog.build_records', wraps=build_records) as build:
            first, second = await asyncio.gather(self.catalog.get(), self.catalog.get())
        self.assertIs(first, second)
        self.assertEqual(build.call_count, 1)

    async def test_cancellation_drains_reader_and_releases_lock(self):
        started = asyncio.Event()
        release, stopped = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def read():
            loop.call_soon_threadsafe(started.set)
            release.wait(5)
            stopped.set()
        with patch.object(self.catalog, 'refresh', side_effect=read):
            task = asyncio.create_task(self.catalog.get())
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.assertFalse(self.catalog.lock.locked())

    def test_packaged_bundle_location_does_not_invalidate_cache(self):
        first = self.install / 'bundle-first.json'
        second = self.install / 'bundle-second.json'
        first.write_bytes(b'{"records":[]}')
        second.write_bytes(first.read_bytes())
        self.assertEqual(source_signature([self.root], first), source_signature([self.root], second))

    def test_unresolved_report_keeps_evidence_without_creating_alias(self):
        goal = parse_collect_goal('聚集 未知测试物件 地点：科学中心 (0/3)')
        candidate = {'internal': 'CL-Unknown', 'display': 'Unmapped Item', 'code': 'New_12', 'score': 0}
        path = self.catalog.report_unresolved('ScienceCenter', goal, [candidate], 'Unmapped Item')
        report = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(report['target'], goal.target)
        self.assertEqual(report['candidate_sample'], [candidate])
        self.assertEqual(report['popup'], 'Unmapped Item')
        self.assertEqual(self.catalog.names.score(goal.target, 'Unmapped Item'), 0)

    def test_language_parser_keeps_record_boundaries(self):
        data = lang('Test', [('1', 'A\u2028B', '名称'), ('2', 'Second', '第二项')])
        self.assertEqual(list(parse_language(data))[1], ('Test_2', 'Second', '第二项'))
        self.assertEqual(list(parse_language(b'bad')), [])

    def test_archive_reader_does_not_read_beyond_compressed_payload(self):
        entries = list(language_files(self.overlay))
        self.assertEqual(list(parse_language(entries[0][1]))[0],
                         (f'{self.table}_0001', self.english, self.chinese))
