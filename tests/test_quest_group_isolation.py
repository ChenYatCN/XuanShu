import ast
import asyncio
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.hotkey_groups import HotkeyGroups
from src.quest_party import QuestParty, resolve_quest_party


TREE = ast.parse(Path('XuanShu.py').read_text(encoding='utf-8'))


def source_function(name):
    return next(n for n in ast.walk(TREE)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def load(name, namespace):
    exec(compile(ast.Module(body=[source_function(name)], type_ignores=[]),
                 'XuanShu.py', 'exec'), namespace)
    return namespace[name]


def gui_namespace():
    return dict(gui_send_queue=queue.Queue(), xuanshu_gui=SimpleNamespace(
        GUICommand=lambda kind, payload: payload,
        GUICommandType=SimpleNamespace(UpdateWindow='update')))


class PartyIsolationTests(unittest.TestCase):
    def setUp(self):
        self.clients = [SimpleNamespace(title=f'p{i}', questing_status=False)
                        for i in range(1, 5)]
        self.left, self.right = self.clients[:2], self.clients[2:]
        self.ns = dict(walker=SimpleNamespace(clients=self.clients),
                       QuestParty=QuestParty, resolve_quest_party=resolve_quest_party,
                       quest_party_enabled=True, questing_client_titles=['p1', 'p3'],
                       questing_hitter_client_titles=['p2', 'p4'],
                       quest_hitter_assignment_mode='manual',
                       quest_hitter_assignments={'p2': 'p1', 'p4': 'p3'},
                       mainline_finder_enabled=True)
        self.current = load('current_quest_party', self.ns)
        self.apply = load('apply_questing_roles', self.ns)

    def test_starting_right_group_does_not_rebind_left_after_settings_change(self):
        left_party = self.apply(True, self.left)
        self.ns['questing_client_titles'] = ['p3']
        self.ns['questing_hitter_client_titles'] = ['p4']
        self.ns['quest_hitter_assignments'] = {'p4': 'p3'}
        self.apply(True, self.right)
        current = self.current()
        self.assertEqual([c.title for c in current.questers], ['p1', 'p3'])
        self.assertEqual([(h.title, q.title) for h, q in current.hitter_assignments],
                         [('p2', 'p1'), ('p4', 'p3')])
        self.assertIs(self.left[0]._quest_party_runtime, left_party)
        self.assertIs(self.left[1].quest_party_quester, self.left[0])
        self.assertEqual(self.left[0].quest_mainline_sync_members, [self.left[0]])

    def test_stop_right_preserves_left_and_restart_uses_new_roles(self):
        self.apply(True, self.left)
        self.apply(True, self.right)
        self.apply(False, self.right)
        self.assertTrue(all(c.questing_status for c in self.left))
        self.assertTrue(all(not c.questing_status for c in self.right))
        self.assertIs(self.left[1].quest_party_quester, self.left[0])
        self.assertTrue(all(c._quest_party_runtime is None for c in self.right))
        self.ns['questing_client_titles'] = ['p4']
        self.ns['questing_hitter_client_titles'] = ['p3']
        self.ns['quest_hitter_assignments'] = {'p3': 'p4'}
        self.apply(True, self.right)
        self.assertIs(self.right[0].quest_party_quester, self.right[1])
        self.assertIs(self.left[1].quest_party_quester, self.left[0])

    def test_subset_and_lost_quester_cannot_assign_to_another_group(self):
        self.apply(True, self.left)
        self.apply(True, self.right)
        subset = self.current([self.left[1], *self.right])
        self.assertEqual(subset.questers, [self.right[0]])
        self.assertEqual(subset.hitter_assignments, [(self.right[1], self.right[0])])
        self.assertEqual(len(self.current(self.left).hitters), 1)

    def test_no_configured_quester_does_not_invent_a_role(self):
        self.ns['questing_client_titles'] = ['p1']
        self.ns['questing_hitter_client_titles'] = ['p2', 'p3', 'p4']
        party = self.apply(True, self.right)
        self.assertEqual(party.questers, [])
        self.assertTrue(all(not c.questing_status for c in self.right))


class StatusIsolationTests(unittest.TestCase):
    def setUp(self):
        self.shared = {}
        self.gui = gui_namespace()
        self.left = self.session('p2', 'p1')
        self.right = self.session('p4', 'p3')

    def session(self, hitter_title, quester_title):
        hitter, quester = SimpleNamespace(title=hitter_title), SimpleNamespace(title=quester_title)
        token = object()
        hitter.quest_party_status_session = token
        ns = dict(self.gui, Client=object, runtime_status={}, status_session=token,
                  quest_party_runtime_status=self.shared,
                  party=QuestParty([quester], [hitter], [], [(hitter, quester)]))
        for name in ('publish_party_status', 'update_party_status', 'remove_party_status'):
            load(name, ns)
        ns.update(hitter=hitter, quester=quester)
        return ns

    def update(self, ns, state='已归队'):
        ns['update_party_status'](ns['hitter'], ns['quester'], state)

    def last_status(self):
        messages = []
        while not self.gui['gui_send_queue'].empty():
            messages.append(self.gui['gui_send_queue'].get())
        return messages[-1][1]

    def cleanup(self, ns):
        final = next(n for n in source_function('questing_loop').body
                     if isinstance(n, ast.Try) and n.finalbody)
        exec(compile(ast.Module(body=final.finalbody, type_ignores=[]),
                     'XuanShu.py', 'exec'), ns)

    def test_two_groups_update_and_remove_only_their_own_row(self):
        self.update(self.left)
        self.update(self.right)
        self.assertEqual(self.last_status(), 'p2 → p1｜已归队\np4 → p3｜已归队')
        self.update(self.right, '正在跟随')
        self.assertEqual(self.last_status(), 'p2 → p1｜已归队\np4 → p3｜正在跟随')
        self.right['remove_party_status'](self.right['hitter'])
        self.assertEqual(self.last_status(), 'p2 → p1｜已归队')

    def test_transient_follower_restart_can_publish_again(self):
        self.update(self.left)
        self.left['remove_party_status'](self.left['hitter'])
        self.update(self.left, '恢复跟随')
        self.assertEqual(self.last_status(), 'p2 → p1｜恢复跟随')

    def test_loop_cleanup_preserves_peer_and_late_cleanup_preserves_new_session(self):
        self.update(self.left)
        self.update(self.right)
        old = self.right
        self.cleanup(old)
        self.assertEqual(self.last_status(), 'p2 → p1｜已归队')
        new = self.session('p4', 'p3')
        new['hitter'] = old['hitter']
        new['party'].hitter_assignments[:] = [(new['hitter'], new['quester'])]
        new['hitter'].quest_party_status_session = new['status_session']
        self.update(new, '新分组')
        self.update(old, '过期状态')
        self.cleanup(old)
        self.assertEqual(self.last_status(), 'p2 → p1｜已归队\np4 → p3｜新分组')
        self.assertIs(new['hitter'].quest_party_status_session, new['status_session'])


class LostClientIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = [SimpleNamespace(title=f'p{i}', questing_status=True,
                                       hotkey_quest_clients=None) for i in range(1, 5)]
        self.left, self.right = self.clients[:2], self.clients[2:]
        self.stopped = []
        async def run(action, members):
            try:
                await asyncio.Future()
            finally:
                self.stopped.append([c.title for c in members])
        self.groups = HotkeyGroups(lambda: self.clients, run)
        self.groups._start('toggle_questing', self.left)
        self.groups._start('toggle_questing', self.right)
        for members, _ in self.groups.groups.values():
            for c in members:
                c.hotkey_quest_clients = members
        self.addAsyncCleanup(self.groups.stop)
        await asyncio.sleep(0)
        def clear(active, members=None):
            for c in self.clients if members is None else members:
                c.questing_status = active
        self.ns = dict(gui_namespace(), asyncio=asyncio, logger=Mock(),
                       walker=SimpleNamespace(clients=self.clients),
                       hotkey_groups=self.groups, questing_status=False,
                       questing_task=None, apply_questing_roles=Mock(side_effect=clear),
                       bool_to_string=lambda value: 'Enabled' if value else 'Disabled')
        wrapper = ast.parse('def wrapper():\n    paused_task_names = set()\n').body[0]
        wrapper.body.extend([source_function('stop_questing_on_client_loss'),
                             ast.Return(value=ast.Name(id='stop_questing_on_client_loss', ctx=ast.Load()))])
        module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        exec(compile(module, 'XuanShu.py', 'exec'), self.ns)
        self.stop = self.ns['wrapper']()

    async def test_right_selection_stops_only_right_and_leaves_left_task_running(self):
        left_task = next(task for members, task in self.groups.groups.values() if members is self.left)
        await self.stop('p4', reason='已返回选角色界面')
        self.assertEqual(self.stopped, [['p3', 'p4']])
        self.assertFalse(left_task.done())
        self.assertTrue(all(c.questing_status for c in self.left))
        self.assertTrue(all(not c.questing_status for c in self.right))
        self.assertEqual(self.ns['gui_send_queue'].get(), ('QuestingStatus', 'Enabled'))

    async def test_unrelated_loss_does_not_stop_any_group(self):
        await self.stop('p9')
        self.assertEqual(self.stopped, [])
        self.ns['apply_questing_roles'].assert_not_called()

    async def test_multi_client_loss_stops_both_affected_groups(self):
        await self.stop('p1, p4')
        self.assertEqual(sorted(self.stopped), [['p1', 'p2'], ['p3', 'p4']])
        self.assertEqual(self.groups.groups, {})
        self.assertEqual(self.ns['gui_send_queue'].get(), ('QuestingStatus', 'Disabled'))

    async def test_legacy_loss_retains_scoped_group_runtime(self):
        legacy = SimpleNamespace(title='p5', questing_status=True, hotkey_quest_clients=None)
        self.clients.append(legacy)
        self.ns['questing_status'] = True
        self.ns['questing_task'] = asyncio.create_task(asyncio.Event().wait())
        task = self.ns['questing_task']
        await self.stop('p5')
        self.assertTrue(task.cancelled())
        self.assertFalse(legacy.questing_status)
        self.assertTrue(all(c.questing_status for c in self.left + self.right))
        self.assertEqual(len(self.groups.groups), 2)
