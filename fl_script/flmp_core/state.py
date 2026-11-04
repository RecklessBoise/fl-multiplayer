"""State diffing with anti-loop protection.

The FL script reads the current values (as wire ints) into a dict keyed by
`(kind, index)` and calls `diff` on every refresh. Values applied from the
network go through `apply_remote`, so reading them back never produces an echo.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

from .messages import Change


def sort_key(key: tuple[str, int | None]) -> tuple[str, int]:
    return (key[0], -1 if key[1] is None else key[1])


class _Suppress:
    def __init__(self, tracker: StateTracker) -> None:
        self._tracker = tracker

    def __enter__(self) -> None:
        self._tracker._suppress += 1

    def __exit__(self, *exc: object) -> None:
        self._tracker._suppress -= 1


class StateTracker:
    """Remembers the last known value of each key and reports local changes.

    `tolerance` gives, per kind, how far (in wire units) FL may round a value
    we set from the network before a read-back counts as a real local move.
    """

    def __init__(self, tolerance: dict[str, int] | None = None) -> None:
        self._tolerance = dict(tolerance or {})
        self._known: dict[tuple[str, int | None], int] = {}
        self._remote: dict[tuple[str, int | None], int] = {}
        self._suppress = 0
        self._baselined = False

    def suppressing(self) -> _Suppress:
        """Context manager: values read inside it update state silently."""
        return _Suppress(self)

    def apply_remote(self, change: Change) -> None:
        """Record a value that came from the network and is being applied."""
        self._known[change.key] = change.value
        self._remote[change.key] = change.value

    def diff(self, current: dict[tuple[str, int | None], int]) -> list[Change]:
        """Return local changes since the last call.

        The first call only records a baseline. Afterwards, a key seen for the
        first time (e.g. a channel added to the project) is a change.
        """
        out = []
        baseline = not self._baselined
        self._baselined = True
        for key in sorted(current, key=sort_key):
            value = current[key]
            if self._suppress or (baseline and key not in self._known):
                self._known[key] = value
                self._remote.pop(key, None)
                continue
            if value == self._known.get(key):
                continue
            remote = self._remote.get(key)
            if remote is not None and abs(value - remote) <= self._tolerance.get(key[0], 0):
                self._known[key] = value  # FL rounding of a remote value: absorb
                continue
            self._remote.pop(key, None)
            self._known[key] = value
            out.append(Change(key[0], key[1], value))
        return out

    def snapshot(self) -> list[Change]:
        return [Change(k[0], k[1], self._known[k]) for k in sorted(self._known, key=sort_key)]

    def reset(self) -> None:
        self._known.clear()
        self._remote.clear()
        self._baselined = False
