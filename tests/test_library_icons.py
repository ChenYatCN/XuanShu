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
        self.assertEqual(len(names), 10)
        for name in names:
            for color in ('#63cdda', '#202020'):
                svg = library_svg(name, color)
                self.assertNotIn('currentColor', svg)
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


if __name__ == '__main__':
    unittest.main()
