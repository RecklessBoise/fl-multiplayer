"""Piano roll patterns sent between users (v0.2).

A pattern is `ppq` (the sender's ticks per quarter note) and a list of notes
`[pitch, time, length, velocity, pan, release]`, as FL's piano roll scripting
exposes them (`flpianoroll.Note`). Shared by the relay and the server, so both
apply the same limits.

Built-ins only, like the rest of flmp_core.
"""

from __future__ import annotations

from .messages import ProtocolError

MAX_NOTES = 4000
PPQ_MIN = 24
PPQ_MAX = 1920
PITCH_MAX = 131  # flpianoroll.Note.number: 0..131
TICKS_MAX = (1 << 31) - 1
FIELDS = 6


def _int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _unit(x: object) -> float:
    """A float in 0..1 (ints accepted), rounded for a compact wire format."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ProtocolError("expected a number")
    x = float(x)
    if x != x or not 0.0 <= x <= 1.0:  # x != x: NaN
        raise ProtocolError("value outside 0..1")
    return round(x, 4)


def _note(raw: object) -> tuple:
    if not isinstance(raw, (list, tuple)) or len(raw) != FIELDS:
        raise ProtocolError("a note is [pitch, time, length, velocity, pan, release]")
    pitch, time, length = raw[0], raw[1], raw[2]
    if not _int(pitch) or not 0 <= pitch <= PITCH_MAX:
        raise ProtocolError("bad pitch")
    for ticks in (time, length):
        if not _int(ticks) or not 0 <= ticks <= TICKS_MAX:
            raise ProtocolError("bad time or length")
    return (pitch, time, length, _unit(raw[3]), _unit(raw[4]), _unit(raw[5]))


def check_pattern(ppq: object, notes: object) -> tuple:
    """Validate and normalise a pattern; raise ProtocolError if anything is off."""
    if not _int(ppq) or not PPQ_MIN <= ppq <= PPQ_MAX:
        raise ProtocolError("bad ppq")
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ProtocolError(f"notes must be a list of at most {MAX_NOTES} notes")
    return ppq, [_note(n) for n in notes]
