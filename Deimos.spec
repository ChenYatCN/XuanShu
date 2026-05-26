# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = Path.cwd()


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


hiddenimports = []

hiddenimports += safe_collect_submodules("wizwalker")
hiddenimports += safe_collect_submodules("wizwalker.extensions")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter.combat_backends")

hiddenimports += safe_collect_submodules("wizsprinter")
hiddenimports += safe_collect_submodules("wizsprinter.combat_backends")
hiddenimports += safe_collect_submodules("wizlaunch")
hiddenimports += safe_collect_submodules("lark")

hiddenimports += [
    "wizwalker.extensions",
    "wizwalker.extensions.wizsprinter",
    "wizwalker.extensions.wizsprinter.wiz_navigator",
    "wizwalker.extensions.wizsprinter.sprinty_combat",
    "wizwalker.extensions.wizsprinter.combat_backends",
    "wizsprinter",
    "wizsprinter.wiz_navigator",
    "wizsprinter.sprinty_combat",
    "wizsprinter.combat_backends",
    "wizlaunch",
]

datas = [
    ("Deimos-logo.ico", "."),
    ("Deimos-logo.png", "."),
    ("locale", "locale"),
]
traversal_src = ROOT / "libs" / "wizsprinter" / "wizwalker" / "extensions" / "wizsprinter" / "traversalData"

if traversal_src.exists():
    for file in traversal_src.rglob("*"):
        if file.is_file():
            relative_parent = file.relative_to(traversal_src).parent
            target_dir = Path("wizwalker/extensions/wizsprinter/traversalData") / relative_parent
            datas.append((str(file), str(target_dir).replace("\\", "/")))
else:
    raise FileNotFoundError(f"traversalData not found: {traversal_src}")


datas += safe_collect_data_files("wizwalker")
datas += safe_collect_data_files("wizwalker.extensions")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter")
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends")

datas += safe_collect_data_files("wizsprinter")
datas += safe_collect_data_files("wizsprinter.combat_backends")

datas += safe_collect_data_files("wizwalker.extensions.wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends", include_py_files=True)
datas += safe_collect_data_files("wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizsprinter.combat_backends", include_py_files=True)


a = Analysis(
    ["Deimos.py"],
    pathex=[
        str(ROOT),
        str(ROOT / "src"),
        str(ROOT / "libs" / "wizwalker"),
        str(ROOT / "libs" / "wizsprinter"),
        str(ROOT / "libs" / "wizlaunch"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        str(ROOT / "_pyi_rthook_wizsprinter.py"),
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="Deimos-logo.ico",
    version="version_info.txt",
    manifest="app.manifest",
)