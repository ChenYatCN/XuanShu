"""Compatibility for inline short UTF-16 strings in game text controls."""
from wizwalker import Primitive


async def read_control_text(window):
    kind = await window.maybe_read_type_name()
    # ControlFreeChat shares the +712 text layout. At +736 we would read
    # its inline capacity (7) as a pointer, followed by unrelated fields.
    offset = 712 if kind in ("ControlText", "ControlList", "ControlFreeChat") else 736
    address = await window.read_base_address() + offset
    length = await window.read_typed(address + 16, Primitive.int64)
    capacity = await window.read_typed(address + 24, Primitive.int64)
    details = f"{kind or 'unknown'} text@{address:#x}, length={length}, capacity={capacity}"
    # Reject an invalid layout, including an invalid empty draft. Never turn
    # an unreadable control into empty text and permit overwriting its draft.
    if capacity < 7 or length < 0 or length > capacity:
        raise RuntimeError(f"控件文本结构无效：{details}")
    if capacity == 7:
        string_address = address
    else:
        string_address = await window.read_typed(address, Primitive.int64)
        # Windows reserves the low 64 KiB; a value such as 7 is not a pointer.
        if not 0x10000 <= string_address <= 0x7FFFFFFFFFFF or string_address % 2:
            raise RuntimeError(f"控件文本指针无效：{details}, pointer={string_address}")
    if length == 0:
        return ""
    # MSVC stores up to seven UTF-16 code units inside the string object.
    # Use the same verified header for heap text too: maybe_text may use the
    # old 584 offset in the local dependency and silently change layouts.
    return (await window.read_bytes(string_address, length * 2)).decode("utf-16-le")
