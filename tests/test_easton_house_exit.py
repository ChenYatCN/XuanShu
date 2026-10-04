from types import SimpleNamespace
from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.questing import Quester
from tests import test_dueling_tent_recovery as recovery_tests


class EastonHouseExitTests(recovery_tests.DuelingTentRecoveryTests):
    source_zone = Quester.EASTON_HOUSE_ZONE
    owner = 'easton_house'
    exit_xyz = (4.903, 1730.343, 2.000)
    failed_attr = '_xuanshu_easton_house_failed'
    recovered_attr = '_easton_house_recovered_at'

    async def test_exactly_three_blocked_tps_over_ten_seconds_trigger_exit(self):
        self.transition()
        await self.move(0, 5)
        self.client.teleport.assert_not_awaited()
        await self.move(10)
        self.client.teleport.assert_awaited_once()

    async def test_similar_house_map_never_triggers_exit(self):
        self.zone = 'Darkmoor/Interiors/DM_Z01I02_EastonHouse'
        await self.move(0, 5, 10, 30)
        self.client.teleport.assert_not_awaited()

    async def test_target_jitter_does_not_reset_stall_or_failed_stage(self):
        for self.now in (0, 5, 10):
            await self.quester.teleport_to_quest_target(self.client, XYZ(9000 + self.now, 0, 0))
        self.assertEqual(self.client.teleport.await_count, 2)
        count = self.collision.await_count
        for self.now in (40, 50):
            await self.quester.teleport_to_quest_target(self.client, XYZ(9200 + self.now, 0, 0))
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, count)
        self.zone = 'Darkmoor/DM_Z01'
        await self.move(60)
        self.assertIsNone(getattr(self.client, self.failed_attr))

    async def test_assigned_hitter_cannot_run_quester_exit(self):
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()

    async def test_stopped_refilling_probe_and_restart_do_not_start_exit(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending',
                     'quest_party_quest_worker_restart_requested', 'quest_party_battle_rescue_active',
                     'post_combat_movement_active', 'mainline_chain_retry_active'):
            with self.subTest(attr=attr):
                setattr(self.client, attr, True)
                await self.move(0, 5, 10)
                setattr(self.client, attr, False)
        self.client.questing_status = False
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.collision.assert_not_awaited()

    async def test_priority_changes_during_last_snapshot_block_special_tp(self):
        for attr in ('refilling_potions', 'quest_party_probe_pending',
                     'quest_party_quest_worker_restart_requested'):
            with self.subTest(attr=attr):
                self.client.quest_recovery_owner = self.owner
                async def snapshot(_):
                    setattr(self.client, attr, True)
                    return self.progress
                self.quester._dungeon_quest_snapshot.side_effect = snapshot
                with self.assertRaises(RuntimeError):
                    await self.quester._recover_dueling_tent(
                        self.client, self.progress, AsyncMock(return_value=False), easton_house=True)
                setattr(self.client, attr, False)
        self.client.teleport.assert_not_awaited()

    async def test_battle_or_zone_change_during_snapshot_prevents_tp(self):
        for kind in ('battle', 'zone'):
            with self.subTest(kind=kind):
                self.client.quest_recovery_owner = self.owner
                self.zone = self.source_zone
                self.client.in_battle.return_value = False
                async def snapshot(_):
                    if kind == 'battle':
                        self.client.in_battle.return_value = True
                    else:
                        self.zone = 'Darkmoor/DM_Z01'
                    return self.progress
                self.quester._dungeon_quest_snapshot.side_effect = snapshot
                with self.assertRaises(RuntimeError):
                    await self.quester._recover_dueling_tent(
                        self.client, self.progress, AsyncMock(return_value=False), easton_house=True)
        self.client.teleport.assert_not_awaited()

    async def test_active_exit_blocks_generic_trigger_reentry(self):
        self.client.quest_recovery_owner = self.owner
        self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, XYZ(9000, 0, 0)))

    async def test_takeover_before_first_tp_defers_without_exhausting_stage(self):
        original = self.quester._recover_dueling_tent
        async def takeover(client, progress, interaction_pending, **kwargs):
            client.refilling_potions = True
            return await original(client, progress, interaction_pending, **kwargs)
        self.quester._recover_dueling_tent = takeover
        await self.move(0, 5, 10)
        self.client.teleport.assert_not_awaited()
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assertIsNone(self.client.quest_recovery_owner)
        self.client.refilling_potions = False
        self.quester._recover_dueling_tent = original
        self.transition()
        await self.move(20, 25, 30)
        self.client.teleport.assert_awaited_once()

    async def test_unreadable_task_never_uses_special_exit(self):
        self.quester._dungeon_quest_snapshot.return_value = None
        self.quester._dungeon_quest_snapshot.side_effect = None
        await self.move(0, 5, 10, 30)
        self.client.teleport.assert_not_awaited()

