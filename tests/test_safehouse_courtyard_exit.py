from unittest.mock import AsyncMock

from wizwalker import XYZ
from src.automation_ownership import get_client_automation_ownership
from src.questing import Quester
from tests import test_easton_day_guard_exit as guard_tests


class SafehouseCourtyardExitTests(guard_tests.EastonDayGuardExitTests):
    source_zone = Quester.DARKMOOR_COURTYARD_ZONE
    failed_attr = '_xuanshu_safehouse_courtyard_failed'

    def setUp(self):
        super().setUp()
        self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 10, '跟随 断枝 地点：Graveholm')

    def transition(self):
        async def teleport(point):
            self.assertEqual(point, Quester.DARKMOOR_COURTYARD_EXIT)
            self.assertEqual(self.client.quest_recovery_owner, 'easton_house')
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.zone = 'Darkmoor/DM_Z01_Graveholm'
        self.client.teleport.side_effect = teleport

    async def test_matching_guard_directly_exits_without_stall_or_normal_tp(self):
        self.transition()
        await self.quester.teleport_to_quest_target(self.client, XYZ(-588, 0, 0))
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_COURTYARD_EXIT)
        self.collision.assert_not_awaited()
        self.assertTrue(self.client.quest_party_probe_pending)
        self.assertEqual(self.client.quest_party_quest_worker_zone, 'Darkmoor/DM_Z01_Graveholm')
        self.assertIsNone(getattr(self.client, self.failed_attr))
        self.assert_released()

    async def test_hud_graveholm_location_is_not_used_as_actual_source_zone(self):
        self.zone = Quester.DARKMOOR_CANTRIP_ZONE
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.zone = 'Darkmoor/Interiors/DM_Z01I08_SafehouseCourtyardNight_01'
        self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_other_goals_of_same_parent_do_not_exit_courtyard(self):
        for text in ('Wait 在石像鬼形态中 地点：Graveholm',
                     '跟随 其他 NPC 地点：Graveholm',
                     '跟随 断枝 地点：Elsewhere',
                     '采集 石像鬼尘埃 地点：Graveholm'):
            self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 10, text)
            self.assertFalse(await self.quester._maybe_exit_easton_day_guard(self.client))
        self.client.teleport.assert_not_awaited()

    async def test_html_and_spacing_use_existing_task_text_normalization(self):
        self.progress = (Quester.DARKMOOR_GARGOYLE_QUEST_ID, 10,
                         '<center>跟随   断枝<br>地点：Graveholm</center>')
        self.transition()
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_COURTYARD_EXIT)

    async def test_prior_guard_failure_marker_does_not_block_courtyard_stage(self):
        self.client._xuanshu_easton_day_guard_failed = {'snapshot': self.progress, 'attempts': 2}
        self.quester._recover_easton_day_ritual = AsyncMock()
        self.transition()
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.DARKMOOR_COURTYARD_EXIT)
        self.quester._recover_easton_day_ritual.assert_not_awaited()
