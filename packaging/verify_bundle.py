"""Fail the build if PyInstaller selected an incompatible dependency source."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_TOC = ROOT / "build" / "DeimosCN" / "Analysis-00.toc"
OUTPUT_EXE = ROOT / "dist" / "DeimosCN.exe"
VENV_PACKAGES = (ROOT / ".venv" / "Lib" / "site-packages").resolve()
COMBAT_COMPAT = (
    ROOT
    / "libs"
    / "wizsprinter"
    / "wizwalker"
    / "extensions"
    / "wizsprinter"
    / "sprinty_combat.py"
).resolve()
FORBIDDEN_ROOT_PACKAGE = (ROOT / "wizwalker").resolve()


def _norm(value: str | Path) -> str:
    return str(Path(value).resolve()).replace("/", "\\").casefold()


def _load_entries():
    if not ANALYSIS_TOC.exists():
        raise RuntimeError(f"找不到 PyInstaller 分析文件：{ANALYSIS_TOC}")
    tree = ast.literal_eval(ANALYSIS_TOC.read_text(encoding="utf-8"))
    entries: list[tuple[str, str, str]] = []

    def visit(node):
        if not isinstance(node, (list, tuple)):
            return
        if len(node) >= 3 and all(isinstance(node[i], str) for i in range(3)):
            entries.append((node[0], node[1], node[2]))
        for item in node:
            visit(item)

    visit(tree)
    return entries


def main() -> None:
    entries = _load_entries()
    compat_source = _norm(COMBAT_COMPAT)
    venv_source = _norm(VENV_PACKAGES)
    forbidden_source = _norm(FORBIDDEN_ROOT_PACKAGE)

    combat_entries = [
        (name, source, kind)
        for name, source, kind in entries
        if name.replace("\\", "/").endswith(
            "wizwalker/extensions/wizsprinter/sprinty_combat.py"
        )
        or name == "wizwalker.extensions.wizsprinter.sprinty_combat"
    ]
    if not combat_entries:
        raise RuntimeError("成品中缺少 SprintyCombat 战斗模块。")
    wrong_combat = [source for _, source, _ in combat_entries if _norm(source) != compat_source]
    if wrong_combat:
        raise RuntimeError(
            "SprintyCombat 选错来源，已停止交付：\n  "
            + "\n  ".join(wrong_combat)
        )

    root_wizwalker = [
        source
        for _, source, _ in entries
        if _norm(source).startswith(forbidden_source + "\\")
    ]
    if root_wizwalker:
        raise RuntimeError(
            "检测到项目根目录旧版 wizwalker 混入成品：\n  "
            + "\n  ".join(sorted(set(root_wizwalker))[:10])
        )

    base_modules = [
        source
        for name, source, kind in entries
        if name == "wizwalker" and kind.startswith("PYMODULE")
    ]
    if not base_modules or any(
        not _norm(source).startswith(venv_source + "\\") for source in base_modules
    ):
        raise RuntimeError("wizwalker 基础库没有从当前 .venv 打包。")

    archive_names = {
        name.replace("\\", "/").casefold() for name, _, _ in entries
    }
    required_suffixes = (
        "wizpatch.exe",
        "traversaldata/zonemap.txt",
        "_wizsprinter_compat/wizwalker/extensions/wizsprinter/resolution_hook.py",
    )
    for suffix in required_suffixes:
        if not any(name.endswith(suffix) for name in archive_names):
            raise RuntimeError(f"成品中缺少必要文件：{suffix}")
    if not any(
        "wizlaunch/wizlaunch." in name and name.endswith(".pyd")
        for name in archive_names
    ):
        raise RuntimeError("成品中缺少 wizlaunch 原生模块。")

    if not OUTPUT_EXE.exists() or OUTPUT_EXE.stat().st_size < 1_000_000:
        raise RuntimeError(f"成品 EXE 不存在或大小异常：{OUTPUT_EXE}")

    compat_hash = hashlib.sha256(COMBAT_COMPAT.read_bytes()).hexdigest().upper()
    print("[验证通过] wizwalker：.venv 最新版")
    print(f"[验证通过] SprintyCombat：兼容版 SHA256 {compat_hash}")
    print("[验证通过] wizlaunch、wizpatch、分辨率兼容模块和导航数据完整")
    print(f"[验证通过] 成品：{OUTPUT_EXE}")


if __name__ == "__main__":
    main()
