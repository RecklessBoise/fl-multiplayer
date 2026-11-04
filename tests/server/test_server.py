import asyncio
import json

import pytest
from flmp_server.app import Settings, start_server
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

TIMEOUT = 2.0


class Client:
    def __init__(self, ws):
        self.ws = ws

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def recv(self) -> dict:
        return json.loads(await asyncio.wait_for(self.ws.recv(), TIMEOUT))

    async def recv_type(self, kind: str) -> dict:
        while True:
            msg = await self.recv()
            if msg["type"] == kind:
                return msg

    async def nothing(self, delay: float = 0.15) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(self.ws.recv(), delay)

    async def join(self, room="ROOM1", name="Alice", **extra) -> dict:
        await self.send(type="join", room=room, name=name, proto=1, **extra)
        return await self.recv()


@pytest.fixture
async def server():
    servers = []

    async def make(**overrides):
        srv, url = await start_server(Settings(**overrides), host="127.0.0.1", port=0)
        servers.append(srv)
        return url

    yield make
    for srv in servers:
        srv.close()
        await srv.wait_closed()


@pytest.fixture
async def clients():
    opened = []

    async def make(url):
        ws = await connect(url)
        opened.append(ws)
        return Client(ws)

    yield make
    for ws in opened:
        await ws.close()


async def pair(server, clients, **settings):
    """Alice and Bob in ROOM1, with Alice's snapshot request consumed."""
    url = await server(**settings)
    a, b = await clients(url), await clients(url)
    pa = (await a.join())["peer"]
    pb = (await b.join(name="Bob"))["peer"]
    await a.recv_type("snapshot_request")
    return a, b, pa, pb


async def test_hello(server, clients):
    c = await clients(await server())
    await c.send(type="hello")
    assert await c.recv() == {"type": "hello", "server": "flmp", "proto": 1}


async def test_join_welcome_and_peer_list(server, clients):
    url = await server()
    a, b = await clients(url), await clients(url)
    wa = await a.join(name="Alice")
    assert wa["type"] == "welcome" and wa["room"] == "ROOM1" and wa["peers"] == []
    wb = await b.join(name="Bob")
    assert [p["name"] for p in wb["peers"]] == ["Alice"]
    joined = await a.recv_type("peer_joined")
    assert joined == {"type": "peer_joined", "peer": wb["peer"], "name": "Bob"}


async def test_room_code_is_case_insensitive(server, clients):
    url = await server()
    a, b = await clients(url), await clients(url)
    await a.join(room="abcd")
    wb = await b.join(room="ABCD")
    assert len(wb["peers"]) == 1


async def test_change_is_sequenced_and_sent_to_everyone(server, clients):
    a, b, pa, _pb = await pair(server, clients)
    await a.send(type="change", kind="mix_vol", index=3, value=1000)
    for c in (a, b):
        msg = await c.recv_type("change")
        assert msg == {
            "type": "change",
            "kind": "mix_vol",
            "index": 3,
            "value": 1000,
            "seq": 1,
            "from": pa,
        }
    await b.send(type="change", kind="tempo", index=None, value=128000)
    assert (await a.recv_type("change"))["seq"] == 2


async def test_rooms_are_isolated(server, clients):
    url = await server()
    a, b = await clients(url), await clients(url)
    await a.join(room="ROOMA")
    await b.join(room="ROOMB")
    await a.send(type="change", kind="stop", index=None, value=0)
    await a.recv_type("change")
    await b.nothing()


async def test_snapshot_flow(server, clients):
    url = await server()
    a, b = await clients(url), await clients(url)
    pa = (await a.join())["peer"]
    pb = (await b.join(name="Bob"))["peer"]
    assert await a.recv_type("snapshot_request") == {"type": "snapshot_request", "for": pb}
    changes = [["mix_vol", 1, 50], ["tempo", None, 90000]]
    await a.send(type="snapshot", changes=changes, **{"for": pb})
    snap = await b.recv_type("snapshot")
    assert snap["from"] == pa
    assert snap["changes"] == changes
    assert isinstance(snap["seq"], int)


async def test_snapshot_cannot_be_sent_unrequested(server, clients):
    a, b, pa, _pb = await pair(server, clients)
    await b.send(type="snapshot", changes=[["mix_vol", 1, 0]], **{"for": pa})
    assert (await b.recv_type("error"))["code"] == "unexpected_snapshot"
    await a.nothing()


async def test_snapshot_with_invalid_change_is_rejected(server, clients):
    a, b, _pa, pb = await pair(server, clients)
    await a.send(type="snapshot", changes=[["mix_vol", 1, 99999]], **{"for": pb})
    assert (await a.recv_type("error"))["code"] == "bad_snapshot"
    await b.nothing()


async def test_oldest_member_answers_snapshot(server, clients):
    url = await server()
    a, b, c = await clients(url), await clients(url), await clients(url)
    await a.join(name="A")
    await b.join(name="B")
    await a.recv_type("snapshot_request")
    await c.join(name="C")
    assert (await a.recv_type("snapshot_request"))["type"] == "snapshot_request"
    await b.recv_type("peer_joined")
    await b.nothing()


