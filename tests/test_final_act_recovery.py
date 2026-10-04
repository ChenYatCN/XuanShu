import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ
from src.paths import advance_dialog_path
from src.questing import Quester, claim_quest_recovery
from src.automation_ownership import get_client_automation_ownership


class FinalActRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.zone = 'Krokotopia/KT_Selenopolis/KT_Z05_Market'
        self.snapshot = (153685337617326243, 22, '寻找 流氓剧院 地点：Marketplace of Ideas')
        self.code = 'QuestTitle_00002057'
        self.position = XYZ(-50, 7014.8057, -332.561)
        self.target = XYZ(-4.007, 7146.385, -135.003)
        self.dialogue = False
        self.on_tick = None
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            refilling_potions=False, quest_dungeon_recovery=None, quest_party_hitters=[],
            zone_name=AsyncMock(side_effect=lambda: self.zone),
            quest_id=AsyncMock(side_effect=lambda: self.snapshot[0]),
            goal_id=AsyncMock(side_effect=lambda: self.snapshot[1]),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(side_effect=lambda: self.dialogue),
            body=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.position)),
            quest_position=SimpleNamespace(position=AsyncMock(side_effect=lambda: self.target)),
            teleport=AsyncMock(), send_key=AsyncMock())
        self.quester = Quester(self.client, [self.client], None)
        self.configure(self.quester)

        real_sleep = asyncio.sleep
        async def tick(seconds):
            self.now += seconds
            if self.on_tick:
                self.on_tick()
            await real_sleep(0)

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=tick)))
        self.collision = stack.enter_context(patch('src.questing.collision_tp', new=AsyncMock()))
        self.free = stack.enter_context(patch('src.questing.is_free', new=AsyncMock(
            side_effect=lambda c: not self.dialogue and not c.is_loading.return_value
            and not c.in_battle.return_value)))
        stack.enter_context(patch('src.questing.is_free_leader_questing', self.free))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: self.dialogue and p == advance_dialog_path)))
        stack.enter_context(patch('src.questing.get_quest_name', new=AsyncMock(
            side_effect=lambda c: '' if self.dialogue else self.snapshot[2])))
        stack.enter_context(patch('src.questing.read_dialogue_text', new=AsyncMock(
            side_effect=lambda c: 'Story' if self.dialogue else '')))
        self.log = stack.enter_context(patch('src.questing.logger'))

    def configure(self, quester):
        quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c: self.snapshot)
        quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.snapshot[2])
        quester._mainline_identity = AsyncMock(side_effect=lambda c: (
            self.snapshot[0], self.code, 'The Final Act', None, True))
        quester._advance_npc_dialogue = AsyncMock(return_value=True)

    async def move(self, *times, quester=None):
        for self.now in times:
            await (quester or self.quester).teleport_to_quest_target(self.client, self.target)

    def trigger_dialogue(self):
        async def teleport(point):
            self.assertEqual((point.x, point.y, point.z), (-47.276, 7293.811, -234.572))
            self.assertEqual(self.client.quest_recovery_owner, 'final_act')
            self.assertFalse(claim_quest_recovery(self.client, 'trigger_reentry'))
            self.dialogue = True
        self.client.teleport.side_effect = teleport

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_three_stalled_tps_and_ten_seconds_then_exact_point_dialogue(self):
        self.trigger_dialogue()
        await self.move(0, 4, 8)
        self.client.teleport.assert_not_awaited()
        await self.move(11)
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.quester._advance_npc_dialogue.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)
        self.assert_released()

    async def test_two_tps_even_after_ten_seconds_do_not_trigger(self):
        await self.move(0, 12)
        self.client.teleport.assert_not_awaited()

    async def test_wrong_zone_quest_key_target_action_and_location_do_not_trigger(self):
        for zone, code, text in (
            ('Krokotopia/KT_Selenopolis/KT_Z04_Selenopolis', self.code, self.snapshot[2]),
            (self.zone, 'QuestTitle_00002052', self.snapshot[2]),
            (self.zone, self.code, '寻找 其他建筑 地点：Marketplace of Ideas'),
            (self.zone, self.code, '击败 流氓剧院 地点：Marketplace of Ideas'),
            (self.zone, self.code, '寻找 流氓剧院 地点：Selenopolis')):
            self.zone, self.code = zone, code
            self.snapshot = (153685337617326243, 22, text)
            await self.move(0, 4, 8, 11)
            self.client.teleport.assert_not_awaited()
            self.quester._krok_exit_watch.clear()

    async def test_goal_progress_resets_counter(self):
        await self.move(0, 4, 8)
        self.snapshot = (self.snapshot[0], 23, self.snapshot[2])
        await self.move(11, 15, 19)
        self.client.teleport.assert_not_awaited()

    async def test_target_jitter_and_body_bounce_are_not_quest_progress(self):
        self.trigger_dialogue()
        await self.move(0, 4)
        self.target = XYZ(self.target.x + 60, self.target.y, self.target.z)
        self.position = XYZ(self.position.x + 200, self.position.y, self.position.z)
        await self.move(8, 11)
        self.client.teleport.assert_awaited_once()

    async def test_normal_dialogue_after_tp_cancels_special_fallback(self):
        await self.move(0, 4, 8)
        async def normal(*args, **kwargs):
            self.dialogue = True
        self.collision.side_effect = normal
        await self.move(11)
        self.client.teleport.assert_not_awaited()
        self.assertNotIn(id(self.client), self.quester._krok_exit_watch)

    async def test_busy_or_assigned_hitter_does_not_special_tp(self):
        await self.move(0, 4, 8)
        for attr, value in (('refilling_potions', True), ('quest_party_probe_pending', True),
                            ('quest_party_battle_rescue_active', True), ('post_combat_movement_active', True),
                            ('quest_party_status_session', object()), ('quest_recovery_owner', 'potion_refill')):
            with self.subTest(attr=attr):
                setattr(self.client, attr, value)
                await self.move(11)
                self.client.teleport.assert_not_awaited()
                setattr(self.client, attr, None if attr in ('quest_party_status_session', 'quest_recovery_owner') else False)

    async def test_two_failed_special_tps_stop_same_stage_loop_across_restart(self):
        await self.move(0, 4, 8, 11)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assert_released()
        count = self.collision.await_count
        restarted = Quester(self.client, [self.client], None)
        self.configure(restarted)
        await self.move(40, 50, 60, quester=restarted)
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.collision.await_count, count)

    async def test_loading_during_special_tp_aborts_without_second_tp(self):
        async def teleport(point):
            self.client.is_loading.return_value = True
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_progress_during_special_tp_aborts_without_second_tp(self):
        async def teleport(point):
            self.snapshot = (self.snapshot[0], 23, '交谈 卡利斯托 地点：Marketplace of Ideas')
        self.client.teleport.side_effect = teleport
        await self.move(0, 4, 8, 11)
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_cancellation_during_special_tp_releases_both_locks(self):
        self.client.teleport.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.move(0, 4, 8, 11)
        self.assert_released()

    async def test_generic_trigger_reentry_yields_this_stage_to_dedicated_tp(self):
        self.quester._trigger_reentry_blocked = AsyncMock(return_value=False)
        self.position = self.target
        self.client.goto = AsyncMock()
        for _ in range(5):
            self.assertFalse(await self.quester._maybe_reenter_quest_trigger(self.client, self.target))
        self.client.goto.assert_not_awaited()

    async def test_dialogue_handoff_reaches_existing_dialogue_step(self):
        self.trigger_dialogue()
        await self.move(0, 4, 8, 11)
        self.assertTrue(await self.quester._quest_dialogue_blocks_movement(self.client))
        self.quester._advance_npc_dialogue.assert_awaited_once_with(self.client)

    async def test_priority_changed_during_last_memory_read_prevents_special_tp(self):
        for attr in ('refilling_potions', 'post_combat_movement_active',
                     'quest_party_quest_worker_restart_requested', 'mainline_chain_retry_active'):
            with self.subTest(attr=attr):
                self.quester._krok_exit_watch.clear()
                self.client._xuanshu_final_act_failed = None
                self.position = XYZ(-50, 7014, -332)
                await self.move(0, 4, 8)
                async def free(c):
                    if c.quest_recovery_owner == 'final_act':
                        setattr(c, attr, True)
                    return True
                self.free.side_effect = free
                await self.move(11)
                self.client.teleport.assert_not_awaited()
                setattr(self.client, attr, False)
                self.free.side_effect = lambda c: True
                self.assert_released()


if __name__ == '__main__':
    unittest.main()
