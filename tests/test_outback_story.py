import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.questing import Quester
from src.mainline_progress import quest_rows
from src.paths import quest_helper_arrow_path, quest_helper_distance_path, quest_helper_hud_path


class OutbackStoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.dialogue = False
        self.row = next(r for r in quest_rows() if r['keys'] == ['QuestTitle_17D895'])
        self.identity = (123, 'QuestTitle_17D895', 'Stop and Goanna', self.row, True)
        self.goal_id = 1
        self.goal_code = 'WizQst17D895_00000007'
        self.position = XYZ(200, 200, 0)
        self.target = XYZ(0, 0, 0)
        goal = SimpleNamespace(name_lang_key=AsyncMock(side_effect=lambda: self.goal_code))
        quest = SimpleNamespace(goal_data=AsyncMock(side_effect=lambda: {self.goal_id: goal}))
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            refilling_potions=False, quest_party_hitters=[], root_window=object(),
            zone_name=AsyncMock(return_value=Quester.OUTBACK_STORY_ZONE),
            quest_id=AsyncMock(side_effect=lambda: self.identity[0]),
            goal_id=AsyncMock(side_effect=lambda: self.goal_id),
            quest_manager=AsyncMock(return_value=SimpleNamespace(
                quest_data=AsyncMock(side_effect=lambda: {self.identity[0]: quest}))),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            in_battle=AsyncMock(return_value=False), is_loading=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(side_effect=lambda: self.dialogue),
            teleport=AsyncMock(), send_key=AsyncMock(),
        )
        self.quester = Quester(self.client, [self.client], None)
        async def identity(c):
            self.assertFalse(self.dialogue, 'Must not rematch mainline during story dialogue')
            return self.identity
        self.quester._mainline_identity = AsyncMock(side_effect=identity)
        self.quester._advance_npc_dialogue = AsyncMock(return_value=True)
        self.on_tick = None
        async def tick(seconds):
            self.now += seconds
            if self.on_tick:
                self.on_tick()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing',
            new=AsyncMock(side_effect=lambda c: not self.dialogue and not self.client.is_loading.return_value
                          and not self.client.in_battle.return_value)))
        stack.enter_context(patch('src.questing.read_dialogue_text', new=AsyncMock(
            side_effect=lambda c: 'Story' if self.dialogue else '')))
        stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(return_value=False)))
        self.windows = stack.enter_context(patch('src.questing.get_window_from_path', new=AsyncMock(return_value=None)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.log = stack.enter_context(patch('src.questing.logger'))

    async def start(self):
        for point in (0, .5, 1.6):
            self.now = point
            result = await self.quester._maybe_handle_outback_story(self.client)
        return result

    def advance(self, next_quest=True):
        self.dialogue = False
        self.goal_id = 2
        self.goal_code = 'NewGoal'
        if next_quest:
            row = next(r for r in quest_rows() if r['world'].casefold() == 'wallaru' and r['number'] == 16)
            self.identity = (124, row['keys'][0], row['english'], row, True)

    def story(self, next_quest=True, transient=False):
        async def tp(position):
            self.assertEqual(self.client.quest_recovery_owner, 'outback_story')
            self.assertEqual(position, Quester.OUTBACK_STORY_POSITION)
            self.position = position
            self.dialogue = True
            end = self.now + 1.0
            def finish():
                if transient and self.dialogue:
                    self.identity = (999, '', '', None, False)
                if self.now >= end:
                    self.advance(next_quest)
            self.on_tick = finish
        self.client.teleport.side_effect = tp

    async def test_trigger_waits_dialogue_and_stable_next_mainline(self):
        self.story()
        self.assertTrue(await self.start())
        self.client.teleport.assert_awaited_once_with(Quester.OUTBACK_STORY_POSITION)
        self.client.send_key.assert_not_awaited()  # No X/Q/END.
        self.assertGreaterEqual(self.now, 5.6)
        self.assertFalse(self.client.outback_story_pending)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertEqual(self.client.quest_outback_story['result'], 'complete')
        self.quester._advance_npc_dialogue.assert_awaited()

    async def test_same_quest_real_goal_progress_resumes(self):
        self.story(next_quest=False)
        self.assertTrue(await self.start())
        self.assertEqual(self.identity[0], 123)
        self.assertEqual(self.client.quest_outback_story['result'], 'complete')

    async def test_transient_dialogue_identity_never_rematches_or_reteleports(self):
        self.story(transient=True)
        await self.start()
        self.client.teleport.assert_awaited_once()
        self.assertFalse(self.client.outback_story_pending)

    async def test_valid_target_never_triggers_even_with_missing_arrow(self):
        self.target = XYZ(100, 200, 5)
        self.assertFalse(await self.start())
        self.client.teleport.assert_not_awaited()

    async def test_visible_normal_guide_blocks_zero_target_trigger(self):
        arrow = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        distance = SimpleNamespace(maybe_text=AsyncMock(return_value='123'))
        self.windows.side_effect = lambda root, path: arrow if path == quest_helper_arrow_path else (
            distance if path == quest_helper_distance_path else None)
        self.assertFalse(await self.start())
        self.client.teleport.assert_not_awaited()

    async def test_missing_visible_hud_guide_does_not_trust_stale_xyz(self):
        self.target = XYZ(900, 900, 0)
        hud = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        arrow = SimpleNamespace(is_visible=AsyncMock(return_value=False))
        distance = SimpleNamespace(maybe_text=AsyncMock(return_value=''))
        self.windows.side_effect = lambda root, path: {
            tuple(quest_helper_hud_path): hud, tuple(quest_helper_arrow_path): arrow,
            tuple(quest_helper_distance_path): distance,
        }.get(tuple(path))
        self.story()
        await self.start()
        self.client.teleport.assert_awaited_once()

    async def test_wrong_zone_key_index_goal_and_unmatched_quest_do_not_trigger(self):
        original = self.identity
        for identity in [(123, 'OtherKey', 'Stop and Goanna', self.row, True),
                         (123, 'QuestTitle_17D895', 'Stop and Goanna', None, True),
                         (123, 'QuestTitle_17D895', 'Stop and Goanna', {**self.row, 'number': 14}, True)]:
            self.identity = identity
            self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.identity = original
        self.goal_code = 'OtherGoal'
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.goal_code = 'WizQst17D895_00000007'
        self.client.zone_name.return_value = 'Wallaru/Other'
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_stopped_battle_loading_and_dialogue_do_not_start(self):
        self.client.questing_status = False
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.questing_status = True
        for attr in ('in_battle', 'is_loading'):
            getattr(self.client, attr).return_value = True
            self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
            getattr(self.client, attr).return_value = False
        self.dialogue = True
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_existing_recovery_and_probe_are_not_overridden(self):
        self.client.quest_recovery_owner = 'other'
        await self.start()
        self.assertEqual(self.client.quest_recovery_owner, 'other')
        self.client.quest_recovery_owner = None
        self.client.quest_party_probe_pending = True
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_failure_max_two_tps_survives_worker_restart_and_warns_once(self):
        await self.start()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertTrue(self.client.outback_story_pending)
        self.log.warning.assert_called_once()
        replacement = Quester(self.client, [self.client], None)
        replacement._mainline_identity = self.quester._mainline_identity
        replacement._advance_npc_dialogue = self.quester._advance_npc_dialogue
        for _ in range(4):
            self.assertTrue(await replacement._maybe_handle_outback_story(self.client))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.log.warning.assert_called_once()
        self.assertTrue(await replacement._maybe_recover_mainline(self.client))
        self.client.send_key.assert_not_awaited()

    async def test_arrived_without_dialogue_does_not_reteleport(self):
        async def tp(position):
            self.position = position
        self.client.teleport.side_effect = tp
        await self.start()
        self.client.teleport.assert_awaited_once()

    async def test_late_dialogue_after_timeout_can_finish_without_more_tp(self):
        await self.start()
        self.dialogue = True
        self.assertTrue(await self.quester._maybe_handle_outback_story(self.client))
        self.advance()
        self.assertTrue(await self.quester._maybe_handle_outback_story(self.client))
        self.now += 3.1
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.assertFalse(self.client.outback_story_pending)
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_read_gap_during_hold_never_rearms_or_crashes(self):
        await self.start()
        self.client.goal_id.side_effect = RuntimeError('read gap')
        self.assertTrue(await self.quester._maybe_handle_outback_story(self.client))
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_id_change_with_delayed_index_stays_held_without_tp(self):
        await self.start()
        self.identity = (124, 'NextKey', '', None, True)
        self.assertTrue(await self.quester._maybe_handle_outback_story(self.client))
        self.assertTrue(self.client.quest_outback_story['stage_ended'])
        self.assertTrue(self.client.outback_story_pending)
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_completed_phase_cannot_rearm_when_tracking_changes_back(self):
        self.story()
        await self.start()
        self.identity = (123, 'QuestTitle_17D895', 'Stop and Goanna', self.row, True)
        self.goal_id = 1
        self.goal_code = 'WizQst17D895_00000007'
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.teleport.assert_awaited_once()

    async def test_hitter_does_not_execute_but_solo_quester_can(self):
        self.client.quest_party_status_session = object()
        self.assertFalse(await self.quester._maybe_handle_outback_story(self.client))
        self.client.quest_party_status_session = None
        self.client.in_solo_zone = True
        self.story()
        await self.start()
        self.client.teleport.assert_awaited_once()

    async def test_mainline_group_sync_waits_while_story_is_pending(self):
        self.client.outback_story_pending = True
        self.assertTrue(await self.quester._mainline_sync_blocks_movement(self.client))
        self.quester._mainline_identity.assert_not_awaited()

    async def test_cancel_releases_lock_without_rearming_tp(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.start()
        self.assertIsNone(self.client.quest_recovery_owner)
        self.client.teleport.side_effect = None
        self.assertTrue(await self.quester._maybe_handle_outback_story(self.client))
        self.client.teleport.assert_awaited_once()


    async def test_nonfinite_target_can_trigger_exact_stage(self):
        self.target = XYZ(float('nan'), 0, 0)
        self.story()
        await self.start()
        self.client.teleport.assert_awaited_once()

    async def test_real_shared_dialogue_worker_accepts_story_owner(self):
        from wizwalker import Keycode
        self.client.quest_recovery_owner = 'outback_story'
        button = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.windows.return_value = button
        self.quester._window_text = AsyncMock(return_value='继续')
        self.assertTrue(await Quester._advance_npc_dialogue(self.quester, self.client))
        self.client.send_key.assert_awaited_once_with(Keycode.SPACEBAR)

    async def test_solo_story_priority_skips_normal_dialogue_and_finder(self):
        self.quester._maybe_handle_outback_story = AsyncMock(return_value=True)
        self.quester._quest_dialogue_blocks_movement = AsyncMock()
        self.quester._maybe_recover_mainline = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester._quest_dialogue_blocks_movement.assert_not_awaited()
        self.quester._maybe_recover_mainline.assert_not_awaited()

    async def test_group_and_normal_tp_respect_story_priority(self):
        self.quester._maybe_handle_outback_story = AsyncMock(return_value=True)
        self.quester._maybe_handle_bumbles_pet = AsyncMock()
        await self.quester.auto_quest_leader(False, False, None, False, False)
        await self.quester.teleport_to_quest_target(self.client, XYZ(100, 100, 0))
        self.quester._maybe_handle_bumbles_pet.assert_not_awaited()
        self.client.teleport.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
