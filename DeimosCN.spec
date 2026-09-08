# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
import importlib
import importlib.util

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path.cwd().resolve()
VENV_SITE_PACKAGES = (
    ROOT / ".venv" / "Lib" / "site-packages"
).resolve()

if not VENV_SITE_PACKAGES.exists():
    raise FileNotFoundError(
        f"venv site-packages not found: {VENV_SITE_PACKAGES}"
    )

# 打包时明确优先使用 .venv 中已安装的最新 wizwalker / wizsprinter。
# 项目根目录仍用于加载 DeimosCN.py，但不得覆盖虚拟环境里的依赖包。
LIB_PATHS = [
    VENV_SITE_PACKAGES,
    ROOT / "src",
    ROOT,
]

# Analysis 直接使用虚拟环境的 site-packages，不再将它重复加入
# pathex。主脚本改为根目录的 DeimosCN.py，让脚本目录排在依赖包之后。
ANALYSIS_PATHS = [
    ROOT / "src",
]

for path in reversed(LIB_PATHS):
    path_str = str(path)
    if path.exists():
        while path_str in sys.path:
            sys.path.remove(path_str)
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


def require_venv_package(package_name: str) -> Path:
    package_dir = get_package_dir(package_name).resolve()
    if not package_dir.is_relative_to(VENV_SITE_PACKAGES):
        raise RuntimeError(
            f"{package_name} resolved outside .venv: {package_dir}"
        )
    print(f"[DeimosCN.spec] {package_name}: {package_dir}")
    return package_dir


wizwalker_pkg_dir = require_venv_package("wizwalker")
wizsprinter_pkg_dir = require_venv_package(
    "wizwalker.extensions.wizsprinter"
)
wizlaunch_pkg_dir = require_venv_package("wizlaunch")

# Account Steam mode, validation and per-account window profiles require the
# current native wizlaunch API. Fail early instead of producing an EXE whose
# launcher controls silently do nothing because an older global copy was found.
_wizlaunch = importlib.import_module("wizlaunch")
_required_wizlaunch_api = (
    "set_account_steam",
    "get_account_steam",
    "validate_account",
    "get_window_config",
    "set_window_config",
    "clear_window_config",
    "launch_instances",
)
_missing_wizlaunch_api = [
    name for name in _required_wizlaunch_api if not hasattr(_wizlaunch, name)
]
if _missing_wizlaunch_api:
    raise RuntimeError(
        "The .venv wizlaunch is too old; missing: "
        + ", ".join(_missing_wizlaunch_api)
    )
print(
    f"[DeimosCN.spec] wizlaunch version: "
    f"{getattr(_wizlaunch, '__version__', 'unknown')}"
)


hiddenimports = []

# 从当前虚拟环境收集，不再从 libs 收集
hiddenimports += safe_collect_submodules("wizwalker")
hiddenimports += safe_collect_submodules("wizwalker.extensions")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter")
hiddenimports += safe_collect_submodules("wizwalker.extensions.wizsprinter.combat_backends")
hiddenimports += safe_collect_submodules("lark")

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

# wizsprinter 实际包名是 wizwalker.extensions.wizsprinter；不收集不存在的
# 顶层 wizsprinter。wizwalker.__main__ 是命令行入口，本 GUI 不使用。
hiddenimports = [
    module_name
    for module_name in dict.fromkeys(hiddenimports)
    if module_name != "wizwalker.__main__"
]

datas = []

add_data_if_exists(datas, "Deimos-logo.ico", ".")
add_data_if_exists(datas, "Deimos-logo.png", ".")
add_data_if_exists(datas, "locale", "locale")
add_data_if_exists(datas, "src/data/collect_names.json", "src/data")

# Optional-at-runtime but required for this build: the launcher setting
# "verify/patch game files before launch" calls this official helper.
wizpatch_exe = ROOT / "libs" / "wizpatch" / "target" / "release" / "wizpatch.exe"
if wizpatch_exe.exists():
    datas.append((str(wizpatch_exe), "."))
else:
    raise FileNotFoundError(
        f"wizpatch helper not built: {wizpatch_exe}"
    )

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

datas += safe_collect_data_files("wizlaunch")

datas += safe_collect_data_files("wizwalker.extensions.wizsprinter", include_py_files=True)
datas += safe_collect_data_files("wizwalker.extensions.wizsprinter.combat_backends", include_py_files=True)

# The Laurenz package remains authoritative for wizsprinter as a whole, but its
# stock SprintyCombat currently skips valid priorities in DeimosCN.  Replace only
# that module with the repository's compatibility version (target selection +
# Willcast fixes), matching the pre-find hook used for the compiled PYZ module.
combat_compat = (
    ROOT
    / "libs"
    / "wizsprinter"
    / "wizwalker"
    / "extensions"
    / "wizsprinter"
    / "sprinty_combat.py"
)
if not combat_compat.exists():
    raise FileNotFoundError(
        f"wizsprinter combat compatibility module not found: {combat_compat}"
    )
datas = [
    entry
    for entry in datas
    if not (
        Path(entry[0]).name == "sprinty_combat.py"
        and Path(entry[0]).parent.name == "wizsprinter"
    )
]
datas.append(
    (
        str(combat_compat),
        "wizwalker/extensions/wizsprinter",
    )
)

# Laurenz 最新 wizsprinter 不包含项目原有的分辨率钩子。
# 只叠加这一个兼容模块，不将整套旧 wizsprinter 混入新版。
resolution_hook = (
    ROOT
    / "libs"
    / "wizsprinter"
    / "wizwalker"
    / "extensions"
    / "wizsprinter"
    / "resolution_hook.py"
)
if resolution_hook.exists():
    datas.append(
        (
            str(resolution_hook),
            "_wizsprinter_compat/wizwalker/extensions/wizsprinter",
        )
    )
else:
    raise FileNotFoundError(
        f"wizsprinter compatibility module not found: {resolution_hook}"
    )

# 不再从 libs 取 traversalData，而是从 venv 里的包目录取
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

icon_file = ROOT / "Deimos-logo.ico"
version_file = ROOT / "version_info.txt"
manifest_file = ROOT / "app.manifest"

a = Analysis(
    [str(ROOT / "DeimosCN.py")],
    pathex=[str(path) for path in ANALYSIS_PATHS if path.exists()],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[
        str(font_runtime_hook),
        str(runtime_hook),
    ],
    excludes=[],
    noarchive=False,
    optimize=2,
)

# PyInstaller can resolve Windows API-set/UCRT forwarder DLLs through the
# environment of the program that launched this BAT (for example Codex or an
# editor).  Those private runtime copies must never be shipped because they can
# override the target machine's system DLLs and make PyQt6.QtCore fail to load.
_forbidden_binary_source_markers = (
    "\\.cache\\codex-runtimes\\",
    "\\.codex\\tmp\\",
)


def _is_forbidden_build_source(entry):
    source = str(Path(entry[1]).resolve()).replace("/", "\\").casefold()
    return any(marker in source for marker in _forbidden_binary_source_markers)


for _collection_name in ("binaries", "datas"):
    _collection = getattr(a, _collection_name)
    _removed = [entry for entry in _collection if _is_forbidden_build_source(entry)]
    if _removed:
        _collection[:] = [
            entry for entry in _collection if not _is_forbidden_build_source(entry)
        ]
        print(
            f"[DeimosCN.spec] removed {len(_removed)} contaminated "
            f"{_collection_name} entries"
        )

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DeimosCN",
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
