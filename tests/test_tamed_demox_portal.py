import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.interaction_prompts import portal_kind, resolve_portal_destination
from src.questing import Quester
from src.utils import new_portals_cycle


class PortalSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.page = 0., 1
        self.labels = [['流亡者营地', '墓都', '凡人原野', '啸狼荒原'], ['黑湖', '斯科洛曼斯']]
        self.selected = None
        self.events, self.options = [], {}
        self.accept_selection = self.travel_enabled = self.change_page = True
        self.button = self.node('teleportButton')
        self.button.is_control_grayed.side_effect = lambda: self.selected is None or not self.travel_enabled
        self.panel = self.node('optionWindow')
        self.panel.children.side_effect = self.children
        self.container = SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[self.panel]))
        self.button.parent.return_value = self.container
        self.mouse = MagicMock()
        self.mouse.click_window = AsyncMock(side_effect=self.click)
        self.client = SimpleNamespace(title='p1', questing_status=True, refilling_potions=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Darkmoor/Camp'),
            mouse_handler=self.mouse, wait_for_zone_change=AsyncMock())
        real_sleep = asyncio.sleep
        async def tick(seconds):
            self.now += seconds
            await real_sleep(0)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.utils.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.utils.asyncio.sleep', AsyncMock(side_effect=tick)))
        stack.enter_context(patch('src.utils.get_spiral_teleport_button', AsyncMock(return_value=self.button)))
        stack.enter_context(patch('src.utils.read_control_text', AsyncMock(side_effect=lambda w: w.text)))
        stack.enter_context(patch('src.utils.read_control_checkbox_text', AsyncMock(side_effect=lambda w: w.text)))
        stack.enter_context(patch('src.utils.logger'))

    def node(self, name, text=''):
        node = AsyncMock()
        node.text = text
        node.name.return_value = name
        node.is_visible.return_value = True
        node.is_control_grayed.return_value = False
        return node

    def children(self):
        nodes = [self.node('pageCount', f'<center>{self.page}/{len(self.labels)}</center>'),
                 self.node('leftButton'), self.node('rightButton')]
        for index, text in enumerate(self.labels[self.page - 1]):
            key = (self.page, index)
            if key not in self.options:
                self.options[key] = self.node(f'opt{index}')
                self.options[key].maybe_checked.side_effect = lambda key=key: self.selected == key
            self.options[key].text = text
            nodes.append(self.options[key])
        return nodes

    async def click(self, window):
        name = await window.name()
        self.events.append((self.page, name))
        if name in ('rightButton', 'leftButton') and self.change_page:
            self.page += 1 if name == 'rightButton' else -1
        elif name.startswith('opt') and self.accept_selection:
            self.selected = (self.page, int(name[-1]))
        elif name == 'teleportButton':
            self.client.zone_name.return_value = 'Darkmoor/BlackLagoon'

    def assert_no_travel(self):
        self.assertFalse(any(name == 'teleportButton' for _, name in self.events))

    async def test_second_page_chinese_button_and_travel_once(self):
        self.assertTrue(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [(1, 'rightButton'), (2, 'opt0'), (2, 'teleportButton')])
        self.client.wait_for_zone_change.assert_awaited_once_with('Darkmoor/Camp')

    async def test_english_button(self):
        self.labels[1][0] = 'Black Lagoon'
        self.assertTrue(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.selected, (2, 0))

    async def test_first_page_match_returns_after_full_scan(self):
        self.assertTrue(await new_portals_cycle(self.client, 'graveholm'))
        self.assertEqual(self.events, [(1, 'rightButton'), (2, 'leftButton'), (1, 'opt1'), (1, 'teleportButton')])

    async def test_initial_second_page_scans_from_first(self):
        self.page = 2
        self.assertTrue(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events[0], (2, 'leftButton'))

    async def test_unknown_destination_never_picks_first(self):
        self.assertFalse(await new_portals_cycle(self.client, 'unknown'))
        self.assertIsNone(self.selected)
        self.assert_no_travel()

    async def test_duplicate_matches_across_pages(self):
        self.labels[0][0] = 'Black Lagoon'
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertIsNone(self.selected)
        self.assert_no_travel()

    async def test_duplicate_matches_same_page(self):
        self.labels[1][1] = 'Black Lagoon'
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assert_no_travel()

    async def test_longer_location_not_substring_match(self):
        self.labels[1][0] = 'Black Lagoon Music Room'
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assert_no_travel()

    async def test_unconfirmed_page_change_clicks_arrow_once(self):
        self.change_page = False
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [(1, 'rightButton')])

    async def test_gray_destination_not_clicked(self):
        self.page = 2
        self.children()
        self.options[(2, 0)].is_control_grayed.return_value = True
        self.page = 1
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertIsNone(self.selected)

    async def test_selection_not_confirmed_never_travels(self):
        self.accept_selection = False
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assert_no_travel()

    async def test_disabled_travel_not_clicked(self):
        self.travel_enabled = False
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assert_no_travel()

    async def test_quest_change_after_selection_cancels_travel(self):
        guard = AsyncMock(side_effect=lambda: self.selected is None)
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon', before_input=guard))
        self.assert_no_travel()

    async def test_hidden_duplicate_menu_ignored(self):
        hidden = self.node('optionWindow')
        hidden.is_visible.return_value = False
        self.container.get_windows_with_name.return_value = [hidden, self.panel]
        self.assertTrue(await new_portals_cycle(self.client, 'black lagoon'))

    async def test_multiple_visible_menus_block_input(self):
        self.container.get_windows_with_name.return_value = [self.panel, self.node('optionWindow')]
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [])

    async def test_cancellation_propagates(self):
        self.mouse.click_window.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await new_portals_cycle(self.client, 'black lagoon')

    async def test_no_zone_change_never_reports_success(self):
        self.client.wait_for_zone_change.side_effect = TimeoutError('unchanged')
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(sum(name == 'teleportButton' for _, name in self.events), 1)

    async def test_busy_stopped_states_block_input(self):
        for attr in ('is_loading', 'in_battle'):
            getattr(self.client, attr).return_value = True
            self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
            getattr(self.client, attr).return_value = False
        self.client.refilling_potions = True
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.client.refilling_potions = False
        self.client.questing_status = False
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [])

    async def test_invalid_page_count_is_not_clicked(self):
        self.panel.children.side_effect = None
        self.panel.children.return_value = [self.node('pageCount', 'not a page')]
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [])

    async def test_disabled_page_arrow_is_not_clicked(self):
        original = self.children
        def children():
            nodes = original()
            nodes[2].is_control_grayed.return_value = True
            return nodes
        self.panel.children.side_effect = children
        self.assertFalse(await new_portals_cycle(self.client, 'black lagoon'))
        self.assertEqual(self.events, [])


class TamedDemoxQuestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p1', questing_status=True, refilling_potions=False,
            quest_recovery_owner=None, is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False), zone_name=AsyncMock(return_value='Darkmoor/DM_Z00_OutsidersCamp'))
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_spiral_door_title = AsyncMock(return_value='<center>Tamed Demox</center>')
        self.quester.read_quest_txt = AsyncMock(return_value='拜访 NPC 地点：Black Lagoon')
        self.snapshot = (123, 1, '拜访 NPC 地点：Black Lagoon')
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot)

    def test_verified_title_and_bilingual_exact_destination(self):
        self.assertEqual(portal_kind('<string;GUI2_00002095>'), 'tamed_demox')
        self.assertEqual(portal_kind('Tamed Demox'), 'tamed_demox')
        self.assertEqual(resolve_portal_destination('黑湖', ['black lagoon'], exact=True), 'black lagoon')
        self.assertIsNone(resolve_portal_destination('Black Lagoon Music Room', ['black lagoon'], exact=True))

    async def test_task_location_not_current_region_selects_destination(self):
        with patch('src.questing.new_portals_cycle', AsyncMock(return_value=True)) as select:
            self.assertTrue(await self.quester.new_world_doors(self.client))
        self.assertEqual(select.await_args.args, (self.client, 'black lagoon'))
        guard = select.await_args.kwargs['before_input']
        self.assertTrue(await guard())
        self.snapshot = (123, 2, '新任务 地点：Graveholm')
        self.assertFalse(await guard())

    async def test_chinese_task_location(self):
        self.quester.read_quest_txt.return_value = '拜访 NPC 地点：黑湖'
        with patch('src.questing.new_portals_cycle', AsyncMock(return_value=True)) as select:
            await self.quester.new_world_doors(self.client)
        self.assertEqual(select.await_args.args[1], 'black lagoon')

    async def test_unknown_or_ambiguous_location_clears_stale_destination(self):
        for text in ('任务不可读', '拜访 NPC 地点：Unknown', '拜访 NPC 地点：Black Lagoon / Graveholm',
                     '拜访 NPC 地点：Black Lagoon Music Room'):
            self.quester.d_location = 'graveholm'
            self.quester.read_quest_txt.return_value = text
            with patch('src.questing.new_portals_cycle', AsyncMock()) as select:
                self.assertTrue(await self.quester.new_world_doors(self.client))
            select.assert_not_awaited()
            self.assertIsNone(self.quester.d_location)

    async def test_failed_leader_does_not_dispatch_followers(self):
        self.quester.current_leader_client = self.client
        with patch('src.questing.new_portals_cycle', AsyncMock(return_value=False)) as select:
            await self.quester.handle_spiral_navigation()
        select.assert_awaited_once()
        self.assertIsNone(self.quester.d_location)

    async def test_normal_world_gate_keeps_existing_route(self):
        self.quester.read_spiral_door_title.return_value = '世界之门'
        self.assertFalse(await self.quester.new_world_doors(self.client))
        self.quester._dungeon_quest_snapshot.assert_not_awaited()

    async def test_separate_visible_title_in_current_menu_is_recognized(self):
        title, button, text = AsyncMock(), AsyncMock(), AsyncMock()
        title.is_visible.return_value = False
        text.maybe_read_type_name.return_value = 'ControlText'
        self.quester._visible_window_nodes = AsyncMock(return_value=[(text, [])])
        self.quester._window_text = AsyncMock(return_value='<center>Tamed Demox</center>')
        with (patch('src.questing.get_window_from_path', AsyncMock(return_value=title)),
              patch('src.questing.get_spiral_teleport_button', AsyncMock(return_value=button))):
            result = await Quester.read_spiral_door_title(self.quester, self.client)
        self.assertEqual(portal_kind(result), 'tamed_demox')
        title.maybe_text.assert_not_awaited()

    async def test_duplicate_rendered_titles_are_not_guessed(self):
        title, button, text = AsyncMock(), AsyncMock(), AsyncMock()
        title.is_visible.return_value = False
        text.maybe_read_type_name.return_value = 'ControlText'
        self.quester._visible_window_nodes = AsyncMock(return_value=[(text, []), (text, [])])
        self.quester._window_text = AsyncMock(return_value='Tamed Demox')
        with (patch('src.questing.get_window_from_path', AsyncMock(return_value=title)),
              patch('src.questing.get_spiral_teleport_button', AsyncMock(return_value=button))):
            self.assertEqual(await Quester.read_spiral_door_title(self.quester, self.client), '')

    async def test_unreadable_title_fallback_preserves_safe_empty_result(self):
        with (patch('src.questing.get_window_from_path', AsyncMock(return_value=None)),
              patch('src.questing.get_spiral_teleport_button', AsyncMock(side_effect=RuntimeError('unreadable')))):
            self.assertEqual(await Quester.read_spiral_door_title(self.quester, self.client), '')

    async def test_followers_use_confirmed_leader_location_not_their_own_quest(self):
        self.client.process_id = 1
        follower = SimpleNamespace(process_id=2)
        self.quester.clients = [self.client, follower]
        self.quester.current_leader_client = self.client
        self.quester.current_leader_pid = 1
        with (patch('src.questing.new_portals_cycle', AsyncMock(return_value=True)) as select,
              patch('src.questing.is_visible_by_path', AsyncMock(return_value=True))):
            await self.quester.handle_spiral_navigation()
        self.assertEqual(select.await_count, 2)
        self.assertEqual(select.await_args.args, (follower, 'black lagoon'))

    async def test_solo_open_tamed_menu_uses_selection_not_bare_travel(self):
        for method in ('_maybe_handle_overgrown_estate', '_maybe_handle_darkmoor_castle',
                       '_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
                       '_maybe_handle_bumbles_pet', 'handle_pending_dungeon_confirmation'):
            setattr(self.quester, method, AsyncMock(return_value=False))
        with (patch('src.questing.close_npc_quest_menu', AsyncMock(return_value=False)),
              patch('src.questing.close_automation_popup', AsyncMock(return_value=False)),
              patch('src.questing.is_spiral_door_open', AsyncMock(return_value=True)),
              patch('src.questing.new_portals_cycle', AsyncMock(return_value=True)) as select,
              patch('src.questing.spiral_door_with_quest', AsyncMock()) as bare,
              patch('src.questing.collision_tp', AsyncMock()) as move):
            await self.quester.auto_quest_solo()
        self.assertEqual(select.await_args.args[1], 'black lagoon')
        bare.assert_not_awaited()
        move.assert_not_awaited()
