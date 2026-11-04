"""Last-writer-wins filter for the relay.

The server sends every change to every member, the sender included, in
sequence order. While one of our own writes for a key is still on its way
back, any foreign change for that key we receive has a lower sequence number
than ours, so ours wins and the foreign one must not be applied locally. The
other members receive our write after theirs and converge on it.
"""

from __future__ import annotations

Key = tuple[str, int | None]


class OwnWrites:
    def __init__(self) -> None:
        self._in_flight: dict[Key, int] = {}

    def sent(self, key: Key) -> None:
        self._in_flight[key] = self._in_flight.get(key, 0) + 1

    def echoed(self, key: Key) -> None:
        left = self._in_flight.get(key, 0) - 1
        if left > 0:
            self._in_flight[key] = left
        else:
            self._in_flight.pop(key, None)

    def blocks(self, key: Key) -> bool:
        return key in self._in_flight

    def reset(self) -> None:
        self._in_flight.clear()
