# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
import importlib.util

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path.cwd()

# 只保留项目目录，不再加入 libs
LIB_PATHS = [
    ROOT,
    ROOT / "src",
]

for path in reversed(LIB_PATHS):
    path_str = str(path)
    if path.exists() and path_str not in sys.path:
        sys.path.insert(0, path_str)


def safe_collect_submodules(package_name: str):
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


def safe_collect_data_files(package_name: str, include_py_files: bool = False):
    try:
        return collect_data_files(package_name, include_py_files=include_py_files)
    except Exception:
        return []


def add_data_if_exists(datas_list, source, target):
    source_path = ROOT / source
    if source_path.exists():
        datas_list.append((str(source_path), target))


def get_package_dir(package_name: str) -> Path:
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError(f"Package not found in current venv: {package_name}")

    if spec.submodule_search_locations:
        return Path(list(spec.submodule_search_locations)[0])

    return Path(spec.origin).parent


hiddenimports = []

# 从当前虚拟环境收集，不再从 libs 收集
hiddenimports += safe_collect_submodules("wizwalker")
hiddenimports += safe_collect_submodules("wizwalker.extensions")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter.combat_backends")
hiddenimports += safe_collect_submodules("lark")

# 如果你的 venv 里确实有独立的 wizsprinter / wizlaunch，也可以保留
hiddenimports += safe_collect_submodules("wizsprinter")
hiddenimports += safe_collect_submodules("wizsprinter.combat_backends")
hiddenimports += safe_collect_submodules("wizlaunch")

hiddenimports += [
    "wizwalker",
    "wizwalker.extensions",
    "wizwalker.extensions.wizsprinter",
    "wizwalker.extensions.wizsprinter.sprinty_combat",
    "wizwalker.extensions.wizsprinter.wiz_navigator",
    "wizwalker.extensions.wizsprinter.combat_backends",
    "lark",
    "wizlaunch",
]

datas = []

add_data_if_exists(datas, "Winter.ico", ".")
add_data_if_exists(datas, "Deimos-logo.png", ".")
add_data_if_exists(datas, "locale", "locale")

font_file = ROOT / "assets" / "fonts" / "DreamHanSansCN-W21.ttf"

if font_file.exists():
    datas.append((str(font_file), "assets/fonts"))
else:
    raise FileNotFoundError(f"font file not found: {font_file}")

# 从当前虚拟环境收集 package data
datas += safe_collect_data_files("wizwalker")
datas += safe_collect_data_files("wizwalker.extensions")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends")

datas += safe_collect_data_files("wizsprinter")
datas += safe_collect_data_files("wizsprinter.combat_backends")
datas += safe_collect_data_files("wizlaunch")

datas += safe_collect_data_files("wizwalker.extensions.wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends", include_py_files=True)

# 不再从 libs 取 traversalData，而是从 venv 里的包目录取
wizsprinter_pkg_dir = get_package_dir("wizwalker.extensions.wizsprinter")
traversal_src = wizsprinter_pkg_dir / "traversalData"

if traversal_src.exists():
    for file in traversal_src.rglob("*"):
        if file.is_file():
            relative_parent = file.relative_to(traversal_src).parent
            target_dir = (
                Path("wizwalker/extensions/wizsprinter/traversalData")
                / relative_parent
            )
            datas.append((str(file), str(target_dir).replace("\\", "/")))
else:
    raise FileNotFoundError(f"traversalData not found in venv package: {traversal_src}")

runtime_hook = ROOT / "_pyi_rthook_wizsprinter.py"
font_runtime_hook = ROOT / "_pyi_rthook_font.py"

if not runtime_hook.exists():
    raise FileNotFoundError(f"runtime hook not found: {runtime_hook}")

if not font_runtime_hook.exists():
    raise FileNotFoundError(f"font runtime hook not found: {font_runtime_hook}")

icon_file = ROOT / "Winter.ico"
version_file = ROOT / "version_info.txt"
manifest_file = ROOT / "app.manifest"

a = Analysis(
    ["Deimos.py"],
    pathex=[str(path) for path in LIB_PATHS if path.exists()],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        str(font_runtime_hook),
        str(runtime_hook),
    ],
    excludes=[],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Deimos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,

    # 测试阶段建议 True，方便看报错；确认没问题后再改 False
    console=False,

    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
    version=str(version_file) if version_file.exists() else None,
    manifest=str(manifest_file) if manifest_file.exists() else None,
)