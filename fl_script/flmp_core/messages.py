"""SysEx message format shared by the FL script and the relay.

Frame: F0 7D <version> <kind code> [index: 2 bytes] [value: n bytes] F7
All data bytes are 7-bit. Values are wire integers (see `KINDS`); conversion
from FL floats happens in `codec`.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

from . import PROTOCOL_VERSION
from .codec import U14_MAX, pack_uint, unpack_uint

MANUFACTURER_ID = 0x7D  # non-commercial / educational use
SYSEX_START = 0xF0
SYSEX_END = 0xF7
INDEX_BYTES = 2
INDEX_MAX = U14_MAX

# name: (code, has_index, value_bytes, max_value)
KINDS = {
    "play": (0x01, False, 4, (1 << 28) - 1),  # song position, absolute ticks
    "stop": (0x02, False, 0, 0),
    "tempo": (0x03, False, 3, (1 << 21) - 1),  # BPM * 1000
    "mix_vol": (0x10, True, 2, U14_MAX),
    "mix_pan": (0x11, True, 2, U14_MAX),
    "mix_mute": (0x12, True, 1, 1),
    "mix_solo": (0x13, True, 1, 1),
    "ch_vol": (0x20, True, 2, U14_MAX),
    "ch_pan": (0x21, True, 2, U14_MAX),
    "ch_mute": (0x22, True, 1, 1),
    "pattern": (0x30, False, 2, U14_MAX),
    "snapshot_req": (0x40, False, 0, 0),
    "snapshot_begin": (0x41, False, 0, 0),
    "snapshot_end": (0x42, False, 0, 0),
}

_BY_CODE = {spec[0]: name for name, spec in KINDS.items()}


class ProtocolError(ValueError):
    """Raised for any message that does not follow the protocol."""


class Change:
    """One state change or event: `kind`, optional `index`, wire `value`."""

    __slots__ = ("index", "kind", "value")

    def __init__(self, kind: str, index: int | None, value: int) -> None:
        self.kind = kind
        self.index = index
        self.value = value

    @property
    def key(self) -> tuple[str, int | None]:
        return (self.kind, self.index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Change):
            return NotImplemented
        return (self.kind, self.index, self.value) == (other.kind, other.index, other.value)

    def __hash__(self) -> int:
        return hash((self.kind, self.index, self.value))

    def __repr__(self) -> str:
        return f"Change({self.kind!r}, {self.index!r}, {self.value!r})"


def _spec(kind: str) -> tuple[int, bool, int, int]:
    spec = KINDS.get(kind)
    if spec is None:
        raise ProtocolError(f"unknown kind {kind!r}")
    return spec


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def validate(change: Change) -> None:
    """Raise ProtocolError unless `change` can be put on the wire."""
    _code, has_index, _nbytes, max_value = _spec(change.kind)
    if has_index:
        if not _is_int(change.index) or not 0 <= change.index <= INDEX_MAX:
            raise ProtocolError(f"{change.kind} needs an index in 0..{INDEX_MAX}")
    elif change.index is not None:
        raise ProtocolError(f"{change.kind} takes no index")
    if not _is_int(change.value) or not 0 <= change.value <= max_value:
        raise ProtocolError(f"{change.kind} value must be in 0..{max_value}")


def encode(change: Change) -> bytes:
    validate(change)
    code, has_index, nbytes, _max = KINDS[change.kind]
    out = [SYSEX_START, MANUFACTURER_ID, PROTOCOL_VERSION, code]
    if has_index:
        out += pack_uint(change.index, INDEX_BYTES)  # type: ignore[arg-type]
    if nbytes:
        out += pack_uint(change.value, nbytes)
    out.append(SYSEX_END)
    return bytes(out)


def decode(raw: bytes | list[int]) -> Change:
    data = list(raw)
    if len(data) < 5 or data[0] != SYSEX_START or data[-1] != SYSEX_END:
        raise ProtocolError("not a complete SysEx frame")
    if data[1] != MANUFACTURER_ID:
        raise ProtocolError("foreign manufacturer id")
    if data[2] != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {data[2]}")
    name = _BY_CODE.get(data[3])
    if name is None:
        raise ProtocolError(f"unknown kind code {data[3]}")
    _code, has_index, nbytes, _max = KINDS[name]
    body = data[4:-1]
    expected = (INDEX_BYTES if has_index else 0) + nbytes
    if len(body) != expected:
        raise ProtocolError(f"{name} payload must be {expected} bytes")
    try:
        index = unpack_uint(body[:INDEX_BYTES]) if has_index else None
        value = unpack_uint(body[expected - nbytes :]) if nbytes else 0
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc
    change = Change(name, index, value)
    validate(change)
    return change
