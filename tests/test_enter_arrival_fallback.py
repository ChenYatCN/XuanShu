"""Enter aliases require actual current-target movement, never proximity alone."""
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from wizwalker import Keycode, XYZ
from src import teleport_math as movement
from tests.test_stale_interaction_movement import Fixture


class EnterArrivalTests(Fixture):
    def setUp(self):
        super().setUp()
        self.objective, self.title, self.prompt = 'Go to Task Route', 'Cabin', '点击 进入'
        self.nearby(800)
        self.client.teleport = AsyncMock(side_effect=lambda point: setattr(self, 'position', point))
        self.prompt_visible = True
        from src.paths import npc_range_path
        visible = AsyncMock(side_effect=lambda _client, path: path == npc_range_path and self.prompt_visible)
        self.stack.enter_context(patch('src.questing.is_visible_by_path', visible))
        self.stack.enter_context(patch.object(movement, 'is_visible_by_path', visible))

    async def verified_arrival(self, gap=0):
        task = await movement._movement_task_snapshot(self.client)
        origin = self.position
        state = dict(key=(task[3], (task[0], task[1], tuple(task[2]), task[3]), tuple(self.target)),
                     control=None, origin=origin, anchor=origin, walks=0,
                     rejected=False, confirmed=False, ready=False)
        self.prompt_visible = False
        async def transport(*_args, **_kwargs):
            self.assertTrue(await movement._teleport_once_verified(self.client, self.target, origin,
                task[3], validation_state=state))
            self.position = XYZ(self.target.x-gap, self.target.y, self.target.z)
            self.prompt_visible = True
            return 'ready'
        with patch('src.questing.collision_tp', AsyncMock(side_effect=transport)):
            await self.quester.move_until_quest_interaction(self.client, self.target)

    async def test_cabin_and_gatehouse_aliases_after_verified_target_arrival_send_one_x(self):
        for title, gap in [('Cabin', 0), ('Gatehouse', 46)]:
            with self.subTest(title=title):
                self.setUp()
                self.title = title
                await self.verified_arrival(gap)
                self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))
                self.on_x = lambda: self.client.zone_name.configure_mock(return_value='Next/Room')
                await self.quester.auto_quest_solo()
                self.assertEqual(self.keys, [Keycode.X])
                self.stack.close()

    async def test_close_enter_without_movement_provenance_is_rejected(self):
        self.position = self.target
        self.assertFalse(await self.quester.quest_interaction_ready(self.client, self.target))
        self.assertNotIn(Keycode.X, self.keys)

    async def test_far_enter_is_rejected_and_normal_navigation_runs(self):
        self.position = XYZ(self.target.x-9500, self.target.y, self.target.z)
        await self.quester.auto_quest_solo()
        self.assertNotIn(Keycode.X, self.keys)
        # The real wrapper is used; only the collision transport is simulated.
        from src.questing import collision_tp
        self.assertEqual(collision_tp.await_count, 1)

    async def test_walk_completion_records_its_own_current_target_arrival(self):
        from unittest.mock import patch
        async def walk(*_a, **_kw):
            self.position = XYZ(self.target.x-46, self.target.y, self.target.z)
            self.prompt_visible = True
            return 'ready'
        self.prompt_visible = False
        with patch('src.questing.collision_tp', AsyncMock(side_effect=walk)):
            self.assertEqual(await self.quester.move_until_quest_interaction(self.client, self.target), 'ready')
        self.assertTrue(await self.quester.quest_interaction_ready(self.client, self.target))

    async def test_verified_arrival_does_not_relax_npc_collect_or_use(self):
        await self.verified_arrival()
        for action in ('Press X to Talk', '按下 采集', 'Press X to Use'):
            self.prompt = action
            self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_transition_distance_is_250u_and_noop_navigation_is_not_provenance(self):
        await self.verified_arrival(250)
        self.assertTrue((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.position = XYZ(self.target.x-251, self.target.y, self.target.z)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)
        self.client._quest_tp_landing = None
        self.client._quest_target_approach = None
        self.position = self.target
        with patch('src.questing.collision_tp', AsyncMock(return_value='ready')):
            await self.quester.move_until_quest_interaction(self.client, self.target)
        self.assertFalse((await self.quester._interaction_eligibility(self.client, self.target)).accepted)

    async def test_fallback_cannot_survive_control_change_or_popup_disappearance(self):
        from src.paths import npc_range_path
        await self.verified_arrival(46)
        proof = await self.quester._interaction_eligibility(self.client, self.target)
        self.assertTrue(proof.accepted)
        parent = SimpleNamespace(read_base_address=AsyncMock(return_value=205),
                                 global_id_full=AsyncMock(return_value=210))
        pet = SimpleNamespace(position=self.client.body.position,
                              parent_client_object=AsyncMock(return_value=parent))
        with patch('src.questing.is_visible_by_path', AsyncMock(return_value=False)):
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        with patch.object(movement, 'get_controlled_body', AsyncMock(return_value=(pet, 200))):
            self.assertFalse(await self.quester._send_quest_interaction_x(self.client, self.target, proof))
        self.assertEqual(self.keys, [])

    async def test_fallback_final_x_is_cancelled_on_snapshot_or_prompt_change(self):
        for cause in ('quest', 'goal', 'zone', 'target', 'title', 'action', 'loading', 'battle'):
            with self.subTest(cause=cause):
                self.setUp()
                await self.verified_arrival(46)
                proof = await self.quester._interaction_eligibility(self.client, self.target)
                self.assertTrue(proof.accepted)
                if cause == 'quest': self.client.quest_id.return_value = 99
                if cause == 'goal': self.goal += 1
                if cause == 'zone': self.client.zone_name.return_value = 'Elsewhere'
                if cause == 'target': self.target = XYZ(1200, 2000, 3)
                if cause == 'title': self.title = 'Different Door'
                if cause == 'action': self.prompt = 'Press X to Use'
                if cause == 'loading': self.client.is_loading.return_value = True
                if cause == 'battle': self.client.in_battle.return_value = True
                self.assertFalse(await self.quester._send_quest_interaction_x(
                    self.client, XYZ(*proof.target), proof))
                self.assertEqual(self.keys, [])
                self.stack.close()
