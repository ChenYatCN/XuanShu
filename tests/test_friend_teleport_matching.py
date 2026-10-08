import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, call, patch

from wizwalker.memory import WindowFlags
from src.utils import _cycle_friends_list, close_friend_windows, teleport_to_friend_from_list


def row(name, *, icon=0, icon_list=1):
    return (f'<Y;0><X;0><indent;0><Color;FFFFFF><left>'
            f'<icon;FriendsList/Friend_Icon_List_0{icon_list}.dds;0;0;{icon}></left>'
            f'<Y;0><X;0><indent;0><Color;FFFFFF><left><COLOR;FFFFFF>{name}<br>')


class FriendMatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client, self.arrow, self.list = AsyncMock(), AsyncMock(), AsyncMock()

    async def match(self, names, name=None, *, icon=None, icon_list=None, page=1):
        self.list.maybe_text.return_value = ''.join(row(n) for n in names)
        return await _cycle_friends_list(self.client, self.arrow, self.list, icon, icon_list, name, page)

    async def test_screenshot_first_name_matches_unique_full_chinese_name(self):
        match, index = await self.match(['寒斯 玛瑙', '梅森 火焰炽焰', '伊桑 黑暗之眼', '凯伊', '马龙'], '梅森')
        self.assertEqual(match.group('name'), '梅森 火焰炽焰')
        self.assertEqual(index, 1)

    async def test_full_name_selects_correct_same_first_name_friend(self):
        match, index = await self.match(['梅森 冰霜', '梅森 火焰炽焰'], '梅森 火焰炽焰')
        self.assertEqual(index, 1)
        self.assertEqual(match.group('name'), '梅森 火焰炽焰')

    async def test_name_whitespace_and_case_are_normalized(self):
        match, _ = await self.match(['Mason  FireFlame'], '  MASON\u3000FireFlame  ')
        self.assertIsNotNone(match)

    async def test_short_name_never_picks_first_of_multiple_candidates(self):
        for names in (['梅森 冰霜', '梅森 火焰炽焰'], ['梅森', '梅森 火焰炽焰']):
            with self.assertRaisesRegex(ValueError, '不唯一'):
                await self.match(names, '梅森')
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_prefix_inside_a_different_first_name_is_not_a_match(self):
        match, _ = await self.match(['梅森斯 火焰', 'Masonry Stone'], '梅森')
        self.assertIsNone(match)
        match, _ = await self.match(['Masonry Stone'], 'Mason')
        self.assertIsNone(match)

    async def test_duplicate_exact_name_is_ambiguous(self):
        with self.assertRaisesRegex(ValueError, '不唯一'):
            await self.match(['梅森 火焰炽焰', '梅森 火焰炽焰'], '梅森 火焰炽焰')

    async def test_icon_can_disambiguate_but_duplicate_icon_cannot(self):
        self.list.maybe_text.return_value = row('梅森 冰霜', icon=1) + row('梅森 火焰炽焰', icon=2)
        match, index = await _cycle_friends_list(self.client, self.arrow, self.list, 2, 1, '梅森', 1)
        self.assertEqual(index, 1)
        self.assertEqual(match.group('name'), '梅森 火焰炽焰')
        self.list.maybe_text.return_value = row('梅森 冰霜', icon=2) + row('梅森 火焰炽焰', icon=2)
        with self.assertRaisesRegex(ValueError, '不唯一'):
            await _cycle_friends_list(self.client, self.arrow, self.list, 2, 1, None, 1)

    async def test_missing_and_empty_friend_list_have_no_click(self):
        match, _ = await self.match(['伊桑 黑暗之眼'], '梅森')
        self.assertIsNone(match)
        self.list.maybe_text.return_value = None
        match, _ = await _cycle_friends_list(self.client, self.arrow, self.list, None, None, '梅森', 1)
        self.assertIsNone(match)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_unique_match_keeps_original_row_index_and_forward_pagination(self):
        match, index = await self.match([f'玩家{i}' for i in range(11)] + ['梅森 火焰炽焰'], '梅森')
        self.assertEqual(index, 11)
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.arrow)

    async def test_previous_page_does_not_click_the_same_row_on_wrong_page(self):
        with self.assertRaisesRegex(ValueError, '前一页'):
            await self.match(['梅森 火焰炽焰'], '梅森', page=2)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_actual_teleport_utility_reaches_selected_friend_without_esc(self):
        friends, character, page = AsyncMock(), AsyncMock(), AsyncMock()
        friends.is_visible.return_value = True
        character.is_visible.return_value = True
        character.get_parents.return_value = []
        self.client.is_loading.return_value = False
        self.client.root_window.get_windows_with_name.return_value = [character]
        page.maybe_text.return_value = '<center>1 / 1</center>'
        self.list.maybe_text.return_value = row('梅森 火焰炽焰')
        named = {'NewFriendsListWindow': friends, 'listFriends': self.list,
                 'PageNumber': page, 'btnArrowDown': self.arrow, 'wndCharacter': character}
        with patch('src.utils._maybe_get_named_window', new=AsyncMock(side_effect=lambda parent, name: named[name])), \
                patch('src.utils._cycle_to_online_friends', new=AsyncMock()), \
                patch('src.utils._click_on_friend', new=AsyncMock()) as select, \
                patch('src.utils._teleport_to_friend', new=AsyncMock()) as teleport:
            await teleport_to_friend_from_list(self.client, name='梅森')
        select.assert_awaited_once_with(self.client, self.list, 0)
        teleport.assert_awaited_once_with(self.client, character)
        self.client.send_key.assert_not_awaited()


class FriendProfileTeleportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.is_loading.return_value = False
        self.friends, self.list, self.page, self.arrow = (
            AsyncMock() for _ in range(4))
        self.friends.is_visible.return_value = True
        self.list.maybe_text.return_value = row('梅森 火焰炽焰', icon=0, icon_list=2)
        self.page.maybe_text.return_value = '<center>1 / 1</center>'
        self.named = {'NewFriendsListWindow': self.friends, 'listFriends': self.list,
                      'PageNumber': self.page, 'btnArrowDown': self.arrow}
        self.teleport = AsyncMock()
        self.select = AsyncMock()
        self.sleep = AsyncMock()
        for patcher in (
                patch('src.utils._maybe_get_named_window', AsyncMock(
                    side_effect=lambda parent, name: self.named[name])),
                patch('src.utils._cycle_to_online_friends', AsyncMock()),
                patch('src.utils._click_on_friend', self.select),
                patch('src.utils._teleport_to_friend', self.teleport),
                patch('src.utils.asyncio.sleep', self.sleep)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def profile(self, *, visible=True, parent_visible=True):
        window, parent = AsyncMock(), AsyncMock()
        window.is_visible.return_value = visible
        parent.is_visible.return_value = parent_visible
        window.get_parents.return_value = [parent]
        return window

    async def test_hidden_duplicate_uses_only_visible_profile(self):
        hidden, active = self.profile(visible=False), self.profile()
        self.client.root_window.get_windows_with_name.return_value = [hidden, active]
        await teleport_to_friend_from_list(self.client, name='梅森')
        self.teleport.assert_awaited_once_with(self.client, active)
        self.client.send_key.assert_not_awaited()

    async def test_visible_flag_under_hidden_parent_is_not_active_profile(self):
        inactive, active = self.profile(parent_visible=False), self.profile()
        self.client.root_window.get_windows_with_name.return_value = [inactive, active]
        await teleport_to_friend_from_list(self.client, name='梅森')
        self.teleport.assert_awaited_once_with(self.client, active)

    async def test_multiple_visible_profiles_never_pick_first(self):
        self.client.root_window.get_windows_with_name.return_value = [self.profile(), self.profile()]
        with self.assertRaisesRegex(ValueError, '多个可见'):
            await teleport_to_friend_from_list(self.client, name='梅森')
        self.teleport.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.friends.write_flags.assert_not_awaited()

    async def test_no_visible_profile_waits_bounded_then_does_not_teleport(self):
        self.client.root_window.get_windows_with_name.return_value = [self.profile(visible=False)]
        with self.assertRaisesRegex(ValueError, '尚未显示'):
            await teleport_to_friend_from_list(self.client, name='梅森')
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count, 5)
        self.assertEqual(self.sleep.await_count, 4)
        self.teleport.assert_not_awaited()

    async def test_delayed_profile_is_re_read_and_selected(self):
        active = self.profile()
        self.client.root_window.get_windows_with_name.side_effect = [[], [], [active]]
        await teleport_to_friend_from_list(self.client, name='梅森')
        self.assertEqual(self.sleep.await_count, 2)
        self.teleport.assert_awaited_once_with(self.client, active)

    async def test_loading_or_disappearing_profile_never_teleports(self):
        for loading in (True, False):
            with self.subTest(loading=loading):
                self.client.is_loading.return_value = loading
                active = self.profile()
                if not loading:
                    active.is_visible.side_effect = [True, False]
                self.client.root_window.get_windows_with_name.return_value = [active]
                with self.assertRaisesRegex(ValueError, '关闭或客户端正在加载'):
                    await teleport_to_friend_from_list(self.client, name='梅森')
                self.teleport.assert_not_awaited()

    async def test_fish_icon_fallback_keeps_visible_profile_filter(self):
        active = self.profile()
        self.client.root_window.get_windows_with_name.return_value = [self.profile(visible=False), active]
        await teleport_to_friend_from_list(self.client, icon_list=2, icon_index=0)
        self.select.assert_awaited_once_with(self.client, self.list, 0)
        self.teleport.assert_awaited_once_with(self.client, active)

    async def test_actual_helper_clicks_selected_profile_teleport_and_confirmation(self):
        from wizwalker.extensions.scripting.utils import _teleport_to_friend
        active = self.profile()
        go, yes, close, modal = (AsyncMock() for _ in range(4))
        self.client.root_window.get_windows_with_name.return_value = [self.profile(visible=False), active]
        def lookup(parent, name):
            expected, target = {
                'btnGoToFriend': (active, go),
                'btnCharacterClose': (active, close),
                'MessageBoxModalWindow': (self.client.root_window, modal),
                'centerButton': (modal, yes),
            }[name]
            self.assertIs(parent, expected)
            return target
        with patch('src.utils._teleport_to_friend', _teleport_to_friend), \
                patch('wizwalker.extensions.scripting.utils._maybe_get_named_window',
                      AsyncMock(side_effect=lookup)):
            await teleport_to_friend_from_list(self.client, name='梅森')
        self.assertEqual(self.client.mouse_handler.click_window.await_args_list,
                         [call(go), call(yes), call(close)])
        self.friends.write_flags.assert_awaited_once()
        self.client.send_key.assert_not_awaited()


class CloseFriendWindowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.is_loading.return_value = False
        self.client.root_window.get_windows_with_name.return_value = []

    def panel(self, name, *, parent_visible=True, button=True):
        parent, panel, close = AsyncMock(), AsyncMock(), AsyncMock()
        parent.is_visible.return_value = parent_visible
        panel.is_visible.return_value = close.is_visible.return_value = True
        panel.get_parents.return_value = [parent]
        close.get_parents.return_value = [panel, parent]
        panel.get_windows_with_name.return_value = [close] if button else []
        panel.flags.return_value = WindowFlags.visible | WindowFlags.noclip
        self.client.root_window.get_windows_with_name.side_effect = lambda value: [panel] if value == name else []
        return panel, close

    async def test_clicks_only_current_friend_panel_close_button(self):
        for name, button_name in (('wndCharacter', 'btnCharacterClose'), ('NewFriendsListWindow', 'btnFriendListClose')):
            self.client.mouse_handler.click_window.reset_mock()
            panel, close = self.panel(name)
            self.assertTrue(await close_friend_windows(self.client))
            panel.get_windows_with_name.assert_awaited_once_with(button_name)
            self.client.mouse_handler.click_window.assert_awaited_once_with(close)
            self.client.send_key.assert_not_awaited()
            panel.write_flags.assert_not_awaited()

    async def test_hidden_parent_is_not_treated_as_open_friend_panel(self):
        panel, _ = self.panel('NewFriendsListWindow', parent_visible=False)
        self.assertFalse(await close_friend_windows(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()
        panel.write_flags.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_fallback_only_hides_exact_friend_panel_and_keeps_other_flags(self):
        panel, _ = self.panel('NewFriendsListWindow', button=False)
        panel.flags.return_value |= WindowFlags(64)  # Preserve bits not declared by the library.
        self.assertTrue(await close_friend_windows(self.client))
        panel.write_flags.assert_awaited_once_with(WindowFlags.noclip | WindowFlags.disabled | WindowFlags(64))
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_closed_panels_do_not_toggle_friends_or_open_menu(self):
        self.assertFalse(await close_friend_windows(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_panel_disappears_while_waiting_for_mouse_no_input(self):
        panel, _ = self.panel('NewFriendsListWindow')
        async def acquired():
            panel.is_visible.return_value = False
        self.client.mouse_handler.__aenter__.side_effect = acquired
        self.assertFalse(await close_friend_windows(self.client))
        self.client.mouse_handler.click_window.assert_not_awaited()
        panel.write_flags.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_loading_does_not_click_or_write_old_window(self):
        panel, _ = self.panel('NewFriendsListWindow')
        self.client.is_loading.return_value = True
        self.assertFalse(await close_friend_windows(self.client))
        panel.write_flags.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_actual_follow_cleanup_delegates_without_esc(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'close_stale_friend_ui')
        close = AsyncMock()
        ns = dict(Client=object, close_friend_windows=close)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'XuanShu.py', 'exec'), ns)
        await ns['close_stale_friend_ui'](self.client)
        close.assert_awaited_once_with(self.client)
        self.client.send_key.assert_not_awaited()
