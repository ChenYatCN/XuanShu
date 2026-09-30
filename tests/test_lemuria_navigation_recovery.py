import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src.questing import Quester
from src.paths import quest_helper_hud_path, quest_helper_arrow_path, quest_helper_distance_path


class LemuriaNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.zone = 'Lemuria/LM_Z07_Heap'
        self.loading = False
        self.text = 'Talk to Dog Tracy in Heap'
        self.hud_visible = True
        self.arrow_visible = False
        self.distance = ''
        self.target = XYZ(0, 0, 0)
        self.goal = SimpleNamespace(
            no_quest_helper=AsyncMock(return_value=False),
            pet_only_quest=AsyncMock(return_value=False),
            goal_type=AsyncMock(return_value=SimpleNamespace(value=4)),
            goal_destination_zone=AsyncMock(return_value=self.zone),
            name_lang_key=AsyncMock(return_value='goal-talk'),
        )
        self.quest = SimpleNamespace(
            goal_data=AsyncMock(return_value={1: self.goal}),
            permit_quest_helper=AsyncMock(return_value=True),
            pet_only_quest=AsyncMock(return_value=False),
        )
        self.client = SimpleNamespace(
            title='p1', questing_status=True, refilling_potions=False,
            in_solo_zone=False, quest_recovery_owner=None,
            quest_party_probe_pending=False, quest_party_hitters=[],
            root_window=object(), zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(return_value=123), goal_id=AsyncMock(return_value=1),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value={123: self.quest}))),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(9000, 0, 0))),
            in_battle=AsyncMock(return_value=False),
            is_loading=AsyncMock(side_effect=lambda: self.loading),
            send_key=AsyncMock(),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.identity = (123, 'QuestTitle_test', 'Test', {'world': 'LEMURIA', 'number': 1}, True)
        self.quester._mainline_identity = AsyncMock(side_effect=lambda _: self.identity)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: '' if self.loading else self.text)
        self.on_tick = None
        async def tick(seconds):
            self.now += seconds
            if self.on_tick:
                self.on_tick()
        async def window(root, path):
            if path == quest_helper_hud_path:
                return SimpleNamespace(is_visible=AsyncMock(return_value=self.hud_visible))
            if path == quest_helper_arrow_path:
                return SimpleNamespace(is_visible=AsyncMock(return_value=self.arrow_visible))
            if path == quest_helper_distance_path:
                return SimpleNamespace(maybe_text=AsyncMock(return_value=self.distance))
            return None
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)))
        self.window = stack.enter_context(patch('src.questing.get_window_from_path', new=AsyncMock(side_effect=window)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(return_value=True)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.log = stack.enter_context(patch('src.questing.logger'))

    def restored(self):
        self.arrow_visible = True
        self.distance = '123'
        self.target = XYZ(123, 456, 7)

    async def check(self, *times):
        result = None
        for self.now in times:
            result = await self.quester._maybe_recover_lemuria_navigation(self.client)
        return result

    def end_to_hub(self, restore=True):
        async def press(key, duration):
            self.assertEqual(key, Keycode.END)
            self.assertEqual(self.client.quest_recovery_owner, 'lemuria_navigation')
            self.zone = ''
            self.loading = True
            ready_at = self.now + .6
            def ready():
                if self.now >= ready_at:
                    self.loading = False
                    self.zone = Quester.LEMURIA_NAVIGATION_HUB
                    if restore:
                        self.restored()
            self.on_tick = ready
        self.client.send_key.side_effect = press

    async def test_direct_missing_requires_45_seconds_and_success_needs_navigation(self):
        self.end_to_hub()
        self.assertFalse(await self.check(0, 20, 44))
        self.client.send_key.assert_not_awaited()
        self.assertTrue(await self.check(45))
        self.client.send_key.assert_awaited_once()
        self.assertGreater(self.now, 48)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(self.client.quest_lemuria_navigation_recovery['holding'])
        self.assertFalse(await self.check(self.now + .2))

    async def test_fallback_needs_both_invalid_target_and_300_seconds(self):
        self.window.side_effect = RuntimeError('UI unavailable')
        self.assertEqual((await self.quester._lemuria_navigation_sample(self.client))['mode'], 'fallback')
        self.assertFalse(await self.check(0, 60, 299))
        self.client.send_key.assert_not_awaited()
        self.assertTrue(await self.check(300))
        self.client.send_key.assert_awaited_once()

    async def test_valid_xyz_alone_with_unreadable_ui_does_not_trigger(self):
        self.window.side_effect = RuntimeError('UI unavailable')
        self.target = XYZ(123, 456, 7)
        await self.check(0, 300, 600)
        self.client.send_key.assert_not_awaited()

    async def test_missing_mainline_index_never_sends_end(self):
        self.identity = (123, '', '', None, True)
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()
        self.assertFalse(self.client.lemuria_navigation_pending)

    async def test_other_world_is_out_of_scope(self):
        self.zone = 'Novus/NV_Z03_PuertoNuovo_VA'
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()

    async def test_safe_area_allowlist_and_instance_markers(self):
        for zone in ['Lemuria/Interiors/LM_Z07_BumblesMind', 'Lemuria/Unknown', 'Lemuria/LM_Z99_Instance']:
            self.zone = zone
            await self.check(0, 60, 300)
        self.zone = 'Lemuria/LM_Z07_Heap'
        self.client.in_solo_zone = True
        await self.check(0, 60, 300)
        self.client.in_solo_zone = False
        self.client.quest_party_group_dungeon_zone = self.zone
        await self.check(0, 60, 300)
        self.client.quest_party_group_dungeon_zone = None
        self.client.quest_dungeon_recovery = {'zone': self.zone, 'active': False}
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()

    async def test_no_helper_pet_photo_and_non_navigation_goals_are_excluded(self):
        for api, value in [(self.goal.no_quest_helper, True), (self.quest.permit_quest_helper, False),
                           (self.quest.pet_only_quest, True), (self.goal.pet_only_quest, True)]:
            original = api.return_value
            api.return_value = value
            self.assertIsNone(await self.quester._lemuria_navigation_sample(self.client))
            api.return_value = original
        for kind in [0, 1, 2, 3, 7, 9, 12]:
            self.goal.goal_type.return_value = SimpleNamespace(value=kind)
            self.assertIsNone(await self.quester._lemuria_navigation_sample(self.client))
        self.goal.goal_type.return_value = SimpleNamespace(value=8)
        self.text = 'Photomance Object in Heap'
        self.assertIsNone(await self.quester._lemuria_navigation_sample(self.client))

    async def test_hidden_hud_is_not_navigation_failure(self):
        self.hud_visible = False
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()

    async def test_near_target_without_arrow_is_not_loss(self):
        self.target = XYZ(9001, 0, 0)
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()

    async def test_quest_and_stage_change_reset_observation(self):
        await self.check(0, 30)
        self.text = 'Talk to Another NPC in Heap'
        await self.check(44, 60)
        self.client.send_key.assert_not_awaited()

    async def test_navigation_returns_before_threshold_cancels_observation(self):
        await self.check(0, 30)
        self.restored()
        self.assertFalse(await self.check(46))
        self.client.send_key.assert_not_awaited()
        self.assertIsNone(self.client.quest_lemuria_navigation_recovery)

    async def test_other_recovery_loading_battle_dialogue_pause_detector(self):
        await self.check(0, 30)
        self.client.quest_recovery_owner = 'npc_dialogue'
        await self.check(300)
        self.client.quest_recovery_owner = None
        await self.check(301, 330)
        self.client.send_key.assert_not_awaited()
        self.client.in_battle.return_value = True
        self.free.return_value = False
        await self.check(400)
        self.client.in_battle.return_value = False
        self.free.return_value = True
        self.loading = True
        self.free.return_value = False
        await self.check(500)
        self.client.send_key.assert_not_awaited()

    async def test_hitter_never_runs_detector(self):
        self.client.quest_party_status_session = object()
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()
        self.client.quest_party_status_session = None
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.check(0, 60, 300)
        self.client.send_key.assert_not_awaited()

    async def test_two_attempt_limit_cooldown_and_no_per_frame_warning(self):
        await self.check(0, 45)
        first_done = self.now
        await self.check(first_done + 1, first_done + 59)
        self.assertEqual(self.client.send_key.await_count, 1)
        await self.check(first_done + 61)
        self.assertEqual(self.client.send_key.await_count, 2)
        await self.check(1000, 2000)
        self.assertEqual(self.client.send_key.await_count, 2)
        self.assertEqual(self.log.warning.call_count, 2)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_failed_return_keeps_tp_paused_even_after_hub_zone_change(self):
        self.end_to_hub(restore=False)
        await self.check(0, 45)
        done = self.now
        self.assertTrue(await self.check(done + 1))
        self.assertEqual(self.client.send_key.await_count, 1)
        self.assertTrue(self.client.quest_lemuria_navigation_recovery['holding'])
        self.client.teleport = AsyncMock()
        with patch('src.questing.collision_tp', new=AsyncMock()) as collision:
            await self.quester.teleport_to_quest_target(self.client, XYZ(1, 2, 3))
        collision.assert_not_awaited()

    async def test_changed_zone_alone_is_not_success(self):
        self.client.send_key.side_effect = lambda *args: setattr(self, 'zone', 'Lemuria/LM_Z04_Badlands')
        await self.check(0, 45)
        self.assertTrue(self.client.quest_lemuria_navigation_recovery['holding'])
        self.log.warning.assert_called_once()

    async def test_stage_changes_during_end_cancel_original_recovery(self):
        self.client.send_key.side_effect = lambda *args: setattr(self.client.goal_id, 'return_value', 2)
        await self.check(0, 45)
        self.assertFalse(self.client.quest_lemuria_navigation_recovery['holding'])
        self.assertFalse(self.client.lemuria_navigation_pending)
        self.log.warning.assert_not_called()

    async def test_unreadable_navigation_after_failed_end_is_not_restoration(self):
        await self.check(0, 45)
        done = self.now
        self.window.side_effect = RuntimeError('UI unavailable')
        self.target = XYZ(1, 2, 3)
        self.assertTrue(await self.check(done + 1))
        self.assertTrue(self.client.quest_lemuria_navigation_recovery['holding'])

    async def test_late_restoration_waits_stable_and_rearms_party_sync(self):
        peer = SimpleNamespace(quest_mainline_sync_state={'old': True})
        self.client.quest_mainline_sync_members = [self.client, peer]
        self.end_to_hub(restore=False)
        await self.check(0, 45)
        done = self.now
        self.restored()
        self.assertTrue(await self.check(done + 1, done + 2))
        self.assertFalse(await self.check(done + 3))
        self.assertIsNone(peer.quest_mainline_sync_state)
        self.assertFalse(self.client.lemuria_navigation_pending)

    async def test_cancelled_end_releases_lock_and_preserves_attempt_count(self):
        self.client.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.check(0, 45)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertEqual(self.client._lemuria_navigation_attempts[123], 1)

    async def test_counter_survives_quester_recreation(self):
        await self.check(0, 45)
        count = self.client.send_key.await_count
        restarted = Quester(self.client, [self.client], None)
        restarted._mainline_identity = self.quester._mainline_identity
        restarted.read_quest_txt = self.quester.read_quest_txt
        self.quester = restarted
        await self.check(self.now + 1)
        self.assertEqual(self.client.send_key.await_count, count)

    async def test_invalid_targets_include_nonfinite_and_zero(self):
        self.window.side_effect = RuntimeError('UI unavailable')
        for value in [XYZ(0, 0, 0), XYZ(float('nan'), 0, 0), XYZ(1, float('inf'), 3)]:
            self.target = value
            self.assertEqual((await self.quester._lemuria_navigation_sample(self.client))['mode'], 'fallback')

    async def test_readable_real_index_match_is_required(self):
        from src.mainline_progress import quest_rows
        row = next(row for row in quest_rows() if row['world'] == 'LEMURIA' and row['keys'])
        self.quest.name_lang_key = AsyncMock(return_value=row['keys'][0])
        self.quest.mainline = AsyncMock(return_value=True)
        self.client.cache_handler = SimpleNamespace(get_langcode_name=AsyncMock(return_value=row['english']))
        del self.quester._mainline_identity
        self.assertIsNotNone(await self.quester._lemuria_navigation_sample(self.client))
        self.quest.name_lang_key.return_value = 'unknown-title'
        self.client.cache_handler.get_langcode_name.return_value = 'unknown-title'
        self.assertIsNone(await self.quester._lemuria_navigation_sample(self.client))

    async def test_dialogue_interrupt_resets_continuous_loss_clock(self):
        await self.check(0, 30)
        self.quester._advance_npc_dialogue = AsyncMock(return_value=True)
        self.now = 100
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.assertIsNone(self.client.quest_lemuria_navigation_recovery['since'])
        self.client.quest_dialogue_settle = None
        await self.check(101, 130)
        self.client.send_key.assert_not_awaited()

    async def test_restoration_after_lock_before_end_sends_no_key(self):
        run = self.quester._run_lemuria_navigation_recovery
        async def restored_before_end(client, state):
            self.restored()
            return await run(client, state)
        self.quester._run_lemuria_navigation_recovery = restored_before_end
        await self.check(0, 45)
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.client._lemuria_navigation_attempts.get(123, 0), 0)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_unsafe_manual_zone_change_cancels_navigation_hold(self):
        await self.check(0, 45)
        self.zone = 'Lemuria/Interiors/LM_Z05_I01_Dungeon'
        self.assertFalse(await self.check(self.now + 1))
        self.assertIsNone(self.client.quest_lemuria_navigation_recovery)
        self.assertEqual(self.client.send_key.await_count, 1)

    async def test_only_client_with_missing_navigation_sends_end(self):
        peer = SimpleNamespace(**vars(self.client))
        peer.title = 'p2'
        peer.root_window = object()
        peer.send_key = AsyncMock()
        peer.quest_position = SimpleNamespace(position=AsyncMock(return_value=XYZ(100, 200, 3)))
        self.quester.clients = [self.client, peer]
        window = self.window.side_effect
        async def per_client_window(root, path):
            if root is peer.root_window:
                if path in (quest_helper_hud_path, quest_helper_arrow_path):
                    return SimpleNamespace(is_visible=AsyncMock(return_value=True))
                return SimpleNamespace(maybe_text=AsyncMock(return_value='120'))
            return await window(root, path)
        self.window.side_effect = per_client_window
        for self.now in (0, 45):
            await self.quester._maybe_recover_lemuria_navigation(self.client)
            self.assertFalse(await self.quester._maybe_recover_lemuria_navigation(peer))
        self.client.send_key.assert_awaited_once()
        peer.send_key.assert_not_awaited()

    async def test_battle_interrupt_during_return_does_not_trigger_more_keys(self):
        self.client.send_key.side_effect = lambda *args: setattr(self.client.in_battle, 'return_value', True)
        await self.check(0, 45)
        self.client.send_key.assert_awaited_once()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertTrue(self.client.quest_lemuria_navigation_recovery['holding'])

    async def test_missing_goal_permissions_cannot_use_fallback(self):
        self.goal.no_quest_helper.side_effect = RuntimeError('unreadable')
        await self.check(0, 300, 600)
        self.client.send_key.assert_not_awaited()

    async def test_temporary_zero_quest_id_after_loading_is_not_task_change(self):
        self.end_to_hub()
        original_key = self.client.send_key.side_effect
        async def press(*args):
            await original_key(*args)
            original_tick = self.on_tick
            zero_until = self.now + 1.0
            def tick():
                original_tick()
                self.client.quest_id.return_value = 0 if self.now < zero_until else 123
            self.on_tick = tick
        self.client.send_key.side_effect = press
        await self.check(0, 45)
        self.assertFalse(self.client.quest_lemuria_navigation_recovery['holding'])
        self.log.warning.assert_not_called()

    async def test_new_stage_on_arrival_cancels_without_waiting_navigation_timeout(self):
        self.end_to_hub()
        original_key = self.client.send_key.side_effect
        async def press(*args):
            await original_key(*args)
            original_tick = self.on_tick
            def tick():
                original_tick()
                if not self.loading:
                    self.text = 'Talk to New NPC in Heap'
            self.on_tick = tick
        self.client.send_key.side_effect = press
        await self.check(0, 45)
        self.assertFalse(self.client.quest_lemuria_navigation_recovery['holding'])
        self.assertLess(self.now, 47)
        self.log.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
