"""Exercise real path traversal, visibility, text decoding and entry dispatch."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.questing import Quester
from src.paths import popup_msgtext_path, popup_title_path, quest_name_path, team_up_wait_path, team_up_button_path
from tests.test_chat_control_text import control


class PartyEntryUITreeTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, title, *, team_path=team_up_wait_path, team_visible=True):
        root = SimpleNamespace(children=AsyncMock(return_value=[]))
        nodes = {}
        def add(path, text=None, visible=True):
            parent = root
            for index, name in enumerate(path):
                key = tuple(path[:index + 1])
                if key not in nodes:
                    node = SimpleNamespace(name=AsyncMock(return_value=name),
                        children=AsyncMock(return_value=[]), is_visible=AsyncMock(return_value=True))
                    parent.children.return_value.append(node)
                    nodes[key] = node
                parent = nodes[key]
            parent.is_visible.return_value = visible
            if text is not None:
                parent.__dict__.update(control(text, kind='ControlText').__dict__)
            return parent
        # A five-character Chinese message uses the real inline UTF-16 layout.
        message = add(popup_msgtext_path, '点击X进入')
        add(popup_title_path, '阿兰娜的洞穴')
        hud = add(quest_name_path, '前往 Alorma的洞穴 地点：冻泪河')
        # get_quest_name's normal heap reader remains independent of popup fixes.
        hud.maybe_text = AsyncMock(return_value='前往 Alorma的洞穴 地点：冻泪河')
        add(team_path, visible=team_visible)
        client = SimpleNamespace(title=title, root_window=root, questing_status=True,
            refilling_potions=False, post_combat_cleanup_active=False,
            quest_party_battle_rescue_active=False, quest_recovery_owner=None,
            potion_dungeon_returned=None, quest_party_group_dungeon_zone=None,
            quest_party_dungeon_interaction=None, quest_party_shared_target=None,
            in_solo_zone=False, use_potions=False, auto_pet_status=False,
            entity_detect_combat_status=False,
            quest_id=AsyncMock(return_value=42), goal_id=AsyncMock(return_value=0),
            zone_name=AsyncMock(return_value='Polaris/PL_Z03_IcefallPassage'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(13929, -15107, 1398))),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=XYZ(13929, -15107, 1392))),
            teleport=AsyncMock(), send_key=AsyncMock())
        return client, message, nodes

    async def test_actual_auto_quest_path_reads_tree_and_sends_both_entry_keys(self):
        for team_path in (team_up_wait_path, team_up_button_path):
            with self.subTest(team_path=team_path):
                leader, message, _ = self.make_client('p3', team_path=team_path)
                hitter, _, _ = self.make_client('p4', team_path=team_path)
                leader.quest_party_hitters = [hitter]
                quester = Quester(leader, [leader], None)
                for name in (
                    '_maybe_handle_overgrown_estate', '_maybe_handle_darkmoor_castle',
                    '_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
                    '_maybe_handle_bumbles_pet', 'handle_pending_dungeon_confirmation',
                    '_maybe_recover_lemuria_navigation', '_quest_party_probe_blocks_movement',
                    '_maybe_handle_no_blood_hideout', '_maybe_handle_bumbles_mind',
                    '_maybe_handle_tamarin_house', '_maybe_handle_panopticon_book',
                    '_maybe_handle_darkmoor_cantrips', '_mainline_sync_blocks_movement',
                    '_maybe_enter_avalon_grain_map', '_maybe_handle_callisto',
                    '_maybe_recover_mainline', '_maybe_photo_giant_vat',
                    '_maybe_recover_nightmare', '_maybe_refresh_stalled_dungeon_quest'):
                    setattr(quester, name, AsyncMock(return_value=False))
                quester._maybe_reenter_quest_trigger = AsyncMock(return_value=False)
                quester.teleport_to_quest_target = AsyncMock()
                quester._mainline_identity = AsyncMock(return_value=None)
                for member in (leader, hitter):
                    async def press(key, duration, member=member):
                        self.assertEqual(key, Keycode.X)
                        member.zone_name.return_value = 'Polaris/Interiors/Alorma'
                        member.is_loading.side_effect = [True, True, False, False, False, False]
                    member.send_key.side_effect = press
                with ExitStack() as stack:
                    for module, names, result in (
                        ('src.questing', ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'), False),
                        ('src.questing', ('is_free', 'is_free_leader_questing', 'clients_share_live_area'), True),
                        ('src.utils', ('is_free',), True)):
                        for name in names:
                            stack.enter_context(patch(f'{module}.{name}', AsyncMock(return_value=result)))
                    stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
                    stack.enter_context(patch('src.questing.asyncio.sleep', AsyncMock()))
                    await quester.auto_quest_solo()
                leader.send_key.assert_awaited_once()
                hitter.send_key.assert_awaited_once()
                message.maybe_text.assert_not_awaited()
                quester.teleport_to_quest_target.assert_not_awaited()
                quester._maybe_reenter_quest_trigger.assert_not_awaited()

    async def test_existing_but_hidden_team_up_never_authorizes_translated_entrance(self):
        client, _, _ = self.make_client('p3', team_visible=False)
        quester = Quester(client, [client], None)
        with patch('src.utils.is_free', AsyncMock(return_value=True)):
            self.assertFalse(await quester.party_dungeon_entry_visible(client))
            self.assertFalse(await quester.quest_interaction_ready(client, await client.quest_position.position()))
        client.send_key.assert_not_awaited()
