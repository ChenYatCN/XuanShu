"""Returned scene validation with real quest/idle/area readers and simulated game IO."""
from contextlib import ExitStack
import importlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src import utils
from src.questing import Quester


class PotionReturnValidationTests(unittest.IsolatedAsyncioTestCase):
    ZONE = 'Azteca/AZ_Z12_Xibalba'

    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 0.0
        async def sleep(seconds):
            self.now += seconds
        self.stack.enter_context(patch.object(utils.asyncio, 'sleep', AsyncMock(side_effect=sleep)))
        self.stack.enter_context(patch.object(utils.time, 'monotonic', lambda: self.now))
        self.log = self.stack.enter_context(patch.object(utils, 'logger'))
        self.stack.enter_context(patch.object(utils, 'closed_dungeon_popup', AsyncMock(return_value=False)))
        self.visible = self.stack.enter_context(patch.object(utils, 'is_visible_by_path', AsyncMock(return_value=False)))
        self.button = SimpleNamespace(is_visible=AsyncMock(return_value=True),
                                      is_control_grayed=AsyncMock(return_value=False))
        self.hud = SimpleNamespace(maybe_text=AsyncMock(return_value='Defeat boss'))
        async def window(root, path):
            return self.hud if path == utils.quest_name_path else self.button
        self.stack.enter_context(patch.object(utils, 'get_window_from_path', AsyncMock(side_effect=window)))
        self.stack.enter_context(patch('src.questing.get_window_from_path', AsyncMock(side_effect=window)))
        self.client = self.make_client('p1', 1001, 11)
        self.peer = self.make_client('p2', 2001, 22)
        self.client.quest_party_hitters = [self.peer]
        self.peer.quest_party_quester = self.client
        self.client.quest_party_group_dungeon_zone = self.ZONE
        self.client.quest_party_status_session = object()
        # First prove the real peer before departure, then make it unrendered.
        entity = SimpleNamespace(global_id_full=AsyncMock(return_value=22))
        self.entities = AsyncMock(return_value=[entity])
        self.stack.enter_context(patch.object(utils, 'SprintyClient',
            Mock(return_value=SimpleNamespace(get_base_entity_list=self.entities))))
        self.assertTrue(await utils.prepare_potion_dungeon_return(self.client, self.ZONE))
        self.assertIn(id(self.peer), self.client.potion_return_context['peer_areas'])
        self.entities.return_value = []
        self.client.zone_name.return_value = 'WizardCity/WC_Hub'
        await utils.observe_party_area(self.client)
        self.client.area.read_base_address.return_value = 1002
        async def arrive(_):
            self.client.zone_name.return_value = self.ZONE
        self.client.mouse_handler.click_window.side_effect = arrive
        self.tp = self.stack.enter_context(patch.object(utils, '_potion_dungeon_room_tp', AsyncMock()))

    def make_client(self, title, address, player_id):
        area = SimpleNamespace(read_base_address=AsyncMock(return_value=address),
                               zone_id=AsyncMock(return_value=123))
        return SimpleNamespace(title=title, questing_status=True, root_window=object(), area=area,
            refilling_potions=False, quest_recovery_owner=None,
            zone_name=AsyncMock(return_value=self.ZONE), quest_id=AsyncMock(return_value=42),
            goal_id=AsyncMock(return_value=8), is_loading=AsyncMock(return_value=False),
            in_battle=AsyncMock(return_value=False), mouse_handler=AsyncMock(),
            client_object=SimpleNamespace(client_zone=AsyncMock(return_value=area),
                                           global_id_full=AsyncMock(return_value=player_id)))

    async def test_returned_scene_unrendered_peer_uses_unchanged_departure_proof(self):
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.tp.assert_not_awaited()
        self.assertEqual(self.client.potion_return_context['returned_snapshot'], (42, 8, 'Defeat boss'))
        self.assertTrue(await utils.clients_share_live_area(self.client, self.peer))

    async def test_returned_scene_changed_peer_token_cannot_confirm_instance(self):
        self.peer.area.read_base_address.return_value = 2002
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.tp.assert_not_awaited()
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)

    async def test_returned_scene_changed_zone_id_is_rejected(self):
        self.client.area.zone_id.return_value = 456
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.tp.assert_not_awaited()

    async def test_returned_scene_wrong_quest_is_rejected(self):
        self.client.quest_id.return_value = 99
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.tp.assert_not_awaited()

    def last_validation(self):
        return self.client.potion_return_context['return_validation']

    async def test_returned_scene_unreadable_snapshot_logs_blocker_without_spam(self):
        self.hud.maybe_text.return_value = ''
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        state = self.last_validation()
        self.assertEqual(state['condition'], 'snapshot_unreadable')
        self.assertIsNone(state['snapshot'])
        self.assertEqual(state['zone_id'], 123)
        self.assertTrue(state['free'])
        self.assertEqual(state['zone'], self.ZONE)
        self.assertGreater(state['blocked_reads']['snapshot_unreadable'], 100)
        self.assertLessEqual(self.log.info.call_count, 7)
        self.log.error.assert_called_once()

    async def test_returned_scene_loading_logs_separate_condition(self):
        async def arrive(_):
            self.client.zone_name.return_value = self.ZONE
            self.client.is_loading.return_value = True
        self.client.mouse_handler.click_window.side_effect = arrive
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'loading')
        self.assertTrue(self.last_validation()['loading'])
        self.tp.assert_not_awaited()

    async def test_returned_scene_dialogue_logs_idle_failure(self):
        self.visible.return_value = True
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'busy')
        self.assertTrue(self.last_validation()['dialogue'])
        self.assertFalse(self.last_validation()['free'])
        self.tp.assert_not_awaited()

    async def test_returned_scene_unreadable_zone_id_is_distinguished(self):
        self.client.area.zone_id.return_value = None
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'zone_id_unreadable')

    async def test_returned_scene_goal_progress_keeps_same_quest_compatible(self):
        self.client.goal_id.return_value = 9
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['same_instance'], 'unchanged_departure_peer')
        self.assertEqual(self.last_validation()['snapshot'][:2], (42, 9))

    async def test_peer_loading_observation_invalidates_departure_proof(self):
        self.peer.is_loading.return_value = True
        await utils.observe_party_area(self.peer)
        self.peer.is_loading.return_value = False
        await utils.observe_party_area(self.peer)
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'same_instance_unproved')

    async def test_missing_original_zone_id_cannot_bootstrap_same_instance(self):
        self.client.potion_return_context['zone_id'] = None
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'same_instance_unproved')

    async def test_live_entity_proof_still_accepts_return_when_peer_token_changed(self):
        self.peer.area.read_base_address.return_value = 2002
        self.entities.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=22))]
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['same_instance'], 'live_area')

    async def test_peer_filters_are_visible_and_do_not_become_instance_proof(self):
        self.peer.refilling_potions = True
        self.hud.maybe_text.return_value = ''
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        state = self.last_validation()
        self.assertIsNone(state['peer'])
        self.assertTrue(state['peer_candidates'][0]['departure_proof'])
        self.assertTrue(state['peer_candidates'][0]['refilling'])
        self.assertEqual(state['peer_candidates'][0]['zone'], self.ZONE)

    async def test_refill_success_releases_protection_and_retains_active_tasks(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
                                           current_gold=AsyncMock(return_value=100000))
        # A full refill makes its own fresh departure context.
        self.entities.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=22))]
        async def leave(client):
            self.assertTrue(client.refilling_potions)
            self.assertEqual(client.quest_recovery_owner, 'potion_refill')
            client.zone_name.return_value = 'WizardCity/WC_Hub'
            await utils.observe_party_area(client)
            client.area.read_base_address.return_value = 1003
            self.entities.return_value = []
        async def buy(client, *args, **kwargs):
            self.assertTrue(client.refilling_potions)
            return await utils.return_to_dungeon_after_potions(client, self.ZONE)
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=leave)), \
             patch.object(utils, 'navigate_to_commons_from_ravenwood', AsyncMock()), \
             patch.object(utils, 'navigate_to_potions', AsyncMock()), \
             patch.object(utils, 'buy_potions', AsyncMock(side_effect=buy)):
            self.assertTrue(await utils.refill_potions(self.client))
        self.assertFalse(self.client.refilling_potions)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertTrue(self.client.questing_status)
        self.assertTrue(self.peer.questing_status)

    async def test_refill_failure_stops_only_p1_and_releases_protection(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
                                           current_gold=AsyncMock(return_value=100000))
        self.entities.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=22))]
        async def leave(client):
            client.zone_name.return_value = 'WizardCity/WC_Hub'
            await utils.observe_party_area(client)
            self.entities.return_value = []
            self.peer.area.read_base_address.return_value = 2002
        async def buy(client, *args, **kwargs):
            return await utils.return_to_dungeon_after_potions(client, self.ZONE)
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=leave)), \
             patch.object(utils, 'navigate_to_commons_from_ravenwood', AsyncMock()), \
             patch.object(utils, 'navigate_to_potions', AsyncMock()), \
             patch.object(utils, 'buy_potions', AsyncMock(side_effect=buy)):
            self.assertFalse(await utils.refill_potions(self.client))
        self.assertFalse(self.client.refilling_potions)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.questing_status)
        self.assertTrue(self.peer.questing_status)
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)

    def production_follower(self):
        # Bind the loaded production code to its real main-program globals.
        appdata = Path(__file__).resolve().parents[1] / 'artifacts/potion-return-fix-20261009-211221/probe-appdata'
        appdata.mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, {'APPDATA': str(appdata)}):
            app = importlib.import_module('XuanShu')
        def codes(code):
            yield code
            for constant in code.co_consts:
                if isinstance(constant, types.CodeType):
                    yield from codes(constant)
        code = next(c for c in codes(inspect.unwrap(app.main).__code__)
                    if c.co_name == '_follow_quester_session')
        self.remove_status = Mock()
        closed = dict(members=[self.client, self.peer], change_party_equipment=AsyncMock(),
            prepare_quester_name=AsyncMock(), update_party_status=Mock(),
            remove_party_status=self.remove_status, restart_quest_worker_after_probe=Mock())
        self.assertEqual(set(closed), set(code.co_freevars))
        def cell(value):
            return (lambda: value).__closure__[0]
        self.stack.enter_context(patch.object(app, 'walker',
            SimpleNamespace(clients=[self.client, self.peer]), create=True))
        follower = types.FunctionType(code, app.__dict__, code.co_name,
            closure=tuple(cell(closed[name]) for name in code.co_freevars))
        return app, follower

    async def test_stopped_p1_with_p2_enabled_exits_real_follow_loop_without_inputs(self):
        app, follower = self.production_follower()
        self.client.questing_status = False
        self.peer.teleport = AsyncMock()
        self.peer.send_key = AsyncMock()
        with patch.object(app, 'teleport_to_friend_from_list', AsyncMock()) as friend:
            await follower(self.peer, self.client, False)
        self.assertTrue(self.peer.questing_status)
        self.peer.teleport.assert_not_awaited()
        self.peer.send_key.assert_not_awaited()
        friend.assert_not_awaited()
        self.remove_status.assert_called_once_with(self.peer)

    async def test_confirmed_return_resumes_real_coordinate_follow_and_releases_marker(self):
        from wizwalker import XYZ
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        app, follower = self.production_follower()
        self.client.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(2000, 0, 0)))
        self.peer.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0)))
        self.peer.entity_detect_combat_status = False
        self.peer.send_key = AsyncMock()
        async def follow(position):
            self.assertIsNone(self.client.potion_dungeon_returned)
            self.peer.questing_status = False  # Finish this simulated session after one input.
        self.peer.teleport = AsyncMock(side_effect=follow)
        reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
            _resume_party_dungeon_interaction=AsyncMock(return_value=False))
        async def tick(seconds):
            self.now += seconds
            self.assertLess(self.now, 5, 'real follower failed to resume after confirmed return')
        with patch.object(app, 'Quester', Mock(return_value=reader)), \
             patch.object(app, 'is_spiral_door_open', AsyncMock(return_value=False)), \
             patch.object(app, 'close_automation_popup', AsyncMock(return_value=False)), \
             patch.object(app, 'is_visible_by_path', AsyncMock(return_value=False)), \
             patch.object(app, 'is_free', AsyncMock(return_value=True)), \
             patch.object(app, 'use_potions', False), \
             patch.object(utils.asyncio, 'sleep', AsyncMock(side_effect=tick)), \
             patch.object(app, 'teleport_to_friend_from_list', AsyncMock()) as friend:
            await follower(self.peer, self.client, False)
        self.peer.teleport.assert_awaited_once_with(self.client.body.position.return_value)
        friend.assert_not_awaited()

    async def test_returned_scene_with_old_shared_transition_resumes_existing_follow_check(self):
        from wizwalker import XYZ
        self.client.quest_party_shared_target = {'zone': 'Dungeon/OldRoom',
            'source_tokens': {id(self.peer): ('old-token',)}}
        self.client.quest_party_dungeon_interaction = {
            'members': (id(self.client), id(self.peer)), 'zone': 'Dungeon/OldRoom',
            'phase': 'transition', 'movement': True, 'deadline': -1,
            'destination': self.ZONE, 'changed': True, 'stable': 0,
            'prompt': '', 'title': '', 'xyz': XYZ(0, 0, 0), 'warned': True,
        }
        self.client.quest_party_probe_pending = True
        self.client.refilling_potions = True
        self.client.quest_recovery_owner = 'potion_refill'
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        # The refill caller releases ownership before the existing follower resumes.
        self.client.refilling_potions = False
        self.client.quest_recovery_owner = None
        quester = Quester(self.client, [self.client, self.peer], None)
        for stable in (1, 2):
            await quester._resume_party_dungeon_interaction(self.peer)
            self.assertEqual(self.client.quest_party_dungeon_interaction['stable'], stable)
        self.assertTrue(await quester._resume_party_dungeon_interaction(self.peer))
        self.assertIsNone(self.client.quest_party_dungeon_interaction)
        self.assertFalse(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_shared_target['source_tokens'], {})
        self.tp.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
