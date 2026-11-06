"""The relay: FL Studio (SysEx over IAC) <-> room server (JSON over WebSocket).

Ordering rules (see docs/PROTOCOL.md):
- Our writes: last writer wins via `OwnWrites` (foreign changes for a key are
  ignored while our own write on it is in flight).
- Joining a busy room: live changes are buffered until the snapshot arrives,
  then the snapshot is applied and only buffered changes newer than the
  snapshot's `base_seq` are replayed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import NETWORK_KINDS, Change, ProtocolError, decode, encode, parse_fields
from flmp_core.pattern import check_pattern
from flmp_core.throttle import Throttle
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from flmp_relay.lww import OwnWrites
from flmp_relay.midi import MidiIO
from flmp_relay.patterns import Mailbox

log = logging.getLogger("flmp.relay")

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
FATAL_CODES = frozenset({"bad_secret", "bad_secret_format", "bad_room", "bad_name", "bad_proto"})
DEFAULT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
MAX_INCOMING_BYTES = 1024 * 1024
MAX_SNAPSHOT_ITEMS = 20_000
MAX_BUFFERED = 50_000
MAX_SNAPSHOT_TARGETS = 16
STABLE_SESSION = 10.0  # seconds connected before the backoff resets
SNAPSHOT_CHUNK = 200


class FatalJoinError(RuntimeError):
    """The server refused us for a reason retrying will not fix."""


def clean(text: object, limit: int = 32) -> str:
    """Make untrusted text (peer names, server messages) safe to log."""
    if not isinstance(text, str):
        return "?"
    return repr(text[:limit])


def _int(x: object, default: int = 0) -> int:
    return x if isinstance(x, int) and not isinstance(x, bool) else default


def check_server_url(url: str, allow_insecure: bool = False) -> None:
    """Require wss://, except ws:// to this machine (or with allow_insecure)."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme == "wss" and host:
        return
    if parts.scheme == "ws" and host and (host in LOCAL_HOSTS or allow_insecure):
        return
    raise ValueError(
        f"Adresse de serveur refusée : {url!r}. Utilisez wss://… "
        "(ws:// n'est accepté que pour localhost, ou avec --insecure)."
    )


