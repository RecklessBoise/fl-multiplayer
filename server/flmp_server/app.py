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
import ipaddress
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import ProtocolError, parse_fields
from flmp_core.pattern import check_pattern
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("flmp.server")

ROOM_RE = re.compile(r"^[A-Z0-9]{4,12}$")
NAME_MAX = 32
SECRET_MIN = 8
SECRET_MAX = 128
POLICY_VIOLATION = 1008


@dataclass(frozen=True)
class Settings:
    max_message_bytes: int = 256 * 1024
    max_peers_per_room: int = 8
    max_rooms: int = 1000
    rate_per_second: float = 200.0
    rate_burst: int = 400
    pattern_rate: float = 1.0  # piano roll patterns can be large: their own budget
    pattern_burst: int = 3
    join_timeout: float = 10.0
    snapshot_timeout: float = 10.0
    max_snapshot_changes: int = 20000
    max_bad_messages: int = 20
    send_queue_size: int = 2000
    max_connections_per_ip: int = 16
    max_rooms_per_ip: int = 8
    max_failed_joins_per_ip: int = 10
    failed_join_window: float = 300.0
    max_hello_before_join: int = 3
    trust_proxy_header: bool = False  # True only behind a proxy that sets Fly-Client-IP


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
        self.pattern_bucket = TokenBucket(settings.pattern_rate, settings.pattern_burst)
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(settings.send_queue_size)
        self.closing = False
        self.dropped = 0
        self.last_rate_error = 0.0
        self.bad_messages = 0
        self.hellos = 0
        self.ip = ""

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
    def __init__(self, code: str, secret_hash: bytes | None, creator_ip: str = "") -> None:
        self.code = code
        self.secret_hash = secret_hash
        self.creator_ip = creator_ip
        self.peers: dict[str, Peer] = {}  # insertion order == join order
        self.seq = 0
        # newcomer id -> (responder id, timeout task, room seq when requested)
        self.pending: dict[str, tuple[str, asyncio.Task[None], int]] = {}

    def broadcast(self, msg: dict[str, Any], exclude: str | None = None) -> None:
        for peer in self.peers.values():
            if peer.id != exclude:
                peer.push(msg)


