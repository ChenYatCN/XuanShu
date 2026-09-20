import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QPlainTextEdit, QCheckBox, QLabel
from PyQt6.QtGui import QIcon
from src.gui.ibao_dialog import build_ibao_dialog, LocationTable


class IbaoUITests(unittest.TestCase):
    def test_editor_and_live_results(self):
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        ctx = SimpleNamespace(window=parent, stroke_color='#80d8e8',
            text_color='#eeeeee', bg_color='#181824', alt_bg='#222233',
            titlebar_svg_icon=lambda *args: QIcon(), icon_btn_style='',
            svgs={'play':'', 'kill':''}, exports={}, send_queue=Mock())
        dialog = build_ibao_dialog(ctx)
        self.assertIsNone(dialog.parentWidget())
        self.assertTrue(any('最小化生产线' in b.toolTip() for b in dialog.findChildren(QPushButton)))
        api = ctx.exports['ibao']
        api['set_available_clients'](['p1'])
        button = next(b for b in dialog.findChildren(QPushButton)
                      if b.accessibleName() == '编辑采集地点')
        button.click()
        edits = dialog.findChildren(QPlainTextEdit)
        editor = dialog.findChild(LocationTable)
        self.assertTrue(editor.isVisible())
        editor.setPlainText('The Commons|Location|')
        next(c for c in dialog.findChildren(QCheckBox) if c.text() == 'p1').setChecked(True)
        next(b for b in dialog.findChildren(QPushButton)
             if b.accessibleName() == '启动所选').click()
        self.assertEqual(ctx.send_queue.put.call_args_list[-2].args[0].data['settings']['locations'],
                         'The Commons|Location|')
        api['set_running_groups']([{'clients':['p1'], 'collected':12, 'state':'正在重启'}])
        report = next(e for e in edits if e.isReadOnly())
        self.assertIn('12', report.toPlainText())
        self.assertIn('正在重启', report.toPlainText())
        check = next(c for c in dialog.findChildren(QCheckBox) if c.text() == 'p1')
        self.assertTrue(check.isChecked())
        check.setChecked(False)
        self.assertEqual(ctx.send_queue.put.call_args.args[0].data, {'clients': ['p1']})
        api['set_running_groups']([{'clients':['p1'], 'collected':12, 'state':'正在停止'}])
        self.assertTrue(any(label.text() == '正在停止：p1' for label in dialog.findChildren(QLabel)))
        self.assertTrue(check.isChecked())
        self.assertFalse(any(label.text() == '未运行' for label in dialog.findChildren(QLabel)))
        api['set_running_groups']([{'clients': ['p1'], 'collected': 12, 'state': '已归档', 'archived': True}])
        self.assertEqual(report.toPlainText(), '')
        dialog.close()
        parent.close()
        dialog.deleteLater()
        app.processEvents()
