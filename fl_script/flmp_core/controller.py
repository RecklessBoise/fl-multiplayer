"""The FL script's logic, independent of FL Studio's modules.

Each user's transport (play, stop, position) stays local: only the tempo,
mixer and channel rack are synced. Play/stop frames from older peers are ignored.

`fl` is an adapter (see device_FLMultiplayer.py) with:
    read_state() -> dict          ((kind, index) -> wire value, full state)
    apply(change) -> bool         (write a remote value; False if unsupported)
`send` takes one encoded SysEx frame.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

from .messages import NETWORK_KINDS, Change, ProtocolError, decode, encode
from .state import StateTracker

# FL Studio's tempo range, in BPM * 1000.
TEMPO_MIN = 10000
TEMPO_MAX = 522000

# How far FL may round a value we set, per kind, in wire units.
TOLERANCE = {"tempo": 1, "mix_vol": 2, "mix_pan": 2, "ch_vol": 2, "ch_pan": 2}


class Controller:
    def __init__(self, fl, send, log=None) -> None:
        self.fl = fl
        self.send = send
        self.log = log
        self.tracker = StateTracker(TOLERANCE)
        self.last_error = None

    # -- FL callbacks --------------------------------------------------------------

    def on_init(self) -> None:
        state = self._call(self.fl.read_state, None)
        if state is not None:
            self.tracker.diff(state)

    def on_idle(self) -> None:
        self._sync_state()

    def on_sysex(self, data) -> bool:
        """Handle one SysEx frame; True if it was ours."""
        try:
            change = decode(data)
        except ProtocolError:
            return False
        kind = change.kind
        if kind == "snapshot_req":
            self._sync_state()  # send what changed, then dump the current state
            self._emit(Change("snapshot_begin", None, 0))
            for c in self.tracker.snapshot():
                if c.kind in NETWORK_KINDS:
                    self._emit(c)
            self._emit(Change("snapshot_end", None, 0))
        elif kind in ("snapshot_begin", "snapshot_end"):
            pass  # values in between are applied like any remote change
        elif kind in ("play", "stop"):
            pass  # each user's transport is independent
        elif kind == "tempo" and not TEMPO_MIN <= change.value <= TEMPO_MAX:
            pass  # outside what FL accepts: ignore
        elif self.tracker.knows(change.key):
            if self._call(lambda: self.fl.apply(change), False):
                self.tracker.apply_remote(change)
        return True

    # -- internals ---------------------------------------------------------------

    def _sync_state(self) -> None:
        state = self._call(self.fl.read_state, None)
        if state is None:
            return
        for change in self.tracker.diff(state):
            self._emit(change)

    def _emit(self, change: Change) -> None:
        try:
            self.send(encode(change))
        except Exception as exc:  # never let an error escape into FL
            self._error(exc)

    def _call(self, fn, default):
        try:
            return fn()
        except Exception as exc:
            self._error(exc)
            return default

    def _error(self, exc: Exception) -> None:
        text = repr(exc)
        if text == self.last_error:
            return  # same failure every idle: log it once
        self.last_error = text
        if self.log is not None:
            self.log("FL Multiplayer : erreur " + text)
