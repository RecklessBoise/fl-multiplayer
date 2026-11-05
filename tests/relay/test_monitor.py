import pytest
from flmp_core.codec import pan_to_u14, unit_to_u14
from flmp_relay.monitor import describe, parse_command


class TestParseCommand:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("play", {"kind": "play", "index": None, "value": 0}),
            ("play 384", {"kind": "play", "index": None, "value": 384}),
            ("stop", {"kind": "stop", "index": None, "value": 0}),
            ("tempo 95", {"kind": "tempo", "index": None, "value": 95000}),
            ("tempo 128.5", {"kind": "tempo", "index": None, "value": 128500}),
            ("  TEMPO   140  ", {"kind": "tempo", "index": None, "value": 140000}),
            ("mix vol 1 0.5", {"kind": "mix_vol", "index": 1, "value": unit_to_u14(0.5)}),
            ("mix pan 2 -0.25", {"kind": "mix_pan", "index": 2, "value": pan_to_u14(-0.25)}),
            ("mix mute 3 on", {"kind": "mix_mute", "index": 3, "value": 1}),
            ("mix solo 0 off", {"kind": "mix_solo", "index": 0, "value": 0}),
            ("ch vol 0 1", {"kind": "ch_vol", "index": 0, "value": unit_to_u14(1.0)}),
            ("ch pan 4 1", {"kind": "ch_pan", "index": 4, "value": pan_to_u14(1.0)}),
            ("ch mute 2 off", {"kind": "ch_mute", "index": 2, "value": 0}),
        ],
    )
    def test_valid(self, line, expected):
        assert parse_command(line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "jump",
            "tempo",
            "tempo abc",
            "play -1",
            "tempo 99999",
            "mix vol 1",
            "mix vol 1 1.5",
            "mix pan 1 -2",
            "mix mute 1 maybe",
            "mix vol -1 0.5",
            "mix vol 99999 0.5",
            "ch solo 1 on",
            "ch vol x 0.5",
            "mix vol 1 nan",
        ],
    )
    def test_invalid(self, line):
        assert parse_command(line) is None


class TestDescribe:
    def test_change(self):
        msg = {"type": "change", "kind": "tempo", "index": None, "value": 128500, "from": "p1"}
        assert describe(msg, {"p1": "Moi"}) == "Moi → tempo 128.500 BPM"

    def test_play(self):
        msg = {"type": "change", "kind": "play", "index": None, "value": 384, "from": "p1"}
        assert describe(msg, {}) == "? → lecture depuis le tick 384"

    @pytest.mark.parametrize(
        ("kind", "index", "value", "text"),
        [
            ("mix_vol", 1, unit_to_u14(0.5), "Moi → mixer piste 1 : volume 50.0 %"),
            ("mix_pan", 2, pan_to_u14(-0.25), "Moi → mixer piste 2 : pan -25 %"),
            ("mix_mute", 3, 1, "Moi → mixer piste 3 : mute activé"),
            ("mix_solo", 0, 0, "Moi → mixer piste 0 : solo désactivé"),
            ("ch_vol", 0, unit_to_u14(1.0), "Moi → channel 0 : volume 100.0 %"),
            ("ch_mute", 2, 1, "Moi → channel 2 : mute activé"),
        ],
    )
    def test_mixer_and_channels(self, kind, index, value, text):
        msg = {"type": "change", "kind": kind, "index": index, "value": value, "from": "p1"}
        assert describe(msg, {"p1": "Moi"}) == text

    def test_other_messages(self):
        assert describe({"type": "peer_joined", "peer": "p2", "name": "Bob"}, {}) == (
            "'Bob' a rejoint le salon"
        )
        assert describe({"type": "nope"}, {}) is None


def test_describe_never_prints_raw_server_values():
    msg = {"type": "change", "kind": "mix_vol", "index": 1, "value": "\x1b[2J", "from": "p1"}
    text = describe(msg, {"p1": "Moi"})
    assert "\x1b" not in text


@pytest.mark.parametrize(
    ("kind", "value"),
    [("mix_vol", 10**400), ("ch_pan", -5), ("mix_mute", 7), ("tempo", 10**400), ("play", -1)],
)
def test_describe_rejects_out_of_range_values(kind, value):
    # A hostile server must not crash the monitor or make it print nonsense.
    msg = {"type": "change", "kind": kind, "index": 1, "value": value, "from": "p1"}
    assert describe(msg, {"p1": "Moi"}) == "Moi → valeur invalide ignorée"
