import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.questing import Quester
from src.paths import npc_range_path
from src.automation_ownership import get_client_automation_ownership


class Member(SimpleNamespace):
    __eq__ = object.__eq__


class PartyDungeonInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.source = 'Avalon/Interiors/AV_Z04_WOwlGarden'
        self.destination = 'Avalon/Interiors/AV_Z04_WhiteOwlTower'
        self.target = XYZ(894.631, 253.874, -385.131)
        self.prompt = '按下 X 攀爬'
        self.title = '魔法藤'
        self.identity = (99, 7)
        self.text = '击败 Kiva White Talon 地点：荒野'
        self.p1 = self.member('p1')
        self.p2 = self.member('p2')
        self.p1.quest_party_hitters = [self.p2]
        self.p1.quest_party_group_dungeon_zone = self.source
        self.p1._party_area_peers = {id(self.p2): (self.token(self.p1), self.token(self.p2))}
        self.quester = Quester(self.p1, [self.p1], None)
        self.quester.read_popup = AsyncMock(side_effect=lambda _: self.prompt)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda _: self.text)
        self.quester.handle_pending_dungeon_confirmation = AsyncMock(return_value=False)
        self.quester.handle_npc_talking_quests = AsyncMock(return_value=True)
        self.quester.enter_party_dungeon = AsyncMock(return_value=True)
        self.quester.prepare_party_dungeon_entry = AsyncMock(return_value=[self.p1, self.p2])
        self.events = []
        for member in (self.p1, self.p2):
            async def press(key, duration, member=member):
                self.assertEqual(key, Keycode.X)
                self.events.append(member.title)
                member.zone_name.return_value = self.destination
            member.send_key.side_effect = press
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.proof = stack.enter_context(patch('src.questing.clients_share_live_area', AsyncMock(return_value=True)))
        self.free = stack.enter_context(patch('src.questing.is_free', AsyncMock(return_value=True)))
        self.title_reader = stack.enter_context(patch('src.questing.get_popup_title', AsyncMock(side_effect=lambda _: self.title)))
        self.visible = stack.enter_context(patch('src.questing.is_visible_by_path', AsyncMock(side_effect=lambda _, path: path == npc_range_path)))
        self.tokens = stack.enter_context(patch('src.questing._party_area_token', AsyncMock(side_effect=self.token)))
        stack.enter_context(patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)))
        self.real_sleep = asyncio.sleep
        async def tick(seconds):
            self.now += seconds
            await self.real_sleep(0)
        stack.enter_context(patch('src.questing.asyncio.sleep', tick))

    def token(self, member):
        return (self.source, id(member), 7, getattr(member, '_party_area_generation', 0))

    async def test_unknown_prompt_allows_existing_shared_x_with_live_proof(self):
        self.prompt = '按X 未知动作'
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p1.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.p2.send_key.assert_awaited_once_with(Keycode.X, .1)

    async def test_world_gate_uses_only_quester_input_and_world_selection(self):
        self.prompt = '按下 X 或 <icon;mouse> 互动'
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester._maybe_photo_giant_vat = AsyncMock(return_value=False)
        self.quester.new_world_doors = AsyncMock(return_value=True)
        with patch('src.questing.is_free_leader_questing', AsyncMock(return_value=True)), \
                patch('src.questing.is_spiral_door_open', AsyncMock(return_value=True)):
            for self.title in ('世界之门', 'World Gate'):
                self.p1.zone_name.return_value = self.source
                self.p1.send_key.reset_mock()
                self.assertFalse(await self.quester.handle_party_dungeon_interaction(self.target))
                self.assertTrue(await self.quester.handle_quest_interaction(self.p1, self.target))
                self.p1.send_key.assert_awaited_once_with(Keycode.X, .1)
                self.p2.send_key.assert_not_awaited()
        self.quester.new_world_doors.assert_awaited()
        self.proof.assert_not_awaited()

    async def test_old_world_gate_recovery_never_replays_hitter_x(self):
        self.p1.quest_party_dungeon_interaction = {'phase': 'transition', 'title': '世界之门'}
        self.assertFalse(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.assertIsNone(self.p1.quest_party_dungeon_interaction)
        self.p2.send_key.assert_not_awaited()

    async def test_legacy_world_selector_never_selects_a_world_on_hitter(self):
        self.quester.current_leader_client = self.p1
        self.p1.process_id = self.quester.current_leader_pid = 1
        self.p2.process_id = 2
        self.quester.clients = [self.p1, self.p2]
        self.quester.read_spiral_door_title = AsyncMock(return_value='世界之门')
        self.quester.new_world_doors = AsyncMock(return_value=False)
        with patch('src.questing.spiral_door_with_quest', AsyncMock()) as select, \
                patch('src.questing.go_to_new_world', AsyncMock()) as follower_select:
            await self.quester.handle_spiral_navigation()
        select.assert_awaited_once_with(self.p1)
        follower_select.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()

    def member(self, title):
        return Member(title=title, questing_status=True, in_solo_zone=False,
            entity_detect_combat_status=False, post_combat_cleanup_active=False,
            refilling_potions=False, quest_recovery_owner=None, potion_dungeon_returned=None,
            quest_id=AsyncMock(side_effect=lambda: self.identity[0]),
            goal_id=AsyncMock(side_effect=lambda: self.identity[1]),
            zone_name=AsyncMock(return_value=self.source),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            quest_position=SimpleNamespace(position=AsyncMock(return_value=self.target)),
            body=SimpleNamespace(position=AsyncMock(return_value=self.target)), send_key=AsyncMock())

    async def regroup(self):
        for _ in range(7):
            await self.quester._resume_party_dungeon_interaction(self.p2)
            self.now += .5

    def automatic_transition(self):
        self.p1.quest_party_shared_target = {
            'zone': self.source, 'identity': self.identity, 'xyz': self.target,
            'members': (id(self.p1), id(self.p2)),
            'source_tokens': {id(self.p2): self.token(self.p2)},
        }
        self.p1.zone_name.return_value = self.destination
        self.quester.teleport_to_quest_target = AsyncMock()

    async def test_post_battle_automatic_transition_replays_source_target_for_lagging_hitter(self):
        self.automatic_transition()
        self.p1.quest_position = SimpleNamespace(position=AsyncMock(return_value=XYZ(9999, 9999, 0)))
        async def catchup(member, xyz):
            self.assertIs(member, self.p2)
            self.assertIs(xyz, self.target)
            self.assertTrue(member.quest_party_target_sync_active)
            member.zone_name.return_value = self.destination
        with patch('src.questing.collision_tp', AsyncMock(side_effect=catchup)) as move:
            await self.regroup()
            move.assert_awaited_once_with(self.p2, self.target)
            self.assertFalse(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.p1.quest_position.position.assert_not_awaited()
        self.p1.send_key.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()
        self.assertFalse(self.p1.quest_party_probe_pending)
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, self.destination)
        self.assertFalse(self.p2.quest_party_target_sync_active)

    async def test_source_room_change_or_new_quest_never_replays_old_coordinates(self):
        for changed in ('source_instance', 'quest'):
            with self.subTest(changed=changed):
                self.p1.quest_party_dungeon_interaction = None
                self.automatic_transition()
                if changed == 'source_instance':
                    self.p2._party_area_generation = 1
                else:
                    self.identity = (100, 8)
                with patch('src.questing.collision_tp', AsyncMock()) as move:
                    await self.quester._resume_party_dungeon_interaction(self.p2)
                    move.assert_not_awaited()

    async def test_source_collision_move_can_finish_after_the_old_five_second_read_budget(self):
        self.automatic_transition()
        completed = []
        async def catchup(member, xyz):
            await self.real_sleep(5.1)
            completed.append(member.title)
            member.zone_name.return_value = self.destination
        with patch('src.questing.collision_tp', AsyncMock(side_effect=catchup)):
            await self.quester._resume_party_dungeon_interaction(self.p2)
        self.assertEqual(completed, ['p2'])
        self.assertEqual(self.p2.zone_name.return_value, self.destination)
        self.assertFalse(self.p2.quest_party_target_sync_active)

    async def test_screenshot_quest_local_npc_never_requires_any_hitter_prompt(self):
        self.prompt = '按下 X 交谈'
        self.title = '小精灵凯利'
        self.visible.side_effect = lambda member, path: member is self.p1 and path == npc_range_path
        self.title_reader.side_effect = lambda member: self.title if member is self.p1 else ''
        self.quester.read_popup.side_effect = lambda member: self.prompt if member is self.p1 else ''
        with patch('src.questing.interaction_kind', return_value='talk'):
            self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.p1, [self.p1])
        self.p2.zone_name.assert_not_awaited()
        self.p2.body.position.assert_not_awaited()
        self.proof.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()

    async def test_missing_hitter_prompt_blocks_shared_input_without_solo_fallback(self):
        self.visible.side_effect = lambda member, path: member is self.p1 and path == npc_range_path
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p1.send_key.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()
        self.assertEqual(self.now, 0)
        self.assertFalse(await self.quester._resume_party_dungeon_interaction())

    async def test_stopped_loading_refilling_or_distant_hitter_blocks_all_input(self):
        for attr, value in (('questing_status', False), ('refilling_potions', True),
                            ('quest_recovery_owner', 'potion_refill')):
            previous = getattr(self.p2, attr)
            setattr(self.p2, attr, value)
            self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
            setattr(self.p2, attr, previous)
        self.p2.is_loading.return_value = True
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p2.is_loading.return_value = False
        self.p2.body.position.return_value = XYZ(3000, 3000, 0)
        self.assertTrue(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p1.send_key.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()

    async def test_climb_both_inputs_start_in_the_same_group_operation(self):
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.events, ['p1', 'p2'])
        await self.regroup()
        self.assertEqual(self.events, ['p1', 'p2'])
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, self.destination)
        self.assertFalse(self.p1.quest_party_probe_pending)
        self.assertIsNone(self.p1.quest_party_dungeon_interaction)
        self.p1.send_key.assert_awaited_once()
        self.p2.send_key.assert_awaited_once()

    async def test_only_lagging_follower_may_retry_its_prevalidated_source_prompt(self):
        async def follower_press(*_):
            if self.p2.send_key.await_count == 2:
                self.p2.zone_name.return_value = self.destination
        self.p2.send_key.side_effect = follower_press
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.identity = (99, 8)
        await self.regroup()
        self.p1.send_key.assert_awaited_once()
        self.assertEqual(self.p2.send_key.await_count, 2)
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, self.destination)

    async def test_failed_shared_transition_expires_into_manual_sync_wait(self):
        self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.visible.side_effect = lambda member, path: member is self.p1 and path == npc_range_path
        self.assertTrue(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.now = 31
        with patch('src.questing.logger') as log:
            for _ in range(3):
                self.assertFalse(await self.quester._resume_party_dungeon_interaction(self.p2))
            log.warning.assert_called_once()
        self.p2.send_key.assert_awaited_once()
        self.assertFalse(await self.quester._resume_party_dungeon_interaction())
        self.assertEqual(self.p1.zone_name.return_value, self.destination)
        self.assertTrue(await self.quester._quest_party_probe_blocks_movement())
        self.assertTrue(self.p1.quest_party_probe_pending)

    async def test_unreadable_follower_recovery_cannot_hold_follow_forever_after_deadline(self):
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p2.zone_name.side_effect = ValueError('zone unreadable')
        self.assertTrue(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.now = 31
        self.assertFalse(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.assertFalse(await self.quester._resume_party_dungeon_interaction())

    async def test_changed_source_prompt_never_replays_another_button(self):
        self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.title_reader.side_effect = lambda member: '其他机关' if member is self.p2 else self.title
        await self.regroup()
        self.p2.send_key.assert_awaited_once()
        self.p1.send_key.assert_awaited_once()

    async def test_changed_source_instance_never_replays_follower_input(self):
        self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p2._party_area_generation = 1
        await self.regroup()
        self.p2.send_key.assert_awaited_once()
        self.tokens.side_effect = ValueError('area unreadable')
        await self.quester._resume_party_dungeon_interaction(self.p2)
        self.p2.send_key.assert_awaited_once()

    async def test_live_proof_allows_initial_group_input_but_no_retained_proof_disables_replay(self):
        self.p1._party_area_peers.clear()
        self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        await self.regroup()
        self.p1.send_key.assert_awaited_once()
        self.p2.send_key.assert_awaited_once()

    async def test_busy_hitter_defers_entire_group_then_both_resume(self):
        self.p2.quest_recovery_owner = 'potion_refill'
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p1.send_key.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()
        self.p2.quest_recovery_owner = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p2.send_key.assert_awaited_once()
        self.p1.send_key.assert_awaited_once()

    async def test_follower_expiry_still_accepts_late_manual_same_instance_arrival(self):
        self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.now = 31
        self.assertFalse(await self.quester._resume_party_dungeon_interaction(self.p2))
        self.p2.zone_name.return_value = self.destination
        await self.regroup()
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, self.destination)
        self.assertIsNone(self.p1.quest_party_dungeon_interaction)
        self.p2.send_key.assert_awaited_once()

    async def test_leader_can_advance_another_room_while_hitter_recovers(self):
        await self.quester.handle_party_dungeon_interaction(self.target)
        await self.quester._resume_party_dungeon_interaction(self.p2)
        next_room = 'Avalon/Interiors/NextRoom'
        self.p1.zone_name.return_value = self.p2.zone_name.return_value = next_room
        await self.regroup()
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, next_room)

    async def test_same_named_destination_without_live_proof_does_not_mark_shared_instance(self):
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p2.zone_name.return_value = self.destination
        self.proof.return_value = False
        await self.regroup()
        self.assertEqual(self.p1.quest_party_group_dungeon_zone, self.source)
        self.assertIsNotNone(self.p1.quest_party_dungeon_interaction)

    async def test_dialogue_uses_only_quester_but_sigil_reuses_whole_party_entry(self):
        for kind in ('talk', 'enter'):
            with patch('src.questing.interaction_kind', return_value=kind), \
                 patch.object(self.quester, 'party_dungeon_entry_visible', AsyncMock(return_value=kind == 'enter')):
                await self.quester.handle_party_dungeon_interaction(self.target)
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.p1, [self.p1])
        self.quester.prepare_party_dungeon_entry.assert_awaited_once()
        self.quester.enter_party_dungeon.assert_awaited_once_with([self.p1, self.p2])
        self.p2.send_key.assert_not_awaited()

    async def test_unchanged_shared_mechanism_is_not_repeated_after_worker_recreation(self):
        self.p1.send_key.side_effect = self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        recreated = Quester(self.p1, [self.p1], None)
        for name in ('read_popup', 'read_quest_txt'):
            setattr(recreated, name, getattr(self.quester, name))
        await recreated.handle_party_dungeon_interaction(self.target)
        self.p1.send_key.assert_awaited_once()
        self.p2.send_key.assert_awaited_once()
        self.identity = (99, 8)
        await recreated.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.p1.send_key.await_count, 2)
        self.assertEqual(self.p2.send_key.await_count, 2)

    async def test_unchanged_shared_exit_can_retry_once_after_checked_wait(self):
        self.p1.send_key.side_effect = self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.now = 2.5
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p1.send_key.assert_awaited_once()
        self.now = 4
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.p1.send_key.await_count, 2)
        self.assertEqual(self.p2.send_key.await_count, 2)
        for _ in range(5):
            self.now += 4
            await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertEqual(self.p1.send_key.await_count, 2)
        self.assertEqual(self.p2.send_key.await_count, 2)

    async def test_automatic_source_room_landing_then_checked_x_joins_party(self):
        self.automatic_transition()
        self.prompt, self.title = '按下 X 离开', '传送器'
        self.p1.quest_position.position.return_value = XYZ(99999, 99999, 0)
        with patch('src.questing.collision_tp', AsyncMock()) as move:
            await self.regroup()
        move.assert_awaited_once_with(self.p2, self.target)
        self.p2.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.p1.send_key.assert_not_awaited()
        self.p1.quest_position.position.assert_not_awaited()
        self.assertIsNone(self.p1.quest_party_dungeon_interaction)

    async def test_group_retry_does_not_replay_in_replaced_source_instance(self):
        self.p1.send_key.side_effect = self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.now = 4
        self.p1._party_area_generation = 1
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p1.send_key.assert_awaited_once()
        self.p2.send_key.assert_awaited_once()

    async def test_group_retry_rechecks_source_instance_after_waiting_for_input_lock(self):
        self.p1.send_key.side_effect = self.p2.send_key.side_effect = None
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.now = 4
        from src.automation_ownership import automation_owner
        async with automation_owner(self.p2, 'test-held'):
            task = asyncio.create_task(self.quester.handle_party_dungeon_interaction(self.target))
            await self.real_sleep(.02)
            self.p2._party_area_generation = 1
        await asyncio.wait_for(task, 1)
        self.p1.send_key.assert_awaited_once()
        self.p2.send_key.assert_awaited_once()
        self.assertFalse(self.p1.quest_party_target_sync_active)
        self.assertFalse(self.p2.quest_party_target_sync_active)

    async def test_source_landing_x_is_cancelled_for_loading_new_quest_instance_or_world_gate(self):
        for change in ('loading', 'quest', 'instance', 'stopped', 'world_gate', 'empty_prompt'):
            with self.subTest(change=change):
                self.setUp()
                self.automatic_transition()
                self.prompt, self.title = '按下 X 离开', '传送器'
                async def invalidate(*_):
                    if change == 'loading':
                        self.p2.is_loading.return_value = True
                    elif change == 'quest':
                        self.identity = (100, 8)
                    elif change == 'instance':
                        self.p2._party_area_generation = 1
                    elif change == 'stopped':
                        self.p2.questing_status = False
                    elif change == 'world_gate':
                        self.title = '世界之门'
                    else:
                        self.prompt = ''
                with patch('src.questing.collision_tp', AsyncMock(side_effect=invalidate)):
                    await self.quester._resume_party_dungeon_interaction(self.p2)
                self.p2.send_key.assert_not_awaited()
                self.p1.send_key.assert_not_awaited()
                self.assertFalse(self.p2.quest_party_target_sync_active)

    async def test_source_landing_x_has_only_two_attempts_before_sync_wait(self):
        self.automatic_transition()
        self.prompt, self.title = '按下 X 离开', '传送器'
        self.p2.send_key.side_effect = None
        with patch('src.questing.collision_tp', AsyncMock()):
            for _ in range(10):
                await self.quester._resume_party_dungeon_interaction(self.p2)
                self.now += 2
        self.assertEqual(self.p2.send_key.await_count, 2)
        self.p1.send_key.assert_not_awaited()

    async def test_both_party_labels_handle_local_exit_before_more_movement(self):
        self.prepare_solo_worker()
        self.p1.quest_party_dungeon_interaction = None
        self.visible.side_effect = lambda _, path: path == npc_range_path
        self.prompt, self.title = '按下 X 离开', '传送器'
        self.quester.teleport_party_to_quest_target = AsyncMock(return_value=True)
        other = self.member('unassigned')
        with patch('src.questing.interaction_kind', return_value='interact'):
            for titles in (('p1', 'p2'), ('p3', 'p4')):
                self.p1.title, self.p2.title = titles
                self.p1.zone_name.return_value = self.p2.zone_name.return_value = self.source
                self.p1.quest_party_dungeon_interaction = None
                self.p1.send_key.reset_mock()
                self.p2.send_key.reset_mock()
                await self.quester.auto_quest_solo()
                self.p1.send_key.assert_awaited_once_with(Keycode.X, .1)
                self.p2.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.quester.teleport_party_to_quest_target.assert_not_awaited()
        self.quester._resume_party_dungeon_interaction.assert_not_awaited()
        other.send_key.assert_not_awaited()

    async def test_changed_landing_context_prevents_old_input(self):
        self.p1.quest_party_shared_target = {'zone': self.source, 'identity': (99, 6), 'xyz': self.target}
        await self.quester.handle_party_dungeon_interaction(self.target)
        self.p1.send_key.assert_not_awaited()

    async def test_quester_input_cancellation_releases_owner_and_sync_flag(self):
        self.p1.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertFalse(self.p1.quest_party_target_sync_active)
        self.assertFalse(get_client_automation_ownership(self.p1).locked)

    async def test_shared_follower_input_cancellation_drains_group_and_releases_owners(self):
        self.p2.send_key.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.quester.handle_party_dungeon_interaction(self.target)
        self.assertFalse(get_client_automation_ownership(self.p2).locked)
        self.assertFalse(get_client_automation_ownership(self.p1).locked)
        self.assertFalse(self.p1.quest_party_target_sync_active)
        self.assertFalse(self.p2.quest_party_target_sync_active)

    async def test_solo_unmarked_and_no_prompt_keep_normal_quester_flow(self):
        self.p1.in_solo_zone = True
        self.assertFalse(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p1.in_solo_zone = False
        self.p1.quest_party_group_dungeon_zone = None
        self.assertFalse(await self.quester.handle_party_dungeon_interaction(self.target))
        self.p1.quest_party_group_dungeon_zone = self.source
        self.quester.read_popup.side_effect = lambda _: ''
        self.assertFalse(await self.quester.handle_party_dungeon_interaction(self.target))

    def prepare_solo_worker(self):
        for name in (
            '_maybe_handle_overgrown_estate', '_maybe_handle_darkmoor_castle',
            '_maybe_handle_outback_story', '_quest_dialogue_blocks_movement',
            '_maybe_handle_bumbles_pet', '_maybe_recover_lemuria_navigation',
            '_quest_party_probe_blocks_movement', '_maybe_handle_no_blood_hideout',
            '_maybe_handle_bumbles_mind', '_maybe_handle_tamarin_house',
            '_maybe_handle_panopticon_book', '_maybe_handle_darkmoor_cantrips',
            '_mainline_sync_blocks_movement', '_maybe_enter_avalon_grain_map',
            '_maybe_handle_callisto', '_maybe_recover_mainline', '_maybe_photo_giant_vat',
            '_maybe_recover_nightmare', '_maybe_reenter_quest_trigger',
            '_maybe_refresh_stalled_dungeon_quest', 'handle_questing_zone_change',
        ):
            setattr(self.quester, name, AsyncMock(return_value=False))
        self.quester._resume_party_dungeon_interaction = AsyncMock(side_effect=AssertionError('quester must not wait for hitter'))
        self.p1.quest_party_dungeon_interaction = {'phase': 'waiting'}
        self.p1.use_potions = self.p1.auto_pet_status = self.p1.entity_detect_combat_status = False
        self.p1.quest_position = SimpleNamespace(position=AsyncMock(return_value=self.target))
        self.p1.quest_party_shared_target = {'zone': self.source, 'identity': self.identity, 'xyz': self.target}
        self.quester.teleport_to_quest_target = AsyncMock()
        self.visible.side_effect = lambda member, path: member is self.p1 and path == npc_range_path
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in ('close_npc_quest_menu', 'close_automation_popup', 'is_spiral_door_open', 'is_potion_needed'):
            stack.enter_context(patch('src.questing.' + name, AsyncMock(return_value=False)))
        stack.enter_context(patch('src.mainline_progress.log_mainline_progress', AsyncMock()))
        stack.enter_context(patch('src.questing.interaction_kind', return_value='talk'))

    async def test_worker_continues_own_npc_without_running_hitter_recovery(self):
        self.prepare_solo_worker()
        await self.quester.auto_quest_solo()
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.p1, [self.p1])
        self.quester._resume_party_dungeon_interaction.assert_not_awaited()

    async def test_new_target_moves_both_before_nearby_quester_npc_interaction(self):
        self.prepare_solo_worker()
        self.p1.quest_party_shared_target = None
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        events = []
        self.quester.teleport_to_quest_target.side_effect = lambda member, xyz, **kw: events.append(member.title)
        self.quester.handle_npc_talking_quests.side_effect = lambda *args: events.append('NPC')
        await self.quester.auto_quest_solo()
        self.assertEqual(events, ['p1', 'p2', 'NPC'])
        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()

    async def test_unready_shared_movement_never_falls_back_to_quester_only_npc(self):
        self.prepare_solo_worker()
        self.p1.quest_party_shared_target = None
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.teleport_party_to_quest_target = AsyncMock(return_value=False)
        await self.quester.auto_quest_solo()
        self.quester.teleport_party_to_quest_target.assert_awaited_once_with(self.target)
        self.quester.handle_npc_talking_quests.assert_not_awaited()
        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()

    async def test_matching_npc_prompt_runs_dialogue_before_reentry_or_party_movement(self):
        self.prepare_solo_worker()
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.teleport_party_to_quest_target = AsyncMock()
        self.quester._trigger_reentry[id(self.p1)] = {'walking': True}
        await self.quester.auto_quest_solo()
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.p1, [self.p1])
        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()
        self.quester.teleport_party_to_quest_target.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()
        self.assertNotIn(id(self.p1), self.quester._trigger_reentry)
        self.assertTrue(self.p1.questing_status)
        self.assertTrue(self.p2.questing_status)

    async def test_waiting_party_can_talk_to_current_npc_but_never_start_new_movement(self):
        self.prepare_solo_worker()
        self.quester._quest_party_probe_blocks_movement = AsyncMock(return_value=True)
        self.quester.quest_interaction_ready = AsyncMock(return_value=True)
        self.quester.teleport_party_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.handle_npc_talking_quests.assert_awaited_once_with(self.p1, [self.p1])
        self.quester.teleport_party_to_quest_target.assert_not_awaited()
        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()
        self.quester._resume_party_dungeon_interaction.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()

    async def test_split_party_actual_worker_waits_but_other_group_keeps_moving(self):
        self.prepare_solo_worker()
        self.quester._quest_party_probe_blocks_movement = Quester._quest_party_probe_blocks_movement.__get__(self.quester)
        self.quester.read_popup.return_value = ''
        self.quester.read_popup.side_effect = None
        self.p1.zone_name.return_value = self.destination
        self.p1.quest_party_quest_worker_zone = self.source
        self.p1.quest_party_probe_pending = True
        self.quester.teleport_party_to_quest_target = AsyncMock(return_value=True)
        await self.quester.auto_quest_solo()
        self.quester.teleport_party_to_quest_target.assert_not_awaited()
        self.assertTrue(self.p1.quest_party_probe_pending)
        other = self.member('p3')
        peer = self.member('p4')
        other.quest_party_hitters = [peer]
        other.quest_party_probe_pending = True
        other.zone_name.return_value = peer.zone_name.return_value = self.destination
        other.quest_party_quest_worker_zone = self.source
        other.quest_party_group_dungeon_zone = self.source
        other.use_potions = other.auto_pet_status = other.entity_detect_combat_status = False
        other.quest_position = SimpleNamespace(position=AsyncMock(return_value=self.target))
        other_quester = Quester(other, [other], None)
        for name, value in vars(self.quester).items():
            if isinstance(value, AsyncMock) and name != '_quest_party_probe_blocks_movement':
                setattr(other_quester, name, AsyncMock(return_value=False))
        other_quester.quest_interaction_ready = AsyncMock(return_value=False)
        other_quester.read_popup = AsyncMock(return_value='')
        other_quester.teleport_party_to_quest_target = AsyncMock(return_value=False)
        await other_quester.auto_quest_solo()
        other_quester.teleport_party_to_quest_target.assert_awaited_once()
        self.assertTrue(self.p1.quest_party_probe_pending)

    async def test_dialogue_opened_before_reentry_is_handled_without_movement(self):
        self.prepare_solo_worker()
        self.quester._quest_dialogue_blocks_movement.side_effect = [False, True]
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        self.quester.teleport_party_to_quest_target = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester._maybe_reenter_quest_trigger.assert_not_awaited()
        self.quester.teleport_party_to_quest_target.assert_not_awaited()
        self.quester.quest_interaction_ready.assert_not_awaited()
        self.quester.handle_npc_talking_quests.assert_not_awaited()
        self.assertTrue(self.p1.questing_status)

    async def test_dialogue_opened_during_movement_is_handled_before_more_input(self):
        self.prepare_solo_worker()
        self.quester._quest_dialogue_blocks_movement.side_effect = [False, False, True]
        self.quester.quest_interaction_ready = AsyncMock(return_value=False)
        self.quester.handle_questing_zone_change = AsyncMock()
        self.quester.handle_party_dungeon_interaction = AsyncMock()
        await self.quester.auto_quest_solo()
        self.quester.teleport_to_quest_target.assert_any_await(self.p1, self.target, leader_client=self.p1)
        self.quester.handle_questing_zone_change.assert_not_awaited()
        self.quester.handle_party_dungeon_interaction.assert_not_awaited()
        self.quester.handle_npc_talking_quests.assert_not_awaited()
        self.p2.send_key.assert_not_awaited()