async def test_next_oldest_answers_after_first_leaves(server, clients):
    url = await server()
    a, b, c = await clients(url), await clients(url), await clients(url)
    await a.join(name="A")
    await b.join(name="B")
    await a.ws.close()
    await b.recv_type("peer_left")
    await c.join(name="C")
    assert (await b.recv_type("snapshot_request"))["type"] == "snapshot_request"


async def test_peer_left_is_broadcast(server, clients):
    a, b, _pa, pb = await pair(server, clients)
    await b.ws.close()
    assert await a.recv_type("peer_left") == {"type": "peer_left", "peer": pb}


class TestSecret:
    async def test_matching_secret_joins(self, server, clients):
        url = await server()
        a, b = await clients(url), await clients(url)
        await a.join(secret="s3cret-value")
        assert (await b.join(name="Bob", secret="s3cret-value"))["type"] == "welcome"

    async def test_wrong_or_missing_secret_is_refused(self, server, clients):
        url = await server()
        a, b, c = await clients(url), await clients(url), await clients(url)
        await a.join(secret="s3cret-value")
        assert (await b.join(name="Bob", secret="nope"))["code"] == "bad_secret"
        assert (await c.join(name="Carl"))["code"] == "bad_secret"
        with pytest.raises(ConnectionClosed):
            await b.recv()


class TestValidation:
    @pytest.mark.parametrize(
        "join",
        [
            {"room": "ab", "name": "x"},  # too short
            {"room": "ROOM-1", "name": "x"},  # bad char
            {"room": "ROOM1", "name": ""},
            {"room": "ROOM1", "name": "x" * 33},
            {"room": "ROOM1", "name": "bad\nname"},
            {"room": "ROOM1", "name": "x", "secret": 5},
            {"room": "ROOM1", "name": "x", "secret": "s" * 200},
            {"room": 1234, "name": "x"},
            {"room": "ROOM1", "name": "x", "proto": 99},
        ],
    )
    async def test_bad_join_is_refused(self, server, clients, join):
        c = await clients(await server())
        join.setdefault("proto", 1)
        await c.send(type="join", **join)
        assert (await c.recv())["type"] == "error"

    async def test_change_before_join_is_refused(self, server, clients):
        c = await clients(await server())
        await c.send(type="change", kind="stop", index=None, value=0)
        assert (await c.recv())["code"] == "not_joined"

    @pytest.mark.parametrize(
        "change",
        [
            {"kind": "mix_vol", "index": 1, "value": 99999},
            {"kind": "mix_vol", "index": 1, "value": 1.5},
            {"kind": "mix_vol", "index": True, "value": 1},
            {"kind": "snapshot_req", "index": None, "value": 0},  # FL-only kind
            {"kind": "nope", "index": None, "value": 0},
            {"kind": "mix_vol", "index": 1},
        ],
    )
    async def test_invalid_change_is_rejected_not_broadcast(self, server, clients, change):
        a, b, _pa, _pb = await pair(server, clients)
        await a.send(type="change", **change)
        assert (await a.recv_type("error"))["code"] == "bad_change"
        await b.nothing()

    @pytest.mark.parametrize("raw", ["not json", "[1,2]", '{"type": 3}', '{"type": "zzz"}'])
    async def test_garbage_gets_error(self, server, clients, raw):
        c = await clients(await server())
        await c.ws.send(raw)
        assert (await c.recv())["code"] == "bad_message"

    async def test_oversized_message_closes_connection(self, server, clients):
        c = await clients(await server(max_message_bytes=1024))
        await c.ws.send("x" * 5000)
        with pytest.raises(ConnectionClosed):
            await c.recv()

    async def test_room_full(self, server, clients):
        url = await server(max_peers_per_room=2)
        a, b, c = await clients(url), await clients(url), await clients(url)
        await a.join()
        await b.join(name="B")
        assert (await c.join(name="C"))["code"] == "room_full"

    async def test_join_twice_is_refused(self, server, clients):
        c = await clients(await server())
        await c.join()
        assert (await c.join())["code"] == "already_joined"


async def test_rate_limit(server, clients):
    a, b, _pa, _pb = await pair(server, clients, rate_per_second=20, rate_burst=5)
    for v in range(30):
        await a.send(type="change", kind="mix_vol", index=1, value=v)
    got = []
    try:
        while True:
            got.append(await asyncio.wait_for(b.recv_type("change"), 0.3))
    except asyncio.TimeoutError:
        pass
    assert 5 <= len(got) < 30
    assert (await a.recv_type("error"))["code"] == "rate_limited"


async def test_join_timeout_closes_idle_connection(server, clients):
    c = await clients(await server(join_timeout=0.2))
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(c.ws.recv(), 2)


async def test_empty_room_is_deleted(server, clients):
    url = await server()
    a = await clients(url)
    await a.join(secret="first-secret")
    await a.ws.close()
    await asyncio.sleep(0.05)
    b = await clients(url)
    # The room was recreated, so a different secret is accepted.
    assert (await b.join(secret="other-secret"))["type"] == "welcome"
