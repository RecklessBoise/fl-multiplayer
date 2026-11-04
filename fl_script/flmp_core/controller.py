"""The FL script's logic, independent of FL Studio's modules.

`fl` is an adapter (see device_FLMultiplayer.py) with:
    is_playing() -> bool
    song_pos() -> int | None      (absolute ticks; None if the API lacks it)
    play_from(pos: int) -> None
    stop() -> None
    read_state() -> dict          ((kind, index) -> wire value, full state)
    apply(change) -> bool         (write a remote value; False if unsupported)
`send` takes one encoded SysEx frame.

Built-ins only: this module runs inside FL Studio's embedded Python.
"""

from __future__ import annotations

from .messages import NETWORK_KINDS, Change, ProtocolError, decode, encode
from .state import StateTracker

MAX_TICKS = (1 << 28) - 1

# After a remote play/stop, wait up to this many OnIdle calls for FL to report
# the new state. FL's scripts have no clock, so this counts idles (~10-20 ms each).
# If FL never gets there, resync silently: never echo a "stop" FL didn't do.
TRANSPORT_CONFIRM_IDLES = 50

# FL Studio's tempo range, in BPM * 1000.
TEMPO_MIN = 10000
TEMPO_MAX = 522000

_FAILED = object()

# How far FL may round a value we set, per kind, in wire units.
TOLERANCE = {"tempo": 1, "mix_vol": 2, "mix_pan": 2, "ch_vol": 2, "ch_pan": 2}


class Controller:
    def __init__(self, fl, send, log=None) -> None:
        self.fl = fl
        self.send = send
        self.log = log
        self.tracker = StateTracker(TOLERANCE)
        self._playing = False
        self._pending_play = None  # [expected bool, idles left] after a remote play/stop
        self.last_error = None

    # -- FL callbacks --------------------------------------------------------------

    def on_init(self) -> None:
        self._playing = bool(self._call(self.fl.is_playing, False))
        state = self._call(self.fl.read_state, None)
        if state is not None:
            self.tracker.diff(state)

    def on_idle(self) -> None:
        self._sync_transport()
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
        elif kind == "play":
            if self._call(lambda: self.fl.play_from(change.value), _FAILED) is not _FAILED:
                self._pending_play = [True, TRANSPORT_CONFIRM_IDLES]
        elif kind == "stop":
            if self._call(self.fl.stop, _FAILED) is not _FAILED:
                self._pending_play = [False, TRANSPORT_CONFIRM_IDLES]
        elif kind == "tempo" and not TEMPO_MIN <= change.value <= TEMPO_MAX:
            pass  # outside what FL accepts: ignore
        elif self.tracker.knows(change.key):
            if self._call(lambda: self.fl.apply(change), False):
                self.tracker.apply_remote(change)
        return True

    # -- internals ---------------------------------------------------------------

    def _sync_transport(self) -> None:
        playing = self._call(self.fl.is_playing, None)
        if playing is None:
            return
        playing = bool(playing)
        pending = self._pending_play
        if pending is not None:
            if playing == pending[0]:
                self._pending_play = None  # FL did what the peer asked
                self._playing = playing
                return
            pending[1] -= 1
            if pending[1] <= 0:
                self._pending_play = None  # FL never followed: resync, send nothing
                self._playing = playing
            return
        if playing == self._playing:
            return
        self._playing = playing
        if playing:
            pos = self._call(self.fl.song_pos, None)
            pos = 0 if pos is None else max(0, min(MAX_TICKS, int(pos)))
            self._emit(Change("play", None, pos))
        else:
            self._emit(Change("stop", None, 0))

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
