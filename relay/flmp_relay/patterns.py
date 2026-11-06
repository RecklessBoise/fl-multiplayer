"""Pattern exchange with FL's piano roll scripts, through two small JSON files (v0.2).

The "FL Multiplayer - Envoyer" piano roll script writes `outbox.json`; the relay
notices the new file and sends the pattern to the room. A pattern received from
the room is written to `inbox.json`, which "FL Multiplayer - Recevoir" reads.
Piano roll scripts can't talk to the network, but they can read and write files
(checked in FL Studio 2026, API 45).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from flmp_core.messages import ProtocolError
from flmp_core.pattern import check_pattern

log = logging.getLogger("flmp.relay")

OUTBOX = "outbox.json"
INBOX = "inbox.json"
Pattern = tuple[int, list[tuple]]


def notes_label(count: int) -> str:
    return f"{count} note" if count == 1 else f"{count} notes"


class Mailbox:
    MAX_BYTES = 512 * 1024  # 4000 notes fit easily
    INBOX_MIN_INTERVAL = 1.0  # a peer can't keep overwriting the inbox
    NAME_MAX = 40
    SETTLE = 0.2  # the FL script writes in place: let it finish before reading

    def __init__(
        self,
        folder: Path,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.folder = Path(folder)
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_inbox: float | None = None
        self._held: tuple[str, int, list[tuple]] | None = None
        self.folder.mkdir(parents=True, exist_ok=True)
        self._outbox = self.folder / OUTBOX
        self._inbox = self.folder / INBOX
        self._seen = self._stamp()  # a pattern left from an earlier session is not resent

    def _stamp(self) -> tuple[int, int, int] | None:
        try:
            st = self._outbox.stat()
        except OSError:
            return None
        # The FL script writes in place (it can't rename), so mtime and size are the
        # signals; the inode only helps if something else replaces the file.
        return st.st_mtime_ns, st.st_size, st.st_ino

    def take_outbox(self) -> Pattern | None:
        """The pattern written since the last call, validated, or None."""
        stamp = self._stamp()
        if stamp is None or stamp == self._seen or stamp[1] == 0:
            return None  # nothing new, or truncated by the script and not written yet
        if self._wall_clock() - stamp[0] / 1e9 < self.SETTLE:
            return None  # maybe still being written: look again on the next poll
        self._seen = stamp
        if stamp[1] > self.MAX_BYTES:
            log.warning("Pattern à envoyer ignoré : fichier trop gros")
            return None
        try:
            data = json.loads(self._outbox.read_text(encoding="utf-8"))
            return check_pattern(data["ppq"], data["notes"])
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            # ProtocolError is a ValueError; RecursionError: absurdly nested JSON
            reason = str(exc) if isinstance(exc, ProtocolError) else type(exc).__name__
            log.warning("Pattern à envoyer ignoré : %s", reason[:120])
            return None

    def write_inbox(self, sender: str, ppq: int, notes: list[tuple]) -> bool:
        """Write a received pattern now (True), or hold it if it came too soon (False).

        A held pattern is written by `flush_inbox` once the interval has passed; a
        newer one replaces it (the latest pattern wins, none is silently lost).
        """
        now = self._clock()
        if self._last_inbox is not None and now - self._last_inbox < self.INBOX_MIN_INTERVAL:
            self._held = (sender, ppq, notes)
            return False
        self._write(sender, ppq, notes)
        self._last_inbox = now  # only once the write succeeded
        self._held = None
        return True

    def flush_inbox(self) -> tuple[str, int] | None:
        """Write the held pattern if its time has come; (sender, note count) or None."""
        held = self._held
        if held is None:
            return None
        if self._last_inbox is not None and (
            self._clock() - self._last_inbox < self.INBOX_MIN_INTERVAL
        ):
            return None
        self._write(*held)
        self._last_inbox = self._clock()
        self._held = None
        return held[0], len(held[2])

    def _write(self, sender: str, ppq: int, notes: list[tuple]) -> None:
        name = "".join(ch for ch in sender if ch.isprintable())[: self.NAME_MAX] or "?"
        data = {"flmp": 1, "from": name, "ppq": ppq, "notes": [list(n) for n in notes]}
        tmp = self.folder / ".inbox.tmp"
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._inbox)  # the piano roll script never sees a half-written file
