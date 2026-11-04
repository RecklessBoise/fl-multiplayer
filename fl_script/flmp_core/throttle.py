"""Per-key rate limiting for continuous controls (faders, knobs, tempo).

Leading edge goes out immediately; during a burst only the latest value is
kept and sent once the interval has elapsed, so the final value is never lost.
Time is passed in by the caller (seconds, monotonic).
"""

from __future__ import annotations

from .messages import Change
from .state import sort_key

THROTTLED_KINDS = frozenset({"mix_vol", "mix_pan", "ch_vol", "ch_pan", "tempo"})


class Throttle:
    def __init__(
        self, max_per_second: float = 30.0, kinds: frozenset[str] = THROTTLED_KINDS
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._kinds = kinds
        self._last_sent: dict[tuple[str, int | None], float] = {}
        self._pending: dict[tuple[str, int | None], Change] = {}

    def offer(self, change: Change, now: float) -> list[Change]:
        if change.kind not in self._kinds:
            return [change]
        key = change.key
        last = self._last_sent.get(key)
        if last is None or now - last >= self._interval:
            self._last_sent[key] = now
            self._pending.pop(key, None)
            return [change]
        self._pending[key] = change
        return []

    def flush(self, now: float) -> list[Change]:
        due = [k for k in self._pending if now - self._last_sent[k] >= self._interval]
        out = []
        for key in sorted(due, key=sort_key):
            out.append(self._pending.pop(key))
            self._last_sent[key] = now
        return out

    def next_deadline(self) -> float | None:
        if not self._pending:
            return None
        return min(self._last_sent[k] for k in self._pending) + self._interval
