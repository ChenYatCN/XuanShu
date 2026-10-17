# Modified 2026-10-17: single-file sync for the #77 dispatch callback patch.
"""Synchronise the repository's patched wizwalker mouse handler into the venv.

Only ``mouse_handler.py`` is copied. Every other wizwalker module still comes
from ``.venv/Lib/site-packages/wizwalker``; the repository root package is not
put on ``sys.path`` and does not shadow the installed distribution.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV_PACKAGES = (ROOT / ".venv" / "Lib" / "site-packages").resolve()
SOURCE = ROOT / "wizwalker" / "mouse_handler.py"
TARGET = VENV_PACKAGES / "wizwalker" / "mouse_handler.py"


def fail(message: str) -> None:
    print(f"[同步失败] {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _find_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MouseHandler"
    ]
    if len(matches) != 1:
        fail(f"MouseHandler 类必须唯一，实际 {len(matches)} 个")
    return matches[0]


def _find_method(
    class_node: ast.ClassDef, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        fail(f"MouseHandler.{name} 必须唯一，实际 {len(matches)} 个")
    return matches[0]


def _ancestors(target: ast.AST, node: ast.AST, chain: tuple[ast.AST, ...] = ()):
    if node is target:
        return chain
    for child in ast.iter_child_nodes(node):
        found = _ancestors(target, child, chain + (node,))
        if found is not None:
            return found
    return None


def _is_negative_100(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and node.operand.value == 100
    )


def _reset_line(node: ast.AST) -> int | None:
    for item in ast.walk(node):
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "set_mouse_position"
            and len(item.args) == 2
            and _is_negative_100(item.args[0])
            and _is_negative_100(item.args[1])
        ):
            return item.lineno
    return None


def verify(path: Path) -> None:
    """Validate the dispatch contract without importing the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        fail(f"无法解析 {path}: {exc}")

    mouse = _find_class(tree)
    click = _find_method(mouse, "click")
    click_window = _find_method(mouse, "click_window")

    for method in (click, click_window):
        positional = list(method.args.posonlyargs) + list(method.args.args)
        if any(argument.arg == "on_dispatched" for argument in positional):
            fail(f"{method.name} 的 on_dispatched 必须是 keyword-only")
        if not any(
            argument.arg == "on_dispatched" for argument in method.args.kwonlyargs
        ):
            fail(f"{method.name} 缺少 keyword-only on_dispatched")

    forwards = [
        node
        for node in ast.walk(click_window)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "click"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and any(
            keyword.arg == "on_dispatched"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "on_dispatched"
            for keyword in node.keywords
        )
    ]
    if len(forwards) != 1:
        fail("click_window 必须恰好一次以 on_dispatched=on_dispatched 调用 self.click")

    dispatched = [
        node
        for node in ast.walk(click)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "on_dispatched"
        and not node.args
        and not node.keywords
    ]
    if len(dispatched) != 1:
        fail(f"click 必须恰好调用 on_dispatched() 一次，实际 {len(dispatched)} 次")

    sends = [
        node
        for node in ast.walk(click)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "send_method"
    ]
    if len(sends) != 2:
        fail(f"click 必须恰好发送 mouse down/up 两次，实际 {len(sends)} 次")
    if dispatched[0].lineno <= max(send.lineno for send in sends):
        fail("on_dispatched 必须在 mouse down/up 之后调用")

    chain = _ancestors(dispatched[0], click)
    if chain is None:
        fail("无法定位 click 中的 on_dispatched 调用")
    guarded = [
        item
        for item in chain
        if isinstance(item, ast.Try) and item.finalbody
    ]
    if not guarded:
        fail("on_dispatched 必须位于带 finally 的 try 中")
    reset_lines = [
        line
        for line in (
            _reset_line(ast.Module(body=item.finalbody, type_ignores=[]))
            for item in guarded
        )
        if line
    ]
    if not reset_lines:
        fail("on_dispatched 所在的 try/finally 必须在 finally 中重置鼠标")
    if min(reset_lines) <= dispatched[0].lineno:
        fail("鼠标重置必须在 on_dispatched 之后执行")


def main() -> int:
    if not SOURCE.is_file():
        fail(f"仓库 source 不存在：{SOURCE}")
    if not TARGET.is_file():
        fail(f"venv target 不存在，请先安装 wizwalker：{TARGET}")

    try:
        TARGET.resolve().relative_to(VENV_PACKAGES)
    except ValueError:
        fail(f"target 不在当前 .venv：{TARGET}")
    if SOURCE.resolve() == TARGET.resolve():
        fail("source 与 target 不能是同一个文件")

    verify(SOURCE)
    source_hash = sha256(SOURCE)
    original = TARGET.read_bytes()
    try:
        shutil.copyfile(SOURCE, TARGET)
        verify(TARGET)
        target_hash = sha256(TARGET)
        if target_hash != source_hash:
            fail(f"SHA256 不一致：source={source_hash} target={target_hash}")
    except BaseException:
        TARGET.write_bytes(original)
        print("[同步失败] 已恢复同步前的 target", file=sys.stderr)
        raise

    print("[同步完成] 只同步了 wizwalker/mouse_handler.py")
    print(f"source: {SOURCE}")
    print(f"source SHA256: {source_hash}")
    print(f"target: {TARGET}")
    print(f"target SHA256: {target_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
