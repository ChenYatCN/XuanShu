"""Special portal rows use the supported text layout and fresh bounded snapshots."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import ExitStack
from wizwalker import MemoryReadError, XYZ
from src import utils
from src.questing import Quester
from tests.test_chat_control_text import control

REAL_SLEEP=asyncio.sleep


def row(name,text,kind='ControlCheckBox'):
    window=control(text,kind=kind)
    window.name=AsyncMock(return_value=name)
    window.is_visible=AsyncMock(return_value=True)
    window.read_wide_string_from_offset=AsyncMock(side_effect=MemoryReadError(1065353216))
    if kind == 'ControlText':
        window.maybe_text=AsyncMock(return_value=text)
    return window


def page(number=1,maximum=3,destination='Aeriel'):
    option=row('opt0',destination)
    panel=SimpleNamespace(is_visible=AsyncMock(return_value=True),
        children=AsyncMock(return_value=[row('pageCount',f'<center>{number}/{maximum}</center>','ControlText'),option]))
    return panel,option


class PortalReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.zone='Empyrea/EM_Z15_Nexus'; self.goal=7
        self.panel,self.option=page()
        self.button=object()
        self.client=SimpleNamespace(title='p1',questing_status=True,refilling_potions=False,
            is_loading=AsyncMock(return_value=False),in_battle=AsyncMock(return_value=False),
            quest_id=AsyncMock(return_value=42),goal_id=AsyncMock(side_effect=lambda:self.goal),
            zone_name=AsyncMock(side_effect=lambda:self.zone),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(side_effect=lambda _:[self.panel])),
            mouse_handler=AsyncMock(),wait_for_zone_change=AsyncMock(side_effect=self.travel))
        async def sleep(delay): await REAL_SLEEP(0)
        self.stack.enter_context(patch('src.utils.asyncio.sleep',sleep))
        self.stack.enter_context(patch('src.utils.get_spiral_teleport_button',AsyncMock(return_value=self.button)))

    async def travel(self):
        self.zone='Empyrea/EM_Z01_Aeriel'
        self.panel.children.side_effect=MemoryReadError('menu destroyed after travel')

    async def test_checkbox_uses_existing_reader_and_success_never_rereads_destroyed_menu(self):
        self.assertTrue(await utils.new_portals_cycle(self.client,'aeriel'))
        self.option.read_wide_string_from_offset.assert_not_awaited()
        self.client.wait_for_zone_change.assert_awaited_once()
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count,2)
        self.assertEqual([c.args[0] for c in self.client.mouse_handler.click_window.await_args_list],
                         [self.option,self.button])

    async def test_stale_row_is_discarded_and_new_root_snapshot_can_recover(self):
        stale,old=page()
        old.read_typed.side_effect=MemoryReadError(3710247013414076416)
        root=self.client.root_window.get_windows_with_name
        root.side_effect=[[stale],[self.panel],[self.panel]]
        self.assertTrue(await utils.new_portals_cycle(self.client,'aeriel'))
        self.assertEqual(root.await_count,3)
        self.assertIs(self.client.mouse_handler.click_window.await_args_list[0].args[0],self.option)

    async def test_persistent_read_error_is_finite_failure_without_input(self):
        self.option.read_typed.side_effect=MemoryReadError(1065353216)
        self.assertFalse(await utils.new_portals_cycle(self.client,'aeriel'))
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count,3)
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.client.wait_for_zone_change.assert_not_awaited()

    async def test_actual_quest_caller_defers_only_failed_client_without_generic_fallback(self):
        self.option.read_typed.side_effect=MemoryReadError(1065353216)
        self.client.body=SimpleNamespace(position=AsyncMock(return_value=XYZ(10,20,0)))
        self.client.quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(100,200,0)))
        reader=Quester(self.client,[self.client],None)
        reader._npc_dialogue_visible=AsyncMock(return_value=False)
        reader.read_spiral_door_title=AsyncMock(return_value='Streamportal')
        reader.find_quest_zone_area_name=AsyncMock(return_value='aeriel')
        other=SimpleNamespace(questing_status=True)
        self.client._quest_region_transfer_diagnostic={'reason':'区域菜单文本读取失败'}
        with patch('src.questing.travel_region_menu',new=AsyncMock(return_value=False)) as select:
            self.assertTrue(await reader.new_world_doors(self.client))  # Handled; no generic fallback.
            self.assertTrue(await reader.new_world_doors(self.client))
        select.assert_awaited_once()
        self.assertTrue(self.client.questing_status)
        self.assertTrue(other.questing_status)
        self.assertIsNone(reader.d_location)
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count,0)

    async def test_programming_error_is_not_swallowed_as_a_ui_refresh(self):
        self.panel.children.side_effect=RuntimeError('unrelated defect')
        with self.assertRaisesRegex(RuntimeError,'unrelated defect'):
            await utils.new_portals_cycle(self.client,'aeriel')
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count,1)

    async def test_changed_task_ends_old_selection_without_failure_or_input(self):
        read=self.option.read_bytes.side_effect
        async def advance(*args):
            self.goal+=1
            return await read(*args)
        self.option.read_bytes.side_effect=advance
        self.assertIsNone(await utils.new_portals_cycle(self.client,'aeriel'))
        self.assertTrue(self.client.questing_status)
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_task_change_during_teleport_button_lookup_prevents_teleport(self):
        async def change(_client):
            self.goal+=1
            return self.button
        with patch('src.utils.get_spiral_teleport_button',AsyncMock(side_effect=change)):
            self.assertIsNone(await utils.new_portals_cycle(self.client,'aeriel'))
        self.assertEqual(self.client.mouse_handler.click_window.await_count,1)
        self.client.wait_for_zone_change.assert_not_awaited()

    async def test_pagination_reacquires_replacement_option_window(self):
        first,_=page(1,2,'Zanadu')
        second,option=page(2,2,'Aeriel')
        self.panel=first
        async def flip(name):
            self.assertEqual(name,'rightButton')
            self.panel=second
        self.client.mouse_handler.click_window_with_name.side_effect=flip
        self.assertTrue(await utils.new_portals_cycle(self.client,'aeriel'))
        self.assertIs(self.client.mouse_handler.click_window.await_args_list[0].args[0],option)
        self.assertEqual(self.client.root_window.get_windows_with_name.await_count,3)

    async def test_cancel_after_selection_does_not_click_teleport_and_releases_mouse(self):
        started=asyncio.Event()
        async def sleep(delay):
            if delay==.4:
                started.set(); await asyncio.Event().wait()
            await REAL_SLEEP(0)
        with patch('src.utils.asyncio.sleep',sleep):
            task=asyncio.create_task(utils.new_portals_cycle(self.client,'aeriel'))
            await asyncio.wait_for(started.wait(),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.client.mouse_handler.click_window.await_count,1)
        self.client.mouse_handler.__aexit__.assert_awaited_once()

