"""Potion returns keep client switches and distinguish resume from party reunion."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wizwalker import XYZ
from src import utils
from src.automation_ownership import automation_owner, get_client_automation_ownership
from tests import test_potion_return_validation as fixture

REAL_SLEEP = asyncio.sleep


class PotionReturnLifecycleTests(unittest.IsolatedAsyncioTestCase):
    ZONE = fixture.PotionReturnValidationTests.ZONE
    async def asyncSetUp(self):
        await fixture.PotionReturnValidationTests.asyncSetUp(self)

    make_client = fixture.PotionReturnValidationTests.make_client
    last_validation = fixture.PotionReturnValidationTests.last_validation
    production_follower = fixture.PotionReturnValidationTests.production_follower

    async def hub_departure(self, *, battle=False, source_proof=True):
        hub = 'Khrysalis/KR_Z00_Hub'
        source = self.client._party_area_peers[id(self.peer)][0]
        self.client.zone_name.return_value = self.peer.zone_name.return_value = hub
        self.client.area.zone_id.return_value = self.peer.area.zone_id.return_value = 321
        self.client.quest_party_group_dungeon_zone = hub
        self.client.quest_party_dungeon_interaction = {
            'zone': self.ZONE, 'phase': 'transition', 'destination': hub,
            'snapshot': (42, 7, 'Find lens'),
            'source_token': source if source_proof else None,
        }
        self.entities.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=22))]
        self.assertTrue(await utils.prepare_potion_dungeon_return(self.client, hub))
        self.client.zone_name.return_value = 'WizardCity/WC_Hub'
        self.entities.return_value = []
        async def arrive(_):
            self.client.zone_name.return_value = self.ZONE
            self.client.area.zone_id.return_value = 123
            self.client.in_battle.return_value = battle
        self.client.mouse_handler.click_window.side_effect = arrive
        return hub

    async def test_quester_resume_from_hub_uses_saved_scene_not_hitter_hub(self):
        original = await self.hub_departure()
        self.assertEqual(self.client.potion_return_context['resume_zone'], self.ZONE)
        self.assertEqual(self.client.potion_return_context['resume_zone_id'], 123)
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, original))
        state = self.last_validation()
        self.assertEqual(state['target_zone'], self.ZONE)
        self.assertTrue(state['client_returned'])
        self.assertEqual(await self.peer.zone_name(), original)
        self.assertEqual(self.client.potion_dungeon_returned[2], {id(self.peer)})
        self.tp.assert_not_awaited()
        self.assertTrue(self.client.questing_status)
        self.assertTrue(self.peer.questing_status)

    async def test_battle_after_proved_quester_resume_does_not_move_or_negate_return(self):
        original = await self.hub_departure(battle=True)
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, original))
        self.assertTrue(self.last_validation()['battle'])
        self.assertTrue(self.last_validation()['client_returned'])
        self.tp.assert_not_awaited()
        self.assertLess(self.now, 3)

    async def test_battle_without_saved_scene_is_handoff_not_return_success(self):
        original = await self.hub_departure(battle=True, source_proof=False)
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, original))
        self.assertEqual(self.last_validation()['condition'], 'combat_handoff')
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)
        self.tp.assert_not_awaited()
        self.assertTrue(self.client.questing_status)

    async def test_source_scene_same_name_wrong_zone_id_does_not_confirm(self):
        original = await self.hub_departure()
        async def wrong_instance(_):
            self.client.zone_name.return_value = self.ZONE
            self.client.area.zone_id.return_value = 999
        self.client.mouse_handler.click_window.side_effect = wrong_instance
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, original))
        self.assertEqual(self.last_validation()['condition'], 'zone_id_mismatch')
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)
        self.tp.assert_not_awaited()

    async def test_any_scene_and_readable_quest_do_not_replace_source_proof(self):
        original = await self.hub_departure(source_proof=False)
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, original))
        self.assertNotIn('returned_snapshot', self.client.potion_return_context)
        self.tp.assert_not_awaited()

    async def test_normal_self_return_does_not_require_hitter_to_have_returned(self):
        self.peer.zone_name.return_value = 'Khrysalis/KR_Z00_Hub'
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertTrue(self.last_validation()['client_returned'])
        self.tp.assert_not_awaited()

    async def test_hitter_entering_battle_in_first_room_stops_catchup(self):
        self.client.quest_party_hitters = []
        self.client.quest_party_quester = self.peer
        async def arrive(_):
            self.client.zone_name.return_value = 'Dungeon/Entrance'
            self.client.in_battle.return_value = True
        self.client.mouse_handler.click_window.side_effect = arrive
        self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertEqual(self.last_validation()['condition'], 'combat_handoff')
        self.tp.assert_not_awaited()
        self.assertTrue(self.client.questing_status)

    async def test_success_does_not_replace_another_active_recovery_or_battle_state(self):
        saved = {'zone': self.ZONE, 'active': False}
        other = {'zone': self.ZONE, 'active': True, 'owner': object()}
        self.client.potion_return_context.update(dungeon_state=dict(saved), dungeon_state_ref=saved)
        self.client.quest_dungeon_recovery = other
        self.client.quest_party_battle_sync_state = 'trying'
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.assertIs(self.client.quest_dungeon_recovery, other)
        self.assertEqual(self.client.quest_party_battle_sync_state, 'trying')

    async def test_user_stop_while_waiting_for_red_button_never_clicks_or_reenables(self):
        self.button.is_visible.return_value = False
        async def stop(seconds):
            self.now += seconds
            self.client.questing_status = False
        with patch.object(utils.asyncio, 'sleep', AsyncMock(side_effect=stop)):
            self.assertFalse(await utils.return_to_dungeon_after_potions(self.client, self.ZONE))
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.assertFalse(self.client.questing_status)
        self.assertLess(self.now, 1)

    async def test_failed_refill_backoff_hands_off_without_more_purchases_or_buttons(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
            current_gold=AsyncMock(return_value=100000))
        async def leave(client):
            client.zone_name.return_value = 'WizardCity/WC_Hub'
        async def fail(client, *args, **kwargs):
            client.potion_return_context['purchase_zone'] = await client.zone_name()
            return False
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=leave)) as travel, \
             patch.object(utils, 'navigate_to_commons_from_ravenwood', AsyncMock()), \
             patch.object(utils, 'navigate_to_potions', AsyncMock()), \
             patch.object(utils, 'buy_potions', AsyncMock(side_effect=fail)) as buy:
            self.assertFalse(await utils.refill_potions(self.client))
            self.assertTrue(self.client.questing_status)
            self.assertFalse(self.client.refilling_potions)
            self.assertIsNone(self.client.quest_recovery_owner)
            for _ in range(3):
                self.assertFalse(await utils.refill_potions(self.client))
            self.now += 31
            self.hud.maybe_text.return_value = '<center>Defeat boss</center>'
            self.assertTrue(await utils.refill_potions(self.client))  # No purchase attempted; normal recovery resumes.
            self.assertTrue(self.client.potion_return_context['failure']['handed_off'])
            self.assertNotIn('returned_snapshot', self.client.potion_return_context)
            buy.assert_awaited_once()
            travel.assert_awaited_once()
            self.hud.maybe_text.return_value = ''
            self.assertTrue(await utils.refill_potions(self.client))
            buy.assert_awaited_once()  # An unreadable HUD is not task progress.
            self.hud.maybe_text.return_value = 'Defeat boss'
            self.client.goal_id.return_value = 9
            self.assertFalse(await utils.refill_potions(self.client))
            self.assertEqual(buy.await_count, 2, 'a genuinely changed task can request a fresh refill')

    async def test_refill_exception_keeps_switch_and_other_recovery_owner(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
            current_gold=AsyncMock(return_value=100000))
        async def fail(client):
            client.quest_recovery_owner = 'npc-dialogue'
            raise TimeoutError('travel timeout')
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=fail)):
            self.assertFalse(await utils.refill_potions(self.client))
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.client.refilling_potions)
        self.assertEqual(self.client.quest_recovery_owner, 'npc-dialogue')

    async def test_user_stop_during_return_releases_refill_without_reopening(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
            current_gold=AsyncMock(return_value=100000))
        async def leave(client):
            client.zone_name.return_value = 'WizardCity/WC_Hub'
        async def stop(_):
            self.client.zone_name.return_value = self.ZONE
            self.client.questing_status = False
        self.client.mouse_handler.click_window.side_effect = stop
        async def buy(client, *args, **kwargs):
            return await utils.return_to_dungeon_after_potions(client, self.ZONE)
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=leave)), \
             patch.object(utils, 'navigate_to_commons_from_ravenwood', AsyncMock()), \
             patch.object(utils, 'navigate_to_potions', AsyncMock()), \
             patch.object(utils, 'buy_potions', AsyncMock(side_effect=buy)) as purchase:
            self.assertFalse(await utils.refill_potions(self.client))
            self.assertFalse(await utils.refill_potions(self.client))
        self.assertFalse(self.client.questing_status)
        self.assertFalse(self.client.refilling_potions)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(self.client.potion_return_context)
        purchase.assert_awaited_once()

    async def test_cancel_owned_refill_drains_input_and_keeps_user_switch(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
            current_gold=AsyncMock(return_value=100000))
        started, released = asyncio.Event(), asyncio.Event()
        async def wait(client):
            async with automation_owner(client, 'potion-travel'):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    released.set()
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=wait)):
            task = asyncio.create_task(utils.refill_potions(self.client))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(released.is_set())
        self.assertTrue(self.client.questing_status)
        self.assertFalse(self.client.refilling_potions)
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertIsNone(self.client.potion_return_context)
        self.assertFalse(get_client_automation_ownership(self.client).locked)

    async def test_superseded_refill_never_clears_new_flow_state(self):
        self.client.zone_name.return_value = self.ZONE
        self.client.stats = SimpleNamespace(reference_level=AsyncMock(return_value=100),
            current_gold=AsyncMock(return_value=100000))
        new_task, new_context = object(), {'owner': 'new-refill'}
        async def replace(client):
            client._potion_refill_task = new_task
            client.potion_return_context = new_context
            raise ValueError('old flow failed')
        with patch.object(utils, 'navigate_to_ravenwood', AsyncMock(side_effect=replace)):
            self.assertFalse(await utils.refill_potions(self.client))
        self.assertTrue(self.client.refilling_potions)
        self.assertEqual(self.client.quest_recovery_owner, 'potion_refill')
        self.assertIs(self.client._potion_refill_task, new_task)
        self.assertIs(self.client.potion_return_context, new_context)
        self.assertTrue(self.client.questing_status)

    async def test_return_marker_allows_real_friend_follow_from_hub(self):
        original = await self.hub_departure()
        self.assertTrue(await utils.return_to_dungeon_after_potions(self.client, original))
        app, follower = self.production_follower()
        self.client.quest_party_dungeon_interaction = None
        self.client.wizard_name = 'Test Wizard'
        self.peer.entity_detect_combat_status = False
        for member in (self.client, self.peer):
            member.body = SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0)))
            member.send_key, member.teleport = AsyncMock(), AsyncMock()
        reader = SimpleNamespace(handle_pending_dungeon_confirmation=AsyncMock(return_value=False),
            _resume_party_dungeon_interaction=AsyncMock(return_value=False),
            get_truncated_quest_objectives=AsyncMock(return_value='Defeat boss'))
        callbacks = dict(zip(follower.__code__.co_freevars, (cell.cell_contents for cell in follower.__closure__)))
        def finish(hitter, quester, state):
            if state == '已归队':
                hitter.questing_status = False  # Simulated user stop after observing reunion.
        callbacks['update_party_status'].side_effect = finish
        async def friend(client, **kwargs):
            self.peer.zone_name.return_value = self.ZONE
            self.peer.area.zone_id.return_value = 123
            self.entities.return_value = [SimpleNamespace(global_id_full=AsyncMock(return_value=22))]
        async def tick(seconds):
            self.now += seconds
            self.assertLess(self.now, 12, 'verified quester marker still blocks different-zone friend follow')
            await REAL_SLEEP(0)
        with patch.object(app, 'Quester', Mock(return_value=reader)), \
             patch.object(app, 'is_spiral_door_open', AsyncMock(return_value=False)), \
             patch.object(app, 'close_automation_popup', AsyncMock(return_value=False)), \
             patch.object(app, 'is_visible_by_path', AsyncMock(return_value=False)), \
             patch.object(app, 'is_free', AsyncMock(return_value=True)), \
             patch.object(app, 'use_potions', False), \
             patch.object(app, 'quest_friend_icons', {}, create=True), \
             patch.object(app, 'resolve_quester_friend_icon', Mock(return_value=None)), \
             patch.object(app, 'close_friend_windows', AsyncMock()), \
             patch.object(app, 'is_friend_teleport_error', AsyncMock(return_value=False)), \
             patch.object(utils.asyncio, 'sleep', AsyncMock(side_effect=tick)), \
             patch.object(app, 'teleport_to_friend_from_list', AsyncMock(side_effect=friend)) as tp:
            await follower(self.peer, self.client, False)
        tp.assert_awaited_once_with(self.peer, name='Test Wizard')
        self.assertIsNone(self.client.potion_dungeon_returned)
        self.assertTrue(self.client.questing_status)
        self.client.teleport.assert_not_awaited()


class PotionCatchupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(title='p2', questing_status=True, _xuanshu_dungeon_closed=False,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Dungeon/Entrance'))

    async def test_battle_cancels_actual_owned_room_movement(self):
        client = self.client()
        released = asyncio.Event()
        async def move(client, **kwargs):
            client.in_battle.return_value = True
            try:
                await asyncio.Future()
            finally:
                released.set()
        with patch.object(utils, 'is_free', AsyncMock(return_value=True)), \
             patch('src.teleport_math.navmap_tp', AsyncMock(side_effect=move)) as tp:
            await utils._potion_dungeon_room_tp(client, 'Dungeon/Entrance', reenter=False)
        self.assertTrue(released.is_set())
        self.assertFalse(get_client_automation_ownership(client).locked)
        tp.assert_awaited_once()

    async def test_user_stop_while_waiting_for_other_input_keeps_foreign_owner(self):
        client = self.client()
        async with automation_owner(client, 'npc-dialogue'):
            with patch.object(utils, 'is_free', AsyncMock(return_value=True)), \
                 patch('src.teleport_math.navmap_tp', AsyncMock()) as tp:
                task = asyncio.create_task(utils._potion_dungeon_room_tp(client, 'Dungeon/Entrance', reenter=False))
                await REAL_SLEEP(.02)
                client.questing_status = False
                await asyncio.wait_for(task, 1)
                tp.assert_not_awaited()
            self.assertTrue(get_client_automation_ownership(client).locked)
        self.assertFalse(get_client_automation_ownership(client).locked)
        self.assertFalse(client.questing_status)


class QuestStatusDisplayTests(unittest.TestCase):
    def test_live_client_display_distinguishes_group_on_from_stopped_member(self):
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8-sig'))
        statement = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == 'snapshot' for t in node.targets)
            and 'questing_status' in ast.unparse(node))
        first, second = SimpleNamespace(title='p1', questing_status=False), SimpleNamespace(title='p2', questing_status=True)
        namespace = dict(snapshot={}, action='toggle_questing', effective_group_ids={id(first), id(second)},
            legacy_on=False, legacy_freecam_ids=set(), live_clients=[first, second])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[statement], type_ignores=[])), 'XuanShu.py', 'exec'), namespace)
        self.assertEqual(namespace['snapshot']['toggle_questing'], {'p1': False, 'p2': True})