class Relay:
    def __init__(
        self,
        midi: MidiIO,
        server: str,
        room: str,
        name: str,
        secret: str | None = None,
        *,
        max_per_second: float = 30.0,
        allow_insecure: bool = False,
        backoff: Sequence[float] = DEFAULT_BACKOFF,
        own_write_ttl: float = 2.0,
        snapshot_wait: float = 15.0,
        collect_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        exchange_dir: Path | None = None,
    ) -> None:
        check_server_url(server, allow_insecure)
        self._mailbox = Mailbox(exchange_dir) if exchange_dir is not None else None
        self.midi = midi
        self.server = server
        self.room = room
        self.name = name
        self._secret = secret
        self._backoff = tuple(backoff) or DEFAULT_BACKOFF
        self._clock = clock
        self._snapshot_wait = snapshot_wait
        self._collect_timeout = collect_timeout
        self._throttle = Throttle(max_per_second)
        self._own = OwnWrites(own_write_ttl)
        self._names: dict[str, str] = {}
        self._ws: ClientConnection | None = None  # open socket (maybe not joined)
        self._live: ClientConnection | None = None  # joined socket
        self._stop = asyncio.Event()
        self.connected = asyncio.Event()
        self.peer_id: str | None = None
        self._reset_session_state()

    def _reset_session_state(self) -> None:
        # Answering snapshot requests (we are the responder).
        self._targets: list[str] = []  # newcomers served by the dump requested from FL
        self._next_targets: list[str] = []  # arrived after that request: need a fresh dump
        self._requested_at = 0.0
        self._last_frame_error = 0.0
        self._collecting: list[Change] | None = None
        # Receiving our own snapshot (we are the newcomer).
        self._awaiting = False
        self._await_deadline = 0.0
        self._buffer: list[tuple[int, Change, bool]] = []

    # -- lifecycle ----------------------------------------------------------------

    async def run(self) -> None:
        """Stay connected until `stop()`; raises FatalJoinError if refused."""
        reader = asyncio.create_task(self._midi_reader())
        watcher = asyncio.create_task(self._outbox_watcher()) if self._mailbox else None
        attempt = 0
        try:
            while not self._stop.is_set():
                started = self._clock()
                try:
                    await self._session()
                except FatalJoinError:
                    raise
                except Exception as exc:
                    if not self._stop.is_set():
                        log.warning(
                            "Connexion au serveur impossible ou perdue : %s",
                            clean(str(exc) or type(exc).__name__, 120),
                        )
                if self.connected.is_set() and self._clock() - started >= STABLE_SESSION:
                    attempt = 0
                self.connected.clear()
                if self._stop.is_set():
                    break
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                delay *= random.uniform(0.8, 1.2)  # noqa: S311 - jitter, not crypto
                attempt += 1
                log.info("Nouvelle tentative dans %.1f s…", delay)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), delay)
        finally:
            tasks = [t for t in (reader, watcher) if t is not None]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.connected.clear()

    async def _outbox_watcher(self, interval: float = 0.25) -> None:
        """Send each pattern the piano roll script writes to the outbox."""
        mailbox = self._mailbox
        if mailbox is None:
            return
        while True:
            await asyncio.sleep(interval)
            pattern = mailbox.take_outbox()
            if pattern is None:
                continue
            ws = self._live
            if ws is None:
                log.warning(
                    "Pas connecté au salon : pattern non envoyé. Renvoyez-le une fois connecté."
                )
                continue
            ppq, notes = pattern
            msg = {"type": "pattern", "ppq": ppq, "notes": [list(n) for n in notes]}
            try:
                await ws.send(json.dumps(msg))
                log.info("Pattern envoyé (%d notes)", len(notes))
            except ConnectionClosed:
                log.warning(
                    "Connexion perdue : pattern non envoyé. Renvoyez-le une fois reconnecté."
                )

    def _on_pattern(self, msg: dict[str, Any]) -> None:
        if self._mailbox is None:
            return
        try:
            ppq, notes = check_pattern(msg.get("ppq"), msg.get("notes"))
        except ProtocolError:
            log.warning("Pattern reçu invalide ignoré")
            return
        sender = msg.get("from")
        name = self._names.get(sender, "?") if isinstance(sender, str) else "?"
        self._mailbox.write_inbox(name, ppq, notes)
        log.info(
            "Pattern reçu de %s (%d notes) : ouvrez le piano roll du même channel, puis "
            "Tools > Scripting > FL Multiplayer - Recevoir",
            clean(name),
            len(notes),
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            await self._ws.close()

    async def _session(self) -> None:
        async with connect(
            self.server,
            open_timeout=10,
            max_size=MAX_INCOMING_BYTES,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            stopper = asyncio.create_task(self._stop.wait())
            tasks: list[asyncio.Task[Any]] = [stopper]
            try:
                joiner = asyncio.create_task(self._join(ws))
                await asyncio.wait({joiner, stopper}, return_when=asyncio.FIRST_COMPLETED)
                if not joiner.done():
                    joiner.cancel()
                    await asyncio.gather(joiner, return_exceptions=True)
                    return
                joiner.result()  # raises FatalJoinError / ConnectionError
                tasks += [
                    asyncio.create_task(self._from_server(ws)),
                    asyncio.create_task(self._ticker(ws)),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t is not stopper and not t.cancelled() and t.exception() is not None:
                        raise t.exception()  # type: ignore[misc]
            finally:
                self._live = None
                self._ws = None
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _join(self, ws: ClientConnection) -> None:
        join: dict[str, Any] = {
            "type": "join",
            "room": self.room,
            "name": self.name,
            "proto": PROTOCOL_VERSION,
        }
        if self._secret:
            join["secret"] = self._secret
        await ws.send(json.dumps(join))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not isinstance(reply, dict):
            raise ConnectionError("réponse inattendue du serveur")
        if reply.get("type") == "error":
            code, message = reply.get("code"), clean(reply.get("message"), 120)
            if code in FATAL_CODES:
                raise FatalJoinError(f"{code}: {message}")
            raise ConnectionError(f"refusé par le serveur ({clean(code)}: {message})")
        if reply.get("type") != "welcome" or not isinstance(reply.get("peer"), str):
            raise ConnectionError("réponse inattendue du serveur")
        self.peer_id = reply["peer"]
        peers = reply.get("peers") if isinstance(reply.get("peers"), list) else []
        self._names = {
            p["peer"]: p["name"]
            for p in peers
            if isinstance(p, dict)
            and isinstance(p.get("peer"), str)
            and isinstance(p.get("name"), str)
        }
        self._own.reset()
        self._throttle.clear()
        self._reset_session_state()
        if self._names:
            self._awaiting = True
            self._await_deadline = self._clock() + self._snapshot_wait
        others = ", ".join(clean(n) for n in self._names.values()) or "personne pour l'instant"
        log.info("Connecté au salon %s. Avec : %s", clean(reply.get("room")), others)
        self._live = ws  # before `connected`, so no FL frame is dropped in between
        self.connected.set()

    # -- FL -> server -------------------------------------------------------------

    async def _midi_reader(self) -> None:
        """Runs for the relay's whole life; frames that arrive offline are dropped."""
        while True:
            frame = await self.midi.receive()
            ws = self._live
            if ws is None:
                log.debug("Hors ligne : message de FL ignoré")
                continue
            try:
                await self._handle_fl_frame(ws, frame)
            except ConnectionClosed:
                pass
            except Exception:
                now = self._clock()
                if now - self._last_frame_error > 5.0:  # avoid flooding the log
                    self._last_frame_error = now
                    log.exception("Erreur en traitant un message de FL")

    async def _handle_fl_frame(self, ws: ClientConnection, frame: bytes) -> None:
        try:
            change = decode(frame)
        except ProtocolError:
            log.debug("SysEx ignoré (pas FL Multiplayer) : %s", bytes(frame[:8]).hex())
            return
        if change.kind == "snapshot_begin":
            self._collecting = [] if self._targets else None
        elif change.kind == "snapshot_end":
            if self._collecting is not None:
                payload = [[c.kind, c.index, c.value] for c in self._collecting]
                for target in self._targets:
                    await ws.send(
                        json.dumps({"type": "snapshot", "for": target, "changes": payload})
                    )
                log.info("État complet envoyé (%d valeurs)", len(payload))
            self._collecting = None
            self._targets = []
            self._start_next_dump()
        elif change.kind in NETWORK_KINDS:
            if self._collecting is not None:
                if len(self._collecting) < MAX_SNAPSHOT_ITEMS:
                    self._collecting.append(change)
                return
            for c in self._throttle.offer(change, self._clock()):
                await self._send_change(ws, c)

    async def _send_change(self, ws: ClientConnection, change: Change) -> None:
        self._own.sent(change.key, self._clock())
        await ws.send(
            json.dumps(
                {
                    "type": "change",
                    "kind": change.kind,
                    "index": change.index,
                    "value": change.value,
                }
            )
        )

    async def _ticker(self, ws: ClientConnection) -> None:
        """Throttle flushes and timeouts."""
        while True:
            deadline = self._throttle.next_deadline()
            now = self._clock()
            await asyncio.sleep(0.02 if deadline is None else min(0.02, max(0.0, deadline - now)))
            now = self._clock()
            for c in self._throttle.flush(now):
                await self._send_change(ws, c)
            if self._targets and now - self._requested_at > self._collect_timeout:
                log.warning("FL n'a pas fourni l'état complet à temps (script FL actif ?)")
                self._targets = []
                self._collecting = None
                self._start_next_dump()
            if self._awaiting and now > self._await_deadline:
                log.warning("État complet non reçu : on continue sans")
                self._end_waiting()

    # -- server -> FL -------------------------------------------------------------

    async def _from_server(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                await self._handle_server(raw)
            except Exception:
                log.exception("Message du serveur ignoré après une erreur")
        raise ConnectionError("le serveur a fermé la connexion")

    async def _handle_server(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("Message illisible du serveur ignoré")
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "change":
            self._on_change(msg)
        elif kind == "snapshot_request":
            self._on_snapshot_request(msg)
        elif kind == "snapshot":
            await self._on_snapshot(msg)
        elif kind == "pattern":
            self._on_pattern(msg)
        elif kind == "peer_joined":
            peer, name = msg.get("peer"), msg.get("name")
            if isinstance(peer, str) and isinstance(name, str):
                self._names[peer] = name
                log.info("%s a rejoint le salon", clean(name))
        elif kind == "peer_left":
            peer = msg.get("peer")
            name = self._names.pop(peer, None) if isinstance(peer, str) else None
            log.info("%s a quitté le salon", clean(name) if name else "Un participant")
        elif kind == "error":
            code = msg.get("code")
            if code in ("snapshot_timeout", "snapshot_unavailable") and self._awaiting:
                log.warning("Aucun état complet disponible : on continue sans")
                self._end_waiting()
            else:
                log.warning(
                    "Erreur du serveur : %s (%s)", clean(code), clean(msg.get("message"), 120)
                )

    def _on_change(self, msg: dict[str, Any]) -> None:
        try:
            change = parse_fields(msg.get("kind"), msg.get("index"), msg.get("value"))
        except ProtocolError as exc:
            log.warning("Changement invalide du serveur ignoré : %s", exc)
            return
        own = msg.get("from") == self.peer_id
        if own:
            self._own.echoed(change.key)
        if self._awaiting:
            if len(self._buffer) < MAX_BUFFERED:
                self._buffer.append((_int(msg.get("seq")), change, own))
            return
        if not own:
            self._apply_foreign(change)

    def _apply_foreign(self, change: Change) -> None:
        if self._own.blocks(change.key, self._clock()):
            log.debug("Conflit sur %s : notre écriture plus récente l'emporte", change.key)
            return
        self._throttle.discard(change.key)
        self.midi.send(encode(change))

    def _on_snapshot_request(self, msg: dict[str, Any]) -> None:
        target = msg.get("for")
        if not isinstance(target, str) or target in self._targets + self._next_targets:
            return
        if len(self._targets) + len(self._next_targets) >= MAX_SNAPSHOT_TARGETS:
            return
        # A dump already requested from FL may predate this request's base_seq,
        # so a late newcomer waits for a fresh dump instead of reusing it.
        self._next_targets.append(target)
        if not self._targets:
            self._start_next_dump()

    def _start_next_dump(self) -> None:
        if self._targets or not self._next_targets:
            return
        self._targets, self._next_targets = self._next_targets, []
        self._requested_at = self._clock()
        self.midi.send(encode(Change("snapshot_req", None, 0)))
        log.info("Un participant arrive : envoi de l'état complet demandé à FL")

    async def _on_snapshot(self, msg: dict[str, Any]) -> None:
        if not self._awaiting:
            log.warning("État complet inattendu ignoré")
            return
        raw_changes = msg.get("changes")
        if not isinstance(raw_changes, list):
            return
        base_seq = _int(msg.get("base_seq"))
        self._awaiting = False
        now = self._clock()
        self.midi.send(encode(Change("snapshot_begin", None, 0)))
        applied = 0
        for item in raw_changes[:MAX_SNAPSHOT_ITEMS]:
            try:
                if not isinstance(item, list) or len(item) != 3:
                    raise ProtocolError("bad snapshot item")
                c = parse_fields(*item)
            except ProtocolError:
                log.warning("Valeur invalide ignorée dans l'état complet")
                continue
            if self._own.blocks(c.key, now):
                continue  # our newer write wins
            self._throttle.discard(c.key)
            self.midi.send(encode(c))
            applied += 1
            if applied % SNAPSHOT_CHUNK == 0:
                await asyncio.sleep(0)  # let FL and the event loop breathe
        self.midi.send(encode(Change("snapshot_end", None, 0)))
        log.info("État complet reçu et transmis à FL (%d valeurs)", applied)
        self._replay(lambda seq: seq > base_seq)

    def _end_waiting(self) -> None:
        self._awaiting = False
        self._replay(lambda seq: True)

    def _replay(self, keep: Callable[[int], bool]) -> None:
        buffered, self._buffer = self._buffer, []
        for seq, change, own in sorted(buffered, key=lambda item: item[0]):
            if not keep(seq):
                continue
            if own:
                self.midi.send(encode(change))  # restore our value over the snapshot
            else:
                self._apply_foreign(change)
