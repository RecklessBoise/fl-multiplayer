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
from pathlib import Path

from flmp_core.messages import ProtocolError
from flmp_core.pattern import check_pattern

log = logging.getLogger("flmp.relay")

OUTBOX = "outbox.json"
INBOX = "inbox.json"
Pattern = tuple[int, list[tuple]]


class Mailbox:
    MAX_BYTES = 512 * 1024  # 4000 notes fit easily

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._outbox = self.folder / OUTBOX
        self._inbox = self.folder / INBOX
        self._seen = self._stamp()  # a pattern left from an earlier session is not resent

    def _stamp(self) -> tuple[int, int] | None:
        try:
            st = self._outbox.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def take_outbox(self) -> Pattern | None:
        """The pattern written since the last call, validated, or None."""
        stamp = self._stamp()
        if stamp is None or stamp == self._seen:
            return None
        self._seen = stamp
        if stamp[1] > self.MAX_BYTES:
            log.warning("Pattern à envoyer ignoré : fichier trop gros")
            return None
        try:
            data = json.loads(self._outbox.read_text(encoding="utf-8"))
            return check_pattern(data["ppq"], data["notes"])
        except (OSError, ValueError, KeyError, TypeError) as exc:  # ProtocolError is a ValueError
            reason = str(exc) if isinstance(exc, ProtocolError) else type(exc).__name__
            log.warning("Pattern à envoyer ignoré : %s", reason[:120])
            return None

    def write_inbox(self, sender: str, ppq: int, notes: list[tuple]) -> None:
        data = {"flmp": 1, "from": sender, "ppq": ppq, "notes": [list(n) for n in notes]}
        tmp = self.folder / ".inbox.tmp"
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._inbox)  # the piano roll script never sees a half-written file
