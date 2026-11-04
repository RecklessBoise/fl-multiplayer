"""7-bit packing and value scaling for SysEx payloads.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

U14_MAX = 16383


def pack_uint(value: int, nbytes: int) -> list[int]:
    """Pack a non-negative int into `nbytes` big-endian 7-bit groups."""
    if value < 0 or value >= 1 << (7 * nbytes):
        raise ValueError(f"value {value} does not fit in {nbytes} 7-bit bytes")
    return [(value >> (7 * (nbytes - 1 - i))) & 0x7F for i in range(nbytes)]


def unpack_uint(data: list[int] | bytes) -> int:
    """Inverse of `pack_uint`. Rejects any byte that is not 7-bit."""
    value = 0
    for b in data:
        if b < 0 or b > 0x7F:
            raise ValueError(f"byte {b} is not 7-bit")
        value = (value << 7) | b
    return value


def unit_to_u14(x: float) -> int:
    """Map 0.0..1.0 (clamped) to 0..16383."""
    if x <= 0.0:
        return 0
    if x >= 1.0:
        return U14_MAX
    return round(x * U14_MAX)


def u14_to_unit(v: int) -> float:
    return v / U14_MAX


def pan_to_u14(p: float) -> int:
    """Map pan -1.0 (left)..1.0 (right) to 0..16383."""
    return unit_to_u14((p + 1.0) / 2.0)


def u14_to_pan(v: int) -> float:
    return u14_to_unit(v) * 2.0 - 1.0
