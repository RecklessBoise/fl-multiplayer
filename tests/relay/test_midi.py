import asyncio

import mido
import pytest
from flmp_relay import midi as midi_mod
from flmp_relay.midi import (
    MidiPortError,
    MidoMidiIO,
    QueueMidiIO,
    find_port,
    frame_from_message,
    message_from_frame,
)


class TestFindPort:
    NAMES = ("IAC Driver FLMP In", "IAC Driver FLMP Out", "Other Synth")

    def test_exact_name(self):
        assert find_port(list(self.NAMES), "IAC Driver FLMP Out") == "IAC Driver FLMP Out"

    def test_suffix_match_is_case_insensitive(self):
        assert find_port(list(self.NAMES), "flmp in") == "IAC Driver FLMP In"

    def test_does_not_confuse_in_and_out(self):
        assert find_port(["IAC Driver FLMP Out"], "FLMP In") is None

    def test_missing(self):
        assert find_port(list(self.NAMES), "Nope") is None


class TestSysexConversion:
    def test_roundtrip(self):
        frame = bytes([0xF0, 0x7D, 0x01, 0x02, 0xF7])
        msg = message_from_frame(frame)
        assert msg.type == "sysex"
        assert list(msg.data) == [0x7D, 0x01, 0x02]
        assert frame_from_message(msg) == frame

    def test_non_sysex_is_ignored(self):
        assert frame_from_message(mido.Message("note_on", note=60)) is None

    def test_bad_frame_rejected(self):
        with pytest.raises(ValueError):
            message_from_frame(b"\x90\x3c\x40")


async def test_queue_midi_io():
    io = QueueMidiIO()
    io.fl_sends(b"\xf0\x7d\x01\x02\xf7")
    assert await io.receive() == b"\xf0\x7d\x01\x02\xf7"
    io.send(b"\xf0\x7d\x01\x02\xf7")
    assert await io.fl_receives() == b"\xf0\x7d\x01\x02\xf7"
    await io.fl_receives_nothing(0.05)
    io.send(b"\xf0\x7d\x01\x02\xf7")
    assert io.drain() == [b"\xf0\x7d\x01\x02\xf7"]


class FakeInput:
    def __init__(self, name, callback):
        self.name, self.callback, self.closed = name, callback, False

    def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self, name):
        self.name, self.sent, self.closed = name, [], False

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_mido(monkeypatch):
    opened = {}

    def open_input(name, callback=None, virtual=False):
        opened["in"] = FakeInput(name, callback)
        return opened["in"]

    def open_output(name, virtual=False):
        opened["out"] = FakeOutput(name)
        return opened["out"]

    monkeypatch.setattr(midi_mod.mido, "get_input_names", lambda: ["IAC Driver FLMP Out"])
    monkeypatch.setattr(midi_mod.mido, "get_output_names", lambda: ["IAC Driver FLMP In"])
    monkeypatch.setattr(midi_mod.mido, "open_input", open_input)
    monkeypatch.setattr(midi_mod.mido, "open_output", open_output)
    return opened


async def test_mido_io_bridges_callback_thread_to_asyncio(fake_mido):
    io = MidoMidiIO.open("FLMP Out", "FLMP In", asyncio.get_running_loop())
    assert fake_mido["in"].name == "IAC Driver FLMP Out"
    assert fake_mido["out"].name == "IAC Driver FLMP In"
    fake_mido["in"].callback(mido.Message("sysex", data=[0x7D, 1, 2]))
    fake_mido["in"].callback(mido.Message("clock"))  # ignored
    assert await asyncio.wait_for(io.receive(), 1) == bytes([0xF0, 0x7D, 1, 2, 0xF7])
    io.send(bytes([0xF0, 0x7D, 1, 2, 0xF7]))
    assert list(fake_mido["out"].sent[0].data) == [0x7D, 1, 2]
    io.close()
    assert fake_mido["in"].closed and fake_mido["out"].closed


def test_mido_io_reports_missing_ports(fake_mido, monkeypatch):
    monkeypatch.setattr(midi_mod.mido, "get_input_names", list)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(MidiPortError, match="FLMP Out"):
            MidoMidiIO.open("FLMP Out", "FLMP In", loop)
    finally:
        loop.close()


def test_mido_io_virtual_ports_need_no_iac(fake_mido, monkeypatch):
    # No IAC bus at all: the relay creates its own ports, named as FL will see them.
    monkeypatch.setattr(midi_mod.mido, "get_input_names", list)
    monkeypatch.setattr(midi_mod.mido, "get_output_names", list)
    calls = []

    def open_input(name, callback=None, virtual=False):
        calls.append(("in", name, virtual))
        fake_mido["in"] = FakeInput(name, callback)
        return fake_mido["in"]

    def open_output(name, virtual=False):
        calls.append(("out", name, virtual))
        fake_mido["out"] = FakeOutput(name)
        return fake_mido["out"]

    monkeypatch.setattr(midi_mod.mido, "open_input", open_input)
    monkeypatch.setattr(midi_mod.mido, "open_output", open_output)
    loop = asyncio.new_event_loop()
    try:
        io = MidoMidiIO.open("FLMP Out", "FLMP In", loop, virtual=True)
    finally:
        loop.close()
    assert calls == [("in", "FLMP Out", True), ("out", "FLMP In", True)]
    io.close()


def _no_ports(monkeypatch):
    monkeypatch.setattr(midi_mod.mido, "get_input_names", list)
    monkeypatch.setattr(midi_mod.mido, "get_output_names", list)


def test_virtual_ports_unsupported_give_a_clear_error(fake_mido, monkeypatch):
    _no_ports(monkeypatch)

    def no_virtual(name, callback=None, virtual=False):
        raise NotImplementedError("virtual ports are not supported")

    monkeypatch.setattr(midi_mod.mido, "open_input", no_virtual)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(MidiPortError, match="--virtual"):
            MidoMidiIO.open("FLMP Out", "FLMP In", loop, virtual=True)
    finally:
        loop.close()


def test_virtual_output_failure_closes_the_input(fake_mido, monkeypatch):
    _no_ports(monkeypatch)

    def broken(name, virtual=False):
        raise RuntimeError("backend error")

    monkeypatch.setattr(midi_mod.mido, "open_output", broken)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(MidiPortError):
            MidoMidiIO.open("FLMP Out", "FLMP In", loop, virtual=True)
    finally:
        loop.close()
    assert fake_mido["in"].closed


def test_virtual_refuses_a_name_already_taken(fake_mido, monkeypatch):
    # Another program already offers "FLMP In": FL could end up talking to it.
    monkeypatch.setattr(midi_mod.mido, "get_input_names", list)
    monkeypatch.setattr(midi_mod.mido, "get_output_names", lambda: ["FLMP In"])
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(MidiPortError, match="existe déjà"):
            MidoMidiIO.open("FLMP Out", "FLMP In", loop, virtual=True)
    finally:
        loop.close()
    assert "in" not in fake_mido
