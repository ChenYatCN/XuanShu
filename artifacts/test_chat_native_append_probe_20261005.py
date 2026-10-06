import struct
import unittest

from artifacts.chat_native_append_probe_20261005 import SUFFIX, append_bounded


class BoundedAppendTests(unittest.TestCase):
    def memory(self, text='original\n', capacity=100):
        header, pointer = 0x20000, 0x40000
        encoded = text.encode('utf-16-le')
        length = len(encoded) // 2
        memory = {header: bytearray(struct.pack('<QQQQ', pointer, 0, length, capacity)),
                  pointer: bytearray(encoded + b'\x00\x00' + b'\xcc' * ((capacity - length) * 2))}
        before = {at: bytes(data) for at, data in memory.items()}
        writes = []

        def locate(at, size):
            for base, data in memory.items():
                if base <= at and at + size <= base + len(data):
                    return data, at - base
            raise AssertionError('Out-of-bounds memory access')

        def read(at, size):
            data, index = locate(at, size)
            return bytes(data[index:index + size])

        def write(at, payload):
            data, index = locate(at, len(payload))
            data[index:index + len(payload)] = payload
            writes.append((at, payload))

        return header, pointer, memory, before, writes, read, write

    def test_append_touches_only_tail_and_int64_length(self):
        h, p, mem, before, writes, read, write = self.memory()
        result = append_bounded(read, write, h, 'original\n')
        self.assertEqual([at for at, _ in writes], [p + 18, h + 16])
        self.assertEqual(mem[h][:16], before[h][:16])
        self.assertEqual(mem[h][24:], before[h][24:])
        new_length = result['new_length']
        self.assertEqual(read(p, new_length * 2 + 2), ('original\n' + SUFFIX).encode('utf-16-le') + b'\0\0')
        self.assertEqual(mem[p][new_length * 2 + 2:], before[p][new_length * 2 + 2:])

    def test_capacity_exact_fit_and_overflow_rejection(self):
        needed = len(('original\n' + SUFFIX).encode('utf-16-le')) // 2
        for capacity in (needed, needed - 1):
            h, p, mem, before, writes, read, write = self.memory(capacity=capacity)
            if capacity == needed:
                append_bounded(read, write, h, 'original\n')
            else:
                with self.assertRaisesRegex(RuntimeError, 'Insufficient'):
                    append_bounded(read, write, h, 'original\n')
                self.assertEqual(writes, [])
                self.assertEqual(bytes(mem[p]), before[p])

    def test_changed_history_aborts_without_writes(self):
        h, p, mem, before, writes, read, write = self.memory()
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            append_bounded(read, write, h, 'different\n')
        self.assertEqual(writes, [])

    def test_changed_backed_up_header_or_tail_aborts_without_writes(self):
        for field in ('header', 'tail'):
            h, p, mem, before, writes, read, write = self.memory()
            arguments = {'expected_header': b'wrong'} if field == 'header' else {'expected_tail': b'wrong'}
            with self.assertRaisesRegex(RuntimeError, 'changed after backup'):
                append_bounded(read, write, h, 'original\n', **arguments)
            self.assertEqual(writes, [])

    def test_length_write_failure_restores_touched_bytes(self):
        h, p, mem, before, writes, read, write = self.memory()
        failed = False

        def failing_write(at, payload):
            nonlocal failed
            if at == h + 16 and not failed:
                failed = True
                raise OSError('simulated write failure')
            write(at, payload)

        with self.assertRaises(OSError):
            append_bounded(read, failing_write, h, 'original\n')
        self.assertEqual({at: bytes(data) for at, data in mem.items()}, before)

    def test_existing_marker_rejected(self):
        text = 'existing [测试] 译文显示测试\n'
        h, p, mem, before, writes, read, write = self.memory(text=text)
        with self.assertRaises(RuntimeError):
            append_bounded(read, write, h, text)
        self.assertEqual(writes, [])

    def test_history_without_trailing_newline_gets_separator(self):
        text = '<color;FFFFFF>原消息</color>'
        h, p, mem, before, writes, read, write = self.memory(text=text)
        result = append_bounded(read, write, h, text)
        self.assertEqual(result['expected_text'], text + '\n' + SUFFIX)
        self.assertEqual(read(p, result['new_length'] * 2 + 2),
                         (text + '\n' + SUFFIX).encode('utf-16-le') + b'\0\0')

    def test_invalid_heap_or_inline_header_rejected(self):
        for offset, value in ((0, 7), (24, 7), (24, 20001), (16, 101)):
            h, p, mem, before, writes, read, write = self.memory()
            struct.pack_into('<Q', mem[h], offset, value)
            with self.assertRaises(RuntimeError):
                append_bounded(read, write, h, 'original\n')
            self.assertEqual(writes, [])


if __name__ == '__main__':
    unittest.main()
