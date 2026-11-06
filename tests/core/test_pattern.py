import math

import pytest
from flmp_core.messages import ProtocolError
from flmp_core.pattern import MAX_NOTES, check_pattern

NOTE = [69, 192, 48, 0.78125, 0.5, 0.5]


def test_valid_pattern_is_normalised():
    ppq, notes = check_pattern(96, [NOTE, [60, 0, 96, 1, 0, 1.0]])
    assert ppq == 96
    assert notes == [
        (69, 192, 48, round(0.78125, 4), 0.5, 0.5),
        (60, 0, 96, 1.0, 0.0, 1.0),
    ]


def test_empty_pattern_is_allowed():
    assert check_pattern(96, []) == (96, [])


@pytest.mark.parametrize(
    "ppq",
    [None, "96", 96.0, True, 0, 23, 1921],
)
def test_bad_ppq(ppq):
    with pytest.raises(ProtocolError):
        check_pattern(ppq, [NOTE])


@pytest.mark.parametrize(
    "note",
    [
        [69, 192, 48, 0.8, 0.5],  # missing field
        [69, 192, 48, 0.8, 0.5, 0.5, 1],  # extra field
        [132, 0, 1, 0.8, 0.5, 0.5],  # pitch out of range
        [-1, 0, 1, 0.8, 0.5, 0.5],
        [69.0, 0, 1, 0.8, 0.5, 0.5],  # pitch must be int
        [True, 0, 1, 0.8, 0.5, 0.5],
        [69, -1, 1, 0.8, 0.5, 0.5],  # negative time
        [69, 0, -1, 0.8, 0.5, 0.5],  # negative length
        [69, 1 << 31, 1, 0.8, 0.5, 0.5],  # time too large
        [69, 0, 1, 1.5, 0.5, 0.5],  # velocity out of range
        [69, 0, 1, math.nan, 0.5, 0.5],
        [69, 0, 1, 0.8, math.inf, 0.5],
        [69, 0, 1, 0.8, 0.5, "0.5"],
        "not a list",
        None,
    ],
)
def test_bad_note(note):
    with pytest.raises(ProtocolError):
        check_pattern(96, [note])


def test_notes_must_be_a_bounded_list():
    with pytest.raises(ProtocolError):
        check_pattern(96, "notes")
    with pytest.raises(ProtocolError):
        check_pattern(96, [NOTE] * (MAX_NOTES + 1))
    assert len(check_pattern(96, [NOTE] * MAX_NOTES)[1]) == MAX_NOTES
