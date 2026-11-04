"""End-to-end simulation without FL Studio.

Two scripted fake FL instances (each with the real StateTracker, behaving like
the FL script will) talk SysEx to two real relays, which talk to a real
server on localhost.
"""

import asyncio
import contextlib

from flmp_core.messages import NETWORK_KINDS, Change, decode, encode
from flmp_core.state import StateTracker
from flmp_relay.bridge import Relay
from flmp_relay.midi import QueueMidiIO
from flmp_server.app import Settings, start_server

TICK = 0.01  # FL calls OnIdle roughly every 10-20 ms


def project(overrides=None):
    state = {("tempo", None): 120000}
    for i in range(4):
        state[("mix_vol", i)] = 13107
        state[("mix_pan", i)] = 8192
        state[("mix_mute", i)] = 0
        state[("ch_vol", i)] = 10000
    state.update(overrides or {})
    return state


class FakeFL:
    """Plays the FL script: diffs its state on every tick, applies remote values."""

    def __init__(self, name, state, rounding=1):
        self.name = name
        self.state = dict(state)
        self.io = QueueMidiIO()
        self.tracker = StateTracker(tolerance={"mix_vol": 2, "mix_pan": 2, "ch_vol": 2})
        self.rounding = rounding  # FL may round values it is given
        self.sent = []  # changes this FL emitted
        self.applied = []  # remote changes applied
        self._task = None

    def start(self):
        self.tracker.diff(self.state)
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    def user_sets(self, kind, index, value):
        self.state[(kind, index)] = value

    async def _loop(self):
        while True:
            for frame in self.io.drain():
                self._on_sysex(decode(frame))
            for change in self.tracker.diff(self.state):
                self.sent.append(change)
                self.io.fl_sends(encode(change))
            await asyncio.sleep(TICK)

    def _on_sysex(self, change):
        if change.kind == "snapshot_req":
            self.io.fl_sends(encode(Change("snapshot_begin", None, 0)))
            for c in self.tracker.snapshot():
                if c.kind in NETWORK_KINDS:
                    self.io.fl_sends(encode(c))
            self.io.fl_sends(encode(Change("snapshot_end", None, 0)))
        elif change.kind in ("snapshot_begin", "snapshot_end"):
            pass
        elif self.tracker.knows(change.key):
            value = change.value
            if change.kind == "mix_vol" and value < 16383:
                value = min(16383, value + self.rounding)  # simulated FL rounding
            self.state[change.key] = value
            self.tracker.apply_remote(change)
            self.applied.append(change)


@contextlib.asynccontextmanager
async def session(*fls, port=0, **relay_kw):
    srv, url = await start_server(Settings(), "127.0.0.1", port)
    ctx = {"server": srv, "url": url, "relays": []}
    tasks = []
    try:
        for fl in fls:
            fl.start()
            relay = Relay(fl.io, url, "JAM42", fl.name, backoff=(0.05,), **relay_kw)
            ctx["relays"].append(relay)
            tasks.append(asyncio.create_task(relay.run()))
            await asyncio.wait_for(relay.connected.wait(), 2)
            await asyncio.sleep(0.15)  # let the snapshot (if any) land
        yield ctx
    finally:
        for relay in ctx["relays"]:
            await relay.stop()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for fl in fls:
            await fl.stop()
        ctx["server"].close()
        await ctx["server"].wait_closed()


async def settle(seconds=0.3):
    await asyncio.sleep(seconds)


def same(a, b, tolerance=2):
    return all(abs(b.state[key] - value) <= tolerance for key, value in a.state.items())


async def test_change_on_a_reaches_b_without_echo():
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b):
        a.user_sets("mix_mute", 2, 1)
        a.user_sets("tempo", None, 128500)
        await settle()
        assert b.state[("mix_mute", 2)] == 1
        assert b.state[("tempo", None)] == 128500
        assert a.applied == []  # Alice never gets her own change back
        assert b.sent == []  # Bob applying it did not re-emit it


async def test_newcomer_receives_snapshot_and_does_not_echo_it():
    a = FakeFL("Alice", project({("mix_vol", 1): 3000, ("tempo", None): 95000}))
    b = FakeFL("Bob", project())
    async with session(a, b):
        await settle()
        assert b.state[("tempo", None)] == 95000
        assert abs(b.state[("mix_vol", 1)] - 3000) <= 2
        assert b.sent == []
        assert a.state[("tempo", None)] == 95000  # Alice was not overwritten


async def test_fader_drag_is_throttled_and_lands_on_final_value():
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b):
        for v in range(0, 16000, 80):  # 200 steps, one per tick (~2 s drag)
            a.user_sets("mix_vol", 0, v)
            await asyncio.sleep(TICK)
        await settle(0.4)
        assert abs(b.state[("mix_vol", 0)] - 15920) <= 2
        assert len(b.applied) <= 30 * 3 + 5  # ~30 msg/s over ~2-3 s
        assert b.sent == []


async def test_both_edit_different_controls_at_once():
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b):
        a.user_sets("mix_pan", 1, 0)
        b.user_sets("ch_vol", 3, 500)
        await settle()
        assert b.state[("mix_pan", 1)] == 0
        assert a.state[("ch_vol", 3)] == 500
        assert same(a, b)


async def test_conflict_on_same_control_converges():
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b):
        a.user_sets("mix_mute", 0, 1)
        b.user_sets("mix_mute", 0, 0)
        b.user_sets("tempo", None, 140000)
        a.user_sets("tempo", None, 100000)
        await settle(0.5)
        assert a.state[("mix_mute", 0)] == b.state[("mix_mute", 0)]
        assert a.state[("tempo", None)] == b.state[("tempo", None)]


async def test_system_goes_quiet_no_ping_pong():
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b):
        for i in range(4):
            a.user_sets("mix_vol", i, 2000 + i)
            b.user_sets("mix_pan", i, 100 * i)
        await settle(0.5)
        before = len(a.sent) + len(b.sent) + len(a.applied) + len(b.applied)
        await settle(0.5)
        after = len(a.sent) + len(b.sent) + len(a.applied) + len(b.applied)
        assert before == after
        assert same(a, b)


async def test_sync_resumes_after_server_restart(unused_tcp_port):
    a, b = FakeFL("Alice", project()), FakeFL("Bob", project())
    async with session(a, b, port=unused_tcp_port) as ctx:
        ctx["server"].close()
        await ctx["server"].wait_closed()
        for r in ctx["relays"]:
            r.connected.clear()
        ctx["server"], _ = await start_server(Settings(), "127.0.0.1", unused_tcp_port)
        for r in ctx["relays"]:
            await asyncio.wait_for(r.connected.wait(), 3)
        await settle()
        a.user_sets("mix_mute", 3, 1)
        await settle()
        assert b.state[("mix_mute", 3)] == 1
