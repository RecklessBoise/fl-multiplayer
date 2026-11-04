"""Loads the real device_FLMultiplayer.py with fake FL Studio modules."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from flmp_core.messages import Change, decode, encode

SCRIPT = Path(__file__).resolve().parents[2] / "fl_script" / "device_FLMultiplayer.py"


class FakeFLStudio:
    def __init__(self, api=19):
        self.api = api
        self.playing = False
        self.pos = 0
        self.tempo = 120.0  # BPM, as mixer.getCurrentTempo() returns it
        self.out = []
        self.assigned = True
        self.rec_events = []

    def modules(self):
        fl = self
        transport = types.ModuleType("transport")
        transport.isPlaying = lambda: fl.playing
        transport.start = lambda: setattr(fl, "playing", not fl.playing)  # play/pause toggle
        transport.stop = lambda: setattr(fl, "playing", False)

        def get_song_pos(mode=-1):
            assert mode == 2
            return fl.pos

        def set_song_pos(position, mode=-1):
            assert mode == 2
            fl.pos = position

        transport.getSongPos = get_song_pos
        transport.setSongPos = set_song_pos

        mixer = types.ModuleType("mixer")
        mixer.getCurrentTempo = lambda asInt=False: fl.tempo

        midi = types.ModuleType("midi")
        midi.REC_Tempo = 0x1F0
        midi.REC_Control = 0x0F
        midi.REC_UpdateControl = 0x10

        general = types.ModuleType("general")
        general.getVersion = lambda: fl.api

        def process_rec_event(event_id, value, flags):
            fl.rec_events.append((event_id, value, flags))
            if event_id == midi.REC_Tempo:
                fl.tempo = value / 1000.0
            return value

        general.processRECEvent = process_rec_event

        device = types.ModuleType("device")
        device.isAssigned = lambda: fl.assigned
        device.midiOutSysex = lambda message: fl.out.append(decode(message))
        return {
            "transport": transport,
            "mixer": mixer,
            "midi": midi,
            "general": general,
            "device": device,
        }


class Msg:
    def __init__(self, change):
        self.sysex = encode(change)
        self.handled = False


@pytest.fixture
def load(monkeypatch):
    def _load(fl):
        for name, mod in fl.modules().items():
            monkeypatch.setitem(sys.modules, name, mod)
        spec = importlib.util.spec_from_file_location("device_FLMultiplayer", SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.OnInit()
        return script

    return _load


def test_header_names_the_controller_type():
    first = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first == "# name=FL Multiplayer"


def test_local_play_and_tempo_reach_midi_out(load):
    fl = FakeFLStudio()
    script = load(fl)
    fl.pos = 384
    fl.playing = True
    fl.tempo = 128.5
    script.OnIdle()
    assert fl.out == [Change("play", None, 384), Change("tempo", None, 128500)]


def test_remote_play_sets_position_and_starts_once(load):
    fl = FakeFLStudio()
    script = load(fl)
    msg = Msg(Change("play", None, 1920))
    script.OnSysEx(msg)
    assert msg.handled is True
    assert fl.playing is True and fl.pos == 1920
    script.OnSysEx(Msg(Change("play", None, 0)))  # already playing: must not toggle to pause
    assert fl.playing is True
    script.OnIdle()
    assert fl.out == []


def test_remote_stop(load):
    fl = FakeFLStudio()
    fl.playing = True
    script = load(fl)
    script.OnSysEx(Msg(Change("stop", None, 0)))
    assert fl.playing is False
    script.OnIdle()
    assert fl.out == []


def test_remote_tempo_uses_rec_event(load):
    fl = FakeFLStudio()
    script = load(fl)
    script.OnSysEx(Msg(Change("tempo", None, 90500)))
    assert fl.rec_events == [(0x1F0, 90500, 0x0F | 0x10)]
    script.OnIdle()
    assert fl.out == []


def test_tempo_reported_as_thousandths_is_accepted(load):
    # If getCurrentTempo() returns BPM*1000 on some versions, read it correctly.
    fl = FakeFLStudio()
    fl.tempo = 140000
    script = load(fl)
    fl.tempo = 141000
    script.OnIdle()
    assert fl.out == [Change("tempo", None, 141000)]


def test_old_api_without_song_position(load):
    fl = FakeFLStudio(api=2)
    script = load(fl)
    fl.pos = 999
    fl.playing = True
    script.OnIdle()
    assert fl.out == [Change("play", None, 0)]
    fl.playing = False
    script.OnIdle()
    script.OnSysEx(Msg(Change("play", None, 500)))
    assert fl.pos == 999  # position untouched without the API


def test_foreign_sysex_is_left_unhandled(load):
    fl = FakeFLStudio()
    script = load(fl)
    msg = Msg(Change("stop", None, 0))
    msg.sysex = b"\xf0\x7e\x7f\x06\x01\xf7"
    script.OnSysEx(msg)
    assert msg.handled is False


def test_nothing_is_sent_when_no_output_port(load):
    fl = FakeFLStudio()
    fl.assigned = False
    script = load(fl)
    fl.playing = True
    script.OnIdle()
    assert fl.out == []


def test_deinit_is_safe(load):
    script = load(FakeFLStudio())
    script.OnDeInit()
    script.OnIdle()  # no crash after deinit


def test_callbacks_never_raise_into_fl(load):
    fl = FakeFLStudio()
    script = load(fl)
    msg = Msg(Change("stop", None, 0))
    msg.sysex = None
    script.OnSysEx(msg)  # must not raise
    assert msg.handled is False


def test_init_failure_does_not_raise(load, monkeypatch):
    fl = FakeFLStudio()
    script = load(fl)

    def broken():
        raise RuntimeError("no version")

    monkeypatch.setattr(sys.modules["general"], "getVersion", broken)
    script.OnInit()  # must not raise
    script.OnIdle()