def ip_bucket(ip: str) -> str:
    """Key for per-IP limits: IPv6 is grouped by /64 (one subscriber), IPv4 as is."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _hash_secret(secret: str | None) -> bytes:
    return b"" if secret is None else hashlib.sha256(secret.encode("utf-8")).digest()


class Hub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rooms: dict[str, Room] = {}
        self.connections_by_ip: dict[str, int] = {}
        self.failed_joins: dict[str, list[float]] = {}

    def _client_ip(self, ws: ServerConnection) -> str:
        if self.settings.trust_proxy_header and ws.request is not None:
            forwarded = ws.request.headers.get("Fly-Client-IP")
            if forwarded:
                return ip_bucket(forwarded.strip())
        addr = ws.remote_address
        return ip_bucket(str(addr[0])) if addr else "?"

    def _recent_failures(self, ip: str) -> list[float]:
        cutoff = time.monotonic() - self.settings.failed_join_window
        recent = [t for t in self.failed_joins.get(ip, []) if t > cutoff]
        if recent:
            self.failed_joins[ip] = recent
        else:
            self.failed_joins.pop(ip, None)
        return recent

    # -- connection lifecycle -------------------------------------------------

    async def handle(self, ws: ServerConnection) -> None:
        ip = self._client_ip(ws)
        if self.connections_by_ip.get(ip, 0) >= self.settings.max_connections_per_ip:
            await ws.close(POLICY_VIOLATION, "too many connections")
            return
        self.connections_by_ip[ip] = self.connections_by_ip.get(ip, 0) + 1
        peer = Peer(ws, self.settings)
        peer.ip = ip
        writer = asyncio.create_task(peer.writer())
        try:
            try:
                await self._read_loop(peer)
            except ConnectionClosed:
                pass
            except ClientError as exc:  # fatal: report, then close
                peer.push(_error(exc.code, str(exc)))
                peer.close_soon()
            finally:
                self._leave(peer)
            if peer.closing:
                with contextlib.suppress(asyncio.TimeoutError, ConnectionClosed):
                    await asyncio.wait_for(asyncio.shield(writer), 1.0)
        finally:
            writer.cancel()
            left = self.connections_by_ip.get(ip, 1) - 1
            if left > 0:
                self.connections_by_ip[ip] = left
            else:
                self.connections_by_ip.pop(ip, None)

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
            if peer.room is None:
                peer.hellos += 1
                if peer.hellos > self.settings.max_hello_before_join:
                    raise ClientError("too_many_errors", "join a room", fatal=True)
            peer.push({"type": "hello", "server": "flmp", "proto": PROTOCOL_VERSION})
        elif kind == "join":
            self._join(peer, msg)
        elif kind in ("change", "snapshot", "pattern"):
            if peer.room is None:
                raise ClientError("not_joined", "join a room first")
            if kind == "change":
                self._change(peer, peer.room, msg)
            elif kind == "snapshot":
                self._snapshot(peer, peer.room, msg)
            else:
                self._pattern(peer, peer.room, msg)
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
            not isinstance(secret, str) or not SECRET_MIN <= len(secret) <= SECRET_MAX
        ):
            raise ClientError("bad_secret_format", f"secret: {SECRET_MIN}-{SECRET_MAX} characters")
        if len(self._recent_failures(peer.ip)) >= self.settings.max_failed_joins_per_ip:
            raise ClientError("too_many_attempts", "too many failed attempts, wait", fatal=True)
        code = room_code.upper()
        room = self.rooms.get(code)
        if room is None:
            if len(self.rooms) >= self.settings.max_rooms:
                raise ClientError("server_full", "too many rooms")
            owned = sum(1 for r in self.rooms.values() if r.creator_ip == peer.ip)
            if owned >= self.settings.max_rooms_per_ip:
                raise ClientError("too_many_rooms", "too many rooms from your address")
            room = Room(code, _hash_secret(secret), peer.ip)
            self.rooms[code] = room
        else:
            if not hmac.compare_digest(_hash_secret(secret), room.secret_hash or b""):
                self.failed_joins.setdefault(peer.ip, []).append(time.monotonic())
                log.info("bad secret for room %s", code)
                raise ClientError("bad_secret", "wrong room secret", fatal=True)
            if len(room.peers) >= self.settings.max_peers_per_room:
                self.failed_joins.setdefault(peer.ip, []).append(time.monotonic())
                raise ClientError("room_full", "room is full")
        others = [{"peer": p.id, "name": p.name} for p in room.peers.values()]
        peer.name = name
        peer.room = room
        room.peers[peer.id] = peer
        peer.push({"type": "welcome", "peer": peer.id, "room": code, "peers": others})
        room.broadcast({"type": "peer_joined", "peer": peer.id, "name": name}, exclude=peer.id)
        log.info("peer %s joined room %s (%d members)", peer.id, code, len(room.peers))
        self._request_snapshot(room, peer.id)

    def _request_snapshot(
        self, room: Room, newcomer: str, waiting: frozenset[str] = frozenset()
    ) -> None:
        old = room.pending.pop(newcomer, None)
        if old:
            old[1].cancel()
        others = [p for p in room.peers.values() if p.id != newcomer]
        # A peer still waiting for its own snapshot has no state worth copying.
        responder = next(
            (p for p in others if p.id not in room.pending and p.id not in waiting), None
        )
        if responder is None:
            if others and newcomer in room.peers:
                room.peers[newcomer].push(
                    _error("snapshot_unavailable", "no member can provide the state")
                )
            return
        task = asyncio.create_task(self._snapshot_timeout(room, newcomer))
        room.pending[newcomer] = (responder.id, task, room.seq)
        responder.push({"type": "snapshot_request", "for": newcomer, "base_seq": room.seq})

    async def _snapshot_timeout(self, room: Room, newcomer: str) -> None:
        await asyncio.sleep(self.settings.snapshot_timeout)
        if room.pending.pop(newcomer, None) and newcomer in room.peers:
            room.peers[newcomer].push(_error("snapshot_timeout", "no snapshot received"))

    def _change(self, peer: Peer, room: Room, msg: dict[str, Any]) -> None:
        try:
            change = parse_fields(msg.get("kind"), msg.get("index"), msg.get("value"))
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

    def _pattern(self, peer: Peer, room: Room, msg: dict[str, Any]) -> None:
        """A piano roll pattern: relayed to the others as is, never stored."""
        if not peer.pattern_bucket.take():
            peer.push(_error("rate_limited", "too many patterns, this one was dropped"))
            return
        try:
            ppq, notes = check_pattern(msg.get("ppq"), msg.get("notes"))
        except ProtocolError as exc:
            raise ClientError("bad_message", f"pattern: {exc}") from exc
        room.broadcast(
            {"type": "pattern", "from": peer.id, "ppq": ppq, "notes": [list(n) for n in notes]},
            exclude=peer.id,
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
                c = parse_fields(*item)
                changes.append([c.kind, c.index, c.value])
        except ProtocolError as exc:
            raise ClientError("bad_snapshot", str(exc)) from exc
        del room.pending[target]
        pending[1].cancel()
        room.seq += 1
        room.peers[target].push(
            {
                "type": "snapshot",
                "from": peer.id,
                "seq": room.seq,
                "base_seq": pending[2],
                "changes": changes,
            }
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
        orphans = frozenset(
            n for n, (responder, _t, _s) in room.pending.items() if responder == peer.id
        )
        for newcomer in orphans:
            self._request_snapshot(room, newcomer, waiting=orphans)
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
