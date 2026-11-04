import pytest
from flmp_core.codec import (
    U14_MAX,
    pack_uint,
    pan_to_u14,
    u14_to_pan,
    u14_to_unit,
    unit_to_u14,
    unpack_uint,
)


class TestPackUint:
    @pytest.mark.parametrize(
        ("value", "nbytes", "expected"),
        [
            (0, 1, [0]),
            (127, 1, [127]),
            (128, 2, [1, 0]),
            (16383, 2, [127, 127]),
            (80500, 3, [4, 116, 116]),
            (2**28 - 1, 4, [127, 127, 127, 127]),
        ],
    )
    def test_big_endian_7_bit_groups(self, value, nbytes, expected):
        assert pack_uint(value, nbytes) == expected

    def test_every_byte_is_7_bit(self):
        assert all(b < 0x80 for b in pack_uint(123456789, 4))

    @pytest.mark.parametrize("value", [-1, 128])
    def test_out_of_range_raises(self, value):
        with pytest.raises(ValueError):
            pack_uint(value, 1)

    @pytest.mark.parametrize(
        ("value", "nbytes"),
        [(0, 1), (127, 1), (300, 2), (16383, 2), (2_000_000, 3), (2**28 - 1, 4)],
    )
    def test_roundtrip(self, value, nbytes):
        assert unpack_uint(pack_uint(value, nbytes)) == value

    def test_unpack_rejects_8_bit_byte(self):
        with pytest.raises(ValueError):
            unpack_uint([0x80])


class TestUnitScaling:
    @pytest.mark.parametrize(("x", "expected"), [(0.0, 0), (1.0, U14_MAX), (0.5, 8192)])
    def test_unit_to_u14(self, x, expected):
        assert unit_to_u14(x) == expected

    @pytest.mark.parametrize(("x", "expected"), [(-0.2, 0), (1.7, U14_MAX)])
    def test_unit_is_clamped(self, x, expected):
        assert unit_to_u14(x) == expected

    def test_nan_maps_to_zero(self):
        assert unit_to_u14(float("nan")) == 0
        assert pan_to_u14(float("nan")) == 0

    def test_u14_to_unit_roundtrip_within_one_step(self):
        for x in (0.0, 0.123, 0.8, 1.0):
            assert abs(u14_to_unit(unit_to_u14(x)) - x) <= 1 / U14_MAX

    def test_pan_centre_and_edges(self):
        assert pan_to_u14(-1.0) == 0
        assert pan_to_u14(1.0) == U14_MAX
        assert abs(u14_to_pan(pan_to_u14(0.0))) <= 1 / U14_MAX

    def test_pan_roundtrip(self):
        for p in (-1.0, -0.33, 0.0, 0.5, 1.0):
            assert abs(u14_to_pan(pan_to_u14(p)) - p) <= 2 / U14_MAX
