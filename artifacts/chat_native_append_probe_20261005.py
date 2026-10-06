"""One authorized local display test. No chat sends, injection or allocation."""
import asyncio
import ctypes
import json
import struct
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymem.memory
from artifacts.chat_native_readonly_probe_20261005 import find_chat_controls
from src.window_text import read_control_text


SUFFIX = '<color;FFFF00>[测试] 译文显示测试</color>\n'


def snapshot(read, header):
    raw = read(header, 32)
    pointer, length, capacity = (struct.unpack_from('<Q', raw, offset)[0]
                                 for offset in (0, 16, 24))
    if not 0 < length <= capacity <= 20000 or capacity <= 7:
        raise RuntimeError('Test requires a bounded, nonempty heap string')
    if not 0x10000 <= pointer <= 0x7FFFFFFFFFFF or pointer % 2:
        raise RuntimeError('Invalid heap pointer')
    data = read(pointer, (length + 1) * 2)
    if data[-2:] != b'\x00\x00':
        raise RuntimeError('Original UTF16 string is not null terminated')
    data[:-2].decode('utf-16-le')
    return raw, pointer, length, capacity, data


def append_bounded(read, write, header, expected_text, *, expected_header=None, expected_tail=None):
    raw, pointer, length, capacity, original = snapshot(read, header)
    if expected_header is not None and raw != expected_header:
        raise RuntimeError('Header changed after backup; aborting')
    if original[:-2].decode('utf-16-le') != expected_text:
        raise RuntimeError('History changed before protected write; aborting')
    if '[测试] 译文显示测试' in expected_text:
        raise RuntimeError('Test already present')
    suffix_text = ('' if expected_text.endswith('\n') else '\n') + SUFFIX
    suffix = suffix_text.encode('utf-16-le')
    new_length = length + len(suffix) // 2
    if new_length > capacity:
        raise RuntimeError('Insufficient existing capacity; no allocation permitted')
    tail_address = pointer + length * 2
    tail_before = read(tail_address, len(suffix) + 2)
    if expected_tail is not None and tail_before != expected_tail:
        raise RuntimeError('Tail changed after backup; aborting')
    expected = original[:-2] + suffix + b'\x00\x00'
    try:
        # Caller must hold the process paused across snapshot/write/verification.
        write(tail_address, suffix + b'\x00\x00')
        write(header + 16, struct.pack('<Q', new_length))
        if read(header, 32) != raw[:16] + struct.pack('<Q', new_length) + raw[24:]:
            raise RuntimeError('Header verification failed')
        if read(pointer, len(expected)) != expected:
            raise RuntimeError('Text verification failed')
    except Exception:
        # Same protected section: restore only bytes this test touched.
        write(tail_address, tail_before)
        write(header + 16, struct.pack('<Q', length))
        raise
    return {'header': header, 'pointer': pointer, 'old_length': length,
            'new_length': new_length, 'capacity': capacity,
            'original_header_hex': raw.hex(), 'original_tail_hex': tail_before.hex(),
            'expected_text': expected[:-2].decode('utf-16-le')}


@contextmanager
def paused_process(handle):
    ntdll = ctypes.WinDLL('ntdll')
    suspend = ntdll.NtSuspendProcess
    resume = ntdll.NtResumeProcess
    for api in (suspend, resume):
        api.argtypes = [ctypes.c_void_p]
        api.restype = ctypes.c_long
    status = suspend(handle)
    if status < 0:
        raise RuntimeError(f'Cannot establish protected write: NTSTATUS {status & 0xFFFFFFFF:#x}')
    try:
        yield
    finally:
        status = resume(handle)
        if status < 0:
            raise RuntimeError(f'PROCESS RESUME FAILED: NTSTATUS {status & 0xFFFFFFFF:#x}')


async def inspect_target(handle, pid):
    controls, visited = await find_chat_controls(handle, pid)
    candidates = []
    for name, node in controls:
        if await node.maybe_read_type_name() != 'ControlText':
            continue
        text = await read_control_text(node)
        # Match the user's existing message, not the texture/debug log.
        if '[你] 我 ' in text and '[WARN]' not in text and '[ERRO]' not in text:
            candidates.append((await node.read_base_address() + 712, text))
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one matching player history, found {len(candidates)}')
    return candidates[0], visited


def main():
    pid = int(sys.argv[1])
    output = Path(sys.argv[2]).resolve()
    backup_output = output.with_suffix('.backup.json')
    if (output.parent != Path(__file__).resolve().parent
            or not output.name.startswith('chat_native_append_result_')
            or output.suffix != '.json' or output.exists() or backup_output.exists()):
        raise ValueError('Output must be a new append result JSON in artifacts')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = None
    result = {'pid': pid, 'chat_sent': False, 'injected': False, 'allocated': False,
              'append_verified': False}
    try:
        # QUERY_INFORMATION|VM_READ|VM_WRITE|VM_OPERATION|SUSPEND_RESUME.
        # No CREATE_THREAD or TERMINATE permission.
        handle = kernel.OpenProcess(0xC38, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        (header, original_text), visited = asyncio.run(
            asyncio.wait_for(inspect_target(handle, pid), timeout=20))
        read = lambda address, size: pymem.memory.read_bytes(handle, address, size)
        write = lambda address, data: pymem.memory.write_bytes(handle, address, data, len(data))
        raw, pointer, length, capacity, original = snapshot(read, header)
        suffix_text = ('' if original_text.endswith('\n') else '\n') + SUFFIX
        suffix_bytes = suffix_text.encode('utf-16-le')
        if length + len(suffix_bytes) // 2 > capacity:
            raise RuntimeError('Insufficient existing capacity')
        tail_before = read(pointer + length * 2, len(suffix_bytes) + 2)
        # Persist recovery data before any mutation, outside the brief pause.
        prepared_backup = {'pid': pid, 'header': header, 'pointer': pointer,
                           'old_length': length, 'capacity': capacity,
                           'original_header_hex': raw.hex(), 'original_tail_hex': tail_before.hex(),
                           'original_text': original[:-2].decode('utf-16-le'),
                           'expected_text': original[:-2].decode('utf-16-le') + suffix_text}
        with backup_output.open('x', encoding='utf-8') as backup_file:
            json.dump(prepared_backup, backup_file, ensure_ascii=False, indent=2)
        started = time.perf_counter()
        with paused_process(handle):
            backup = append_bounded(read, write, header, original_text,
                                    expected_header=raw, expected_tail=tail_before)
        result.update(backup)
        result.update(append_verified=True, ui_nodes_visited=visited,
                      protected_section_seconds=time.perf_counter() - started)
        samples = []
        for delay in (0, 1, 3):
            if delay:
                time.sleep(delay)
            try:
                _, _, _, _, data = snapshot(read, header)
                text = data[:-2].decode('utf-16-le')
                samples.append({'after_delay_seconds': delay,
                                'test_present': '[测试] 译文显示测试' in text,
                                'exact_test_text': text == backup['expected_text']})
            except Exception as exc:
                samples.append({'after_delay_seconds': delay, 'read_error': str(exc)})
        result['post_resume_samples'] = samples
        result['visual_display_verified'] = False
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if handle:
            kernel.CloseHandle(handle)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    with output.open('x', encoding='utf-8') as result_file:
        result_file.write(serialized)
    print(serialized)
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    sys.exit(main())
