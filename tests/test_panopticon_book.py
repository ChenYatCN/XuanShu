import asyncio
import ast
import inspect
import textwrap
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode
from src.automation_ownership import get_client_automation_ownership
from src.paths import npc_range_path, advance_dialog_path
from src.questing import Quester


class PanopticonBookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.popup = 0.0, None
        self.text = '<center>带 正确的书交给菲茨休姆 地点：Panopticon</center>'
        self.identity, self.events = (42, 7), []
        self.client = SimpleNamespace(
            title='p1', questing_status=True, quest_recovery_owner=None,
            refilling_potions=False, quest_party_hitters=[], quest_party_status_session=None,
            zone_name=AsyncMock(return_value=Quester.PANOPTICON_BOOK_ZONE),
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            is_in_dialog=AsyncMock(return_value=False), teleport=AsyncMock(), send_key=AsyncMock(),
            body=SimpleNamespace(position=AsyncMock(return_value=XYZ(0, 0, 0))))
        self.quester = Quester(self.client, [self.client], None)
        self.quester.read_quest_txt = AsyncMock(side_effect=lambda c: self.text)
        self.quester._dungeon_quest_snapshot = AsyncMock(side_effect=lambda c:
            (*self.identity, self.text) if self.identity else None)

        async def teleport(point):
            self.assertEqual(self.client.quest_recovery_owner, 'panopticon_book')
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            self.events.append(('tp', (point.x, point.y, point.z), self.now))
            self.client.body.position.return_value = point
            self.popup = (Quester.PANOPTICON_BOOK_TITLE if point == Quester.PANOPTICON_BOOK_POSITION
                          else Quester.PANOPTICON_NPC_TITLE)

        async def key(key, seconds):
            self.assertEqual(key, Keycode.X)
            self.events.append(('x', self.popup, self.now))
            if self.popup == Quester.PANOPTICON_BOOK_TITLE:
                self.popup = None
            else:
                self.client.is_in_dialog.return_value = True

        async def sleep(seconds):
            self.now += seconds

        self.client.teleport.side_effect, self.client.send_key.side_effect = teleport, key
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('src.questing.time.monotonic', side_effect=lambda: self.now))
        stack.enter_context(patch('src.questing.asyncio.sleep', new=AsyncMock(side_effect=sleep)))
        self.free = stack.enter_context(patch('src.questing.is_free_leader_questing', new=AsyncMock(
            side_effect=lambda c: not c.is_loading.return_value and not c.in_battle.return_value
            and not c.is_in_dialog.return_value)))
        stack.enter_context(patch('src.questing.is_spiral_door_open', new=AsyncMock(return_value=False)))
        stack.enter_context(patch('src.questing.is_visible_by_path', new=AsyncMock(
            side_effect=lambda c, p: bool(self.popup) if p == npc_range_path else
            c.is_in_dialog.return_value if p == advance_dialog_path else False)))
        self.popup_title = stack.enter_context(patch('src.questing.get_popup_title', new=AsyncMock(side_effect=lambda c: self.popup)))
        self.log = stack.enter_context(patch('src.questing.logger'))

    async def handle(self):
        return await self.quester._maybe_handle_panopticon_book(self.client)

    def assert_released(self):
        self.assertIsNone(self.client.quest_recovery_owner)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        if hasattr(self.client, '_xuanshu_panopticon_book_stage'):
            self.assertFalse(self.client._xuanshu_panopticon_book_stage['active'])

    async def test_order_coordinates_interactions_dialogue_handoff(self):
        self.assertTrue(await self.handle())
        self.assertEqual([(e[0], e[1]) for e in self.events], [
            ('tp', (-7088.015, -2901.169, -104.569)), ('x', Quester.PANOPTICON_BOOK_TITLE),
            ('tp', (-2685.747, -665.561, -105.242)), ('x', Quester.PANOPTICON_NPC_TITLE)])
        self.assertGreaterEqual(self.events[2][2] - self.events[1][2], .5)
        self.assertEqual(self.client._xuanshu_panopticon_book_stage['phase'], 'completed')
        self.assertEqual(self.client.quest_dialogue_settle, {'snapshot': None, 'since': None})
        self.assert_released()

    async def test_wrong_zone_task_location_or_hitter_never_triggers(self):
        self.client.zone_name.return_value = 'Arcanum/AR_Z02_LowerDecks'
        self.assertFalse(await self.handle())
        self.client.zone_name.return_value = Quester.PANOPTICON_BOOK_ZONE
        for text in ('带 错误的书交给菲茨休姆 地点：Panopticon',
                     '带 正确的书交给菲茨休姆 地点：其他区域', '正确的书', ''):
            self.text = text
            self.assertFalse(await self.handle())
        self.text = '带 正确的书交给菲茨休姆 地点：Panopticon'
        self.quester.clients.append(SimpleNamespace(quest_party_hitters=[self.client]))
        self.assertFalse(await self.handle())
        self.client.teleport.assert_not_awaited()

    async def test_recovery_refill_and_probe_defer_without_inputs(self):
        for name, value in (('quest_recovery_owner', 'dungeon_quest'), ('refilling_potions', True),
                            ('quest_party_probe_pending', True), ('quest_party_battle_rescue_active', True)):
            original = getattr(self.client, name, False)
            setattr(self.client, name, value)
            self.assertTrue(await self.handle())
            setattr(self.client, name, original)
        self.client.teleport.assert_not_awaited()

    async def test_wrong_book_never_sends_x_or_npc_tp(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            self.popup = '错误的书'
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_book_without_response_never_runs_npc_tp_or_replays(self):
        self.client.send_key.side_effect = None
        await self.handle()
        self.client.teleport.assert_awaited_once_with(Quester.PANOPTICON_BOOK_POSITION)
        self.client.send_key.assert_awaited_once_with(Keycode.X, .1)
        self.assertFalse(await self.handle())
        self.assertLess(self.now, 10)
        self.assert_released()

    async def test_book_dialogue_handoff_then_resume_without_recollecting(self):
        original = self.client.send_key.side_effect
        async def key(key, seconds):
            await original(key, seconds)
            self.client.is_in_dialog.return_value = True
        self.client.send_key.side_effect = key
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_panopticon_book_stage['phase'], 'book_wait')
        self.assert_released()
        self.client.is_in_dialog.return_value = False
        self.client.send_key.side_effect = original
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)
        self.assertEqual(self.client.send_key.await_count, 2)

    async def test_same_id_talk_goal_after_collection_is_supported(self):
        original = self.client.send_key.side_effect
        async def key(key, seconds):
            await original(key, seconds)
            if self.popup is None:
                self.identity = (42, 8)
                self.text = '拜访 菲茨休姆 地点：Panopticon'
        self.client.send_key.side_effect = key
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_different_id_after_book_cancels_npc_tp(self):
        original = self.client.send_key.side_effect
        async def key(key, seconds):
            await original(key, seconds)
            self.identity = (99, 8)
        self.client.send_key.side_effect = key
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assertEqual(self.client._xuanshu_panopticon_book_stage['phase'], 'progressed')
        self.assert_released()

    async def test_text_fallback_without_readable_ids(self):
        self.identity = None
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_loading_after_book_cancels_npc_tp(self):
        original = self.client.send_key.side_effect
        async def key(key, seconds):
            await original(key, seconds)
            self.client.is_loading.return_value = True
        self.client.send_key.side_effect = key
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_wrong_npc_prompt_is_bounded_without_wrong_x(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            if point == Quester.PANOPTICON_NPC_POSITION:
                self.popup = '其他 NPC'
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.assertEqual(self.client.teleport.await_count, 3)
        self.client.send_key.assert_awaited_once()
        self.assertLess(self.now, 15)
        self.assert_released()

    async def test_cancelled_flow_releases_and_never_replays(self):
        self.client.teleport.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.handle()
        self.assert_released()
        self.assertFalse(await self.handle())

    async def test_completed_stage_survives_worker_recreation(self):
        await self.handle()
        self.client.is_in_dialog.return_value = False
        restarted = Quester(self.client, [self.client], None)
        restarted.read_quest_txt = self.quester.read_quest_txt
        self.assertFalse(await restarted._maybe_handle_panopticon_book(self.client))
        self.assertEqual(self.client.teleport.await_count, 2)

    async def test_ordinary_tp_is_blocked_during_special_stage(self):
        self.client.quest_recovery_owner = 'panopticon_book'
        self.quester.move_until_quest_interaction = AsyncMock()
        await self.quester.teleport_to_quest_target(self.client, XYZ(1, 1, 1))
        self.quester.move_until_quest_interaction.assert_not_awaited()
        self.client.teleport.assert_not_awaited()

    async def test_refill_during_last_book_title_read_prevents_x(self):
        reads = 0
        async def title(client):
            nonlocal reads
            reads += 1
            if reads == 2:
                client.refilling_potions = True
            return self.popup
        self.popup_title.side_effect = title
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.client.send_key.assert_not_awaited()
        self.assert_released()

    async def test_loading_during_npc_task_recheck_prevents_second_tp(self):
        def snapshot(client):
            state = getattr(client, '_xuanshu_panopticon_book_stage', None)
            if state and state['phase'] == 'npc':
                client.is_loading.return_value = True
            return (*self.identity, self.text)
        self.quester._dungeon_quest_snapshot.side_effect = snapshot
        await self.handle()
        self.client.teleport.assert_awaited_once()
        self.assert_released()

    async def test_goal_change_before_book_x_cancels_old_stage(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            self.identity = (42, 8)
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.client.send_key.assert_not_awaited()
        self.assertEqual(self.client._xuanshu_panopticon_book_stage['phase'], 'progressed')

    async def test_dialogue_in_another_zone_is_not_success(self):
        original = self.client.teleport.side_effect
        async def teleport(point):
            await original(point)
            if point == Quester.PANOPTICON_NPC_POSITION:
                self.client.zone_name.return_value = 'Another/Zone'
                self.client.is_in_dialog.return_value = True
        self.client.teleport.side_effect = teleport
        await self.handle()
        self.assertEqual(self.client._xuanshu_panopticon_book_stage['phase'], 'failed')
        self.assert_released()

    def test_special_flow_precedes_finder_in_both_workers(self):
        for worker in (Quester.auto_quest_solo, Quester.auto_quest_leader):
            tree = ast.parse(textwrap.dedent(inspect.getsource(worker)))
            calls = {name: sorted(n.lineno for n in ast.walk(tree)
                                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                 and n.func.attr == name)
                     for name in ('_maybe_handle_panopticon_book', '_maybe_recover_mainline')}
            self.assertLess(calls['_maybe_handle_panopticon_book'][0], calls['_maybe_recover_mainline'][0])
