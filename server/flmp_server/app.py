"""WebSocket room server.

Every accepted change gets a per-room sequence number and is sent to every
member of the room, the sender included, in sequence order. Clients use the
echo of their own writes to resolve conflicts (last writer wins).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import KINDS, Change, ProtocolError, validate
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("flmp.server")

ROOM_RE = re.compile(r"^[A-Z0-9]{4,12}$")
NAME_MAX = 32
SECRET_MAX = 128
FL_ONLY_KINDS = frozenset({"snapshot_req", "snapshot_begin", "snapshot_end"})
NETWORK_KINDS = frozenset(KINDS) - FL_ONLY_KINDS
POLICY_VIOLATION = 1008


@dataclass(frozen=True)
class Settings:
    max_message_bytes: int = 256 * 1024
    max_peers_per_room: int = 8
    max_rooms: int = 1000
    rate_per_second: float = 200.0
    rate_burst: int = 400
    join_timeout: float = 10.0
    snapshot_timeout: float = 10.0
    max_snapshot_changes: int = 20000
    max_bad_messages: int = 20
    send_queue_size: int = 2000


class ClientError(Exception):
    def __init__(self, code: str, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.fatal = fatal


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = float(burst)
        self.tokens = float(burst)
        self.stamp = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class Peer:
    def __init__(self, ws: ServerConnection, settings: Settings) -> None:
        self.ws = ws
        self.id = "p" + secrets.token_hex(6)
        self.name = ""
        self.room: Room | None = None
        self.bucket = TokenBucket(settings.rate_per_second, settings.rate_burst)
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(settings.send_queue_size)
        self.closing = False
        self.dropped = 0
        self.last_rate_error = 0.0
        self.bad_messages = 0

    def push(self, msg: dict[str, Any]) -> None:
        if self.closing:
            return
        try:
            self.queue.put_nowait(json.dumps(msg, separators=(",", ":")))
        except asyncio.QueueFull:
            log.warning("peer %s too slow, disconnecting", self.id)
            self.close_soon()

    def close_soon(self) -> None:
        """Ask the writer to close the connection after what is queued."""
        if self.closing:
            return
        self.closing = True
        if self.queue.full():
            self.queue = asyncio.Queue(1)
        self.queue.put_nowait(None)

    async def writer(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                await self.ws.close(POLICY_VIOLATION, "closed by server")
                return
            try:
                await self.ws.send(item)
            except ConnectionClosed:
                return


class Room:
    def __init__(self, code: str, secret_hash: bytes | None) -> None:
        self.code = code
        self.secret_hash = secret_hash
        self.peers: dict[str, Peer] = {}  # insertion order == join order
        self.seq = 0
        # newcomer id -> (responder id, timeout task)
        self.pending: dict[str, tuple[str, asyncio.Task[None]]] = {}

    def broadcast(self, msg: dict[str, Any], exclude: str | None = None) -> None:
        for peer in self.peers.values():
            if peer.id != exclude:
                peer.push(msg)


def _hash_secret(secret: str | None) -> bytes:
    return b"" if secret is None else hashlib.sha256(secret.encode("utf-8")).digest()


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def parse_change(kind: Any, index: Any, value: Any) -> Change:
    """Build a validated Change from untrusted JSON fields."""
    if not isinstance(kind, str) or kind not in NETWORK_KINDS:
        raise ProtocolError("kind not allowed")
    if index is not None and not _is_int(index):
        raise ProtocolError("bad index")
    if not _is_int(value):
        raise ProtocolError("bad value")
    change = Change(kind, index, value)
    validate(change)
    return change


class Hub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rooms: dict[str, Room] = {}

    # -- connection lifecycle -------------------------------------------------

    async def handle(self, ws: ServerConnection) -> None:
        peer = Peer(ws, self.settings)
        writer = asyncio.create_task(peer.writer())
        try:
            await self._read_loop(peer)
        except ConnectionClosed:
            pass
        except ClientError as exc:  # fatal: report, then close
            peer.push(_error(exc.code, str(exc)))
            peer.close_soon()
        finally:
            self._leave(peer)
        with contextlib.suppress(asyncio.TimeoutError, ConnectionClosed):
            if peer.closing:
                await asyncio.wait_for(asyncio.shield(writer), 1.0)
        writer.cancel()

    async def _read_loop(self, peer: Peer) -> None:
        deadline = time.monotonic() + self.settings.join_timeout
        while not peer.closing:
            if peer.room is None:
                remaining = deadline - time.monotonic()
                try:
                    raw = await asyncio.wait_for(peer.ws.recv(), max(remaining, 0.0))
                except TimeoutError:
                    await peer.ws.close(POLICY_VIOLATION, "join timeout")
                    return
            else:
                raw = await peer.ws.recv()
            if not peer.bucket.take():
                self._rate_limited(peer)
                continue
            try:
                self._dispatch(peer, raw)
            except ClientError as exc:
                if exc.fatal:
                    raise
                peer.push(_error(exc.code, str(exc)))
                peer.bad_messages += 1
                if peer.bad_messages > self.settings.max_bad_messages:
                    raise ClientError("too_many_errors", "too many bad messages", True) from exc

    def _rate_limited(self, peer: Peer) -> None:
        peer.dropped += 1
        now = time.monotonic()
        if now - peer.last_rate_error >= 1.0:
            peer.last_rate_error = now
            peer.push(_error("rate_limited", "too many messages, some were dropped"))
        if peer.dropped > self.settings.rate_burst * 5:
            peer.close_soon()

    # -- message handling -------------------------------------------------------

    def _dispatch(self, peer: Peer, raw: str | bytes) -> None:
        if not isinstance(raw, str):
            raise ClientError("bad_message", "text frames only")
        try:
            msg = json.loads(raw)
        except ValueError as exc:
            raise ClientError("bad_message", "invalid JSON") from exc
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            raise ClientError("bad_message", "expected an object with a type")
        kind = msg["type"]
        if kind == "hello":
            peer.push({"type": "hello", "server": "flmp", "proto": PROTOCOL_VERSION})
        elif kind == "join":
            self._join(peer, msg)
        elif kind in ("change", "snapshot"):
            if peer.room is None:
                raise ClientError("not_joined", "join a room first")
            if kind == "change":
                self._change(peer, peer.room, msg)
            else:
                self._snapshot(peer, peer.room, msg)
        else:
            raise ClientError("bad_message", "unknown message type")

    def _join(self, peer: Peer, msg: dict[str, Any]) -> None:
        if peer.room is not None:
            raise ClientError("already_joined", "already in a room")
        if msg.get("proto") != PROTOCOL_VERSION:
            raise ClientError("bad_proto", f"protocol {PROTOCOL_VERSION} required")
        room_code, name, secret = msg.get("room"), msg.get("name"), msg.get("secret")
        if not isinstance(room_code, str) or not ROOM_RE.match(room_code.upper()):
            raise ClientError("bad_room", "room code: 4-12 letters or digits")
        if (
            not isinstance(name, str)
            or not 0 < len(name) <= NAME_MAX
            or not name.isprintable()
            or not name.strip()
        ):
            raise ClientError("bad_name", f"name: 1-{NAME_MAX} printable characters")
        if secret is not None and (
            not isinstance(secret, str) or not 0 < len(secret) <= SECRET_MAX
        ):
            raise ClientError("bad_secret_format", f"secret: 1-{SECRET_MAX} characters")
        code = room_code.upper()
        room = self.rooms.get(code)
        if room is None:
            if len(self.rooms) >= self.settings.max_rooms:
                raise ClientError("server_full", "too many rooms")
            room = Room(code, _hash_secret(secret))
            self.rooms[code] = room
        else:
            if not hmac.compare_digest(_hash_secret(secret), room.secret_hash or b""):
                log.info("bad secret for room %s", code)
                raise ClientError("bad_secret", "wrong room secret", fatal=True)
            if len(room.peers) >= self.settings.max_peers_per_room:
                raise ClientError("room_full", "room is full")
        others = [{"peer": p.id, "name": p.name} for p in room.peers.values()]
        peer.name = name
        peer.room = room
        room.peers[peer.id] = peer
        peer.push({"type": "welcome", "peer": peer.id, "room": code, "peers": others})
        room.broadcast({"type": "peer_joined", "peer": peer.id, "name": name}, exclude=peer.id)
        log.info("peer %s joined room %s (%d members)", peer.id, code, len(room.peers))
        self._request_snapshot(room, peer.id)

    def _request_snapshot(self, room: Room, newcomer: str) -> None:
        responder = next((p for p in room.peers.values() if p.id != newcomer), None)
        if responder is None:
            return
        old = room.pending.pop(newcomer, None)
        if old:
            old[1].cancel()
        task = asyncio.create_task(self._snapshot_timeout(room, newcomer))
        room.pending[newcomer] = (responder.id, task)
        responder.push({"type": "snapshot_request", "for": newcomer})

    async def _snapshot_timeout(self, room: Room, newcomer: str) -> None:
        await asyncio.sleep(self.settings.snapshot_timeout)
        if room.pending.pop(newcomer, None) and newcomer in room.peers:
            room.peers[newcomer].push(_error("snapshot_timeout", "no snapshot received"))

    def _change(self, peer: Peer, room: Room, msg: dict[str, Any]) -> None:
        try:
            change = parse_change(msg.get("kind"), msg.get("index"), msg.get("value"))
        except ProtocolError as exc:
            raise ClientError("bad_change", str(exc)) from exc
        room.seq += 1
        room.broadcast(
            {
                "type": "change",
                "kind": change.kind,
                "index": change.index,
                "value": change.value,
                "seq": room.seq,
                "from": peer.id,
            }
        )

    def _snapshot(self, peer: Peer, room: Room, msg: dict[str, Any]) -> None:
        target = msg.get("for")
        pending = room.pending.get(target) if isinstance(target, str) else None
        if pending is None or pending[0] != peer.id or target not in room.peers:
            raise ClientError("unexpected_snapshot", "no snapshot was requested from you")
        raw_changes = msg.get("changes")
        if (
            not isinstance(raw_changes, list)
            or len(raw_changes) > self.settings.max_snapshot_changes
        ):
            raise ClientError("bad_snapshot", "changes must be a bounded list")
        changes = []
        try:
            for item in raw_changes:
                if not isinstance(item, list) or len(item) != 3:
                    raise ProtocolError("each change is [kind, index, value]")
                c = parse_change(*item)
                changes.append([c.kind, c.index, c.value])
        except ProtocolError as exc:
            raise ClientError("bad_snapshot", str(exc)) from exc
        del room.pending[target]
        pending[1].cancel()
        room.seq += 1
        room.peers[target].push(
            {"type": "snapshot", "from": peer.id, "seq": room.seq, "changes": changes}
        )

    def _leave(self, peer: Peer) -> None:
        room = peer.room
        if room is None or room.peers.get(peer.id) is not peer:
            return
        del room.peers[peer.id]
        peer.room = None
        own = room.pending.pop(peer.id, None)
        if own:
            own[1].cancel()
        for newcomer, (responder, task) in list(room.pending.items()):
            if responder == peer.id:
                task.cancel()
                del room.pending[newcomer]
                self._request_snapshot(room, newcomer)
        if room.peers:
            room.broadcast({"type": "peer_left", "peer": peer.id})
        else:
            del self.rooms[room.code]
        log.info("peer %s left room %s", peer.id, room.code)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


def _health(connection: ServerConnection, request: Request) -> Response | None:
    if request.path == "/healthz":
        return connection.respond(HTTPStatus.OK, "ok\n")
    return None


async def start_server(
    settings: Settings | None = None, host: str = "127.0.0.1", port: int = 8765
) -> tuple[Server, str]:
    settings = settings or Settings()
    hub = Hub(settings)
    server = await serve(
        hub.handle,
        host,
        port,
        max_size=settings.max_message_bytes,
        ping_interval=20,
        ping_timeout=20,
        process_request=_health,
    )
    actual_port = server.sockets[0].getsockname()[1]
    return server, f"ws://{host}:{actual_port}"
