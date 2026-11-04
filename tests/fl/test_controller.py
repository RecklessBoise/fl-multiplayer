from flmp_core.controller import Controller
from flmp_core.messages import Change, decode, encode


class FakeFL:
    """Stands in for FL Studio's transport and tempo."""

    def __init__(self, playing=False, pos=0, tempo=120000, has_song_pos=True):
        self.playing = playing
        self.pos = pos
        self.tempo = tempo
        self.has_song_pos = has_song_pos
        self.calls = []

    def is_playing(self):
        return self.playing

    def song_pos(self):
        return self.pos if self.has_song_pos else None

    def play_from(self, pos):
        self.calls.append(("play_from", pos))
        if self.has_song_pos:
            self.pos = pos
        self.playing = True

    def stop(self):
        self.calls.append(("stop",))
        self.playing = False

    def read_state(self):
        return {("tempo", None): self.tempo}

    def apply(self, change):
        if change.kind == "tempo":
            self.calls.append(("tempo", change.value))
            self.tempo = change.value
            return True
        return False


def make(**kw):
    fl = FakeFL(**kw)
    out = []
    ctl = Controller(fl, lambda frame: out.append(decode(frame)))
    ctl.on_init()
    return ctl, fl, out


def test_init_sends_nothing():
    _, _, out = make()
    assert out == []


def test_local_play_sends_position_then_stop():
    ctl, fl, out = make(pos=960)
    fl.playing = True
    ctl.on_idle()
    fl.playing = False
    ctl.on_idle()
    assert out == [Change("play", None, 960), Change("stop", None, 0)]


def test_play_without_song_pos_api_sends_zero():
    ctl, fl, out = make(has_song_pos=False)
    fl.playing = True
    ctl.on_idle()
    assert out == [Change("play", None, 0)]


def test_local_tempo_change_is_sent():
    ctl, fl, out = make()
    fl.tempo = 128500
    ctl.on_idle()
    assert out == [Change("tempo", None, 128500)]


def test_remote_play_starts_fl_at_position_without_echo():
    ctl, fl, out = make()
    assert ctl.on_sysex(encode(Change("play", None, 1920))) is True
    assert fl.calls == [("play_from", 1920)]
    for _ in range(5):
        ctl.on_idle()
    assert out == []


def test_remote_stop_without_echo():
    ctl, fl, out = make(playing=True)
    ctl.on_sysex(encode(Change("stop", None, 0)))
    assert fl.calls == [("stop",)]
    ctl.on_idle()
    assert out == []


def test_remote_play_while_fl_lags_is_not_echoed_as_stop():
    ctl, fl, out = make()
    fl.play_from = lambda pos: fl.calls.append(("play_from", pos))  # FL starts later
    ctl.on_sysex(encode(Change("play", None, 0)))
    ctl.on_idle()  # still stopped: must not send "stop"
    fl.playing = True
    ctl.on_idle()
    assert out == []


def test_user_stops_after_remote_play_is_sent():
    ctl, fl, out = make()
    ctl.on_sysex(encode(Change("play", None, 0)))
    ctl.on_idle()
    fl.playing = False
    ctl.on_idle()
    assert out == [Change("stop", None, 0)]


def test_remote_tempo_applied_without_echo():
    ctl, fl, out = make()
    ctl.on_sysex(encode(Change("tempo", None, 90000)))
    assert fl.calls == [("tempo", 90000)]
    ctl.on_idle()
    assert out == []


def test_unknown_or_foreign_sysex_is_not_handled():
    ctl, _, out = make()
    assert ctl.on_sysex(b"\xf0\x7e\x01\x02\xf7") is False
    assert ctl.on_sysex(b"\xf0\x7d\x01") is False
    assert out == []


def test_change_for_unsupported_kind_is_ignored():
    ctl, fl, out = make()
    assert ctl.on_sysex(encode(Change("mix_vol", 3, 100))) is True
    assert fl.calls == []
    ctl.on_idle()
    assert out == []


def test_snapshot_request_dumps_state_framed():
    ctl, fl, out = make(tempo=99000)
    ctl.on_sysex(encode(Change("snapshot_req", None, 0)))
    assert out == [
        Change("snapshot_begin", None, 0),
        Change("tempo", None, 99000),
        Change("snapshot_end", None, 0),
    ]


def test_snapshot_dump_reflects_latest_local_state():
    ctl, fl, out = make(tempo=99000)
    fl.tempo = 101000  # changed since last idle
    ctl.on_sysex(encode(Change("snapshot_req", None, 0)))
    assert Change("tempo", None, 101000) in out


def test_incoming_snapshot_is_applied_without_echo():
    ctl, fl, out = make()
    for c in (
        Change("snapshot_begin", None, 0),
        Change("tempo", None, 75000),
        Change("snapshot_end", None, 0),
    ):
        ctl.on_sysex(encode(c))
    assert fl.tempo == 75000
    ctl.on_idle()
    assert out == []


def test_errors_in_fl_calls_do_not_escape():
    ctl, fl, out = make()

    def boom(_pos):
        raise RuntimeError("FL said no")

    fl.play_from = boom
    assert ctl.on_sysex(encode(Change("play", None, 0))) is True
    assert ctl.last_error is not None
