"""Offscreen UI verification; does not connect to or control the game."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QWidget, QCheckBox, QLineEdit
from PyQt6.QtGui import QFontDatabase
from src.gui.helpers import build_shared_svgs, titlebar_svg_icon
from src.gui.actions import ActionRegistry
from src.gui.tab_hotkeys import build_hotkeys_tab
from src.gui.tab_actions import build_bot_tab
from src.gui.tab_fishing import build_fishing_tab
from src.gui.theme import compute_styles
from src.lang import load_lang
from src.settings_manager import DEFAULT_SETTINGS, DEFAULT_HOTKEYS

app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
theme = dict(bg_color='#181824', alt_bg='#222233', text_color='#eeeeee',
             stroke_color='#80d8e8', button_color='#303044', titlebar_bg='#181824')
styles = compute_styles(theme, 'Microsoft YaHei', 10)
app.setStyleSheet(styles['app_style'])
window = QWidget()
widgets = []

def context():
    settings = Mock()
    settings.get_hotkeys.return_value = dict(DEFAULT_HOTKEYS)
    settings.get_settings.return_value = dict(DEFAULT_SETTINGS)
    settings.get_setting.side_effect = DEFAULT_SETTINGS.get
    ctx = SimpleNamespace(window=window, settings=settings, tl=load_lang('zh'), send_queue=Mock(),
        stroke_color=theme['stroke_color'], text_color=theme['text_color'],
        bg_color=theme['bg_color'], alt_bg=theme['alt_bg'],
        icon_btn_style=styles['icon_btn_style'], btn_style=styles['btn_style'],
        svgs=build_shared_svgs(theme['stroke_color']),
        titlebar_svg_icon=lambda svg, size=24: titlebar_svg_icon(window, svg, size),
        tracked_svg_labels=[], tracked_toggle_btns=[], widget_tags={}, exports={},
        repo_base='', wiki_base='', tool_name='XuanShu', tool_version='preview')
    ctx.registry = ActionRegistry(settings, ctx.tl, ctx.send_queue,
                                 ctx.btn_style, ctx.icon_btn_style, ctx.titlebar_svg_icon)
    ctx.registry._ctx = ctx
    return ctx

for name, builder in [('hotkeys', build_hotkeys_tab), ('bot', build_bot_tab), ('fishing', build_fishing_tab)]:
    ctx = context()
    with patch('src.gui.icon_manager.get_current_icon_path', return_value=str(Path('XuanShu-logo.png').resolve())):
        tab = builder(ctx)
    widgets.append(tab)
    ctx.exports[name]['set_available_clients'](['p1', 'p2', 'p3'])
    tab.resize(1000, 410)
    if name == 'hotkeys':
        status = ctx.widget_tags['QuestPartyRuntimeStatus']
        status.setText('p1 → p2｜正在执行自动任务')
        status.show()
    tab.show()
    app.processEvents()
    assert not any('获得坐骑' in c.text() for c in tab.findChildren(QCheckBox))
    assert not any('坐骑' in c.placeholderText() for c in tab.findChildren(QLineEdit))
    tab.grab().save(f'artifacts/two-fixes-{name}.png')
    if name == 'bot':
        ctx.registry.callbacks['toggle_bot']()
        assert set(ctx.send_queue.put.call_args.args[0].data) == {'text', 'clients'}
    tab.close()
print('Three source UI previews verified; no retired mount controls or script payload keys.')
