"""Read native chat using an existing, source-validated hook; never inject/write."""
import asyncio
import ctypes
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymem.memory
from wizwalker import Primitive
from wizwalker.memory.handler import HookHandler
from wizwalker.memory.hooks import RootWindowHook
from wizwalker.memory.memory_objects.window import DynamicWindow
from src.window_text import read_control_text


class ReadOnlyHandler(HookHandler):
    async def write_bytes(self, *args, **kwargs):
        raise RuntimeError('Read-only probe forbids writes')

    write_typed = write_bytes
    allocate = write_bytes
    free = write_bytes
    start_thread = write_bytes


async def find_chat_controls(process_handle, pid):
    process = SimpleNamespace(
        process_handle=process_handle, process_id=pid,
        read_bytes=lambda address, size: pymem.memory.read_bytes(process_handle, address, size))
    handler = ReadOnlyHandler(process, None)
    candidates = await handler.pattern_scan(
        RootWindowHook.pattern, module='WizardGraphicalClient.exe', return_multiple=True)
    exports = set()
    for address in candidates:
        jump = await handler.read_bytes(address, 7)
        if jump[0] != 0xE9 or jump[5:] != b'\x90\x90':
            continue
        destination = address + 5 + struct.unpack('<i', jump[1:5])[0]
        code = await handler.read_bytes(destination, 26)
        # Exact RootWindowHook.bytecode_generator implementation, not a guess.
        if (code[:10] == b'\x50\x49\x8b\x85\xd8\x00\x00\x00\x48\xa3'
                and code[18:] == b'\x58\x49\x8b\x8d\xd8\x00\x00\x00'):
            exports.add(struct.unpack('<Q', code[10:18])[0])
    if len(exports) != 1:
        raise RuntimeError(f'Cannot identify one existing root hook: {len(exports)} validated exports')
    export = exports.pop()
    root_address = await handler.read_typed(export, Primitive.uint64)
    if not 0x10000 <= root_address <= 0x7FFFFFFFFFFF:
        raise RuntimeError('Existing root hook is not ready')
    root = DynamicWindow(handler, root_address)
    queue = [root]
    seen = set()
    controls = []
    while queue and len(seen) < 2500:
        node = queue.pop()
        address = await node.read_base_address()
        if address in seen:
            continue
        seen.add(address)
        name = await node.name()
        # Native append concerns history only; don't inspect unrelated drafts.
        if name == 'chatLog':
            controls.append((name, node))
        queue.extend(await node.children())
    if queue:
        raise RuntimeError('UI traversal bound reached; inspection incomplete')
    return controls, len(seen)


async def inspect_chat(process_handle, pid):
    controls, visited = await find_chat_controls(process_handle, pid)
    samples = []
    for sample in range(2):
        rows = []
        for name, node in controls:
            kind = await node.maybe_read_type_name()
            offset = 712 if kind in ('ControlText', 'ControlList', 'ControlFreeChat') else 736
            header = await node.read_base_address() + offset
            length = await node.read_typed(header + 16, Primitive.int64)
            capacity = await node.read_typed(header + 24, Primitive.int64)
            text = await read_control_text(node)
            rows.append({'name': name, 'kind': kind, 'address': hex(header),
                         'length': length, 'capacity': capacity,
                         'spare_utf16_units': capacity - length,
                         'inline': capacity == 7,
                         'text_preview': text[:160] if name == 'chatLog' else '<draft not disclosed>',
                         'text_matches_length': len(text.encode('utf-16-le')) // 2 == length})
        samples.append(rows)
        if sample == 0:
            await asyncio.sleep(1)
    return {'read_only': True, 'pid': pid, 'existing_hook_reused': True,
            'ui_nodes_visited': visited, 'samples': samples,
            'write_or_refresh_test_performed': False}


def main():
    pid = int(sys.argv[1])
    output = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    if output and (output.parent != Path(__file__).resolve().parent
                   or not output.name.startswith('chat_native_readonly_result_')
                   or output.suffix != '.json' or output.exists()):
        raise ValueError('Diagnostic output must be a new result JSON in artifacts')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # QUERY_INFORMATION | VM_READ only: no VM_WRITE/VM_OPERATION/CREATE_THREAD.
    handle = None
    try:
        handle = kernel.OpenProcess(0x410, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = asyncio.run(asyncio.wait_for(inspect_chat(handle, pid), timeout=20))
    except Exception as exc:
        result = {'read_only': True, 'pid': pid, 'error_type': type(exc).__name__,
                  'error': str(exc), 'write_or_refresh_test_performed': False}
    finally:
        if handle:
            kernel.CloseHandle(handle)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        with output.open('x', encoding='utf-8') as result_file:
            result_file.write(serialized)
    print(serialized)
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    sys.exit(main())
