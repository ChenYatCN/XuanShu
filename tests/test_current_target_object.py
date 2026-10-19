"""Localized task objects require real target arrival, never a nearby X alone."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from src.automation_ownership import automation_owner
from src.paths import npc_range_path
from src.collect_matching import CollectNames
from tests.test_stale_interaction_movement import Fixture
from tests import test_target_provenance_lifecycle as lifecycle


class CurrentTargetObjectTests(Fixture):
    confirmed_tp = lifecycle.TargetProvenanceLifecycleTests.confirmed_tp

    def setUp(self):
        super().setUp()
        self.client.quest_id.return_value = 115967690766444950
        self.goal = 606539207
        self.client.zone_name.return_value = 'Krokotopia/KT_Selenopolis/Interiors/KT_Z06I10_DarkChamber_Int'
        self.target = XYZ(-1016.927001953125, -626.1364135742188, -221.414306640625)
        self.objective = 'Take Canopic Jar'
        self.title, self.prompt = '罐子', 'Press X or to Take'
        self.nearby(1109)
        self.range_visible = True
        visible = AsyncMock(side_effect=lambda _client, path: path == npc_range_path and self.range_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))
        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', point))
        self.client.get_base_entity_list = AsyncMock(return_value=[])
        self.client.cache_handler = SimpleNamespace()

    async def object_ready(self, gap=.386):
        await self.confirmed_tp(gap=gap)
        self.range_visible = True
        return await self.quester._interaction_eligibility(self.client, self.target)

    async def test_canopic_jar_real_tp_allows_once_then_waits_for_update(self):
        proof = await self.object_ready()
        self.assertEqual(proof.reason, 'accepted-current-target-object')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertEqual(self.keys, [Keycode.X])

    async def test_unrelated_names_and_reverse_localization_allow_known_object_actions(self):
        for objective, title, action in (
                ('Take Ancient Ritual Vessel', '容器', 'Press X to Take'),
                ('收集 古代容器', 'ANCIENT VESSEL', 'Press X or to Take'),
                ('Use Unnamed Control', '机关', '按 使用'),
                ('Use Ritual Device', '装置', 'Press X to Use')):
            with self.subTest(action=action):
                self.setUp()
                self.objective, self.title, self.prompt = objective, title, action
                proof = await self.object_ready()
                self.assertEqual(proof.reason, 'accepted-current-target-object')
                self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_strong_spatial_talk_allows_localized_title_mismatch(self):
        self.objective = 'Talk to the Guardian'
        self.title, self.prompt = '星语者', '按 或 交谈'
        proof = await self.object_ready(.386)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-task')
        self.assertTrue(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_weak_spatial_localized_talk_still_requires_semantic_evidence(self):
        self.objective = 'Talk to the Guardian'
        self.title, self.prompt = '星语者', '按 或 交谈'
        proof = await self.object_ready(140)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_strong_spatial_collect_allows_localized_title_and_keeps_collector_route(self):
        self.objective = 'Collect Ancient Vessel'
        self.title, self.prompt = '古代容器', 'Press X to Collect'
        proof = await self.object_ready(.386)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-target-object')
        self.assertTrue(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_weak_spatial_localized_collect_still_requires_semantic_evidence(self):
        self.objective = 'Collect Ancient Vessel'
        self.title, self.prompt = '古代容器', 'Press X to Collect'
        proof = await self.object_ready(140)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_visible_use_prompt_after_current_task_approach_can_qualify_when_anchor_is_inside_object(self):
        self.objective = '<center>使用 星辰计 地点：奥秘</center>'
        self.client.zone_name.return_value = 'Arcanum/Interiors/AR_Z01_AstalSchool'
        self.title, self.prompt = 'Stellarometer', '按 或 使用'
        proof = await self.object_ready(140)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertEqual(proof.reason, 'accepted-current-target-object')
        self.assertTrue(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_visible_use_prompt_without_task_movement_provenance_is_rejected(self):
        self.objective = '<center>使用 星辰计 地点：奥秘</center>'
        self.title, self.prompt = 'Stellarometer', '按 或 使用'
        self.position = XYZ(self.target.x-140, self.target.y, self.target.z)
        self.range_visible = True
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_talk_prompt_does_not_use_generic_object_fallback(self):
        self.objective = 'Talk to the Guardian'
        self.title, self.prompt = 'Stellarometer', '按 或 使用'
        proof = await self.object_ready(140)
        self.assertFalse(proof.accepted)
        self.assertNotEqual(proof.reason, 'accepted-current-target-object')
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_enter_or_portal_prompt_does_not_use_object_fallback(self):
        self.objective = 'Enter the Tower'
        self.title, self.prompt = 'World Gate', 'Press X to Use'
        proof = await self.object_ready(140)
        self.assertFalse(proof.accepted)
        self.assertNotEqual(proof.reason, 'accepted-current-target-object')
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_task_changes_before_final_x_cancels_interaction(self):
        proof = await self.object_ready(140)
        self.assertTrue(proof.accepted, proof.reason)
        self.goal += 1
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_prompt_disappears_before_final_x_cancels_interaction(self):
        proof = await self.object_ready(140)
        self.assertTrue(proof.accepted, proof.reason)
        self.range_visible = False
        self.assertFalse(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_large_object_prompt_stops_navigation_before_navmap_recovery(self):
        self.objective = '<center>使用 星辰计 地点：奥秘</center>'
        self.title, self.prompt = 'Stellarometer', '按 或 使用'
        cancelled = asyncio.Event()

        async def transport(*_args, **_kwargs):
            self.position = XYZ(self.target.x-140, self.target.y, self.target.z)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            result = await asyncio.wait_for(self.quester.move_until_quest_interaction(
                self.client, self.target), .5)
        self.assertEqual(result, 'ready')
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.keys, [])
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await self.quester._send_quest_interaction_x(
            self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_interaction_position_available_still_uses_stronger_spatial_evidence(self):
        # The current client wrapper exposes no interaction_position. Exact
        # anchor arrival remains the strongest available spatial control and
        # is not weakened by the new visible-prompt fallback.
        proof = await self.object_ready(.386)
        self.assertTrue(proof.accepted, proof.reason)
        self.client._quest_target_approach = None
        self.position = XYZ(self.target.x-140, self.target.y, self.target.z)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)

    async def test_non_object_action_does_not_use_far_visible_prompt_fallback(self):
        self.objective = '休息 篝火 地点：营地'
        proof = await self.object_ready(5)
        self.assertTrue(proof.accepted, proof.reason)
        for delta in (XYZ(5.01, 0, 0), XYZ(0, 0, 5.01), XYZ(175, 0, 0), XYZ(299, 0, 0)):
            self.position = XYZ(self.target.x+delta.x, self.target.y+delta.y, self.target.z+delta.z)
            proof = await self.quester._interaction_eligibility(self.client, self.target)
            self.assertFalse(proof.accepted)
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_initial_proximity_and_repeated_noop_cannot_authorize_object(self):
        self.position = self.target
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            for _ in range(3):
                await self.quester.move_until_quest_interaction(self.client, self.target)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.assertEqual(self.keys, [])

    async def test_remote_9500u_and_nearby_unrelated_objects_never_send_x(self):
        for gap in (1109, 9500):
            for action in ('Press X or to Take', '按 使用', 'Press X to Talk', '点击 进入'):
                self.nearby(gap)
                self.prompt = action
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertFalse(proof.accepted)
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_npc_collect_unknown_actions_and_portal_are_not_object_fallback(self):
        for title, action in (('无关NPC', 'Press X to Talk'), ('无关物品', '按下 采集'),
                              ('机关', 'Press X to Dance'), ('机关', 'Press X for something'),
                              ('世界之门', 'Press X to Use')):
            with self.subTest(action=action):
                self.setUp()
                self.title, self.prompt = title, action
                proof = await self.object_ready()
                self.assertFalse(proof.accepted)
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_enter_stays_on_existing_gateway_path(self):
        self.objective = '击败 先知 地点：远方'
        self.title, self.prompt = 'Unlisted Entrance', '点击 进入'
        proof = await self.object_ready()
        self.assertEqual(proof.reason, 'accepted-current-target-gateway')
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_strong_and_semantic_success_keep_their_existing_reasons(self):
        for objective, title, action, reason in (
                ('Use Font of Germination', 'FONT OF GERMINATION', 'Press X to Use', 'accepted-current-task'),
                ('休息 篝火 地点：营地', '在营地休息', '按下 或 交谈', 'accepted-task-target-interaction')):
            with self.subTest(reason=reason):
                self.setUp()
                self.objective, self.title, self.prompt = objective, title, action
                proof = await self.object_ready(140 if reason == 'accepted-task-target-interaction' else .386)
                self.assertEqual(proof.reason, reason)
                self.stack.close()

    async def test_final_x_rechecks_every_stale_or_unsafe_state(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'title', 'action', 'hidden', 'loading', 'battle', 'expired', 'body'):
            with self.subTest(cause=cause):
                self.setUp()
                proof = await self.object_ready()
                self.assertTrue(proof.accepted, proof.reason)
                if cause == 'quest': self.client.quest_id.return_value = 99
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Other/Zone'
                if cause == 'target': self.target = XYZ(self.target.x+30, self.target.y, self.target.z)
                if cause == 'title': self.title = '其他容器'
                if cause == 'action': self.prompt = 'Press X to Use'
                if cause == 'hidden': self.range_visible = False
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                if cause == 'expired': self.client._quest_target_approach['confirmed_at'] -= 6
                if cause == 'body':
                    parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                             global_id_full=AsyncMock(return_value=210))
                    pet = SimpleNamespace(position=self.client.body.position,
                                          parent_client_object=AsyncMock(return_value=parent))
                    self.stack.enter_context(patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))))
                self.assertFalse(await self.quester._send_quest_interaction_x(self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()

    async def test_final_x_rechecks_after_last_position_read(self):
        proof = await self.object_ready()
        self.assertTrue(proof.accepted, proof.reason)
        reads = 0
        def position():
            nonlocal reads
            reads += 1
            if reads == 2:
                self.goal += 1
            return self.position
        self.client.body.position.side_effect = position
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_input_owner_conflict_cancels_object_x(self):
        proof = await self.object_ready()
        self.assertTrue(proof.accepted, proof.reason)
        entered, release = asyncio.Event(), asyncio.Event()
        async def occupied():
            async with automation_owner(self.client, 'other'):
                entered.set()
                await release.wait()
        worker = asyncio.create_task(occupied())
        try:
            await entered.wait()
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
            self.assertEqual(self.keys, [])
        finally:
            release.set()
            await worker

    async def test_dedicated_collector_candidate_keeps_priority_over_object_x(self):
        self.objective = 'Collect Ancient Ritual Vessel in This Chamber (0/2)'
        self.title = '容器'
        proof = await self.object_ready()
        self.assertTrue(proof.accepted, proof.reason)
        entity = SimpleNamespace(object_template=AsyncMock(return_value=SimpleNamespace(
            object_name=AsyncMock(return_value='AncientRitualVessel'),
            display_name=AsyncMock(return_value='UnlistedLanguageId'))),
            template_id_full=AsyncMock(return_value=987), location=AsyncMock(side_effect=lambda: self.target))
        self.client.get_base_entity_list.return_value = [entity]
        self.client.cache_handler.get_langcode_name = AsyncMock(return_value='Ancient Ritual Vessel')
        self.quester.is_position_safe = AsyncMock(return_value=True)
        self.stack.enter_context(patch('src.collecting.collision_tp', AsyncMock(return_value='ready')))
        self.stack.enter_context(patch('src.collecting.is_visible_by_path', AsyncMock(return_value=True)))
        self.stack.enter_context(patch('src.collecting.get_popup_title', AsyncMock(side_effect=lambda _: self.title)))
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])
        # The real collector owns the recognized candidate and records its retry,
        # instead of an unmatched name triggering a generic object X.
        self.assertTrue(self.client._deimos_collect_search.recent)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])  # A collector cooldown cannot authorize generic X.

    async def test_sent_object_can_resume_after_real_objective_or_popup_update(self):
        proof = await self.object_ready()
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.objective = 'Take Ancient Ritual Vessel'
        next_proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(next_proof.accepted, next_proof.reason)
        self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, next_proof))
        self.assertEqual(self.keys, [Keycode.X, Keycode.X])

    async def test_collector_priority_probe_uses_ready_names_without_expiring_movement_proof(self):
        proof = await self.object_ready()
        names = CollectNames([])
        async def slow_refresh():
            # A full WAD catalog refresh can outlast the existing 5s proof.
            self.client._quest_target_approach['confirmed_at'] -= 6
            return names
        catalog = SimpleNamespace(names=names, get=AsyncMock(side_effect=slow_refresh))
        entity = SimpleNamespace(object_template=AsyncMock(return_value=SimpleNamespace(
            object_name=AsyncMock(return_value='UnrelatedDecoration'), display_name=AsyncMock(return_value=''))),
            template_id_full=AsyncMock(return_value=999), location=AsyncMock(side_effect=lambda: self.target))
        self.client.get_base_entity_list.return_value = [entity]
        with patch('src.collect_catalog.installed_catalog', return_value=catalog):
            self.assertTrue(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_existing_leader_batch_also_waits_after_one_object_x(self):
        self.quester.current_leader_client = self.client
        proof = await self.object_ready()
        self.assertTrue(proof.accepted, proof.reason)
        self.assertTrue(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertFalse(await self.quester._send_leader_interaction_x(self.target, proof))
        self.assertEqual(self.keys, [Keycode.X])

    async def test_failed_movement_and_old_persistent_popup_cannot_authorize_object(self):
        await self.confirmed_tp(outcome='failed')
        self.range_visible = True
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.assertIsNone(getattr(self.client, '_quest_target_approach', None))
        self.setUp()
        async def arrive(*_args, **_kwargs):
            self.position = self.target
            return 'ready'
        with patch('src.questing.collision_tp', AsyncMock(side_effect=arrive)):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertFalse(proof.accepted)
        self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_cancel_after_game_receives_x_cannot_repeat_the_same_object(self):
        proof = await self.object_ready()
        received = asyncio.Event()
        original = self.client.send_key
        async def receive(key, seconds):
            await original(key, seconds)
            received.set()
            await asyncio.Event().wait()
        self.client.send_key = receive
        worker = asyncio.create_task(self.quester._send_quest_interaction_x(self.client, self.target, proof))
        try:
            await asyncio.wait_for(received.wait(), .5)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker
            self.client.send_key = original
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
            self.assertEqual(self.keys, [Keycode.X])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_auto_quest_waits_after_one_object_x_with_unchanged_popup(self):
        await self.object_ready()
        with patch.object(self.quester, 'teleport_to_quest_target', AsyncMock(return_value='ready')):
            await self.quester.auto_quest_solo()
            await self.quester.auto_quest_solo()
        self.assertEqual(self.keys.count(Keycode.X), 2)
