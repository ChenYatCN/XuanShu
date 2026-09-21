import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QColor, QFont, QFontDatabase
from PyQt6.QtCore import QRectF
from PyQt6.QtSvg import QSvgRenderer
from src.gui.helpers import library_svg

app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
icons = [('钓鱼竿', '2-物品/钓鱼竿.svg'), ('停止（未改）', '9-媒体/停止.svg'),
         ('宝箱', '2-物品/箱子.svg'), ('人物／怪物列表', '8-界面/人物怪物列表.svg'),
         ('UI 树', '8-界面/界面.svg'), ('断开注入', '9-媒体/断链.svg'),
         ('连接注入', '9-媒体/锁链.svg')]
image = QImage(840, 210, QImage.Format.Format_ARGB32)
image.fill(QColor('#181824'))
painter = QPainter(image)
painter.setPen(QColor('#80d8e8'))
painter.setFont(QFont('Microsoft YaHei', 9))
for i, (label, name) in enumerate(icons):
    painter.drawText(QRectF(i * 120, 8, 120, 24), 0x84, label)
    for y, size in ((48, 16), (88, 24), (136, 48)):
        QSvgRenderer(library_svg(name, '#80d8e8').encode()).render(
            painter, QRectF(i * 120 + (120-size)/2, y, size, size))
painter.end()
image.save('artifacts/selected-icons-preview.png')
