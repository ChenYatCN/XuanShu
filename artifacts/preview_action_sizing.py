import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtGui import QFontDatabase
from src.gui.helpers import configure_action_button, configure_titlebar_button, titlebar_svg_icon, build_shared_svgs
from src.gui.tab_fishing import _fish_svg
from src.gui.theme import compute_styles

app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
theme = dict(bg_color='#181824', alt_bg='#222233', text_color='#eeeeee',
             stroke_color='#80d8e8', button_color='#303044', titlebar_bg='#181824')
styles = compute_styles(theme, 'Microsoft YaHei', 10)
app.setStyleSheet(styles['app_style'])
window = QWidget()
layout = QVBoxLayout(window)
svgs = build_shared_svgs(theme['stroke_color'])
svgs['fish'] = _fish_svg(theme['stroke_color'])
for label, keys in [('战斗', ['recent', 'import', 'export', 'apply', 'reset']),
                    ('脚本', ['recent', 'import', 'export', 'play', 'kill']),
                    ('飞行路径', ['recent', 'import', 'export', 'play']),
                    ('钓鱼', ['fish', 'kill']), ('生产线', ['play', 'kill'])]:
    row = QHBoxLayout()
    row.setSpacing(6)
    name = QLabel(label)
    name.setFixedWidth(80)
    row.addWidget(name)
    for key in keys:
        button = QPushButton()
        configure_action_button(button)
        button.setStyleSheet(styles['icon_btn_style'])
        button.setIcon(titlebar_svg_icon(window, svgs[key], 32))
        row.addWidget(button)
    row.addStretch()
    layout.addLayout(row)
for label in ('主窗口标题栏', '生产线标题栏'):
    row = QHBoxLayout()
    row.setSpacing(0)
    row.addWidget(QLabel(label))
    row.addStretch()
    for close in (False, True):
        button = QPushButton()
        configure_titlebar_button(button, lambda svg, size: titlebar_svg_icon(window, svg, size),
                                  theme['stroke_color'], close)
        row.addWidget(button)
    layout.addLayout(row)
window.resize(440, 350)
window.show()
app.processEvents()
window.grab().save('artifacts/action-sizing-preview.png')
