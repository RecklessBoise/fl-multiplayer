import asyncio
import contextlib

import pytest
from flmp_core.messages import Change, decode, encode
from flmp_relay.bridge import FatalJoinError, Relay, check_server_url
from flmp_relay.midi import QueueMidiIO
from flmp_server.app import Settings, start_server


def frame(kind, index, value):
    return encode(Change(kind, index, value))


@contextlib.asynccontextmanager
async def running(relay):
    task = asyncio.create_task(relay.run())
    try:
        await asyncio.wait_for(relay.connected.wait(), 2)
        yield relay
    finally:
        await relay.stop()
        with contextlib.suppress(asyncio.CancelledError, FatalJoinError):
            await task


async def joined_observer(clients, url, name="Obs"):
    obs = await clients(url)
    await obs.join(name=name)
    return obs


async def test_fl_change_goes_to_server(server, clients):
    url = await server()
    obs = await joined_observer(clients, url)
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        io.fl_sends(frame("mix_vol", 2, 1234))
        msg = await obs.recv_type("change")
        assert (msg["kind"], msg["index"], msg["value"]) == ("mix_vol", 2, 1234)


async def test_remote_change_goes_to_fl(server, clients):
    url = await server()
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        obs = await joined_observer(clients, url)
        await io.fl_receives()  # snapshot_req for the observer
        await obs.send(type="change", kind="ch_mute", index=4, value=1)
        assert decode(await io.fl_receives()) == Change("ch_mute", 4, 1)


async def test_own_echo_is_not_sent_back_to_fl(server, clients):
    url = await server()
    obs = await joined_observer(clients, url)
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        io.fl_sends(frame("mix_mute", 1, 1))
        await obs.recv_type("change")
        await io.fl_receives_nothing()


async def test_non_flmp_sysex_from_fl_is_ignored(server, clients):
    url = await server()
    obs = await joined_observer(clients, url)
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        io.fl_sends(b"\xf0\x7e\x01\x02\xf7")
        io.fl_sends(b"garbage")
        io.fl_sends(frame("stop", None, 0))
        msg = await obs.recv_type("change")
        assert msg["kind"] == "stop"


async def test_fader_burst_is_throttled_but_final_value_arrives(server, clients):
    url = await server()
    obs = await joined_observer(clients, url)
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice", max_per_second=10)):
        for v in range(50):
            io.fl_sends(frame("mix_vol", 1, v))
        got = []
        with contextlib.suppress(asyncio.TimeoutError):
            while True:
                got.append(await asyncio.wait_for(obs.recv_type("change"), 0.5))
        assert len(got) < 10
        assert got[-1]["value"] == 49


async def test_snapshot_request_is_answered_from_fl(server, clients):
    url = await server()
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        newcomer = await clients(url)
        await newcomer.join(name="Bob")
        assert decode(await io.fl_receives()) == Change("snapshot_req", None, 0)
        io.fl_sends(frame("snapshot_begin", None, 0))
        io.fl_sends(frame("mix_vol", 1, 77))
        io.fl_sends(frame("tempo", None, 99000))
        io.fl_sends(frame("snapshot_end", None, 0))
        snap = await newcomer.recv_type("snapshot")
        assert snap["changes"] == [["mix_vol", 1, 77], ["tempo", None, 99000]]


async def test_incoming_snapshot_is_replayed_to_fl(server, clients):
    url = await server()
    first = await clients(url)
    await first.join(name="First")
    io = QueueMidiIO()
    async with running(Relay(io, url, "ROOM1", "Alice")):
        req = await first.recv_type("snapshot_request")
        await first.send(type="snapshot", changes=[["mix_pan", 0, 8192]], **{"for": req["for"]})
        got = [decode(await io.fl_receives()) for _ in range(3)]
        assert got == [
            Change("snapshot_begin", None, 0),
            Change("mix_pan", 0, 8192),
            Change("snapshot_end", None, 0),
        ]


async def test_conflict_converges_to_last_writer(server, clients):
    """Both sides write the same key at once; both must end on the same value."""
    url = await server()
    io_a, io_b = QueueMidiIO(), QueueMidiIO()
    async with running(Relay(io_a, url, "ROOM1", "A")), running(Relay(io_b, url, "ROOM1", "B")):
        await io_a.fl_receives()  # snapshot_req sent to A when B joined
        io_a.fl_sends(frame("mix_mute", 3, 1))
        io_b.fl_sends(frame("mix_mute", 3, 0))
        await asyncio.sleep(0.3)
        a_seen = [decode(f) for f in io_a.drain()]
        b_seen = [decode(f) for f in io_b.drain()]
        final_a = a_seen[-1].value if a_seen else 1
        final_b = b_seen[-1].value if b_seen else 0
        assert final_a == final_b
        assert len(a_seen) + len(b_seen) == 1  # only the earlier writer is overridden


async def test_reconnects_after_server_restart(clients, unused_tcp_port):
    srv, url = await start_server(Settings(), "127.0.0.1", unused_tcp_port)
    io = QueueMidiIO()
    relay = Relay(io, url, "ROOM1", "Alice", backoff=(0.05,))
    async with running(relay):
        srv.close()
        await srv.wait_closed()
        relay.connected.clear()
        srv, _ = await start_server(Settings(), "127.0.0.1", unused_tcp_port)
        await asyncio.wait_for(relay.connected.wait(), 3)
        obs = await joined_observer(clients, url)
        io.fl_sends(frame("stop", None, 0))
        assert (await obs.recv_type("change"))["kind"] == "stop"
    srv.close()
    await srv.wait_closed()


async def test_bad_secret_is_fatal(server, clients):
    url = await server()
    first = await clients(url)
    await first.join(secret="right-one")
    relay = Relay(QueueMidiIO(), url, "ROOM1", "Alice", secret="wrong-one")
    with pytest.raises(FatalJoinError, match="bad_secret"):
        await asyncio.wait_for(relay.run(), 3)


class TestServerUrl:
    @pytest.mark.parametrize(
        "url",
        ["wss://flmp.example.org", "ws://localhost:8765", "ws://127.0.0.1:1", "ws://[::1]:9"],
    )
    def test_accepted(self, url):
        check_server_url(url)

    @pytest.mark.parametrize("url", ["ws://flmp.example.org", "http://x", "ftp://y", "nope"])
    def test_rejected(self, url):
        with pytest.raises(ValueError):
            check_server_url(url)

    def test_insecure_override(self):
        check_server_url("ws://192.168.1.10:8765", allow_insecure=True)
