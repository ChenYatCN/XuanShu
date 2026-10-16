"""Offline friend cleanup behavior using the real app's nested functions."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest

from wizwalker import Keycode
from wizwalker.memory import WindowFlags
from src.automation_ownership import automation_owner, get_client_automation_ownership
from src.utils import get_window_from_path


class Window:
    def __init__(self, name, visible=False, children=()):
        self.label, self.visible, self.nodes = name, visible, list(children)
        self.parent = None
        for node in self.nodes:
            node.parent = self
        self.failure = self.gray_failure = None
        self.disabled = self.gray = False

    async def name(self):
        return self.label

    async def children(self):
        return self.nodes

    async def get_windows_with_name(self, name):
        found = [self] if self.label == name else []
        for node in self.nodes:
            found.extend(await node.get_windows_with_name(name))
        return found

    async def get_parents(self):
        return [] if self.parent is None else [self.parent, *await self.parent.get_parents()]

    async def is_visible(self):
        if self.failure:
            raise self.failure
        return self.visible

    async def flags(self):
        return (WindowFlags.visible if self.visible else WindowFlags(0)) | (
            WindowFlags.disabled if self.disabled else WindowFlags(0))

    async def is_control_grayed(self):
        if self.gray_failure:
            raise self.gray_failure
        return self.gray


class Mouse:
    def __init__(self, fixture):
        self.fixture, self.depth = fixture, 0

    async def __aenter__(self):
        self.depth += 1
        self.fixture.on_enter()
        return self

    async def __aexit__(self, *_):
        self.depth -= 1

    async def click_window(self, button):
        test = self.fixture
        test.assertEqual(self.depth, 1)
        test.assertIs(get_client_automation_ownership(test.client)._owner_task, asyncio.current_task())
        test.clicks.append(button.label)
        test.on_click()
        if test.closes:
            (test.character if button.label == 'btnCharacterClose' else test.friends).visible = False


class FriendCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clicks, self.keys = [], []
        self.closes = True
        self.on_enter = self.on_click = self.on_wait = lambda: None
        self.character_button = Window('btnCharacterClose', True)
        self.list_button = Window('btnClose', True)
        self.character = Window('wndCharacter', False, [self.character_button])
        self.friends = Window('NewFriendsListWindow', False, [self.list_button])
        self.hud = Window('windowHUD', True, [self.friends, self.character])
        self.root = Window('root', True, [Window('WorldView', True, [self.hud])])
        self.client = self.make_client('p2')
        self.quester = self.make_client('p1')
        self.client.root_window = self.root
        self.client.mouse_handler = Mouse(self)
        async def send_key(*args, **kwargs):
            self.keys.append((args, kwargs))
        self.client.send_key = send_key
        async def sleep(_):
            self.on_wait()
            await asyncio.sleep(0)
        self.ns = dict(Client=object, hitter=self.client, quester=self.quester,
                       asyncio=SimpleNamespace(sleep=sleep, wait_for=asyncio.wait_for, timeout=asyncio.timeout,
                                               current_task=asyncio.current_task, CancelledError=asyncio.CancelledError,
                                               TimeoutError=asyncio.TimeoutError),
                       sys=__import__('sys'), asynccontextmanager=asynccontextmanager,
                       WindowFlags=WindowFlags, Keycode=Keycode, get_window_from_path=get_window_from_path,
                       automation_owner=automation_owner, get_client_automation_ownership=get_client_automation_ownership,
                       dungeon_session_for=lambda c: c.session)
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        names = {'friend_ui_is_open', 'close_stale_friend_ui', 'probe_mouse', 'friend_window_visible'}
        definitions = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
        exec(compile(ast.Module(body=definitions, type_ignores=[]), 'XuanShu.py', 'exec'), self.ns)

    def make_client(self, title):
        client = SimpleNamespace(title=title, questing_status=True, loading=False, battle=False,
                                 zone='World/Area', quest=42, goal=7, session=object())
        async def read(attr):
            return getattr(client, attr)
        client.is_loading = lambda: read('loading')
        client.in_battle = lambda: read('battle')
        client.zone_name = lambda: read('zone')
        client.quest_id = lambda: read('quest')
        client.goal_id = lambda: read('goal')
        return client

    async def cleanup(self):
        try:
            return await self.ns['close_stale_friend_ui'](self.client)
        finally:
            self.assertEqual(self.keys, [])

    def assert_released(self):
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        self.assertEqual(self.client.mouse_handler.depth, 0)

    async def test_both_open_close_character_then_list_with_confirmation(self):
        self.character.visible = self.friends.visible = True
        def before_click():
            if self.clicks == ['btnCharacterClose', 'btnClose']:
                self.assertFalse(self.character.visible)
        self.on_click = before_click
        await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose', 'btnClose'])
        self.assertFalse(self.character.visible or self.friends.visible)
        self.assert_released()

    async def test_only_list_open_does_not_open_character(self):
        self.friends.visible = True
        await self.cleanup()
        self.assertEqual(self.clicks, ['btnClose'])
        self.assertFalse(self.character.visible or self.friends.visible)

    async def test_only_character_open_does_not_open_list(self):
        self.character.visible = True
        await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'])
        self.assertFalse(self.character.visible or self.friends.visible)

    async def test_both_closed_returns_without_input_or_mouse(self):
        self.on_enter = lambda: self.fail('closed windows must not acquire mouse')
        await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_absent_windows_returns_without_input(self):
        self.hud.nodes.clear()
        await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_hidden_hud_parent_does_not_close_flag_visible_child(self):
        self.character.visible = self.friends.visible = True
        self.hud.visible = False
        await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_visible_read_error_is_not_closed(self):
        self.character.failure = ValueError('unreadable')
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*可见状态读取失败'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_missing_close_button_names_window(self):
        self.character.visible = True
        self.character.nodes.clear()
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*关闭按钮不存在'):
            await self.cleanup()
        self.assert_released()

    async def test_close_button_read_failure_names_window(self):
        self.friends.visible = True
        self.list_button.failure = ValueError('button unreadable')
        with self.assertRaisesRegex(RuntimeError, 'NewFriendsListWindow.*关闭按钮状态读取失败'):
            await self.cleanup()
        self.assert_released()

    async def test_disabled_close_button_does_not_click(self):
        self.character.visible = True
        self.character_button.disabled = True
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*关闭按钮.*灰'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_gray_close_button_does_not_click(self):
        self.character.visible = True
        self.character_button.gray = True
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*关闭按钮.*灰'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_hidden_close_button_does_not_click(self):
        self.friends.visible = True
        self.list_button.visible = False
        with self.assertRaisesRegex(RuntimeError, 'NewFriendsListWindow.*关闭按钮不可见'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])

    async def test_click_does_not_close_bounded_and_never_clicks_list(self):
        self.character.visible = self.friends.visible = True
        self.closes = False
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*3.*仍可见'):
            await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'] * 3)
        self.assertTrue(self.friends.visible)
        self.assert_released()

    async def test_post_click_read_error_does_not_continue_to_list(self):
        self.character.visible = self.friends.visible = True
        self.on_wait = lambda: setattr(self.character, 'failure', ValueError('lost window'))
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*可见状态读取失败'):
            await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'])
        self.assert_released()

    async def test_window_closed_while_acquiring_mouse_does_not_click(self):
        self.character.visible = True
        self.on_enter = lambda: setattr(self.character, 'visible', False)
        await self.cleanup()
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_context_change_while_acquiring_mouse_aborts(self):
        self.character.visible = True
        self.on_enter = lambda: setattr(self.quester, 'goal', 8)
        with self.assertRaisesRegex(RuntimeError, '上下文.*改变'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_loading_battle_and_existing_coordinator_block_input(self):
        self.character.visible = True
        for attr in ('loading', 'battle', 'quest_party_entry_active', 'quest_party_safe_return_active',
                     'post_combat_movement_active', 'refilling_potions', 'quest_recovery_owner'):
            with self.subTest(attr=attr):
                setattr(self.quester, attr, True)
                with self.assertRaises(RuntimeError):
                    await self.cleanup()
                setattr(self.quester, attr, False)
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_cancellation_during_confirmation_releases_ownership(self):
        self.character.visible = self.friends.visible = True
        def cancel():
            raise asyncio.CancelledError()
        self.on_wait = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'])
        self.assert_released()

    async def test_waiting_for_another_owner_cancel_preserves_owner(self):
        self.character.visible = True
        owner = get_client_automation_ownership(self.client)
        async with automation_owner(self.client, 'existing-owner'):
            task = asyncio.create_task(self.cleanup())
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(owner.owner_label, 'existing-owner')
            self.assertIs(owner._owner_task, asyncio.current_task())
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_context_change_while_waiting_owner_does_not_click(self):
        self.character.visible = True
        async with automation_owner(self.client, 'existing-owner'):
            task = asyncio.create_task(self.cleanup())
            await asyncio.sleep(0)
            self.quester.zone = 'World/NewArea'
        with self.assertRaisesRegex(RuntimeError, '上下文.*改变'):
            await task
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_button_runtime_read_error_includes_window(self):
        self.friends.visible = True
        self.list_button.gray_failure = RuntimeError('control memory unavailable')
        with self.assertRaisesRegex(RuntimeError, 'NewFriendsListWindow.*关闭按钮状态读取失败'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_context_runtime_read_error_includes_window(self):
        self.character.visible = True
        async def unavailable():
            raise RuntimeError('quest memory unavailable')
        self.quester.quest_id = unavailable
        with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*上下文读取失败'):
            await self.cleanup()
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_owner_wait_timeout_does_not_clear_other_owner(self):
        self.character.visible = True
        self.ns['asyncio'].timeout = lambda _: asyncio.timeout(.02)
        async with automation_owner(self.client, 'existing-owner'):
            task = asyncio.create_task(self.cleanup())
            with self.assertRaisesRegex(RuntimeError, 'wndCharacter.*超时'):
                await task
            self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'existing-owner')
        self.assertEqual(self.clicks, [])
        self.assert_released()

    async def test_cancel_during_click_restores_same_task_existing_owner(self):
        self.character.visible = True
        def cancel():
            raise asyncio.CancelledError()
        self.on_click = cancel
        async with automation_owner(self.client, 'existing-owner'):
            with self.assertRaises(asyncio.CancelledError):
                await self.cleanup()
            self.assertEqual(get_client_automation_ownership(self.client).owner_label, 'existing-owner')
            self.assertEqual(self.client.mouse_handler.depth, 0)
        self.assert_released()

    async def test_new_session_after_character_close_blocks_list(self):
        self.character.visible = self.friends.visible = True
        self.on_wait = lambda: setattr(self.quester, 'session', object())
        with self.assertRaisesRegex(RuntimeError, 'NewFriendsListWindow.*上下文.*改变'):
            await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'])
        self.assert_released()

    async def test_questing_stop_during_confirmation_blocks_list(self):
        self.character.visible = self.friends.visible = True
        self.on_wait = lambda: setattr(self.client, 'questing_status', False)
        with self.assertRaisesRegex(RuntimeError, 'NewFriendsListWindow.*任务停止'):
            await self.cleanup()
        self.assertEqual(self.clicks, ['btnCharacterClose'])
        self.assert_released()


if __name__ == '__main__':
    unittest.main()
