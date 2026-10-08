import ast
import os
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QCheckBox
from src.gui.actions import ActionRegistry
from src.gui.tab_actions import build_bot_tab, build_combat_tab
from src.gui.tab_fishing import build_fishing_tab
from src.gui.tab_hotkeys import build_hotkeys_tab


def command_body(tree, name):
    return next(case.body for node in ast.walk(tree) if isinstance(node, ast.Match)
                for case in node.cases if isinstance(case.pattern, ast.MatchValue)
                and isinstance(case.pattern.value, ast.Attribute) and case.pattern.value.attr == name)


class RestartSelectionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        settings = Mock()
        settings.get_hotkeys.return_value = {}
        settings.get_settings.return_value = {}
        self.ctx = SimpleNamespace(settings=settings, tl=lambda key: key, send_queue=Mock(),
            stroke_color='#80d8e8', text_color='#eeeeee', bg_color='#171822', alt_bg='#222233',
            icon_btn_style='', svgs=defaultdict(lambda: '<svg xmlns="http://www.w3.org/2000/svg"/>'),
            titlebar_svg_icon=lambda *_: QIcon(), tracked_svg_labels=[], tracked_toggle_btns=[],
            widget_tags={}, exports={}, repo_base='', wiki_base='', tool_name='XuanShu', tool_version='test')
        self.ctx.registry = ActionRegistry(settings, self.ctx.tl, self.ctx.send_queue,
                                          '', '', self.ctx.titlebar_svg_icon)
        self.pages = {key: builder(self.ctx) for key, builder in (
            ('hotkeys', build_hotkeys_tab), ('bot', build_bot_tab),
            ('combat', build_combat_tab), ('fishing', build_fishing_tab))}
        for key in self.pages:
            self.update(key, ['p1', 'p2', 'p3'])
        self.ctx.send_queue.reset_mock()

    def tearDown(self):
        for page in self.pages.values():
            page.close()
            page.deleteLater()
        self.app.processEvents()

    def update(self, key, titles, restarting=()):
        self.ctx.exports[key]['set_available_clients'](titles, restarting_titles=restarting)

    def checks(self, key):
        return {check.text(): check for check in self.pages[key].findChildren(QCheckBox)
                if check.text().startswith('p') and check.text()[1:].isdigit()}

    def selected(self, key):
        # New controls are appended after old controls awaiting deleteLater.
        return {name for name, check in self.checks(key).items() if check.isChecked()}

    def test_selected_restart_preserves_other_clients_and_does_not_start_tasks(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.checks(key)['p3'].setChecked(False)
                self.update(key, ['p1', 'p2', 'p3'], ['p2'])
                self.update(key, ['p1', 'p3'], ['p2'])
                self.assertEqual(self.selected(key) & {'p1', 'p3'}, {'p1'})
                self.update(key, ['p1', 'p2', 'p3'])
                self.assertEqual(self.selected(key), {'p1', 'p2'})
        self.ctx.send_queue.put.assert_not_called()

    def test_unchecked_restart_stays_unchecked_when_remaining_clients_are_all_selected(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.checks(key)['p2'].setChecked(False)
                self.update(key, ['p1', 'p3'], ['p2'])
                self.update(key, ['p1', 'p2', 'p3'])
                self.assertEqual(self.selected(key), {'p1', 'p3'})

    def test_other_client_choices_can_change_while_one_client_restarts(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.update(key, ['p1', 'p3'], ['p2'])
                self.checks(key)['p1'].setChecked(False)
                self.update(key, ['p1', 'p2', 'p3'])
                self.assertEqual(self.selected(key), {'p2', 'p3'})

    def test_single_client_empty_updates_keep_unchecked_choice(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.update(key, ['p1'])
                self.checks(key)['p1'].setChecked(False)
                self.update(key, [], ['p1'])
                self.update(key, [], ['p1'])
                self.update(key, ['p1'])
                self.assertFalse(self.checks(key)['p1'].isChecked())

    def test_failed_restart_discards_saved_choice_before_slot_is_reused(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.checks(key)['p2'].setChecked(False)
                self.update(key, ['p1', 'p3'], ['p2'])
                self.update(key, ['p1', 'p3'])
                self.update(key, ['p1', 'p2', 'p3'])
                self.assertTrue(self.checks(key)['p2'].isChecked())

    def test_multiple_restarts_keep_independent_checked_values(self):
        for key in self.pages:
            with self.subTest(page=key):
                self.checks(key)['p1'].setChecked(False)
                self.checks(key)['p3'].setChecked(False)
                self.update(key, ['p3'], ['p1', 'p2'])
                self.update(key, ['p2', 'p3'], ['p1'])
                self.update(key, ['p1', 'p2', 'p3'])
                self.assertEqual(self.selected(key), {'p2'})

    def test_gui_update_command_passes_restart_state_to_all_four_selectors(self):
        tree = ast.parse(Path('src/gui/main.py').read_text(encoding='utf-8'))
        namespace = dict(com=SimpleNamespace(), widget_tags={}, _hooking_handles=set(),
            _last_hooked_data={}, _refresh_account_eligibility=Mock(),
            _rebuild_hooked_clients_list=Mock(), translation_dialog=Mock(),
            hotkeys_exports=self.ctx.exports['hotkeys'], bot_exports=self.ctx.exports['bot'],
            combat_exports=self.ctx.exports['combat'], fishing_exports=self.ctx.exports['fishing'],
            dev_utils_exports={}, ctx=SimpleNamespace(exports={'ibao': {'set_available_clients': Mock()}}))
        fn = ast.parse('def deliver(): pass').body[0]
        fn.body = command_body(tree, 'UpdateHookedClients')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     'src/gui/main.py', 'exec'), namespace)
        for key in self.pages:
            self.checks(key)['p2'].setChecked(False)
        for titles, restarting in ((['p1', 'p3'], ['p2']), (['p1', 'p2', 'p3'], [])):
            namespace['com'].data = {'hooked': [{'title': t} for t in titles], 'restarting': restarting}
            namespace['deliver']()
        for key in self.pages:
            self.assertEqual(self.selected(key), {'p1', 'p3'})


class RestartSelectionBackendTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))

    def namespace(self, fail=False):
        clients = [SimpleNamespace(title=f'p{i}', window_handle=i * 11, close=AsyncMock())
                   for i in (1, 2, 3)]
        handles = {11, 22, 33}
        namespace = dict(walker=SimpleNamespace(clients=clients[:], _managed_handles=[11, 22, 33]),
            get_all_wizard_handles=lambda: handles, launched_account_map={22: 'account'},
            _relaunching_clients={}, _hooking_in_progress=set(), window_config_applied=set(),
            client_resizing_manager=Mock(teardown_client=AsyncMock()), released_handles=set(),
            stable_client_identity=lambda c: c.title, wizlaunch=Mock(), logger=Mock(),
            stop_questing_on_client_loss=AsyncMock(), _restart_always_on_tasks=Mock(),
            _restart_active_toggle_tasks=AsyncMock(), utils=SimpleNamespace(get_wiz_install=lambda: 'fake-path'),
            com=SimpleNamespace(data=(22, 'account')), snapshots=[])
        namespace['_kill_process_by_handle'] = Mock(side_effect=lambda h: handles.discard(h))
        def launch(*args):
            if fail:
                raise RuntimeError('simulated launch failure')
            handles.add(44)
            return 44
        namespace['launch_account_instance'] = launch
        namespace['asyncio'] = SimpleNamespace(sleep=AsyncMock(),
            to_thread=AsyncMock(side_effect=lambda fn, *args: fn(*args)))
        info = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)
                    and n.name == '_build_hooked_clients_info')
        exec(compile(ast.Module(body=[info], type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
        namespace['_send_hooked_clients_update'] = lambda: namespace['snapshots'].append(namespace['_build_hooked_clients_info']())
        fn = ast.parse('async def restart(): pass').body[0]
        fn.body = command_body(self.tree, 'RelaunchClient')
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     'XuanShu.py', 'exec'), namespace)
        return namespace, clients, handles

    async def test_manual_restart_reserves_title_before_removal_and_transfers_to_new_handle(self):
        namespace, clients, handles = self.namespace()
        await namespace['restart']()
        self.assertEqual(namespace['walker'].clients, [clients[0], clients[2]])
        self.assertEqual(namespace['_relaunching_clients'], {44: {'title': 'p2', 'launching': False}})
        first, missing = namespace['snapshots'][:2]
        self.assertEqual(first['restarting'], ['p2'])
        self.assertEqual([c['title'] for c in first['hooked']], ['p1', 'p2', 'p3'])
        self.assertEqual([c['title'] for c in missing['hooked']], ['p1', 'p3'])
        self.assertEqual(missing['restarting'], ['p2'])
        clients[0].close.assert_not_awaited()
        clients[2].close.assert_not_awaited()

    async def test_launch_failure_releases_title_and_retained_selection(self):
        namespace, clients, handles = self.namespace(fail=True)
        await namespace['restart']()
        self.assertEqual(namespace['_relaunching_clients'], {})
        self.assertEqual(namespace['snapshots'][-1]['restarting'], [])
        self.assertEqual(namespace['walker'].clients, [clients[0], clients[2]])

    async def test_new_window_closed_before_hooking_releases_reservation(self):
        namespace, clients, handles = self.namespace()
        await namespace['restart']()
        handles.remove(44)
        self.assertEqual(namespace['_build_hooked_clients_info']()['restarting'], [])
        self.assertEqual(namespace['_relaunching_clients'], {})

    async def test_explicit_unhook_cancels_retained_selection_even_if_window_stays_open(self):
        namespace, clients, handles = self.namespace()
        await namespace['restart']()
        namespace['released_handles'].add(44)
        self.assertIn(44, handles)
        self.assertEqual(namespace['_build_hooked_clients_info']()['restarting'], [])
        self.assertEqual(namespace['_relaunching_clients'], {})

    async def test_replacement_keeps_reserved_number_and_unrelated_launch_skips_it(self):
        assignment = next(n for n in ast.walk(self.tree) if isinstance(n, ast.Assign)
                          and ast.unparse(n).startswith('nc.title = _relaunching_clients'))
        parent = next(n for n in ast.walk(self.tree) if isinstance(n, ast.Try) and assignment in n.body)
        body = parent.body[:parent.body.index(assignment) + 1]
        for handle, expected in ((44, 'p2'), (55, 'p4')):
            with self.subTest(handle=handle):
                nc = SimpleNamespace(title='Wizard101')
                namespace = dict(nc=nc, handle=handle, walker=SimpleNamespace(clients=[
                    SimpleNamespace(title='p1'), SimpleNamespace(title='p3'), nc]),
                    _relaunching_clients={44: {'title': 'p2', 'launching': False}})
                exec(compile(ast.Module(body=body, type_ignores=[]), 'XuanShu.py', 'exec'), namespace)
                self.assertEqual(nc.title, expected)


if __name__ == '__main__':
    unittest.main()
