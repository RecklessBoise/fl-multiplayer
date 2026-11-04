import pytest
from flmp_core import PROTOCOL_VERSION
from flmp_core.messages import (
    KINDS,
    MANUFACTURER_ID,
    Change,
    ProtocolError,
    decode,
    encode,
)

SAMPLES = [
    Change("play", None, 3840),
    Change("stop", None, 0),
    Change("tempo", None, 140000),
    Change("mix_vol", 0, 13107),
    Change("mix_vol", 125, 0),
    Change("mix_pan", 3, 8192),
    Change("mix_mute", 7, 1),
    Change("mix_solo", 7, 0),
    Change("ch_vol", 300, 16383),
    Change("ch_pan", 0, 1),
    Change("ch_mute", 12, 1),
    Change("pattern", None, 42),
    Change("snapshot_req", None, 0),
    Change("snapshot_begin", None, 0),
    Change("snapshot_end", None, 0),
]


@pytest.mark.parametrize("change", SAMPLES, ids=lambda c: c.kind)
def test_roundtrip(change):
    assert decode(encode(change)) == change


def test_frame_layout():
    raw = encode(Change("mix_vol", 2, 16383))
    assert raw[0] == 0xF0
    assert raw[1] == MANUFACTURER_ID == 0x7D
    assert raw[2] == PROTOCOL_VERSION
    assert raw[-1] == 0xF7
    assert all(b < 0x80 for b in raw[1:-1])


def test_encode_returns_bytes():
    assert isinstance(encode(Change("stop", None, 0)), bytes)


def test_every_kind_has_unique_code():
    codes = [spec[0] for spec in KINDS.values()]
    assert len(codes) == len(set(codes))


@pytest.mark.parametrize(
    "change",
    [
        Change("nope", None, 0),
        Change("mix_vol", None, 1),  # index required
        Change("tempo", 3, 1),  # index forbidden
        Change("mix_vol", 1, 16384),  # value too large
        Change("mix_mute", 1, 2),  # boolean only
        Change("mix_vol", -1, 0),
    ],
)
def test_encode_rejects_invalid(change):
    with pytest.raises(ProtocolError):
        encode(change)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xf0\x7d\x01\x02",  # no terminator
        b"\xf0\x7e\x01\x02\xf7",  # other manufacturer
        b"\xf0\x7d\x63\x02\xf7",  # unknown protocol version
        b"\xf0\x7d\x01\x7f\xf7",  # unknown kind
        b"\xf0\x7d\x01\x10\x00\xf7",  # truncated payload
        b"\xf0\x7d\x01\x02\x00\xf7",  # trailing garbage
        b"\xf0\x7d\x01\x10\x00\x01\x80\x00\xf7",  # 8-bit data byte
        b"\xf0\x7d\x01\x12\x00\x01\x05\xf7",  # bool out of range
    ],
)
def test_decode_rejects_malformed(raw):
    with pytest.raises(ProtocolError):
        decode(raw)


def test_decode_accepts_list_of_ints():
    raw = list(encode(Change("pattern", None, 5)))
    assert decode(raw) == Change("pattern", None, 5)


def test_change_key_and_repr():
    c = Change("ch_pan", 4, 10)
    assert c.key == ("ch_pan", 4)
    assert "ch_pan" in repr(c)
    assert c != Change("ch_pan", 4, 11)
    assert c != "not a change"
