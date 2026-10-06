"""Exercise the existing writer with simulated memory only; never open a process."""
import asyncio
import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wizwalker import Primitive
from wizwalker.memory.memory_objects.window import Window


def load_writer(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    window_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Window')
    method = next(node for node in window_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'write_maybe_text')
    namespace = {'Primitive': Primitive}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['write_maybe_text']


async def check():
    reports = []
    root = Path(__file__).resolve().parents[1]
    actual_path = Path(inspect.getfile(Window)).resolve()
    sources = (('actual_import', actual_path, 584),
               ('venv_copy', root / '.venv/Lib/site-packages/wizwalker/memory/memory_objects/window.py', 712))
    for source, path, writer_offset in sources:
      writer = load_writer(path)
      for case, original, capacity, replacement in (
            ('heap_capacity', 'Hello player', 15, 'Hello player | 译文：你好，玩家'),
            ('inline_storage', 'Test', 7, 'Test 译文')):
        base = 0x20000
        # Model each writer's assumed layout to isolate buffer safety;
        # separately test its compatibility with the current reader layout.
        header = base + writer_offset
        pointer = 0x40000 if capacity > 7 else int.from_bytes(original.encode('utf-16-le')[:8], 'little')
        values = {header: pointer, header + 16: len(original), header + 24: capacity}
        window = SimpleNamespace(
            maybe_read_type_name=AsyncMock(return_value='ControlText'),
            read_base_address=AsyncMock(return_value=base),
            read_typed=AsyncMock(side_effect=lambda at, primitive: values[at]),
            write_bytes=AsyncMock(), write_typed=AsyncMock())
        await writer(window, replacement)
        target, payload = window.write_bytes.await_args.args
        capacity_read = any(c.args[0] == header + 24 for c in window.read_typed.await_args_list)
        reports.append({'source': source, 'path': str(path), 'case': case, 'capacity_checked': capacity_read,
                        'inline_target_correct': target == header if capacity == 7 else None,
                        'write_bytes': len(payload), 'buffer_bytes': (capacity + 1) * 2,
                        'exceeds_buffer': len(payload) > (capacity + 1) * 2})
        if case == 'heap_capacity':
            assert not capacity_read and len(payload) > (capacity + 1) * 2
        else:
            assert target != header
      for kind in ('ControlText', 'ControlFreeChat'):
        header = 0x20000 + 712
        values = {header: 0x40000, header + 16: 12, header + 24: 15}
        window = SimpleNamespace(
            maybe_read_type_name=AsyncMock(return_value=kind),
            read_base_address=AsyncMock(return_value=0x20000),
            read_typed=AsyncMock(side_effect=lambda at, primitive: values[at]),
            write_bytes=AsyncMock(), write_typed=AsyncMock())
        try:
            await writer(window, 'Hello player')
        except KeyError as exc:
            reports.append({'source': source, 'case': 'reader_layout', 'kind': kind,
                            'compatible': False, 'unexpected_read_address': hex(exc.args[0]),
                            'writes_performed': window.write_bytes.await_count})
            assert window.write_bytes.await_count == 0
        else:
            reports.append({'source': source, 'case': 'reader_layout', 'kind': kind, 'compatible': True})
    print(json.dumps({'simulated_memory_only': True, 'unsafe_writer_reproduced': reports}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(check())
