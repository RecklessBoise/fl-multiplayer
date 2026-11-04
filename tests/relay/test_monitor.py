import pytest
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
        ],
    )
    def test_valid(self, line, expected):
        assert parse_command(line) == expected

    @pytest.mark.parametrize("line", ["", "jump", "tempo", "tempo abc", "play -1", "tempo 99999"])
    def test_invalid(self, line):
        assert parse_command(line) is None


class TestDescribe:
    def test_change(self):
        msg = {"type": "change", "kind": "tempo", "index": None, "value": 128500, "from": "p1"}
        assert describe(msg, {"p1": "Moi"}) == "Moi → tempo 128.500 BPM"

    def test_play(self):
        msg = {"type": "change", "kind": "play", "index": None, "value": 384, "from": "p1"}
        assert describe(msg, {}) == "? → lecture depuis le tick 384"

    def test_other_messages(self):
        assert describe({"type": "peer_joined", "peer": "p2", "name": "Bob"}, {}) == (
            "'Bob' a rejoint le salon"
        )
        assert describe({"type": "nope"}, {}) is None


def test_describe_never_prints_raw_server_values():
    msg = {"type": "change", "kind": "mix_vol", "index": 1, "value": "\x1b[2J", "from": "p1"}
    text = describe(msg, {"p1": "Moi"})
    assert "\x1b" not in text
