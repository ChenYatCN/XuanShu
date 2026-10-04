import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode
from src.mainline_progress import quest_rows
from src.paths import advance_dialog_path, cancel_multiple_quest_menu_path, decline_quest_path
from src.questing import Quester
from src.utils import close_npc_quest_menu


def window(name, text='', children=(), visible=True):
    return SimpleNamespace(
        name=AsyncMock(return_value=name), maybe_text=AsyncMock(return_value=text),
        children=AsyncMock(return_value=list(children)),
        is_visible=AsyncMock(return_value=visible),
        is_control_grayed=AsyncMock(return_value=False),
    )


class NpcMainlineMenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rows = quest_rows()
        self.row = next(r for r in rows if r['english'] == 'Extra Life')
        self.later = next(r for r in rows if r['world'] == self.row['world']
                          and r['number'] > self.row['number'] and r['keys'])
        self.other = next(r for r in rows if r['world'] == 'Krokotopia' and r['keys'])
        self.now = 0.0
        self.choices = window('actualServiceChoices')
        self.exit = window('Exit')
        self.menu = window('NPCServicesWin', children=[
            self.choices, window('wndDialogMain', children=[self.exit])])
        self.accept = window('btnRight', '接受', visible=False)
        self.decline = window('btnLeft', visible=False)
        self.offer_title = window('txtQuestTitle', visible=False)
        self.dialog = window('wndDialogMain', children=[self.accept, self.decline, self.offer_title])
        root = window('root', children=[window('WorldView', children=[self.menu, self.dialog])])
        self.quests = {99: SimpleNamespace(
            name_lang_key=AsyncMock(return_value='SideTitle'), mainline=AsyncMock(return_value=False))}
        self.client = SimpleNamespace(
            title='p1', root_window=root, questing_status=True, auto_dialogue_running=False,
            quest_recovery_owner=None, mainline_finder_enabled=True,
            is_loading=AsyncMock(return_value=False), in_battle=AsyncMock(return_value=False),
            zone_name=AsyncMock(return_value='Empyrea/Area'), quest_id=AsyncMock(return_value=99),
            goal_id=AsyncMock(return_value=7), send_key=AsyncMock(),
            quest_manager=AsyncMock(return_value=SimpleNamespace(quest_data=AsyncMock(return_value=self.quests))),
            cache_handler=SimpleNamespace(get_langcode_name=AsyncMock(side_effect=lambda code:
                next((r['english'] for r in rows if code in r['keys']), 'Unindexed sidequest'))),
        )
        self.quester = Quester(self.client, [self.client], None)
        self.target_rows = {}
        self.chosen = None
        self.accepted = asyncio.Event()

        async def click(client, target):
            if target is self.accept:
                self.accept.is_visible.return_value = False
                self.decline.is_visible.return_value = False
                self.offer_title.is_visible.return_value = False
                self.quests[42] = SimpleNamespace(
                    name_lang_key=AsyncMock(return_value=self.chosen['keys'][0]),
                    mainline=AsyncMock(return_value=True))
                self.client.quest_id.return_value = 42
                self.accepted.set()
            else:
                self.chosen = self.target_rows[id(target)]
                self.menu.is_visible.return_value = False
                self.exit.is_visible.return_value = False
                self.accept.is_visible.return_value = True
                self.decline.is_visible.return_value = True
                self.offer_title.is_visible.return_value = True
                self.offer_title.maybe_text.return_value = self.chosen['english']

        async def cancel(client, path):
            self.menu.is_visible.return_value = False
            self.exit.is_visible.return_value = False

        self.click = AsyncMock(side_effect=click)
        self.cancel = AsyncMock(side_effect=cancel)
        for patcher in (
            patch.object(Quester, '_click_ui_window', new=self.click),
            patch('src.questing.time', SimpleNamespace(monotonic=lambda: self.now)),
            patch('src.utils.safe_click_window', new=self.cancel),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.set_choices([('About PvP', None), ('额外生命', self.row)])

    def set_choices(self, entries, nested=False):
        nodes = []
        for index, (title, row) in enumerate(entries):
            node = window(f'liveChoice{index}', title)
            if nested:
                child = window('renderedLabel', title)
                node.children.return_value = [child]
                self.target_rows[id(child)] = row
            self.target_rows[id(node)] = row
            nodes.append(node)
        self.choices.children.return_value = nodes
        return nodes

    async def test_new_darkmoor_three_branch_list_selects_confirmed_mainline(self):
        rows = [row for row in quest_rows() if row['world'] == 'darkmoor'
                and row['number'] in (30, 31, 32)]
        self.client.zone_name.return_value = 'Darkmoor/DM_Z02_MortalPlain'
        targets = self.set_choices([(row['chinese'][0], row) for row in reversed(rows)])
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_awaited_once_with(self.client, targets[-1])
        self.assertEqual(self.client.npc_mainline_menu_selection['row'], rows[0])
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.client.quest_id.return_value, 42)
        self.assertIsNone(self.client.npc_mainline_menu_selection)
        self.cancel.assert_not_awaited()

    async def test_unowned_mainline_is_selected_accepted_and_confirmed_not_cancelled(self):
        target = self.choices.children.return_value[1]
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_awaited_once_with(self.client, target)
        self.assertEqual(self.quests.keys(), {99})
        self.assertTrue(await self.quester._advance_npc_dialogue(self.client))
        self.assertEqual(self.client.quest_id.return_value, 42)
        await self.quester._advance_npc_dialogue(self.client)
        self.assertIsNone(self.client.npc_mainline_menu_selection)
        self.cancel.assert_not_awaited()
        self.client.send_key.assert_not_awaited()

    async def test_two_distinct_mainline_branches_choose_index_order_then_next_branch(self):
        first, last = self.set_choices([(self.later['english'], self.later), ('额外生命', self.row)])
        await close_npc_quest_menu(self.client)
        self.click.assert_awaited_once_with(self.client, last)
        await self.quester._advance_npc_dialogue(self.client)
        await self.quester._advance_npc_dialogue(self.client)
        self.menu.is_visible.return_value = self.exit.is_visible.return_value = True
        next_target = self.set_choices([(self.later['english'], self.later)])[0]
        await close_npc_quest_menu(self.client)
        self.assertIs(self.click.await_args.args[1], next_target)
        self.assertEqual(self.client.npc_mainline_menu_selection['row'], self.later)
        self.cancel.assert_not_awaited()

    async def test_button_and_child_title_are_one_option(self):
        target = self.set_choices([('额外生命', self.row)], nested=True)[0].children.return_value[0]
        await close_npc_quest_menu(self.client)
        self.click.assert_awaited_once_with(self.client, target)

    async def test_duplicate_separate_titles_are_preserved_without_guessing(self):
        self.set_choices([('额外生命', self.row), ('Extra Life', self.row)])
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_not_awaited()
        self.cancel.assert_not_awaited()
        self.assertTrue(self.client.npc_mainline_menu_selection['failed'])

    async def test_mixed_worlds_use_current_world_and_unknown_context_keeps_menu(self):
        targets = self.set_choices([(self.other['english'], self.other), ('额外生命', self.row)])
        self.client.zone_name.return_value = 'Arcanum/Area'
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_not_awaited()
        self.cancel.assert_not_awaited()
        self.client.zone_name.return_value = 'Empyrea/Area'
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_awaited_once_with(self.client, targets[1])

    async def test_sidequest_only_list_keeps_existing_cancel_behavior(self):
        self.set_choices([('About PvP', None), ('Unindexed sidequest', None)])
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.cancel.assert_awaited_once_with(self.client, cancel_multiple_quest_menu_path)
        self.click.assert_not_awaited()

    async def test_selected_mainline_does_not_accept_different_indexed_offer(self):
        await close_npc_quest_menu(self.client)
        self.offer_title.maybe_text.return_value = self.later['english']
        await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.click.await_count, 1)
        self.assertTrue(self.client.npc_mainline_menu_selection['failed'])

    async def test_dialogue_and_quest_workers_cannot_double_select(self):
        await asyncio.gather(close_npc_quest_menu(self.client),
                             self.quester._advance_npc_dialogue(self.client))
        # The second worker can accept the now-visible invitation, but never reselects.
        self.assertEqual([call.args[1] for call in self.click.await_args_list].count(
            self.choices.children.return_value[1]), 1)
        self.cancel.assert_not_awaited()

    async def test_stalled_menu_has_two_click_limit_across_quester_recreation(self):
        self.click.side_effect = None
        for now in (0, .2, 1.6, 3.2, 9.0):
            self.now = now
            self.assertTrue(await close_npc_quest_menu(self.client))
        self.assertEqual(self.click.await_count, 2)
        self.cancel.assert_not_awaited()
        self.assertTrue(self.client.npc_mainline_menu_selection['failed'])

    async def test_stop_during_final_title_read_prevents_click(self):
        target = self.choices.children.return_value[1]
        reads = 0

        async def text():
            nonlocal reads
            reads += 1
            if reads == 2:
                self.client.questing_status = False
            return '额外生命'

        target.maybe_text.side_effect = text
        self.assertTrue(await self.quester._select_npc_mainline_menu(self.client))
        self.click.assert_not_awaited()

    async def test_other_recovery_preserves_menu_without_clicks(self):
        self.client.quest_recovery_owner = 'callisto'
        self.assertTrue(await close_npc_quest_menu(self.client))
        self.click.assert_not_awaited()
        self.cancel.assert_not_awaited()

    async def test_stopping_automation_before_acceptance_does_not_click_accept(self):
        await close_npc_quest_menu(self.client)
        self.client.questing_status = False
        await self.quester._advance_npc_dialogue(self.client)
        self.assertEqual(self.click.await_count, 1)

    async def test_tracked_mainline_world_takes_priority_over_npc_physical_zone(self):
        targets = self.set_choices([(self.other['english'], self.other), ('额外生命', self.row)])
        self.quests[99].name_lang_key.return_value = self.row['keys'][0]
        self.quests[99].mainline.return_value = True
        self.client.zone_name.return_value = 'Arcanum/Area'
        await close_npc_quest_menu(self.client)
        self.click.assert_awaited_once_with(self.client, targets[1])

    async def test_loading_or_battle_does_not_click_list_or_cancel(self):
        for name in ('is_loading', 'in_battle'):
            getattr(self.client, name).return_value = True
            self.assertFalse(await close_npc_quest_menu(self.client))
            self.click.assert_not_awaited()
            self.cancel.assert_not_awaited()
            getattr(self.client, name).return_value = False

    async def test_failed_finder_cleanup_does_not_cancel_preserved_mainline_list(self):
        self.set_choices([('额外生命', self.row), ('Extra Life', self.row)])
        await close_npc_quest_menu(self.client)
        identity = await self.quester._mainline_identity(self.client)
        zone = await self.client.zone_name()
        self.quester._mainline_finder_observations[id(self.client)] = {
            'snapshot': (*identity[:3], zone), 'since': 0.0, 'count': 3,
        }
        self.now = 4.0
        self.quester._mainline_finder_blocked = AsyncMock(return_value=False)
        self.quester._run_mainline_finder = AsyncMock(return_value=False)
        self.assertTrue(await self.quester._maybe_recover_mainline(self.client))
        self.quester._run_mainline_finder.assert_awaited_once()
        self.cancel.assert_not_awaited()
        self.click.assert_not_awaited()
        self.assertIsNone(self.client.quest_recovery_owner)

    async def test_expected_id_selects_its_branch_instead_of_first(self):
        targets = self.set_choices([('额外生命', self.row), (self.later['english'], self.later)])
        self.quests[43] = SimpleNamespace(name_lang_key=AsyncMock(return_value=self.later['keys'][0]))
        self.assertTrue(await self.quester._select_npc_mainline_menu(self.client, expected_id=43))
        self.click.assert_awaited_once_with(self.client, targets[1])

    async def test_same_title_in_multiple_worlds_is_resolved_by_current_world(self):
        row = next(r for r in quest_rows() if 'QuestTitle_17D615' in r['keys'])
        target = self.set_choices([('Monkey Business', row)])[0]
        self.client.zone_name.return_value = 'Novus/Area'
        await close_npc_quest_menu(self.client)
        self.click.assert_awaited_once_with(self.client, target)
        self.assertEqual(self.client.npc_mainline_menu_selection['row'], row)

    async def test_same_title_without_world_context_is_not_cancelled_as_sidequest(self):
        row = next(r for r in quest_rows() if 'QuestTitle_17D615' in r['keys'])
        self.set_choices([('Monkey Business', row)])
        self.client.zone_name.return_value = 'Arcanum/Area'
        await close_npc_quest_menu(self.client)
        self.click.assert_not_awaited()
        self.cancel.assert_not_awaited()
        self.assertTrue(self.client.npc_mainline_menu_selection['failed'])

    async def test_unreadable_expected_id_does_not_choose_other_branch(self):
        self.assertTrue(await self.quester._select_npc_mainline_menu(self.client, expected_id=43))
        self.click.assert_not_awaited()
        self.cancel.assert_not_awaited()

    async def test_finder_handles_multi_quest_list_before_acceptance(self):
        self.quests[99].name_lang_key.return_value = '任务搜寻'
        self.quester._restore_owned_mainline = AsyncMock(return_value=False)

        async def accept(client, path):
            await self.click(client, self.accept)

        with patch('src.questing.click_window_by_path', new=AsyncMock(
                side_effect=accept)), \
             patch('src.mainline_progress.log_mainline_progress', new=AsyncMock()):
            self.assertTrue(await self.quester._run_mainline_finder(self.client))
        self.assertEqual(self.click.await_count, 2)
        self.assertEqual(self.client.quest_id.return_value, 42)
        self.cancel.assert_not_awaited()

    async def test_auto_dialogue_alone_selects_and_accepts_mainline_with_side_accept_off(self):
        self.client.questing_status = False
        tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dialogue_loop')
        namespace = dict(
            asyncio=asyncio, walker=SimpleNamespace(clients=[self.client]), freecam_status=False,
            Quester=Quester, close_npc_quest_menu=close_npc_quest_menu, Keycode=Keycode,
            advance_dialog_path=advance_dialog_path, decline_quest_path=decline_quest_path,
            side_quest_status=False,
        )
        from src.task_lifecycle import gather_owned
        from src.utils import is_visible_by_path
        namespace.update(gather_owned=gather_owned, is_visible_by_path=is_visible_by_path)
        exec(compile('from __future__ import annotations\n' + ast.unparse(function),
                     'XuanShu.py', 'exec'), namespace)
        task = asyncio.create_task(namespace['dialogue_loop']())
        try:
            await asyncio.wait_for(self.accepted.wait(), 1)
            self.assertEqual(self.click.await_count, 2)
            self.client.send_key.assert_not_awaited()
            self.cancel.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
