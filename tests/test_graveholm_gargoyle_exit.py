import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.paths import npc_range_path
from src.questing import Quester
from tests import test_dueling_tent_recovery as recovery_tests


class GraveholmGargoyleExitTests(unittest.IsolatedAsyncioTestCase):
    source_zone = Quester.DARKMOOR_CANTRIP_ZONE
    failed_attr = '_xuanshu_graveholm_gargoyle_failed'

    def setUp(self):
        recovery_tests.DuelingTentRecoveryTests.setUp(self)
        self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 8, 'Wait 在石像鬼形态中 地点：Graveholm')
        self.target = XYZ(7093.863, -1972.587, 31.999)
        self.client.body.position.return_value = XYZ(7100, -2078.461, 32)
        patcher = patch('src.questing.get_quest_name', new=AsyncMock(side_effect=lambda c: self.progress[2]))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def move(self, *times, quester=None):
        for self.now in times:
            await (quester or self.quester).teleport_to_quest_target(self.client, self.target)

    def transition(self):
        async def teleport(point):
            self.assertEqual(point, Quester.DARKMOOR_GARGOYLE_EXIT)
            self.assertEqual(self.client.quest_recovery_owner, 'easton_house')
            self.zone = 'Darkmoor/NewCourtyard'
        self.client.teleport.side_effect = teleport

    async def test_near_hud_point_still_counts_three_stalls_and_ten_seconds(self):
        self.transition()
        await self.move(0, 5)
        self.client.teleport.assert_not_awaited()
        await self.move(10)
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_GARGOYLE_EXIT)
        self.assertEqual(self.collision.await_count, 3)
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, 'Darkmoor/NewCourtyard')
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_known_statue_prompt_does_not_block_zone_recovery(self):
        self.visible.side_effect = lambda c, p: p == npc_range_path
        with patch('src.questing.get_popup_title', new=AsyncMock(return_value='石雕怪兽基座')):
            self.transition()
            await self.move(0, 5, 10)
        self.client.teleport.assert_awaited_once()

    async def test_other_npc_prompt_retains_normal_interaction_priority(self):
        self.visible.side_effect = lambda c, p: p == npc_range_path
        with patch('src.questing.get_popup_title', new=AsyncMock(return_value='其他 NPC')):
            await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()

    async def test_wrong_parent_goal_and_zone_do_not_run_gargoyle_exit(self):
        for progress in ((99, 8, self.progress[2]),
                         (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 7, '采集 石像鬼尘埃 地点：Graveholm'),
                         (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 8, 'Wait 在石像鬼形态中 地点：Elsewhere')):
            self.progress = progress
            await self.move(0, 5, 10)
        self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 8, 'Wait 在石像鬼形态中 地点：Graveholm')
        self.zone = 'Darkmoor/DM_Z02_MortalPlain'
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_goal_progress_and_target_jitter(self):
        self.transition()
        await self.move(0, 5)
        self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 9, self.progress[2])
        await self.move(10, 15)
        self.client.teleport.assert_not_awaited()
        self.target = XYZ(7100, -1970, 31.999)
        await self.move(20)
        self.client.teleport.assert_awaited_once()

    async def test_successful_approach_before_stalling_does_not_trigger_early(self):
        self.client.body.position.return_value = XYZ(0, 0, 0)
        async def approach(*args, **kwargs):
            self.client.body.position.return_value = XYZ(7100, -2078.461, 32)
        self.collision.side_effect = approach
        self.transition()
        await self.move(0)
        self.collision.side_effect = None
        await self.move(4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()

    async def test_two_attempts_then_no_normal_or_special_tp_across_recreation(self):
        await self.move(0, 5, 10)
        self.assertEqual(self.client.teleport.await_count, 2)
        count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        restarted._dungeon_quest_snapshot = self.quester._dungeon_quest_snapshot
        self.target = XYZ(7100, -1970, 32)
        await self.move(40, 50, quester=restarted)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, count)
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_loading_battle_busy_and_priority_guards(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending', 'quest_party_battle_rescue_active',
                     'quest_party_quest_worker_restart_requested', 'post_combat_movement_active', 'mainline_chain_retry_active'):
            setattr(self.client, attr, True)
            await self.move(0, 5, 10)
            setattr(self.client, attr, False)
        self.leader_free.return_value = False
        await self.move(0, 5, 10)
        self.leader_free.return_value = True
        self.client.is_loading.return_value = True
        await self.move(0, 5, 10)
        self.client.is_loading.return_value = False
        self.client.in_battle.return_value = True
        self.free.return_value = False
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()

    async def test_assigned_hitter_cannot_use_quester_recovery(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(0, 5, 10)
        self.collision.assert_not_awaited()
        self.client.teleport.assert_not_awaited()

    async def test_last_snapshot_takeover_prevents_special_tp_and_defers(self):
        original = self.quester._recover_dueling_tent
        async def takeover(client, progress, pending, **kwargs):
            client.refilling_potions = True
            return await original(client, progress, pending, **kwargs)
        self.quester._recover_dueling_tent = takeover
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_cancellation_releases_and_does_not_replay(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 5, 10)
        self.assertIsNone(self.client.quest_recovery_owner)
        await self.move(30, 40)
        self.client.teleport.assert_awaited_once()

    async def test_generic_trigger_reentry_yields_to_supplied_exit(self):
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.teleport.assert_not_awaited()
