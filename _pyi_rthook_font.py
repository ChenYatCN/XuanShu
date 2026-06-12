# -*- coding: utf-8 -*-

import ctypes
import sys
from pathlib import Path


def get_resource_path(relative_path: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return Path.cwd() / relative_path


def load_private_font(font_path: Path) -> bool:
    if not font_path.exists():
        return False

    # FR_PRIVATE：只给当前程序使用，不安装到系统
    FR_PRIVATE = 0x10

    result = ctypes.windll.gdi32.AddFontResourceExW(str(font_path), FR_PRIVATE, 0)

    return result > 0


font_path = get_resource_path("assets/fonts/DreamHanSansCN-W21.ttf")
load_private_font(font_path)
