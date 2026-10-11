import unittest
from unittest.mock import AsyncMock, patch
from src.questing import Quester


class QuestLocalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.title = 'p1'
        self.quester = Quester(self.client, [self.client], None)

    async def test_chinese_location_and_collect_object_reach_quest_methods(self):
        self.quester.read_quest_txt = AsyncMock(return_value='收集 齿轮 地点：三叉大道 (0 of 3)')
        self.assertEqual(await self.quester.get_quest_zone_name(self.client), '三叉大道')
        self.assertEqual(await self.quester.get_collect_quest_object_name(), '齿轮')

    async def test_chinese_special_door_selects_canonical_destination(self):
        self.quester.read_spiral_door_title = AsyncMock(return_value='<center>导航器</center>')
        self.quester.read_quest_txt = AsyncMock(return_value='拜访 伊恩 地点：卡拉梅尔市')
        with patch('src.questing.new_portals_cycle', new=AsyncMock()) as teleport:
            self.assertTrue(await self.quester.new_world_doors(self.client))
        teleport.assert_awaited_once_with(self.client, 'karamelle city')

    async def test_missing_location_does_not_use_first_or_last_destination(self):
        self.quester.d_location = 'karamelle city'
        self.quester.read_spiral_door_title = AsyncMock(return_value='Nanavator')
        self.quester.read_quest_txt = AsyncMock(return_value='任务暂不可读')
        with patch('src.questing.new_portals_cycle', new=AsyncMock()) as teleport:
            self.assertTrue(await self.quester.new_world_doors(self.client))
        teleport.assert_not_awaited()
        self.assertIsNone(self.quester.d_location)

    async def test_npc_is_not_misclassified_as_dungeon(self):
        for text, expected in [('按下 X 交谈', False), ('点击 X 进入', True), ('', False)]:
            self.quester.read_popup = AsyncMock(return_value=text)
            self.assertEqual(await self.quester.detected_interact_from_popup(self.client), expected)


class OpenSpiralDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_solo_handles_open_dialog_before_quest_movement_or_party_probe(self):
        client = AsyncMock()
        quester = Quester(client, [client], None)
        quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        quester._quest_party_probe_blocks_movement = AsyncMock()
        quester.new_world_doors = AsyncMock(return_value=False)
        with (
            patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=True)),
            patch('src.questing.spiral_door_with_quest', new=AsyncMock()) as enter,
            patch('src.questing.collision_tp', new=AsyncMock()) as move,
        ):
            await quester.auto_quest_solo()
        enter.assert_awaited_once_with(client)
        move.assert_not_awaited()
        quester._quest_party_probe_blocks_movement.assert_not_awaited()
        client.quest_position.position.assert_not_awaited()

    async def test_group_handles_open_dialog_before_movement(self):
        client = AsyncMock()
        quester = Quester(client, [client], None)
        quester.handle_spiral_navigation = AsyncMock()
        with patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=True)):
            await quester.handle_normal_quests([], True)
        quester.handle_spiral_navigation.assert_awaited_once()
        client.quest_position.position.assert_not_awaited()


class FriendTeleportModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_yes_no_modal_is_not_a_friend_teleport_error(self):
        from src.utils import is_friend_teleport_error
        with patch('src.utils.is_visible_by_path', new=AsyncMock(side_effect=[True, True])):
            self.assertFalse(await is_friend_teleport_error(AsyncMock()))

    async def test_one_button_error_is_still_detected(self):
        from src.utils import is_friend_teleport_error
        with patch('src.utils.is_visible_by_path', new=AsyncMock(side_effect=[True, False])):
            self.assertTrue(await is_friend_teleport_error(AsyncMock()))


class EndorsementWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicks_only_close_button_inside_visible_endorsement(self):
        from src.utils import close_endorsement_window
        client, panel, button = AsyncMock(), AsyncMock(), AsyncMock()
        client.root_window.get_windows_with_name.return_value = [panel]
        panel.is_visible.return_value = True
        panel.get_windows_with_name.return_value = [button]
        button.is_visible.return_value = True
        self.assertTrue(await close_endorsement_window(client))
        panel.get_windows_with_name.assert_awaited_once_with('CloseEndorsementWindowButton')
        client.mouse_handler.click_window.assert_awaited_once_with(button)

    async def test_hidden_panel_is_not_clicked(self):
        from src.utils import close_endorsement_window
        client, panel = AsyncMock(), AsyncMock()
        client.root_window.get_windows_with_name.return_value = [panel]
        panel.is_visible.return_value = False
        self.assertFalse(await close_endorsement_window(client))
        client.mouse_handler.click_window.assert_not_awaited()


class SpiralWindowResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_hidden_duplicate_modal_and_resolves_both_root_layouts(self):
        from src.utils import get_spiral_teleport_button
        from src.paths import spiral_door_teleport_path
        def node(name, children=(), visible=True):
            window = AsyncMock()
            window.name.return_value = name
            window.children.return_value = list(children)
            window.is_visible.return_value = visible
            return window
        for with_world_view in (True, False):
            button = node('teleportButton')
            branch = button
            for name in reversed(spiral_door_teleport_path[1:-1]):
                branch = node(name, [branch])
            children = [node('', visible=False), branch]
            root = node('root', [node('WorldView', children)] if with_world_view else children)
            client = AsyncMock()
            client.root_window = root
            self.assertIs(await get_spiral_teleport_button(client), button)


class HiddenStreamTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_hidden_default_stream_title_does_not_trigger_special_portal(self):
        client, title = AsyncMock(), AsyncMock()
        title.is_visible.return_value = False
        title.maybe_text.return_value = '<string;GUI2_00000399>'
        quester = Quester(client, [client], None)
        quester.find_quest_zone_area_name = AsyncMock()
        with patch('src.questing.get_window_from_path', new=AsyncMock(return_value=title)):
            self.assertFalse(await quester.new_world_doors(client))
        title.maybe_text.assert_not_awaited()
        quester.find_quest_zone_area_name.assert_not_awaited()

    async def test_visible_special_portal_title_still_selects_destination(self):
        client, title = AsyncMock(), AsyncMock()
        title.is_visible.return_value = True
        title.maybe_text.return_value = '<string;GUI2_00000399>'
        quester = Quester(client, [client], None)
        quester.find_quest_zone_area_name = AsyncMock(return_value='aeriel')
        with (
            patch('src.questing.get_window_from_path', new=AsyncMock(return_value=title)),
            patch('src.questing.new_portals_cycle', new=AsyncMock()) as select,
        ):
            self.assertTrue(await quester.new_world_doors(client))
        select.assert_awaited_once_with(client, 'aeriel')


