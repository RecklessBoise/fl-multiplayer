"""Per-key rate limiting for continuous controls (faders, knobs, tempo).

Leading edge goes out immediately; during a burst only the latest value is
kept and sent once the interval has elapsed, so the final value is never lost.
Time is passed in by the caller (seconds, monotonic).

When a remote value is applied for a key, call `discard(key)` so a stale local
value still waiting here cannot overwrite it.
"""

from __future__ import annotations

from .messages import Change
from .state import sort_key

THROTTLED_KINDS = frozenset({"mix_vol", "mix_pan", "ch_vol", "ch_pan", "tempo"})


class Throttle:
    def __init__(
        self, max_per_second: float = 30.0, kinds: frozenset[str] = THROTTLED_KINDS
    ) -> None:
        if not max_per_second > 0:
            raise ValueError("max_per_second must be positive")
        self._interval = 1.0 / max_per_second
        self._kinds = kinds
        # key -> (time sent, value sent)
        self._last: dict[tuple[str, int | None], tuple[float, int]] = {}
        self._pending: dict[tuple[str, int | None], Change] = {}

    def _due(self, key: tuple[str, int | None], now: float) -> bool:
        last = self._last.get(key)
        if last is None or now < last[0]:  # never sent, or clock went backwards
            return True
        return now >= last[0] + self._interval

    def offer(self, change: Change, now: float) -> list[Change]:
        if now != now:
            raise ValueError("now must be a number")
        if change.kind not in self._kinds:
            return [change]
        key = change.key
        if self._due(key, now):
            self._last[key] = (now, change.value)
            self._pending.pop(key, None)
            return [change]
        if self._last[key][1] == change.value:
            self._pending.pop(key, None)  # back to what peers already have
        else:
            self._pending[key] = change
        return []

    def flush(self, now: float) -> list[Change]:
        if now != now:
            raise ValueError("now must be a number")
        out = []
        for key in sorted([k for k in self._pending if self._due(k, now)], key=sort_key):
            change = self._pending.pop(key)
            self._last[key] = (now, change.value)
            out.append(change)
        return out

    def next_deadline(self) -> float | None:
        if not self._pending:
            return None
        return min(self._last[k][0] for k in self._pending) + self._interval

    def discard(self, key: tuple[str, int | None]) -> None:
        self._pending.pop(key, None)

    def clear(self) -> None:
        self._pending.clear()
        self._last.clear()
