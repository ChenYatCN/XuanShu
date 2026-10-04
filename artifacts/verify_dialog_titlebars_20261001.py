import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import queue
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QPushButton
from src.gui.helpers import titlebar_svg_icon
from src.gui.settings_dialog import show_settings_dialog
from src.gui.popups import show_ui_tree_popup, show_entity_list_popup
from src.gui.commands import GUICommandType
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_THEME

app = QApplication.instance() or QApplication([])
font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
theme = dict(DEFAULT_THEME)
app.setStyleSheet(f'QWidget {{ background: {theme["bg_color"]}; color: {theme["text_color"]}; }}')
parent = QWidget()
parent.setWindowIcon(QIcon('XuanShu-logo.ico'))
settings = SimpleNamespace(get_settings=lambda: dict(DEFAULT_SETTINGS), get_theme=lambda: dict(theme))
translations = {'settings_title': '设置 - 玄枢 · XuanShu', 'ui_tree': 'UI 树 - 玄枢 · XuanShu',
                'entity_list': '实体列表 - 玄枢 · XuanShu', 'settings_cancel': '取消', 'settings_save': '保存',
                'settings_general': '通用', 'settings_theme': '主题', 'search': '搜索', 'close': '关闭'}
ctx = SimpleNamespace(window=parent, settings=settings, tl=lambda key: translations.get(key, key),
    bg_color=theme['bg_color'], text_color=theme['text_color'], alt_bg=theme['alt_bg'],
    btn_color_hex=theme['button_color'], btn_style='', icon_btn_style='', svgs={'reset':'', 'import':''},
    titlebar_svg_icon=lambda svg, size: titlebar_svg_icon(parent, svg, size),
    exports={}, send_queue=queue.Queue(), gui_font='Microsoft YaHei', gui_font_size=10)

def inspect(dialog, name):
    dialog.show()
    app.processEvents()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    bar = dialog.findChild(QWidget, 'dialogTitleBar')
    close = dialog.findChild(QPushButton, 'dialogTitleClose')
    assert bar is not None and bar.height() == 32 and close is not None
    old = dialog.pos()
    bar.mousePressEvent(SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
        globalPosition=lambda: QPointF(old.x()+12, old.y()+12)))
    bar.mouseMoveEvent(SimpleNamespace(buttons=lambda: Qt.MouseButton.LeftButton,
        globalPosition=lambda: QPointF(old.x()+32, old.y()+27)))
    bar.mouseReleaseEvent(None)
    assert dialog.pos() == old + QPointF(20, 15).toPoint()
    bar.grab().save(f'artifacts/{name}_TITLEBAR_20261001.png')
    close.click()
    app.processEvents()
    assert not dialog.isVisible()

def inspect_settings(dialog):
    inspect(dialog, 'SETTINGS')
    assert dialog.result() == QDialog.DialogCode.Rejected
    return 0

with patch.object(QDialog, 'exec', inspect_settings):
    show_settings_dialog(ctx)
entity = show_entity_list_popup(parent, ctx.send_queue, {}, None, None, None, tl=ctx.tl, ctx=ctx)
inspect(entity, 'ENTITY_LIST')
commands = []
while not ctx.send_queue.empty():
    commands.append(ctx.send_queue.get().com_type)
assert GUICommandType.StopEntityStream in commands
show_ui_tree_popup(parent, ctx.send_queue, '[Root] RootWindow\n-[WorldView] WorldWindow', {},
                   lambda cb: QPushButton(), tl=ctx.tl, ctx=ctx)
tree = next(d for d in parent.findChildren(QDialog) if d.windowTitle() == translations['ui_tree'])
inspect(tree, 'UI_TREE')
print('Three themed titlebars: render, drag, close and entity-stream cleanup OK')
