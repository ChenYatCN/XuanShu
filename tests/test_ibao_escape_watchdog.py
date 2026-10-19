"""Exercise the real dialogue worker with game UI and monotonic time controlled."""
import asyncio
from collections import Counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from loguru import logger
from src import ibao_core as core

REAL_SLEEP = asyncio.sleep


class EndOfFrames(Exception):
    pass


class Window:
    def __init__(self, name, children=(), visible=lambda: True):
        self._name, self._children, self._visible = name, list(children), visible

    async def name(self):
        return self._name

    async def children(self):
        return self._children

    async def is_visible(self):
        return self._visible()

    async def get_windows_with_name(self, name):
        found = []
        for child in self._children:
            if child._name == name:
                found.append(child)
            found.extend(await child.get_windows_with_name(name))
        return found


class WorkerHarness:
    def __init__(self, frames, *, shop_closes_after=1, recovery_closes=True):
        self.frames = iter(frames)
        self.now = 0.0
        self.iteration = 0
        self.state = dict(settings=False, shop=False, loading=False, battle=False,
                          dialogue=False, play=False)
        self.inputs = []
        self.shop_closes_after = shop_closes_after
        self.recovery_closes = recovery_closes
        page = Window('SettingPage', visible=lambda: self.state['settings'])
        play = Window('btnPlay', visible=lambda: self.state['play'])
        root = Window('root', [Window('WorldView', [Window('DeckConfiguration', [page]),
                              Window('mainWindow', [play])]),
                              Window('permanentShop', visible=lambda: self.state['shop'])])
        async def loading():
            return self.state['loading']
        async def battle():
            return self.state['battle']
        async def dialogue():
            return self.state['dialogue']
        async def key(code, duration=0):
            self.inputs.append((code.name, self.now, self.iteration, self.state['shop']))
            if code == core.Keycode.ESC:
                if self.state['shop']:
                    self.shop_closes_after -= 1
                    if self.shop_closes_after <= 0:
                        self.state['shop'] = False
                elif self.recovery_closes:
                    self.state['settings'] = False
            elif code == core.Keycode.SPACEBAR:
                self.state['dialogue'] = False
        self.client = SimpleNamespace(title='p1', root_window=root, send_key=key,
            is_loading=loading, in_battle=battle, is_in_dialog=dialogue)

    async def sleep(self, seconds):
        if seconds == .2:  # The existing dialogue worker's outer-loop delay.
            try:
                at, changes = next(self.frames)
            except StopIteration:
                raise EndOfFrames
            self.now = at
            self.iteration += 1
            self.state.update(changes)
        await REAL_SLEEP(0)

    async def run(self):
        clock = SimpleNamespace(sleep=self.sleep, Lock=asyncio.Lock,
            get_running_loop=lambda: SimpleNamespace(time=lambda: self.now))
        with patch.object(core, 'asyncio', clock), patch.object(core, 'CROWNSHOP_DISMISS_TIMEOUT', 0):
            try:
                await core.skipDialogue(self.client)
            except EndOfFrames:
                pass

    def escapes(self):
        return [row for row in self.inputs if row[0] == 'ESC']


class EscapeWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_shop_gets_at_most_one_escape_per_outer_iteration(self):
        worker = WorkerHarness([(0, dict(shop=True)), (.22, {}), (.44, {})],
                               shop_closes_after=2)
        await worker.run()
        counts = Counter(row[2] for row in worker.escapes())
        self.assertEqual(len(worker.escapes()), 2)
        self.assertTrue(all(count == 1 for count in counts.values()), dict(counts))

    async def test_shop_closed_by_first_escape_has_no_ordinary_followup_escape(self):
        worker = WorkerHarness([(0, dict(shop=True)), (.22, {}), (.44, {})])
        await worker.run()
        self.assertEqual(worker.escapes(), [('ESC', 0, 1, True)])

    async def test_peer_closing_shop_while_waiting_for_lock_resets_settings_timer(self):
        worker = WorkerHarness([(0, dict(settings=True)), (4, dict(shop=True))])
        lock = asyncio.Lock()
        worker.client._ibao_crownshop_lock = lock
        await lock.acquire()
        observed = asyncio.Event()
        shop = (await worker.client.root_window.get_windows_with_name('permanentShop'))[0]
        async def shop_visible():
            if worker.state['shop']:
                observed.set()
            return worker.state['shop']
        shop.is_visible = shop_visible
        async def peer():
            await observed.wait()
            await worker.client.send_key(core.Keycode.ESC, .3)
            lock.release()
        other = asyncio.create_task(peer())
        try:
            await worker.run()
            await other
        finally:
            other.cancel()
            await asyncio.gather(other, return_exceptions=True)
        self.assertEqual([(row[1], row[3]) for row in worker.escapes()], [(4, True)])

    async def test_settings_under_four_seconds_never_recovers(self):
        worker = WorkerHarness([(0, dict(settings=True)), (1, {}), (3.99, {})])
        await worker.run()
        self.assertEqual(worker.escapes(), [])

    async def test_settings_visible_for_four_seconds_recovers_exactly_once(self):
        worker = WorkerHarness([(0, dict(settings=True)), (3.99, {}), (4, {}), (4.2, {})])
        await worker.run()
        self.assertEqual(worker.escapes(), [('ESC', 4, 3, False)])

    async def test_persistent_settings_waits_a_full_cooldown_for_each_attempt(self):
        worker = WorkerHarness([(0, dict(settings=True)), (4, {}), (4.01, {}),
                                (7.99, {}), (8, {}), (8.01, {})], recovery_closes=False)
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [4, 8])

    async def test_loading_resets_the_continuous_visibility_timer(self):
        worker = WorkerHarness([(0, dict(settings=True)), (3.9, {}),
            (4, dict(loading=True)), (5, dict(loading=False)), (8.99, {}), (9, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [9])

    async def test_battle_blocks_recovery_and_starts_a_fresh_wait_afterwards(self):
        worker = WorkerHarness([(0, dict(settings=True)), (4, dict(battle=True)),
            (5, dict(battle=False)), (8.99, {}), (9, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [9])

    async def test_disappearing_page_must_start_over_when_it_reappears(self):
        worker = WorkerHarness([(0, dict(settings=True)), (3.9, {}),
            (4, dict(settings=False)), (5, dict(settings=True)), (8.99, {}), (9, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [9])

    async def test_dialogue_progress_resets_settings_watchdog(self):
        worker = WorkerHarness([(0, dict(settings=True)), (3.9, {}),
            (4, dict(dialogue=True)), (4.1, {}), (8.09, {}), (8.1, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [8.1])
        self.assertEqual([row[0] for row in worker.inputs].count('SPACEBAR'), 1)

    async def test_character_selection_progress_resets_settings_watchdog(self):
        worker = WorkerHarness([(0, dict(settings=True)), (4, dict(play=True)),
            (5, dict(play=False)), (8.99, {}), (9, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [9])

    async def test_crown_shop_takes_priority_and_resets_settings_timer(self):
        worker = WorkerHarness([(0, dict(settings=True)), (4, dict(shop=True)),
            (5, {}), (8.99, {}), (9, {})])
        await worker.run()
        self.assertEqual([(row[1], row[3]) for row in worker.escapes()], [(4, True), (9, False)])

    async def test_timer_does_not_survive_worker_exit(self):
        worker = WorkerHarness([(0, dict(settings=True)), (3.99, {})])
        await worker.run()
        worker.frames = iter([(4, {}), (7.99, {}), (8, {})])
        await worker.run()
        self.assertEqual([row[1] for row in worker.escapes()], [8])

    async def test_normal_progress_starting_during_ui_reads_prevents_recovery(self):
        for progress in ('play', 'dialogue'):
            with self.subTest(progress=progress):
                worker = WorkerHarness([(0, dict(settings=True)), (4, {})])
                page = (await worker.client.root_window.get_windows_with_name('SettingPage'))[0]
                def page_visible():
                    if worker.now == 4:
                        worker.state[progress] = True
                    return worker.state['settings']
                page._visible = page_visible
                await worker.run()
                self.assertEqual(worker.escapes(), [])

    async def test_loading_or_shop_starting_during_ui_reads_prevents_recovery(self):
        for unsafe in ('loading', 'battle', 'shop'):
            with self.subTest(unsafe=unsafe):
                worker = WorkerHarness([(0, dict(settings=True)), (4, {})])
                page = (await worker.client.root_window.get_windows_with_name('SettingPage'))[0]
                def page_visible():
                    if worker.now == 4:
                        worker.state[unsafe] = True
                    return worker.state['settings']
                page._visible = page_visible
                await worker.run()
                self.assertEqual(worker.escapes(), [])

    async def test_logs_only_visibility_recovery_and_clearing_boundaries(self):
        worker = WorkerHarness([(0, dict(settings=True)), (1, {}), (2, {}),
                                (3, {}), (4, {}), (4.2, {}), (4.4, {})])
        lines = []
        sink = logger.add(lambda message: lines.append(str(message)), level='DEBUG')
        try:
            await worker.run()
        finally:
            logger.remove(sink)
        events = [line for line in lines if '[ibao_escape]' in line]
        for event in ('settings-visible', 'settings-stuck', 'recovery-esc', 'settings-cleared'):
            self.assertEqual(sum('event=' + event in line for line in events), 1, event)
        self.assertTrue(all('client=p1' in line for line in events))


if __name__ == '__main__':
    unittest.main()
