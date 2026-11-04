"""State diffing with anti-loop protection.

The FL script reads the full current state (as wire ints) into a dict keyed by
`(kind, index)` and calls `diff` on every refresh. A value received from the
network is first written to FL, then recorded with `apply_remote`, so reading
it back never produces an echo.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

from .messages import Change, validate

# How many diffs a remote value waits for FL to report it before a different
# reading is trusted as a real local move.
REMOTE_CONFIRM_TICKS = 3


def sort_key(key: tuple[str, int | None]) -> tuple[str, int]:
    return (key[0], -1 if key[1] is None else key[1])


class _Suppress:
    def __init__(self, tracker: StateTracker) -> None:
        self._tracker = tracker

    def __enter__(self) -> None:
        self._tracker._suppress += 1

    def __exit__(self, *exc: object) -> None:
        self._tracker._suppress = max(0, self._tracker._suppress - 1)


class StateTracker:
    """Remembers the last known value of each key and reports local changes.

    `tolerance` gives, per kind, how far (in wire units) FL may round a value
    we set from the network before a read-back counts as a real local move.
    """

    def __init__(self, tolerance: dict[str, int] | None = None) -> None:
        self._tolerance = dict(tolerance or {})
        self._known: dict[tuple[str, int | None], int] = {}
        # key -> [remote value, diffs left before it expires]
        self._remote: dict[tuple[str, int | None], list[int]] = {}
        self._suppress = 0
        self._baselined = False

    def suppressing(self) -> _Suppress:
        """Context manager: values read inside it update state silently."""
        return _Suppress(self)

    def apply_remote(self, change: Change) -> bool:
        """Record a network value that was just written to FL.

        Returns False (and records nothing) for a key that does not exist in
        the local project, so peers cannot grow our state with bogus indexes.
        Raises ProtocolError for an invalid change.
        """
        validate(change)
        key = change.key
        if key not in self._known:
            return False
        self._known[key] = change.value
        self._remote[key] = [change.value, REMOTE_CONFIRM_TICKS]
        return True

    def diff(self, current: dict[tuple[str, int | None], int]) -> list[Change]:
        """Return local changes since the last call.

        `current` must be the full state. The first call only records a
        baseline. Afterwards a key seen for the first time (e.g. a channel added
        to the project) is a change, and keys no longer present are forgotten.
        """
        out = []
        baseline = not self._baselined
        self._baselined = True
        for gone in [k for k in self._known if k not in current]:
            del self._known[gone]
            self._remote.pop(gone, None)
        for key in sorted(current, key=sort_key):
            value = current[key]
            if self._suppress or baseline:
                self._known[key] = value
                self._remote.pop(key, None)
                continue
            pending = self._remote.get(key)
            if pending is not None:
                if abs(value - pending[0]) <= self._tolerance.get(key[0], 0):
                    self._known[key] = value  # FL reported the remote value
                    del self._remote[key]
                    continue
                pending[1] -= 1
                if pending[1] > 0:
                    continue  # FL may not have applied it yet
                del self._remote[key]
            if value == self._known.get(key):
                continue
            self._known[key] = value
            out.append(Change(key[0], key[1], value))
        return out

    def snapshot(self) -> list[Change]:
        return [Change(k[0], k[1], self._known[k]) for k in sorted(self._known, key=sort_key)]

    def reset(self) -> None:
        self._known.clear()
        self._remote.clear()
        self._baselined = False
