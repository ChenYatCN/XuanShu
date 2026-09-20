"""Read-only source/library audit; writes previews and inventory beside this file."""
import ast
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import Counter
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QColor, QFont, QFontDatabase
from PyQt6.QtCore import QRectF
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
LIB = ROOT / 'assets/icon/game-icon-pack-v1.4-svg-zh'
app = QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
inventory = []
for path in sorted(LIB.rglob('*.svg')):
    text = path.read_text(encoding='utf-8-sig')
    root = ET.fromstring(text)
    inventory.append({'file': path.relative_to(LIB).as_posix(), 'viewBox': root.get('viewBox'),
                      'fill': root.get('fill'), 'valid': QSvgRenderer(text.encode()).isValid(),
                      'elements': len(list(root.iter())), 'currentColor': 'currentColor' in text})
(OUT / 'library_inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')

def sheet(name, entries):
    cols, cw, ch = 6, 190, 115
    img = QImage(cols*cw, ((len(entries)+cols-1)//cols)*ch, QImage.Format.Format_ARGB32)
    img.fill(QColor('#17232b'))
    p = QPainter(img)
    p.setFont(QFont('Microsoft YaHei', 9))
    for i, (label, svg) in enumerate(entries):
        x, y = (i%cols)*cw, (i//cols)*ch
        r = QSvgRenderer(svg.replace('currentColor', '#55dce0').encode())
        for dx, size in ((16, 16), (48, 24), (90, 48)):
            r.render(p, QRectF(x+dx, y+12, size, size))
        p.setPen(QColor('#e3eff4'))
        p.drawText(QRectF(x+5, y+66, cw-10, 44), 0x1000, label)
    p.end()
    img.save(str(OUT / (name+'.png')))

entries = []
for path in sorted((ROOT/'src/gui').glob('*.py')):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.JoinedStr):
                    try:
                        svg = eval(compile(ast.Expression(value), str(path), 'eval'), {'sc':'#55dce0'})
                    except (NameError, TypeError):
                        continue
                    if svg.startswith('<svg'):
                        entries.append((f'{path.stem}\n{key.value}', svg))
for i in range(0, len(entries), 48):
    sheet(f'current-{i//48+1}', entries[i:i+48])

names = ['地图','书','剑','旗','旗-02','旗-03','门','门-02','开门','钓鱼竿','鱼钩','鱼','鱼-02',
         '用户','用户组','用户列表','朋友','还原','还原-02','还原-03','还原-04','刷新',
         '导入','导出','上传','下载','连接','链接','链接-02','播放','停止','暂停','消息','消息-02',
         '时间','相机','摄像头','纸','电源开关','复制','剪贴板','橡皮','可见','切换','切换-02',
         '菜单','菜单-02','菜单-03','菜单-04','菜单-05','界面','书签','信息','问号',
         '多张卡牌','击中','卡牌','角色','指南针','尺子','尺子-02','尺子-03']
candidates=[]
for name in names:
    matches=list((LIB/'无间距').rglob(name+'.svg'))
    for path in matches:
        candidates.append((path.relative_to(LIB).as_posix(),path.read_text(encoding='utf-8-sig')))
for i in range(0,len(candidates),30):
    sheet(f'candidates-{i//30+1}',candidates[i:i+30])
print(json.dumps({'total':len(inventory),'valid':sum(r['valid'] for r in inventory),
    'currentColor':sum(r['currentColor'] for r in inventory),'categories':dict(Counter(r['file'].split('/')[1] for r in inventory)),
    'current_icons':len(entries),'candidates':len(candidates)},ensure_ascii=False))
