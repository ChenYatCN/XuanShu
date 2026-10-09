import ast
import asyncio
import statistics
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.bot_targeting import resolve_bot_clients
from src.gui.commands import GUICommand, GUICommandType, GUIKeys
from src.hotkey_groups import client_available
from src.task_lifecycle import gather_owned


class TeleportHotkeySelectionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))

    def setUp(self):
        self.clients = [SimpleNamespace(
            title=f'p{i}', wizard_name=f'Wizard {i}', is_running=Mock(return_value=True),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=(i, 2, 3))),
            body=SimpleNamespace(position=AsyncMock(return_value=(i, 5, 6)),
                                 yaw=AsyncMock(return_value=i)),
            zone_name=AsyncMock(return_value='same'), is_loading=AsyncMock(return_value=False),
            teleport=AsyncMock(), send_key=AsyncMock(), mouse_handler=AsyncMock(),
        ) for i in range(1, 5)]
        self.ns = dict(
            asyncio=SimpleNamespace(sleep=AsyncMock(), gather=asyncio.gather,
                                    wait_for=asyncio.wait_for),
            statistics=statistics, logger=Mock(), walker=SimpleNamespace(clients=self.clients),
            foreground_client=self.clients[0], freecam_status=False,
            resolve_bot_clients=resolve_bot_clients, client_available=client_available,
            gather_owned=gather_owned, Keycode=SimpleNamespace(A='A', D='D'),
            navmap_tp=AsyncMock(), teleport_to_friend_from_list=AsyncMock(),
            Quester=Mock(), set_wizard_name_from_character_screen=AsyncMock(),
            xuanshu_gui=SimpleNamespace(GUICommandType=GUICommandType), GUIKeys=GUIKeys,
        )
        names = {'teleport_hotkey_clients', 'xyz_sync_hotkey', 'navmap_teleport_hotkey',
                 'mass_navmap_teleport_hotkey', 'friend_teleport_sync_hotkey',
                 'xyz_sync', 'navmap_teleport', 'friend_teleport_sync'}
        functions = [n for n in ast.walk(self.tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        exec(compile('from __future__ import annotations\n' +
                     ast.unparse(ast.Module(body=functions, type_ignores=[])),
                     'XuanShu.py', 'exec'), self.ns)

    async def test_switching_selection_isolates_actual_operations_for_all_four_actions(self):
        for selected in (['p1', 'p2'], ['p3', 'p4']):
            members = [c for c in self.clients if c.title in selected]
            for action in ('navmap_teleport_hotkey', 'mass_navmap_teleport_hotkey',
                           'friend_teleport_sync_hotkey', 'xyz_sync_hotkey'):
                with self.subTest(selected=selected, action=action):
                    self.ns['navmap_tp'].reset_mock()
                    self.ns['teleport_to_friend_from_list'].reset_mock()
                    for c in self.clients:
                        c.teleport.reset_mock()
                        c.send_key.reset_mock()
                        c.quest_position.position.reset_mock()
                        c.body.position.reset_mock()
                        c.mouse_handler.reset_mock()
                    await self.ns[action](selected)
                    source_number = int(selected[0][1:])
                    if action == 'navmap_teleport_hotkey':
                        self.ns['navmap_tp'].assert_awaited_once_with(members[0], (source_number, 2, 3))
                    elif action == 'mass_navmap_teleport_hotkey':
                        calls = self.ns['navmap_tp'].await_args_list
                        self.assertEqual([call.args[0].title for call in calls], selected)
                        self.assertEqual([call.args[1] for call in calls], [(source_number, 2, 3)] * 2)
                    elif action == 'friend_teleport_sync_hotkey':
                        self.ns['teleport_to_friend_from_list'].assert_awaited_once_with(
                            client=members[1], name=members[0].wizard_name)
                    else:
                        members[1].teleport.assert_awaited_once_with((source_number, 5, 6), yaw=source_number)
                    for c in self.clients:
                        if c not in members:
                            c.teleport.assert_not_awaited()
                            c.send_key.assert_not_awaited()
                            c.quest_position.position.assert_not_awaited()
                            c.body.position.assert_not_awaited()
                            c.mouse_handler.__aenter__.assert_not_awaited()

    async def test_empty_missing_or_disconnected_selection_never_falls_back(self):
        self.clients[1].is_running.return_value = False
        self.clients[2]._character_selection_active = True
        for titles in ([], None, ['missing'], ['p2'], ['p3']):
            for action in ('navmap_teleport_hotkey', 'mass_navmap_teleport_hotkey',
                           'friend_teleport_sync_hotkey', 'xyz_sync_hotkey'):
                self.ns['logger'].warning.reset_mock()
                await self.ns[action](titles)
                self.ns['logger'].warning.assert_called_once_with('未选择作用客户端，本次传送已取消。')
        self.ns['navmap_tp'].assert_not_awaited()
        self.ns['teleport_to_friend_from_list'].assert_not_awaited()
        for c in self.clients:
            c.teleport.assert_not_awaited()

    async def test_single_client_preserves_supported_quest_and_mass_teleport(self):
        await self.ns['navmap_teleport_hotkey'](['p3'])
        await self.ns['mass_navmap_teleport_hotkey'](['p3'])
        self.assertEqual([call.args[0].title for call in self.ns['navmap_tp'].await_args_list], ['p3', 'p3'])
        for action in ('friend_teleport_sync_hotkey', 'xyz_sync_hotkey'):
            self.ns['logger'].warning.reset_mock()
            await self.ns[action](['p3'])
            self.ns['logger'].warning.assert_called_once_with('当前分组客户端不足，本次传送已取消。')
        self.ns['teleport_to_friend_from_list'].assert_not_awaited()
        self.clients[2].teleport.assert_not_awaited()

    async def test_selected_foreground_remains_xyz_and_quest_reference(self):
        self.ns['foreground_client'] = self.clients[3]
        await self.ns['xyz_sync_hotkey'](['p3', 'p4'])
        self.clients[2].teleport.assert_awaited_once_with((4, 5, 6), yaw=4)
        await self.ns['navmap_teleport_hotkey'](['p3', 'p4'])
        self.ns['navmap_tp'].assert_awaited_once_with(self.clients[3], (4, 2, 3))
        self.ns['navmap_tp'].reset_mock()
        await self.ns['mass_navmap_teleport_hotkey'](['p3', 'p4'])
        self.assertEqual([call.args[1] for call in self.ns['navmap_tp'].await_args_list], [(4, 2, 3)] * 2)

    async def test_disconnect_is_excluded_and_cannot_supply_xyz_reference(self):
        self.clients[0].is_running.return_value = False
        await self.ns['xyz_sync_hotkey'](['p1', 'p3', 'p4'])
        self.clients[3].teleport.assert_awaited_once_with((3, 5, 6), yaw=3)
        self.clients[0].body.position.assert_not_awaited()

    async def test_friend_target_name_is_read_only_from_selected_first_client(self):
        self.clients[2].wizard_name = None
        quester = SimpleNamespace(open_character_screen=AsyncMock(), close_character_screen=AsyncMock())
        self.ns['Quester'].return_value = quester
        async def read_name(target):
            target.wizard_name = 'Selected Wizard'
        self.ns['set_wizard_name_from_character_screen'].side_effect = read_name
        await self.ns['friend_teleport_sync_hotkey'](['p3', 'p4'])
        self.ns['Quester'].assert_called_once_with(self.clients[2], [self.clients[2]], None)
        quester.close_character_screen.assert_awaited_once_with(self.clients[2])
        self.ns['teleport_to_friend_from_list'].assert_awaited_once_with(
            client=self.clients[3], name='Selected Wizard')

    async def test_unreadable_friend_target_cancels_without_icon_fallback(self):
        self.clients[2].wizard_name = None
        quester = SimpleNamespace(open_character_screen=AsyncMock(side_effect=RuntimeError('closed')),
                                 close_character_screen=AsyncMock())
        self.ns['Quester'].return_value = quester
        await self.ns['friend_teleport_sync_hotkey'](['p3', 'p4'])
        self.ns['teleport_to_friend_from_list'].assert_not_awaited()
        quester.close_character_screen.assert_awaited_once()

    async def test_actual_gui_dispatch_passes_selection_to_all_four_entries(self):
        gui = next(n for n in ast.walk(self.tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle_gui')
        cases = [case for n in ast.walk(gui) if isinstance(n, ast.Match) for case in n.cases
                 if isinstance(case.pattern, ast.MatchValue)
                 and isinstance(case.pattern.value, ast.Attribute)
                 and case.pattern.value.attr in ('Teleport', 'XYZSync', 'FriendTeleport')]
        driver = ast.parse('async def dispatch():\n for com in commands:\n  pass\n')
        driver.body[0].body[0].body = [ast.Match(subject=ast.parse('com.com_type', mode='eval').body, cases=cases)]
        ast.fix_missing_locations(driver)
        exec(compile(driver, 'XuanShu.py', 'exec'), self.ns)
        self.ns['commands'] = [
            GUICommand(GUICommandType.Teleport, {'key': GUIKeys.hotkey_quest_tp, 'clients': ['p3', 'p4']}),
            GUICommand(GUICommandType.Teleport, {'key': GUIKeys.mass_hotkey_mass_tp, 'clients': ['p3', 'p4']}),
            GUICommand(GUICommandType.XYZSync, {'clients': ['p3', 'p4']}),
            GUICommand(GUICommandType.FriendTeleport, {'clients': ['p3', 'p4']}),
        ]
        await self.ns['dispatch']()
        self.assertEqual([call.args[0].title for call in self.ns['navmap_tp'].await_args_list], ['p3', 'p3', 'p4'])
        self.clients[3].teleport.assert_awaited_once()
        self.ns['teleport_to_friend_from_list'].assert_awaited_once_with(client=self.clients[3], name='Wizard 3')
        self.clients[0].body.position.assert_not_awaited()
        self.clients[1].body.position.assert_not_awaited()
        self.ns['walker'].clients = []
        self.ns['logger'].warning.reset_mock()
        await self.ns['dispatch']()
        self.assertEqual(self.ns['logger'].warning.call_count, 4)
        for call in self.ns['logger'].warning.call_args_list:
            self.assertEqual(call.args, ('未选择作用客户端，本次传送已取消。',))
