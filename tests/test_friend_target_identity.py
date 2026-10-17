"""Automatic followers must keep the reference name through friend lookup."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from wizwalker.memory import WindowFlags
from src import utils
from tests.test_friend_teleport_matching import row
from tests.test_full_chain_415_restore import entry


class FriendTargetIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p3', questing_status=True,
            mouse_handler=AsyncMock(), root_window=AsyncMock(),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False))
        self.list, self.arrow = AsyncMock(), AsyncMock()

    async def match(self, text, name='Mason Flame', icon=0):
        self.list.maybe_text.return_value = text
        return await utils._cycle_friends_list(self.client, self.arrow, self.list,
                                               icon, 2, name, 1)

    async def test_different_follower_order_and_duplicate_icons_keep_exact_name(self):
        target = row('Mason Flame', icon=5, icon_list=2)
        other = row('Other Wizard', icon=0, icon_list=2)
        for text, index in ((target+other+other, 0), (other+other+target, 2)):
            with self.subTest(index=index):
                match, actual = await self.match(text)
                self.assertIsNotNone(match)
                self.assertEqual(match.group('name'), 'Mason Flame')
                self.assertEqual(actual, index)

    async def test_exact_name_is_normalized_and_duplicate_full_names_are_ambiguous(self):
        match, _ = await self.match(row('Mason Flame', icon_list=2), '  MASON   Flame ')
        self.assertIsNotNone(match)
        with self.assertRaises(ValueError) as caught:
            await self.match(row('Mason Flame', icon_list=2)*2)
        self.assertEqual(getattr(caught.exception, 'reason', None), 'ambiguous-friend-match')
        self.assertEqual(caught.exception.candidate_count, 2)

    async def test_automatic_icon_without_name_cannot_select_any_friend(self):
        with self.assertRaises(ValueError) as caught:
            await self.match(row('Other Wizard', icon_list=2), name=None)
        self.assertEqual(getattr(caught.exception, 'reason', None), 'quester-name-unavailable')
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_wrapper_never_discards_full_name_after_lookup_failure(self):
        lookup = AsyncMock(side_effect=ValueError('target absent'))
        wrapper = entry('teleport_to_quester_from_friend_list',
                        dict(teleport_to_friend_from_list=lookup))
        wrapper.__kwdefaults__ = dict(before_request=None, on_request_sent=None)
        with self.assertRaises(ValueError):
            await wrapper(self.client, 'Mason Flame', (2, 0))
        self.assertEqual(lookup.await_count, 1)
        self.assertEqual(lookup.await_args.kwargs['name'], 'Mason Flame')

    async def test_known_reference_name_reaches_lookup(self):
        lookup = AsyncMock()
        wrapper = entry('teleport_to_quester_from_friend_list',
                        dict(teleport_to_friend_from_list=lookup))
        wrapper.__kwdefaults__ = dict(before_request=None, on_request_sent=None)
        await wrapper(self.client, 'Mason Flame', (2, 0))
        self.assertEqual(lookup.await_args.kwargs['name'], 'Mason Flame')

    async def test_actual_utility_failure_reasons_and_no_false_request(self):
        for cause, reason in (('success', None), ('ui', 'friend-ui-not-ready'),
                ('missing', 'quester-not-found'), ('duplicate', 'ambiguous-friend-match'),
                ('disabled', 'friend-entry-found-but-tp-unavailable'),
                ('click', 'friend-tp-click-failed'), ('stopped', 'friend-ui-not-ready'),
                ('reordered', 'friend-ui-not-ready')):
            with self.subTest(cause=cause):
                friends, page, profile, go = [AsyncMock() for _ in range(4)]
                friends.is_visible.return_value = profile.is_visible.return_value = go.is_visible.return_value = True
                go.is_control_grayed.return_value = cause == 'disabled'
                go.flags.return_value = WindowFlags.visible
                profile.get_parents.return_value = []
                self.client.root_window.get_windows_with_name.return_value = [profile]
                page.maybe_text.return_value = '<center>1 / 1</center>'
                self.client.questing_status = True
                target = row('Mason Flame', icon_list=2)
                self.list.maybe_text.side_effect = None
                self.list.maybe_text.return_value = (row('Other Wizard', icon_list=2) if cause=='missing'
                    else target*2 if cause=='duplicate' else target)
                named = {'NewFriendsListWindow':friends, 'listFriends':self.list,
                         'PageNumber':page, 'btnArrowDown':self.arrow, 'btnGoToFriend':go}
                async def online(*_):
                    if cause=='ui': raise ValueError('UI label unavailable')
                async def select(*_):
                    if cause=='click': raise ValueError('click failed')
                reads = [0]
                async def list_text():
                    reads[0] += 1
                    if reads[0] >= 3:
                        if cause=='stopped': self.client.questing_status = False
                        if cause=='reordered': return row('Other Wizard', icon_list=2)+target
                    return target
                if cause in ('stopped','reordered'):
                    self.list.maybe_text.side_effect = list_text
                with patch.object(utils, '_maybe_get_named_window', AsyncMock(side_effect=lambda p,n:named[n])), \
                     patch.object(utils, '_cycle_to_online_friends', online), \
                     patch.object(utils, '_click_on_friend', AsyncMock(side_effect=select)) as choose, \
                     patch.object(utils, '_request_friend_teleport', AsyncMock()) as request:
                    if reason is None:
                        await utils.teleport_to_friend_from_list(self.client,name='Mason Flame', strict_identity=True)
                        choose.assert_awaited_once()
                        request.assert_awaited_once()
                    else:
                        with self.assertRaises(ValueError) as caught:
                            await utils.teleport_to_friend_from_list(self.client,name='Mason Flame', strict_identity=True)
                        self.assertEqual(getattr(caught.exception,'reason',None),reason)
                        request.assert_not_awaited()
                    if cause in ('stopped','reordered'): choose.assert_not_awaited()

    async def test_name_read_uses_reference_owner_without_cross_client_lock(self):
        import asyncio
        from src.questing import Quester
        from src.automation_ownership import get_client_automation_ownership
        reference = SimpleNamespace(title='p1',wizard_name=None)
        async def read(client):
            self.assertIs(client,reference)
            self.assertTrue(get_client_automation_ownership(reference).locked)
            self.assertFalse(get_client_automation_ownership(self.client).locked)
            await asyncio.sleep(0)
            client.wizard_name = 'Mason Flame'
        prepare = entry('prepare_quester_name',dict(set_wizard_name_from_character_screen=read))
        with patch.object(Quester,'open_character_screen',AsyncMock()) as opened, \
             patch.object(Quester,'close_character_screen',AsyncMock()):
            await asyncio.gather(prepare(reference),prepare(reference))
        opened.assert_awaited_once()
        self.assertEqual(reference.wizard_name,'Mason Flame')
        self.assertFalse(get_client_automation_ownership(reference).locked)

    async def test_explicit_icon_call_remains_available_when_questing_is_on(self):
        friends, page, profile = [AsyncMock() for _ in range(3)]
        friends.is_visible.return_value = True
        page.maybe_text.return_value = '<center>1 / 1</center>'
        self.list.maybe_text.return_value = row('Other Wizard',icon_list=2)
        named = {'NewFriendsListWindow':friends,'listFriends':self.list,
                 'btnArrowDown':self.arrow,'PageNumber':page,'wndCharacter':profile}
        with patch.object(utils,'_maybe_get_named_window',AsyncMock(side_effect=lambda p,n:named[n])), \
             patch.object(utils,'_cycle_to_online_friends',AsyncMock()), \
             patch.object(utils,'_click_on_friend',AsyncMock()) as choose, \
             patch.object(utils,'_request_friend_teleport',AsyncMock()) as request:
            await utils.teleport_to_friend_from_list(self.client,icon_list=2,icon_index=0)
        choose.assert_awaited_once()
        request.assert_awaited_once()
