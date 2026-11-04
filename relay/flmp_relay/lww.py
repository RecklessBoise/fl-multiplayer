"""Last-writer-wins filter for the relay.

The server sends every change to every member, the sender included, in
sequence order. While one of our own writes for a key is still on its way
back, any foreign change for that key we receive has a lower sequence number
than ours, so ours wins and the foreign one must not be applied locally. The
other members receive our write after theirs and converge on it.

An echo can be lost (the server drops a rate-limited or invalid message), so
each in-flight write expires after `ttl` seconds instead of blocking forever.
"""

from __future__ import annotations

from collections import deque

Key = tuple[str, int | None]


class OwnWrites:
    def __init__(self, ttl: float = 2.0) -> None:
        self._ttl = ttl
        self._in_flight: dict[Key, deque[float]] = {}

    def sent(self, key: Key, now: float) -> None:
        self._in_flight.setdefault(key, deque()).append(now)

    def echoed(self, key: Key) -> None:
        times = self._in_flight.get(key)
        if times:
            times.popleft()
            if not times:
                del self._in_flight[key]

    def blocks(self, key: Key, now: float) -> bool:
        times = self._in_flight.get(key)
        if not times:
            return False
        while times and now - times[0] > self._ttl:
            times.popleft()
        if not times:
            del self._in_flight[key]
            return False
        return True

    def reset(self) -> None:
        self._in_flight.clear()
