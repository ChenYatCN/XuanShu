"""Actual terminal cleanup helpers at the client-specific UI/input boundary."""
import ast
import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import wizwalker
from wizwalker.memory import WindowFlags
from wizwalker.errors import MemoryReadError
from src import utils
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests.test_friend_busy_receipt import Window


class FriendBusyClickTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        names = {'probe_mouse', 'log_friend_cleanup', 'retire_friend_failure',
                 'friend_failure_current', 'friend_request_blocked', 'friend_failure_modal',
                 'settle_friend_failure'}
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        cls.helpers = compile(ast.Module(body=[n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names],
            type_ignores=[]), 'XuanShu.py', 'exec')

    def make_cleanup(self, title='p2', *, close_after=1, pre_failure=False,
                     caption='你的好友现在很忙。', manual=False):
        clock = [0.0]
        clicks, logs = [], []
        button = Window('rightButton')
        modal = Window('MessageBoxModalWindow', children=[
            Window('messageBoxBG', children=[Window('messageBoxLayout', children=[
                Window('CaptionText', text='<center>' + caption + '</center>'),
                Window('AdjustmentWindow', children=[Window('Layout', children=[
                    Window('leftButton', visible=False), Window('centerButton', visible=False), button])])])])])
        root = Window('root', children=[modal])
        client = SimpleNamespace(title=title, questing_status=True, root_window=root,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False))
        quester = SimpleNamespace(title='p1')
        operation = dict(client=client, quester=quester, generation=1, token=f'{id(client):x}-1',
            modal=None, clicked=False, deadline=30.0, state='request-sent', busy_until=0)
        client._friend_follow_failure = operation
        client._friend_request_generation = 1
        probe_state = dict(friend_failure=operation, pending=True)
        failures = [int(pre_failure)]
        class Mouse:
            depth = 0
            async def __aenter__(mouse): mouse.depth += 1
            async def __aexit__(mouse, *_): mouse.depth -= 1
            async def click_window(mouse, target, *, on_dispatched=None):
                self.assertIs(target, button, 'wrong client/button selected')
                self.assertEqual(mouse.depth, 1)
                self.assertIs(get_client_automation_ownership(client)._owner_task, asyncio.current_task())
                if failures[0]:
                    failures[0] -= 1
                    raise MemoryReadError('button rectangle temporarily unavailable')
                clicks.append(target)
                if on_dispatched is not None: on_dispatched()
                if len(clicks) >= close_after: modal.visible = False
        client.mouse_handler = Mouse()
        def record(line):
            logs.append(line)
            if manual and 'decision=modal-detected' in line: modal.visible = False
        async def tick(seconds):
            clock[0] += seconds
            await asyncio.sleep(0)
        ns = dict(asyncio=SimpleNamespace(timeout=asyncio.timeout, wait_for=asyncio.wait_for,
            current_task=asyncio.current_task, CancelledError=asyncio.CancelledError,
            TimeoutError=TimeoutError, sleep=tick), sys=sys, asynccontextmanager=asynccontextmanager,
            hitter=client, quester=quester, probe_state=probe_state, logger=SimpleNamespace(debug=record),
            loop=SimpleNamespace(time=lambda: clock[0]), walker=SimpleNamespace(clients=[client]),
            get_window_from_path=utils.get_window_from_path,
            friend_is_busy_and_dungeon_reset_path=utils.friend_is_busy_and_dungeon_reset_path,
            automation_owner=automation_owner, get_client_automation_ownership=get_client_automation_ownership,
            WindowFlags=WindowFlags, wizwalker=wizwalker, update_party_status=Mock())
        exec(self.helpers, ns)
        return SimpleNamespace(ns=ns, client=client, modal=modal, operation=operation,
            clicks=clicks, logs=logs, clock=clock, state=probe_state)

    async def settle(self, fixture):
        with patch('src.window_text.read_control_text', AsyncMock(side_effect=lambda w: w.text)):
            return await fixture.ns['settle_friend_failure'](fixture.operation)

    async def test_r1_one_busy_confirmation_dispatches_once_and_completes(self):
        f = self.make_cleanup()
        self.assertTrue(await self.settle(f))
        self.assertEqual(len(f.clicks), 1)
        self.assertFalse(f.modal.visible)
        self.assertTrue(any('modal-disappeared clicked=True' in line for line in f.logs))

    async def test_r2_two_clients_close_only_their_own_modal(self):
        left, right = self.make_cleanup('p2'), self.make_cleanup('p3')
        with patch('src.window_text.read_control_text', AsyncMock(side_effect=lambda w: w.text)):
            results = await asyncio.gather(*(f.ns['settle_friend_failure'](f.operation) for f in (left, right)))
        self.assertEqual(results, [True, True])
        for f in (left, right):
            self.assertEqual(len(f.clicks), 1)
            self.assertFalse(f.modal.visible)
            self.assertFalse(get_client_automation_ownership(f.client).locked)

    async def test_r3_visible_after_first_click_is_reread_then_retried(self):
        f = self.make_cleanup(close_after=2)
        self.assertTrue(await self.settle(f))
        self.assertEqual(len(f.clicks), 2)
        self.assertFalse(f.modal.visible)

    async def test_r4_manual_disappearance_has_no_click(self):
        f = self.make_cleanup(manual=True)
        self.assertTrue(await self.settle(f))
        self.assertEqual(f.clicks, [])
        self.assertTrue(any('modal-disappeared clicked=False' in line for line in f.logs))

    async def test_r5_unrelated_message_box_is_not_claimed(self):
        f = self.make_cleanup(caption='Unrelated confirmation')
        self.assertFalse(await self.settle(f))
        self.assertEqual(f.clicks, [])

    async def test_r6_persistent_modal_retries_are_bounded_and_requests_stay_blocked(self):
        f = self.make_cleanup(close_after=100)
        self.assertFalse(await self.settle(f))
        for _ in range(3):
            self.assertTrue(await self.settle(f), 'retired cleanup must not be replayed')
            self.assertTrue(f.ns['friend_request_blocked'](f.client))
        self.assertEqual(len(f.clicks), 3)
        self.assertEqual(f.state['friend_outcome'], 'unavailable')
        self.assertFalse(getattr(f.client, '_friend_follow_failure', None))

    async def test_r7_disappearance_keeps_backoff_and_unavailable_release(self):
        f = self.make_cleanup()
        await self.settle(f)
        self.assertEqual(f.state['friend_outcome'], 'unavailable')
        self.assertTrue(f.state['release_pending'])
        self.assertEqual(f.operation['busy_until'], f.clock[0] + 60)
        self.assertTrue(f.ns['friend_request_blocked'](f.client))

    async def test_pre_dispatch_failure_does_not_consume_click_or_prevent_retry(self):
        f = self.make_cleanup(pre_failure=True)
        for _ in range(3):
            if await self.settle(f): break
        self.assertEqual(len(f.clicks), 1)
        self.assertFalse(f.modal.visible)
        self.assertTrue(any('modal-disappeared clicked=True' in line for line in f.logs))

    async def test_existing_safety_guard_reports_reason_and_releases_without_replaying_old_cleanup(self):
        f = self.make_cleanup()
        f.client.quest_recovery_owner = 'existing-recovery'
        self.assertFalse(await self.settle(f))
        self.assertEqual(f.clicks, [])
        self.assertFalse(f.operation['clicked'])
        self.assertEqual(sum('decision=input-blocked reason=quest_recovery_owner' in line for line in f.logs), 1)
        f.client.quest_recovery_owner = None
        self.assertTrue(await self.settle(f))
        self.assertEqual(f.clicks, [])
        self.assertFalse(getattr(f.client, '_friend_follow_failure', None))
