"""The relay: FL Studio (SysEx over IAC) <-> room server (JSON over WebSocket)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import NETWORK_KINDS, Change, ProtocolError, decode, encode, parse_fields
from flmp_core.throttle import Throttle
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from flmp_relay.lww import OwnWrites
from flmp_relay.midi import MidiIO

log = logging.getLogger("flmp.relay")

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
FATAL_CODES = frozenset({"bad_secret", "bad_secret_format", "bad_room", "bad_name", "bad_proto"})
DEFAULT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
MAX_INCOMING_BYTES = 1024 * 1024


class FatalJoinError(RuntimeError):
    """The server refused us for a reason retrying will not fix."""


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        check_server_url(server, allow_insecure)
        self.midi = midi
        self.server = server
        self.room = room
        self.name = name
        self._secret = secret
        self._backoff = tuple(backoff) or DEFAULT_BACKOFF
        self._clock = clock
        self._throttle = Throttle(max_per_second)
        self._own = OwnWrites()
        self._collecting: list[Change] | None = None
        self._snapshot_for: str | None = None
        self._names: dict[str, str] = {}
        self._ws: ClientConnection | None = None
        self._stop = asyncio.Event()
        self.connected = asyncio.Event()
        self.peer_id: str | None = None

    # -- lifecycle ----------------------------------------------------------------

    async def run(self) -> None:
        """Stay connected until `stop()`; raises FatalJoinError if refused."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
            except FatalJoinError:
                raise
            except (OSError, ConnectionClosed, InvalidHandshake, InvalidURI, TimeoutError) as exc:
                if not self._stop.is_set():
                    log.warning("Connexion au serveur impossible ou perdue : %s", exc or type(exc))
            if self.connected.is_set():
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
            try:
                await self._join(ws)
                tasks = [
                    asyncio.create_task(self._from_fl(ws)),
                    asyncio.create_task(self._from_server(ws)),
                    asyncio.create_task(self._flusher(ws)),
                ]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for t in done:
                    if not t.cancelled() and t.exception() is not None:
                        raise t.exception()  # type: ignore[misc]
            finally:
                self._ws = None

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
        if reply.get("type") == "error":
            code, message = reply.get("code"), reply.get("message")
            if code in FATAL_CODES:
                raise FatalJoinError(f"{code}: {message}")
            raise ConnectionError(f"refusé par le serveur ({code}: {message})")
        if reply.get("type") != "welcome":
            raise ConnectionError("réponse inattendue du serveur")
        self.peer_id = reply.get("peer")
        self._names = {p.get("peer"): p.get("name") for p in reply.get("peers", [])}
        self._own.reset()
        self._throttle.clear()
        self._collecting = None
        self._snapshot_for = None
        others = ", ".join(str(n) for n in self._names.values()) or "personne pour l'instant"
        log.info("Connecté au salon %s (%s). Avec : %s", reply.get("room"), self.name, others)
        self.connected.set()

    # -- FL -> server -------------------------------------------------------------

    async def _from_fl(self, ws: ClientConnection) -> None:
        while True:
            frame = await self.midi.receive()
            await self._handle_fl_frame(ws, frame)

    async def _handle_fl_frame(self, ws: ClientConnection, frame: bytes) -> None:
        try:
            change = decode(frame)
        except ProtocolError:
            log.debug("SysEx ignoré (pas FL Multiplayer) : %s", bytes(frame[:8]).hex())
            return
        if change.kind == "snapshot_begin":
            self._collecting = []
        elif change.kind == "snapshot_end":
            if self._collecting is not None and self._snapshot_for is not None:
                await ws.send(
                    json.dumps(
                        {
                            "type": "snapshot",
                            "for": self._snapshot_for,
                            "changes": [[c.kind, c.index, c.value] for c in self._collecting],
                        }
                    )
                )
                log.info("État complet envoyé (%d valeurs)", len(self._collecting))
            self._collecting = None
            self._snapshot_for = None
        elif change.kind in NETWORK_KINDS:
            if self._collecting is not None:
                self._collecting.append(change)
                return
            for c in self._throttle.offer(change, self._clock()):
                await self._send_change(ws, c)

    async def _send_change(self, ws: ClientConnection, change: Change) -> None:
        self._own.sent(change.key)
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

    async def _flusher(self, ws: ClientConnection) -> None:
        while True:
            deadline = self._throttle.next_deadline()
            now = self._clock()
            await asyncio.sleep(0.02 if deadline is None else max(0.0, deadline - now))
            for c in self._throttle.flush(self._clock()):
                await self._send_change(ws, c)

    # -- server -> FL -------------------------------------------------------------

    async def _from_server(self, ws: ClientConnection) -> None:
        async for raw in ws:
            self._handle_server(raw)
        raise ConnectionError("le serveur a fermé la connexion")

    def _handle_server(self, raw: str | bytes) -> None:
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
            target = msg.get("for")
            if isinstance(target, str):
                self._snapshot_for = target
                self.midi.send(encode(Change("snapshot_req", None, 0)))
                log.info("Un participant arrive : envoi de l'état complet demandé à FL")
        elif kind == "snapshot":
            self._on_snapshot(msg)
        elif kind == "peer_joined":
            self._names[msg.get("peer")] = msg.get("name")
            log.info("%s a rejoint le salon", msg.get("name"))
        elif kind == "peer_left":
            log.info("%s a quitté le salon", self._names.pop(msg.get("peer"), "Un participant"))
        elif kind == "error":
            log.warning("Erreur du serveur : %s (%s)", msg.get("code"), msg.get("message"))

    def _on_change(self, msg: dict[str, Any]) -> None:
        try:
            change = parse_fields(msg.get("kind"), msg.get("index"), msg.get("value"))
        except ProtocolError as exc:
            log.warning("Changement invalide du serveur ignoré : %s", exc)
            return
        if msg.get("from") == self.peer_id:
            self._own.echoed(change.key)
            return
        if self._own.blocks(change.key):
            log.debug("Conflit sur %s : notre écriture plus récente l'emporte", change.key)
            return
        self._throttle.discard(change.key)
        self.midi.send(encode(change))

    def _on_snapshot(self, msg: dict[str, Any]) -> None:
        raw_changes = msg.get("changes")
        if not isinstance(raw_changes, list):
            return
        changes = []
        for item in raw_changes:
            try:
                if not isinstance(item, list) or len(item) != 3:
                    raise ProtocolError("bad snapshot item")
                changes.append(parse_fields(*item))
            except ProtocolError:
                log.warning("Valeur invalide ignorée dans l'état complet")
        self._throttle.clear()
        self.midi.send(encode(Change("snapshot_begin", None, 0)))
        for c in changes:
            self.midi.send(encode(c))
        self.midi.send(encode(Change("snapshot_end", None, 0)))
        log.info("État complet reçu et transmis à FL (%d valeurs)", len(changes))
