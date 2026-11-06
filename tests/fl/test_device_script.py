"""Loads the real device_FLMultiplayer.py with fake FL Studio modules."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from flmp_core.codec import pan_to_u14, unit_to_u14
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
        # Mixer: 0 = master, 1..3 = inserts, 4 = "current" (must be ignored).
        self.tracks = [{"vol": 0.8, "pan": 0.0, "mute": False, "solo": False} for _ in range(5)]
        # Channel rack, by global index. The visible group shows them in another
        # order, so a script using group indexes would touch the wrong channel.
        self.chans = [{"vol": 0.78125, "pan": 0.0, "mute": False} for _ in range(3)]
        self.group = [2, 0]  # group index -> global index
        self.global_calls = []
        self.hints = []
        self.last_insert = 3

    def solo(self, index, on):
        """FL 2026, as observed: solo mutes every other insert; unsolo (even of a
        track that is not solo) unmutes every track, whatever was muted before."""
        for i, t in enumerate(self.tracks):
            t["solo"] = on and i == index
            t["mute"] = on and i not in (0, index)

    def mute(self, index, on):
        """FL 2026, as observed: changing a mute while a track is solo ends the solo."""
        for t in self.tracks:
            t["solo"] = False
        self.tracks[index]["mute"] = on

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
        mixer.trackCount = lambda: len(fl.tracks)
        # FL 2026 grows the mixer as inserts are added; "current" has a special index.
        mixer.getTrackInfo = lambda mode: {0: 0, 1: 1, 2: fl.last_insert, 3: 501}[mode]
        mixer.getTrackVolume = lambda index, mode=0: fl.tracks[index]["vol"]
        mixer.getTrackPan = lambda index: fl.tracks[index]["pan"]
        mixer.isTrackMuted = lambda index: fl.tracks[index]["mute"]
        mixer.isTrackSolo = lambda index: fl.tracks[index]["solo"]

        def set_track(field):
            def setter(index, value, pickupMode=0):
                # FL stores faders with a finite resolution: what it reports back
                # differs slightly from what was written.
                fl.tracks[index][field] = round(value * 12800) / 12800

            return setter

        mixer.setTrackVolume = set_track("vol")
        mixer.setTrackPan = set_track("pan")

        def mute_track(index, value=-1):
            assert value in (0, 1)  # never toggle blindly
            fl.mute(index, bool(value))

        def solo_track(index, value=-1, mode=-1):
            assert value in (0, 1)
            fl.solo(index, bool(value))

        mixer.muteTrack = mute_track
        mixer.soloTrack = solo_track

        channels = types.ModuleType("channels")

        def chan(index, use_global):
            fl.global_calls.append(use_global)
            return fl.chans[index if use_global else fl.group[index]]

        channels.channelCount = lambda globalCount=False: (
            len(fl.chans) if globalCount else len(fl.group)
        )
        channels.getChannelVolume = lambda index, mode=False, useGlobalIndex=False: chan(
            index, useGlobalIndex
        )["vol"]
        channels.getChannelPan = lambda index, useGlobalIndex=False: chan(index, useGlobalIndex)[
            "pan"
        ]
        channels.isChannelMuted = lambda index, useGlobalIndex=False: chan(index, useGlobalIndex)[
            "mute"
        ]

        def set_chan(field):
            def setter(index, value, pickupMode=0, useGlobalIndex=False):
                chan(index, useGlobalIndex)[field] = value

            return setter

        channels.setChannelVolume = set_chan("vol")
        channels.setChannelPan = set_chan("pan")

        def mute_channel(index, value=-1, useGlobalIndex=False):
            assert value in (0, 1)
            chan(index, useGlobalIndex)["mute"] = bool(value)

        channels.muteChannel = mute_channel

        midi = types.ModuleType("midi")
        midi.REC_Tempo = 0x1F0
        midi.REC_Control = 0x0F
        midi.REC_UpdateControl = 0x10
        midi.PIM_None = 0
        midi.TN_Master, midi.TN_FirstIns, midi.TN_LastIns, midi.TN_Sel = 0, 1, 2, 3

        general = types.ModuleType("general")
        general.getVersion = lambda: fl.api

        def process_rec_event(event_id, value, flags):
            fl.rec_events.append((event_id, value, flags))
            if event_id == midi.REC_Tempo:
                fl.tempo = value / 1000.0
            return value

        general.processRECEvent = process_rec_event

        ui = types.ModuleType("ui")
        ui.setHintMsg = fl.hints.append

        device = types.ModuleType("device")
        device.isAssigned = lambda: fl.assigned
        device.midiOutSysex = lambda message: fl.out.append(decode(message))
        return {
            "transport": transport,
            "mixer": mixer,
            "channels": channels,
            "midi": midi,
            "general": general,
            "device": device,
            "ui": ui,
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


def test_transport_stays_local(load):
    fl = FakeFLStudio()
    script = load(fl)
    fl.pos = 384
    fl.playing = True
    fl.tempo = 128.5
    script.OnIdle()
    assert fl.out == [Change("tempo", None, 128500)]  # tempo yes, play no
    script.OnSysEx(Msg(Change("stop", None, 0)))
    script.OnSysEx(Msg(Change("play", None, 1920)))
    assert fl.playing is True and fl.pos == 384  # remote transport ignored


def test_script_never_touches_the_transport():
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "transport" not in names


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


# -- step 5: mixer and channel rack ------------------------------------------------


def test_local_mixer_changes_are_sent(load):
    fl = FakeFLStudio()
    script = load(fl)
    fl.tracks[1]["vol"] = 0.5
    fl.tracks[2]["pan"] = -0.25
    fl.tracks[3]["mute"] = True
    script.OnIdle()
    assert set(fl.out) == {
        Change("mix_vol", 1, unit_to_u14(0.5)),
        Change("mix_pan", 2, pan_to_u14(-0.25)),
        Change("mix_mute", 3, 1),
    }


def test_current_track_is_ignored(load):
    fl = FakeFLStudio()
    script = load(fl)
    fl.tracks[4]["vol"] = 0.1  # the "current" track mirrors whatever is selected
    script.OnIdle()
    assert fl.out == []
    script.OnSysEx(Msg(Change("mix_vol", 4, 0)))
    assert fl.tracks[4]["vol"] == 0.1


def test_remote_mixer_changes_apply_without_echo(load):
    fl = FakeFLStudio()
    script = load(fl)
    for change in (
        Change("mix_vol", 1, unit_to_u14(0.25)),
        Change("mix_pan", 2, pan_to_u14(0.5)),
        Change("mix_mute", 3, 1),
        Change("mix_solo", 1, 1),
    ):
        msg = Msg(change)
        script.OnSysEx(msg)
        assert msg.handled is True
    assert abs(fl.tracks[1]["vol"] - 0.25) < 1e-3
    assert abs(fl.tracks[2]["pan"] - 0.5) < 1e-3
    assert fl.tracks[3]["mute"] is True and fl.tracks[1]["solo"] is True
    script.OnIdle()
    script.OnIdle()
    assert fl.out == []


def test_local_channel_changes_use_global_index(load):
    fl = FakeFLStudio(api=33)
    script = load(fl)
    fl.chans[2]["vol"] = 0.5
    fl.chans[1]["pan"] = 1.0
    fl.chans[0]["mute"] = True
    script.OnIdle()
    assert set(fl.out) == {
        Change("ch_vol", 2, unit_to_u14(0.5)),
        Change("ch_pan", 1, pan_to_u14(1.0)),
        Change("ch_mute", 0, 1),
    }
    assert all(fl.global_calls)


def test_remote_channel_changes_apply_to_the_right_channel(load):
    fl = FakeFLStudio(api=33)
    script = load(fl)
    script.OnSysEx(Msg(Change("ch_vol", 0, unit_to_u14(0.25))))
    script.OnSysEx(Msg(Change("ch_mute", 2, 1)))
    assert abs(fl.chans[0]["vol"] - 0.25) < 1e-3
    assert fl.chans[2]["mute"] is True
    script.OnIdle()
    assert fl.out == []


def test_old_api_does_not_sync_channels(load):
    # Before API 33 channel indexes follow the visible group: switching groups
    # would move values to the wrong channel, so channels are left alone.
    fl = FakeFLStudio(api=32)
    script = load(fl)
    fl.chans[2]["vol"] = 0.5
    script.OnIdle()
    script.OnSysEx(Msg(Change("ch_mute", 0, 1)))
    assert fl.out == []
    assert not any(c["mute"] for c in fl.chans)


def test_unknown_indexes_are_ignored(load):
    fl = FakeFLStudio()
    script = load(fl)
    script.OnSysEx(Msg(Change("mix_vol", 99, 0)))
    script.OnSysEx(Msg(Change("ch_vol", 99, 0)))
    script.OnIdle()
    assert fl.out == []


def test_snapshot_contains_mixer_and_channels(load):
    fl = FakeFLStudio(api=33)
    script = load(fl)
    script.OnSysEx(Msg(Change("snapshot_req", None, 0)))
    kinds = {c.kind for c in fl.out}
    assert {"tempo", "mix_vol", "mix_pan", "mix_mute", "mix_solo", "ch_vol", "ch_pan"} <= kinds
    assert not any(c.kind.startswith("mix_") and c.index == 4 for c in fl.out)
    assert len([c for c in fl.out if c.kind == "ch_mute"]) == 3


def test_script_imports_only_fl_modules_and_core():
    import ast

    allowed = {"transport", "mixer", "channels", "patterns", "device", "general", "midi", "ui"}
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 7))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {a.name for a in node.names} <= allowed
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] == "flmp_core"


def test_local_solo_sends_only_the_solo(load):
    fl = FakeFLStudio()
    fl.tracks[2]["mute"] = True  # a mute the user set before soloing
    script = load(fl)
    fl.solo(1, True)  # FL mutes every other track
    script.OnIdle()
    assert fl.out == [Change("mix_solo", 1, 1)]
    fl.out.clear()
    fl.solo(1, False)  # FL unmutes everything, including track 2
    script.OnIdle()
    assert set(fl.out) == {Change("mix_solo", 1, 0), Change("mix_mute", 2, 0)}


def test_remote_solo_does_not_echo_mutes(load):
    fl = FakeFLStudio()
    script = load(fl)
    script.OnSysEx(Msg(Change("mix_solo", 3, 1)))
    assert fl.tracks[3]["solo"] is True and fl.tracks[1]["mute"] is True
    for _ in range(5):
        script.OnIdle()
    assert fl.out == []
    script.OnSysEx(Msg(Change("mix_solo", 3, 0)))
    for _ in range(5):
        script.OnIdle()
    assert fl.out == []
    assert not any(t["mute"] for t in fl.tracks)


def test_remote_mute_already_set_by_solo_keeps_the_solo(load):
    # In FL, touching a mute during a solo ends the solo: skip a write that
    # would change nothing.
    fl = FakeFLStudio()
    script = load(fl)
    fl.solo(1, True)
    script.OnIdle()
    fl.out.clear()
    script.OnSysEx(Msg(Change("mix_mute", 2, 1)))
    for _ in range(5):
        script.OnIdle()
    assert fl.tracks[1]["solo"] is True
    assert fl.out == []


def test_remote_unsolo_of_a_track_not_solo_keeps_mutes(load):
    # FL's unsolo unmutes every track: never send it for a track that isn't solo.
    fl = FakeFLStudio()
    fl.tracks[3]["mute"] = True
    script = load(fl)
    script.OnSysEx(Msg(Change("mix_solo", 1, 0)))
    script.OnIdle()
    assert fl.tracks[3]["mute"] is True
    assert fl.out == []


def test_fader_rounding_by_fl_is_not_echoed(load):
    fl = FakeFLStudio()
    script = load(fl)
    for v in (0.123456, 0.5, 0.987654):
        script.OnSysEx(Msg(Change("mix_vol", 1, unit_to_u14(v))))
        script.OnSysEx(Msg(Change("mix_pan", 1, pan_to_u14(v - 0.5))))
        for _ in range(4):
            script.OnIdle()
    assert fl.out == []


def test_solo_already_active_at_load_sends_no_false_mutes(load):
    fl = FakeFLStudio()
    fl.solo(1, True)  # the project was saved with track 1 solo
    script = load(fl)
    script.OnIdle()
    fl.solo(1, False)
    script.OnIdle()
    assert fl.out == [Change("mix_solo", 1, 0)]


def test_inserts_added_after_load_are_synced(load):
    fl = FakeFLStudio()
    script = load(fl)
    fl.tracks.append({"vol": 0.8, "pan": 0.0, "mute": False, "solo": False})
    fl.last_insert = 4  # the user adds an insert track in FL 2026
    script.OnIdle()  # new track: its values are announced once
    fl.out.clear()
    fl.tracks[4]["vol"] = 0.5
    script.OnIdle()
    assert fl.out == [Change("mix_vol", 4, unit_to_u14(0.5))]


def test_peer_unsolo_batch_converges(load):
    # Peer A had track 3 muted, soloed track 1, then unsoloed: A's FL unmuted every
    # track, so A sends (sorted) mute 3 = 0, then solo 1 = 0. B must end up like A.
    fl = FakeFLStudio()
    fl.tracks[3]["mute"] = True
    script = load(fl)
    script.OnSysEx(Msg(Change("mix_solo", 1, 1)))
    script.OnIdle()
    script.OnSysEx(Msg(Change("mix_mute", 3, 0)))
    script.OnSysEx(Msg(Change("mix_solo", 1, 0)))
    for _ in range(5):
        script.OnIdle()
    assert not any(t["solo"] for t in fl.tracks)
    assert not any(t["mute"] for t in fl.tracks)
    assert fl.out == []


def test_peer_mute_during_solo_converges(load):
    # On A, a mute during the solo ended it: A sends the mutes FL left (sorted),
    # then solo 1 = 0. B must end up with the same mutes, without echo.
    fl = FakeFLStudio()
    script = load(fl)
    script.OnSysEx(Msg(Change("mix_solo", 1, 1)))
    script.OnIdle()
    for i in (2, 3):
        script.OnSysEx(Msg(Change("mix_mute", i, 1)))
    script.OnSysEx(Msg(Change("mix_solo", 1, 0)))
    for _ in range(5):
        script.OnIdle()
    assert [t["mute"] for t in fl.tracks[:4]] == [False, False, True, True]
    assert not any(t["solo"] for t in fl.tracks)
    assert fl.out == []


def test_missing_output_port_is_shown_in_fl_hint_bar(load):
    fl = FakeFLStudio()
    fl.assigned = False
    load(fl)
    assert any("FLMP Out" in h for h in fl.hints)


def test_no_hint_when_output_port_is_linked(load):
    fl = FakeFLStudio()
    load(fl)
    assert fl.hints == []
