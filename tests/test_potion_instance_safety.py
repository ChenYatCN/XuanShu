import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import utils
from src.questing import Quester, claim_quest_recovery


class PotionInstanceSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(title='p1', questing_status=True,
            zone_name=AsyncMock(return_value='Shop'),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            mouse_handler=AsyncMock(), root_window=object(),
            potion_return_context={'zone': 'Dungeon/Room', 'snapshot': (42, 7, 'Talk'),
                                   'zone_id': 123, 'group_zone': 'Dungeon/Room'})
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                      is_control_grayed=AsyncMock(return_value=False))
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.closed = stack.enter_context(patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=False)))
        stack.enter_context(patch.object(utils, 'get_window_from_path', AsyncMock(return_value=self.button)))
        stack.enter_context(patch.object(utils, 'is_free', AsyncMock(return_value=True)))
        self.snapshot = stack.enter_context(patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=(42, 8, 'Next'))))
        self.zone_id = stack.enter_context(patch.object(utils, 'potion_zone_id', AsyncMock(return_value=123)))
        stack.enter_context(patch.object(utils.asyncio, 'sleep', AsyncMock()))
        async def arrive(_):
            self.client.zone_name.return_value = 'Dungeon/Room'
        self.client.mouse_handler.click_window.side_effect = arrive

    async def test_same_name_different_zone_id_is_rejected(self):
        self.zone_id.return_value = 999
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertFalse(hasattr(self.client, 'potion_dungeon_returned'))

    async def test_different_quest_identity_is_rejected(self):
        self.snapshot.return_value = (99, 8, 'New instance')
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))

    async def test_same_zone_without_readable_task_never_resumes(self):
        self.snapshot.return_value = None
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))

    async def test_disabled_red_button_never_clicked(self):
        self.button.is_control_grayed.return_value = True
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_loading_red_button_never_clicked(self):
        self.client.is_loading.return_value = True
        with patch.object(utils.time, 'monotonic', side_effect=range(0, 300, 3)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_closed_after_red_click_stops_without_other_teleport(self):
        self.closed.side_effect = [False, True]
        with patch.object(utils, 'recall_to_teleport_mark', AsyncMock()) as mark:
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        mark.assert_not_awaited()
        self.client.mouse_handler.click_window.assert_awaited_once()

    async def test_verified_return_preserves_instance_state_and_requires_party_check(self):
        hitter = SimpleNamespace(questing_status=True)
        self.client.quest_party_hitters = [hitter]
        self.client.potion_return_context['dungeon_state'] = {'zone': 'Dungeon/Room', 'since': 100}
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertEqual(self.client.potion_dungeon_returned[2], {id(hitter)})
        self.assertEqual(self.client.quest_dungeon_recovery['snapshot'], (42, 8, 'Next'))
        self.assertIsNone(self.client.quest_dungeon_recovery['since'])

    async def test_original_context_is_required(self):
        self.client.potion_return_context = None
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_solo_return_does_not_wait_for_unreachable_hitter(self):
        self.client.in_solo_zone = True
        self.client.quest_party_hitters = [SimpleNamespace(questing_status=True)]
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, 'Dungeon/Room'))
        self.assertIsNone(self.client.potion_dungeon_returned)


class PotionRoutingSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bumbles_refill_routes_to_dungeon_and_releases_lock(self):
        zone = 'Lemuria/Interiors/LM_Z07_BumblesMind'
        client = SimpleNamespace(title='p1', questing_status=True,
            zone_name=AsyncMock(return_value=zone),
            stats=SimpleNamespace(reference_level=AsyncMock(return_value=100)))
        with ExitStack() as stack:
            prepare = stack.enter_context(patch.object(utils, 'prepare_potion_dungeon_return', AsyncMock(return_value=True)))
            mark = stack.enter_context(patch.object(utils, 'ensure_teleport_mark', AsyncMock()))
            for name in ('navigate_to_ravenwood', 'navigate_to_commons_from_ravenwood', 'navigate_to_potions'):
                stack.enter_context(patch.object(utils, name, AsyncMock()))
            buy = stack.enter_context(patch.object(utils, 'buy_potions', AsyncMock(return_value=True)))
            self.assertTrue(await utils.refill_potions(client))
        prepare.assert_awaited_once_with(client, zone, require_snapshot=True)
        buy.assert_awaited_once_with(client, True, original_zone=zone, dungeon_return=True)
        mark.assert_not_awaited()
        self.assertFalse(client.refilling_potions)
        self.assertIsNone(client.quest_recovery_owner)

    async def test_unreadable_original_task_prevents_dungeon_departure(self):
        zone = 'Dungeon/Room'
        client = SimpleNamespace(title='p1', questing_status=True,
            quest_party_group_dungeon_zone=zone, zone_name=AsyncMock(return_value=zone),
            stats=SimpleNamespace(reference_level=AsyncMock(return_value=100)))
        with patch.object(utils, 'potion_quest_snapshot', AsyncMock(return_value=None)), \
             patch.object(utils, 'navigate_to_ravenwood', AsyncMock()) as travel:
            self.assertFalse(await utils.refill_potions(client))
        travel.assert_not_awaited()
        self.assertFalse(client.questing_status)
        self.assertFalse(client.refilling_potions)
        self.assertIsNone(client.quest_recovery_owner)

    async def test_promptless_bumbles_mind_is_not_treated_as_normal_map(self):
        client = SimpleNamespace()
        self.assertTrue(utils.potion_dungeon_return_required(client, 'Lemuria/Interiors/LM_Z07_BumblesMind'))
        self.assertFalse(utils.potion_dungeon_return_required(client, 'WizardCity/WC_Hub'))
        self.assertFalse(utils.potion_dungeon_return_required(client, 'World/Interiors/Shop'))

    async def test_refilling_prevents_other_recovery_claim(self):
        client = SimpleNamespace(refilling_potions=True)
        self.assertFalse(claim_quest_recovery(client, 'mainline_finder'))
        self.assertFalse(hasattr(client, 'quest_recovery_owner'))

    async def test_hitter_refill_blocks_quest_movement_without_arming_zone_probe(self):
        hitter = SimpleNamespace(refilling_potions=True)
        client = SimpleNamespace(quest_party_hitters=[hitter], quest_party_probe_pending=False)
        self.assertTrue(await Quester._quest_party_probe_blocks_movement(SimpleNamespace(client=client)))
        self.assertFalse(client.quest_party_probe_pending)

    async def test_closed_mark_failure_never_waits_for_cooldown_or_sends_keys(self):
        client = SimpleNamespace(title='p1', send_key=AsyncMock())
        with patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=True)), \
             patch.object(utils, 'wait_for_teleport_mark_timer', AsyncMock()) as timer:
            self.assertFalse(await utils.recall_to_teleport_mark(client, expected_zone='Dungeon/Room'))
        timer.assert_not_awaited()
        client.send_key.assert_not_awaited()

    async def test_global_guard_closed_marker_stops_mark_retry(self):
        client = SimpleNamespace(title='p1', send_key=AsyncMock(), _xuanshu_dungeon_closed=True)
        with patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=False)), \
             patch.object(utils, 'wait_for_teleport_mark_timer', AsyncMock()) as timer:
            self.assertFalse(await utils.recall_to_teleport_mark(client, expected_zone='Dungeon/Room'))
        timer.assert_not_awaited()


class ClosedDungeonPopupTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_caption_uses_single_acknowledgment_not_retry(self):
        caption = SimpleNamespace(value='区域加载失败，地下城已关闭。', is_visible=AsyncMock(return_value=True))
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                get_windows_with_name=AsyncMock(return_value=[caption]))
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        client = SimpleNamespace(title='p1', is_loading=AsyncMock(return_value=False),
            root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[modal])),
            mouse_handler=AsyncMock(), _xuanshu_zone_retry_users=1, questing_status=True)
        async def lookup(_root, path):
            return button if path[-1] == 'rightButton' else False
        with patch.object(utils, 'read_control_text', AsyncMock(side_effect=lambda c: c.value)), \
             patch.object(utils, 'get_window_from_path', AsyncMock(side_effect=lookup)):
            from src.script_popups import close_automation_popup
            self.assertTrue(await close_automation_popup(client))
        client.mouse_handler.click_window.assert_awaited_once_with(button)
        self.assertTrue(client._xuanshu_dungeon_closed)
        self.assertFalse(client.questing_status)

    async def test_retryable_files_caption_is_not_closed_dungeon(self):
        caption = SimpleNamespace(value='区域正在加载必要的文件，请稍后重试。', is_visible=AsyncMock(return_value=True))
        modal = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                get_windows_with_name=AsyncMock(return_value=[caption]))
        client = SimpleNamespace(root_window=SimpleNamespace(get_windows_with_name=AsyncMock(return_value=[modal])))
        with patch.object(utils, 'read_control_text', AsyncMock(side_effect=lambda c: c.value)):
            self.assertFalse(await utils.closed_dungeon_popup(client, dismiss=True))
        self.assertFalse(hasattr(client, '_xuanshu_dungeon_closed'))
