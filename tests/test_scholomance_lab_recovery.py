import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.questing import Quester
from tests import test_final_act_recovery as fixtures


class ScholomanceLabRecoveryTests(unittest.IsolatedAsyncioTestCase):
    move = fixtures.FinalActRecoveryTests.move
    assert_released = fixtures.FinalActRecoveryTests.assert_released

    def setUp(self):
        fixtures.FinalActRecoveryTests.setUp(self)
        self.zone = Quester.SCHOLOMANCE_LAB_ZONE
        self.snapshot = (1234567, 8, '前往 米兰达的实验室 地点：Scholomance')
        self.target = XYZ(-900, 4200, 1000)
        self.position = XYZ(-200, 3500, 1000)

    def configure(self, quester):
        fixtures.FinalActRecoveryTests.configure(self, quester)
        quester.move_until_quest_interaction = AsyncMock()

    def trigger_dialogue(self):
        async def teleport(point):
            self.assertEqual((point.x, point.y, point.z), (-965.466, 4169.139, 1210.233))
            self.assertEqual(self.client.quest_recovery_owner, 'darkmoor_cantrip')
            self.dialogue = True
        self.client.teleport.side_effect = teleport

    def advance(self):
        self.snapshot = (self.snapshot[0], 9, '进入 说谎者之林 地点：Scholomance')

    async def test_three_stalled_tps_over_ten_seconds_then_exact_point_dialogue(self):
        self.trigger_dialogue()
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.collision.await_count, 4)
        self.client.send_key.assert_not_awaited()
        self.quester._advance_npc_dialogue.assert_not_awaited()
        self.assert_released()

    async def test_two_tps_after_ten_seconds_are_not_enough(self):
        await self.move(0, 12)
        self.client.teleport.assert_not_awaited()

    async def test_exactly_three_tps_and_ten_seconds_trigger(self):
        self.trigger_dialogue()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()

    async def test_wrong_zone_or_stage_never_uses_lab_point(self):
        for zone, text in (
            ('Darkmoor/DM_Z05_Scholomance', self.snapshot[2]),
            (self.zone, '前往 另一个实验室 地点：Scholomance'),
            (self.zone, '拜访 米兰达 地点：Scholomance'),
            (self.zone, '进入 说谎者之林 地点：Scholomance')):
            self.zone = zone
            self.snapshot = (1234567, 8, text)
            await self.move(0, 4, 8, 11)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_unreadable_snapshot_never_uses_lab_point(self):
        self.quester._dungeon_quest_snapshot.side_effect = None
        self.quester._dungeon_quest_snapshot.return_value = None
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_not_awaited()

    async def test_actual_goal_id_progress_resets_counter(self):
        self.trigger_dialogue()
        await self.move(0, 4, 8)
        self.snapshot = (self.snapshot[0], 9, self.snapshot[2])
        await self.move(11, 15, 19)
        self.client.teleport.assert_not_awaited()

    async def test_body_bounce_and_target_jitter_are_not_task_progress(self):
        self.trigger_dialogue()
        await self.move(0, 4)
        self.target = XYZ(-700, 4400, 1000)
        self.position = XYZ(-500, 4200, 1150)
        await self.move(8, 11)
        self.client.teleport.assert_awaited_once()

    async def test_normal_tp_dialogue_prevents_special_tp(self):
        await self.move(0, 4, 8)
        self.collision.side_effect = lambda *a, **kw: setattr(self, 'dialogue', True)
        await self.move(11)
        self.client.teleport.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_normal_tp_goal_progress_prevents_special_tp(self):
        await self.move(0, 4, 8)
        self.collision.side_effect = lambda *a, **kw: self.advance()
        await self.move(11)
        self.client.teleport.assert_not_awaited()

    async def test_configured_quester_supported_but_hitter_is_not(self):
        self.trigger_dialogue()
        self.client.quest_party_status_session = object()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.dialogue = False
        self.client.teleport.reset_mock()
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(20, 25, 30)
        self.client.teleport.assert_not_awaited()

    async def test_stopped_refilling_and_other_priority_never_special_tp(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'post_combat_movement_active', 'quest_party_quest_worker_restart_requested',
                     'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.move(0, 5, 10)
            setattr(self.client, attr, False)
        self.client.questing_status = False
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_before_first_tp_takeover_defers_without_exhausting_stage(self):
        original = self.quester._recover_final_act
        async def takeover(client, progress, **kwargs):
            client.refilling_potions = True
            await original(client, progress, **kwargs)
        self.quester._recover_final_act = takeover
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client._xuanshu_scholomance_lab_failed)
        self.assert_released()
        self.quester._recover_final_act = original
        self.client.refilling_potions = False
        self.trigger_dialogue()
        await self.move(11)
        self.client.teleport.assert_awaited_once()

    async def test_no_dialogue_is_bounded_and_does_not_repeat_after_worker_restart(self):
        await self.move(0, 5, 10)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assert_released()
        count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        self.configure(restarted)
        await self.move(40, 50, 60, quester=restarted)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, count)

    async def test_dialogue_uses_normal_worker_then_new_stage_normal_tp(self):
        self.trigger_dialogue()
        await self.move(0, 5, 10)
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.quester._advance_npc_dialogue.assert_awaited_once_with(self.client)
        self.dialogue = False
        self.client.quest_dialogue_settle = None
        await self.move(20, 25, 30)
        self.client.teleport.assert_awaited_once()  # Same stage never retriggers after dialogue.
        self.advance()
        await self.move(31)
        self.quester.move_until_quest_interaction.assert_awaited_once()
        self.client.send_key.assert_not_awaited()

    async def test_task_progress_during_special_tp_stops_second_tp(self):
        self.client.teleport.side_effect = lambda point: self.advance()
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.assertIsNone(self.client._xuanshu_scholomance_lab_failed)
        self.assert_released()

    async def test_loading_during_special_tp_aborts_without_retry(self):
        self.client.teleport.side_effect = lambda point: setattr(self.client.is_loading, 'return_value', True)
        await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_cancellation_releases_both_owners_and_never_replays(self):
        self.client.teleport.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 5, 10)
        self.assert_released()
        await self.move(40, 50)
        self.client.teleport.assert_awaited_once()

    async def test_generic_reentry_yields_to_dedicated_stall_watch(self):
        self.client.goto = AsyncMock()
        for _ in range(5):
            self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.goto.assert_not_awaited()

    async def test_late_priority_during_stage_read_prevents_special_tp(self):
        await self.move(0, 5)
        original = self.quester._scholomance_lab_stage
        async def stage(client):
            result = await original(client)
            if client.quest_recovery_owner == 'darkmoor_cantrip':
                client.post_combat_movement_active = True
            return result
        self.quester._scholomance_lab_stage = stage
        await self.move(10)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(self.client._xuanshu_scholomance_lab_failed)
        self.assert_released()
