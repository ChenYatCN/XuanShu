import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from src.gui.helpers import library_svg, build_shared_svgs

ROOT = Path(__file__).resolve().parents[1]


class LibraryIconsTests(unittest.TestCase):
    def test_selected_resources_render_and_recolor(self):
        tree = ast.parse((ROOT / 'XuanShu.spec').read_text(encoding='utf-8'))
        loop = next(n for n in tree.body if isinstance(n, ast.For)
                    and isinstance(n.target, ast.Name) and n.target.id == 'icon_name')
        names = ast.literal_eval(loop.iter)
        self.assertEqual(len(names), 17)
        for name in names:
            for color in ('#63cdda', '#202020'):
                svg = library_svg(name, color)
                self.assertNotIn('currentColor', svg)
                if name in ('8-界面/人物怪物列表.svg', '8-界面/界面.svg',
                            '8-界面/用户列表.svg'):
                    self.assertIn(f'fill="{color}"', svg)
                else:
                    self.assertIn('fill="none"', svg)
                    self.assertIn('stroke-width="2"', svg)
                self.assertIn(color, svg)
                renderer = QSvgRenderer(svg.encode())
                self.assertTrue(renderer.isValid(), name)
                for size in (14, 16, 24, 32):
                    image = QImage(size, size, QImage.Format.Format_ARGB32)
                    image.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    self.assertTrue(any(image.pixelColor(x, y).alpha()
                                        for x in range(size) for y in range(size)), name)
                with patch.object(sys, '_MEIPASS', str(ROOT), create=True):
                    self.assertEqual(svg, library_svg(name, color))

    def test_shared_copy_and_stop(self):
        icons = build_shared_svgs('#63cdda')
        self.assertEqual(icons['clipboard'], icons['copy_logs'])
        self.assertEqual(icons['kill'], library_svg('9-媒体/停止.svg', '#63cdda'))
        self.assertIn('viewBox="0 0 24 24"', icons['kill'])
        self.assertIn('viewBox="0 0 24 24"', icons['recent'])

    def test_selected_icons_and_fishing_visual_height(self):
        from src.gui.tab_fishing import _fish_svg, _chest_svg
        color = '#80d8e8'
        icons = build_shared_svgs(color)
        for key, path in {
            'eject': '9-媒体/断链.svg', 'hook': '9-媒体/锁链.svg',
            'entity': '8-界面/人物怪物列表.svg', 'window': '8-界面/界面.svg',
        }.items():
            self.assertEqual(icons[key], library_svg(path, color))
        self.assertEqual(_chest_svg(color), library_svg('2-物品/箱子.svg', color))
        self.assertEqual(_fish_svg(color), library_svg('2-物品/钓鱼竿.svg', color))
        for size in (16, 24, 32):
            heights = []
            for svg in (_fish_svg(color), icons['kill']):
                image = QImage(size, size, QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                QSvgRenderer(svg.encode()).render(painter)
                painter.end()
                rows = [y for y in range(size) if any(image.pixelColor(x, y).alpha() > 32
                                                     for x in range(size))]
                heights.append(max(rows) - min(rows) + 1)
            self.assertLessEqual(abs(heights[0] - heights[1]), 1)


if __name__ == '__main__':
    unittest.main()
