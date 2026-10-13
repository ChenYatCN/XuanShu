"""Exercise the app's display callbacks without launching game workers."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from src.quest_party import resolve_quest_party


TREE = ast.parse((Path(__file__).resolve().parents[1] / 'XuanShu.py').read_text(encoding='utf-8'))


def display_function(name, **namespace):
    node = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    namespace['Client'] = object
    exec(compile(module, 'XuanShu.py', 'exec'), namespace)
    return namespace[name]


class QuestStatusDisplayTests(unittest.TestCase):
    def setUp(self):
        self.q1 = SimpleNamespace(title='p1', _xuanshu_mainline_progress='p1 · Karamelle 主线 58/88')
        self.q3 = SimpleNamespace(title='p3', _xuanshu_mainline_progress='p3 · Polaris 主线 19/98')
        self.h2 = SimpleNamespace(title='p2')
        self.h4 = SimpleNamespace(title='p4')
        self.q1.quest_party_hitters = [self.h2]
        self.q3.quest_party_hitters = [self.h4]
        self.clients = [self.q1, self.h2, self.q3, self.h4]
        self.snapshot = {'toggle_questing': {c.title: True for c in self.clients}}
        self.render = display_function('quest_status_text', current_quest_party=lambda clients:
            resolve_quest_party(clients, enabled=True, quester_titles=['p1', 'p3'], hitter_titles=['p2', 'p4']))
        self.update1, self.remove1 = self.callbacks(self.h2, self.q1)
        self.update3, self.remove3 = self.callbacks(self.h4, self.q3)

    def callbacks(self, hitter, quester):
        owner = object()
        hitter.quest_party_status_session = owner
        hitter.quest_party_runtime_status = (owner, quester, '')
        return (display_function('update_party_status', status_session=owner),
                display_function('remove_party_status', status_session=owner))

    def test_two_running_groups_keep_each_actual_state(self):
        self.update1(self.h2, self.q1, '正在好友传送')
        self.update3(self.h4, self.q3, '补药中，等待返回')
        expected = ('p1 · Karamelle 主线 58/88\np2 → p1｜正在好友传送\n\n'
                    'p3 · Polaris 主线 19/98\np4 → p3｜补药中，等待返回')
        self.assertEqual(self.render(self.clients, self.snapshot), expected)
        self.update1(self.h2, self.q1, '等待本组切区')
        self.assertIn('p4 → p3｜补药中，等待返回', self.render(self.clients, self.snapshot))

    def test_stopping_one_group_preserves_other_and_stopping_all_clears(self):
        self.update1(self.h2, self.q1, '已归队')
        self.update3(self.h4, self.q3, '正在地牢内任务TP')
        self.remove1(self.h2)
        self.snapshot['toggle_questing'].update(p1=False, p2=False)
        self.assertEqual(self.render(self.clients, self.snapshot),
                         'p3 · Polaris 主线 19/98\np4 → p3｜正在地牢内任务TP')
        self.snapshot['toggle_questing'].update(p3=False, p4=False)
        self.assertEqual(self.render(self.clients, self.snapshot), '')

    def test_single_group_uses_actual_assignments_and_all_hitters(self):
        # Global automatic assignment would put h4 with q3, but the running
        # scoped group actually assigned both hitters to q1.
        self.q1.quest_party_hitters.append(self.h4)
        self.callbacks(self.h4, self.q1)[0](self.h4, self.q1, '等待战斗结束')
        self.snapshot['toggle_questing']['p3'] = False
        self.update1(self.h2, self.q1, '正在好友传送')
        self.assertEqual(self.render(self.clients, self.snapshot),
            'p1 · Karamelle 主线 58/88\np2 → p1｜正在好友传送\np4 → p1｜等待战斗结束')

    def test_disconnect_unknown_progress_and_initial_state(self):
        self.q1._xuanshu_mainline_progress = ''
        self.snapshot['toggle_questing']['p3'] = False
        self.assertEqual(self.render(self.clients, self.snapshot),
                         'p1 · 主线进度待确认\np2 → p1｜等待跟随状态更新')
        self.assertEqual(self.render([self.q1], self.snapshot), 'p1 · 主线进度待确认')

    def test_restart_ignores_late_previous_updates_and_cleanup(self):
        new_update, _ = self.callbacks(self.h2, self.q1)
        new_update(self.h2, self.q1, '正在好友传送')
        self.update1(self.h2, self.q1, '旧状态')
        self.remove1(self.h2)
        self.assertIn('p2 → p1｜正在好友传送', self.render(self.clients, self.snapshot))

    def test_transient_status_cleanup_does_not_block_next_actual_update(self):
        self.remove1(self.h2)
        self.update1(self.h2, self.q1, '状态读取失败，准备恢复')
        self.assertIn('p2 → p1｜状态读取失败，准备恢复', self.render(self.clients, self.snapshot))
        self.update1(self.h2, self.q1, '正在好友传送')
        self.assertIn('p2 → p1｜正在好友传送', self.render(self.clients, self.snapshot))


if __name__ == '__main__':
    unittest.main()
