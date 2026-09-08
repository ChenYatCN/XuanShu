"""Compatibility for inline short UTF-16 strings in game text controls."""
from wizwalker import Primitive


async def read_control_text(window):
    offset = 712 if await window.maybe_read_type_name() in ("ControlText", "ControlList") else 736
    address = await window.read_base_address() + offset
    length = await window.read_typed(address + 16, Primitive.int64)
    capacity = await window.read_typed(address + 24, Primitive.int64)
    if length == 0:
        return ""
    # MSVC stores up to seven UTF-16 code units inside the string object.
    # maybe_text assumes the first eight bytes always contain a pointer.
    if capacity == 7 and 0 < length <= capacity:
        return (await window.read_bytes(address, length * 2)).decode("utf-16-le")
    return await window.maybe_text()
