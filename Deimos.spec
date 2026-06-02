# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path.cwd()

# Let PyInstaller and collect_submodules see the local workspace libraries.
LIB_PATHS = [
    ROOT,
    ROOT / "src",
    ROOT / "libs" / "wizwalker",
    ROOT / "libs" / "wizsprinter",
    ROOT / "libs" / "wizlaunch",
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


hiddenimports = []

# Core local packages.
hiddenimports += safe_collect_submodules("wizwalker")
hiddenimports += safe_collect_submodules("wizsprinter")
hiddenimports += safe_collect_submodules("wizlaunch")
hiddenimports += safe_collect_submodules("lark")

# wizsprinter may be exposed either as a direct package or under wizwalker.extensions.
hiddenimports += safe_collect_submodules("wizwalker.extensions")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter.combat_backends")
hiddenimports += safe_collect_submodules("wizsprinter.combat_backends")

# Explicit imports used by Deimos.
hiddenimports += [
    "wizwalker",
    "wizwalker.extensions",
    "wizsprinter",
    "wizsprinter.wiz_navigator",
    "wizsprinter.sprinty_combat",
    "wizsprinter.combat_backends",
    "wizlaunch",
]

datas = []

add_data_if_exists(datas, "Deimos-logo.ico", ".")
add_data_if_exists(datas, "Deimos-logo.png", ".")
add_data_if_exists(datas, "locale", "locale")

# Collect package data where available.
datas += safe_collect_data_files("wizwalker")
datas += safe_collect_data_files("wizsprinter")
datas += safe_collect_data_files("wizlaunch")

datas += safe_collect_data_files("wizwalker.extensions")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends")
datas += safe_collect_data_files("wizsprinter.combat_backends")

# Also collect .py files for packages that may become on-disk namespace packages.
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends", include_py_files=True)
datas += safe_collect_data_files("wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizsprinter.combat_backends", include_py_files=True)

# Package wizsprinter traversalData from your actual local path:
# libs\wizsprinter\wizwalker\extensions\wizsprinter\traversalData
traversal_src = (
    ROOT
    / "libs"
    / "wizsprinter"
    / "wizwalker"
    / "extensions"
    / "wizsprinter"
    / "traversalData"
)

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
    raise FileNotFoundError(f"traversalData not found: {traversal_src}")

runtime_hook = ROOT / "_pyi_rthook_wizsprinter.py"

if not runtime_hook.exists():
    raise FileNotFoundError(f"runtime hook not found: {runtime_hook}")

icon_file = ROOT / "Deimos-logo.ico"
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

    # Keep True while testing so errors show in the console.
    # After confirming the exe works, you can change this to False.
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
