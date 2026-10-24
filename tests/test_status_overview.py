import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QAbstractItemView

from src.settings_manager import DEFAULT_THEME


class StatusOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.gui.status_overview import StatusOverviewTable
        self.table = StatusOverviewTable()
        self.table.resize(720, 300)
        self.table.set_theme(DEFAULT_THEME)
        self.table.show()
        self.addCleanup(self.table.close)
        self.addCleanup(self.table.deleteLater)
        self.actions = {'toggle_combat': '自动战斗', 'toggle_sigil': '自动地牢'}

    def refresh(self, clients, states, identities=None):
        self.table.update_snapshot(self.actions, clients, [], states, identities)
        self.app.processEvents()

    def test_headers_use_explicit_account_identity_and_unknown_is_not_disabled(self):
        clients = [dict(title='p2', stable_id='account:仓库', handle=22, account_nick='仓库'),
                   dict(title='p1', stable_id='account:主号', handle=11, account_nick='主号')]
        states = {'toggle_combat': {'p1': True, 'p2': False}, 'toggle_sigil': {'p1': None}}
        self.refresh(clients, states, {'p1': ('account:主号', 11), 'p2': ('account:仓库', 22)})
        self.assertEqual(self.table.horizontalHeaderItem(1).text(), '仓库\np2')
        self.assertEqual(self.table.horizontalHeaderItem(2).text(), '主号\np1')
        self.assertEqual(self.table.item(0, 1).text(), '○')
        self.assertEqual(self.table.item(0, 2).text(), '●')
        self.assertIn('已开启', self.table.item(0, 2).toolTip())
        self.assertEqual(self.table.item(1, 1).text(), '未知')
        self.assertEqual(self.table.item(1, 2).text(), '未知')

    def test_reorder_title_reuse_and_stale_snapshot_never_switch_owners(self):
        a = dict(title='p1', stable_id='account:A', handle=11, account_nick='A')
        b = dict(title='p2', stable_id='account:B', handle=22, account_nick='B')
        ids = {'p1': ('account:A', 11), 'p2': ('account:B', 22)}
        states = {'toggle_combat': {'p1': True, 'p2': False}}
        self.table.begin_session()
        self.refresh([a, b], states, ids)
        self.refresh([b, a], states, ids)
        self.assertEqual(self.table.horizontalHeaderItem(1).text(), 'B\np2')
        self.assertEqual(self.table.item(0, 2).text(), '●')
        c = dict(title='p1', stable_id='account:C', handle=33, account_nick='C')
        self.refresh([b, c], states, ids)
        self.assertEqual(self.table.horizontalHeaderItem(2).text(), 'C\np1')
        self.assertEqual(self.table.item(0, 2).text(), '未知')
        self.assertEqual(self.table.horizontalHeaderItem(3).text(), 'A\np1')
        self.assertEqual(self.table.item(0, 3).text(), '已断开')
        self.refresh([b, c], states, {'p1': ('account:C', 33), 'p2': ('account:B', 22)})
        self.assertEqual(self.table.item(0, 2).text(), '●')
        self.table.end_session()
        self.refresh([b, c], states, ids)
        self.assertEqual(self.table.columnCount(), 3)

    def test_long_headers_read_only_cells_space_distribution_and_frozen_feature_column(self):
        name = '长账号备注' * 15
        client = dict(title='p1', stable_id='account:A', handle=11, account_nick=name)
        self.refresh([client], {'toggle_combat': {'p1': True}}, {'p1': ('account:A', 11)})
        header = self.table.horizontalHeaderItem(1)
        self.assertIn(name, header.toolTip())
        self.assertGreater(self.table.columnWidth(1), 400)
        self.assertEqual(self.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assertFalse(self.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertTrue(self.table.item(0, 0).textAlignment() & Qt.AlignmentFlag.AlignLeft)
        self.assertTrue(self.table.item(0, 1).textAlignment() & Qt.AlignmentFlag.AlignHCenter)
        clients = [dict(title=f'p{i}', stable_id=f'id:{i}', handle=i) for i in range(1, 13)]
        self.refresh(clients, {}, {})
        self.assertGreater(self.table.horizontalScrollBar().maximum(), 0)
        before = self.table.frozen_column.geometry()
        self.table.horizontalScrollBar().setValue(self.table.horizontalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(self.table.frozen_column.geometry(), before)
        self.assertEqual(self.table.frozen_column.columnWidth(0), self.table.columnWidth(0))
        self.assertEqual(self.table.rowHeight(0), self.table.rowHeight(1))

    def test_ambiguous_or_missing_identity_does_not_invent_account_or_off_state(self):
        client = dict(title='p1', handle=11)
        self.refresh([client], {'toggle_combat': {'p1': False}}, {'p1': ('other', 99)})
        self.assertEqual(self.table.horizontalHeaderItem(1).text(), 'p1')
        self.assertEqual(self.table.item(0, 1).text(), '未知')

        self.refresh([client], {'toggle_combat': {'p1': 'error'}}, {'p1': ('', 11)})
        self.assertEqual(self.table.item(0, 1).text(), '未知')

    def test_feature_navigation_scrolls_vertically_without_unfreezing_name_column(self):
        self.actions = {f'action{i}': f'功能 {i}' for i in range(30)}
        clients = [dict(title=f'p{i}', stable_id=f'id:{i}', handle=i) for i in range(12)]
        self.refresh(clients, {}, {})
        self.table.horizontalScrollBar().setValue(200)
        horizontal = self.table.horizontalScrollBar().value()
        self.table.scrollTo(self.table.model().index(29, 0))
        self.assertGreater(self.table.verticalScrollBar().value(), 0)
        self.assertEqual(self.table.horizontalScrollBar().value(), horizontal)

    def test_direction_keys_keep_current_client_outside_the_frozen_feature_column(self):
        clients = [dict(title=f'p{i}', stable_id=f'id:{i}', handle=i) for i in range(1, 13)]
        self.refresh(clients, {}, {})
        self.table.setCurrentCell(0, 1)
        self.table.setFocus(Qt.FocusReason.TabFocusReason)
        for _ in range(11):
            QTest.keyClick(self.table, Qt.Key.Key_Right)
        for _ in range(11):
            QTest.keyClick(self.table, Qt.Key.Key_Left)
            index = self.table.currentIndex()
            self.assertGreater(index.column(), 0)
            self.assertGreaterEqual(self.table.visualRect(index).left(), self.table.columnWidth(0))


if __name__ == '__main__':
    unittest.main()
