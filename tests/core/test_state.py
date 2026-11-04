import pytest
from flmp_core.messages import Change, ProtocolError
from flmp_core.state import REMOTE_CONFIRM_TICKS, StateTracker

VOL = ("mix_vol", 1)


def tracker() -> StateTracker:
    t = StateTracker(tolerance={"mix_vol": 2, "ch_vol": 2})
    t.diff({VOL: 100, ("mix_mute", 1): 0, ("mix_pan", 4): 0})
    return t


def state(vol: int = 100, mute: int = 0, pan: int = 0) -> dict:
    return {VOL: vol, ("mix_mute", 1): mute, ("mix_pan", 4): pan}


class TestDiff:
    def test_first_observation_is_baseline_not_change(self):
        t = StateTracker()
        assert t.diff({VOL: 100}) == []

    def test_reports_changed_values_only(self):
        t = tracker()
        assert t.diff(state(vol=101)) == [Change("mix_vol", 1, 101)]

    def test_one_step_local_moves_are_not_lost(self):
        t = tracker()
        assert t.diff(state(vol=101)) == [Change("mix_vol", 1, 101)]
        assert t.diff(state(vol=102)) == [Change("mix_vol", 1, 102)]

    def test_new_key_after_baseline_is_reported(self):
        t = tracker()
        current = state()
        current[("mix_vol", 2)] = 5
        assert t.diff(current) == [Change("mix_vol", 2, 5)]

    def test_removed_keys_are_forgotten(self):
        t = tracker()
        t.diff({VOL: 100})
        assert [c.key for c in t.snapshot()] == [VOL]

    def test_key_reappearing_is_reported(self):
        t = tracker()
        t.diff({VOL: 100})
        assert t.diff(state(pan=0)) == [Change("mix_mute", 1, 0), Change("mix_pan", 4, 0)]

    def test_output_is_sorted_and_deterministic(self):
        t = StateTracker()
        t.diff({("mix_vol", 2): 0, ("mix_vol", 1): 0})
        out = t.diff({("mix_vol", 2): 9, ("mix_vol", 1): 9})
        assert [c.index for c in out] == [1, 2]

    def test_reset_then_new_baseline(self):
        t = tracker()
        t.reset()
        assert t.snapshot() == []
        assert t.diff(state(vol=7)) == []
        assert t.diff(state(vol=8)) == [Change("mix_vol", 1, 8)]


class TestAntiLoop:
    def test_remote_value_is_never_echoed(self):
        t = tracker()
        assert t.apply_remote(Change("mix_vol", 1, 5000))
        assert t.diff(state(vol=5000)) == []

    def test_rounded_read_back_is_absorbed(self):
        t = tracker()
        t.apply_remote(Change("mix_vol", 1, 5000))
        assert t.diff(state(vol=5002)) == []

    def test_nudge_after_read_back_is_sent(self):
        # The remote marker is one-shot: after the read-back, small moves are real.
        t = tracker()
        t.apply_remote(Change("mix_vol", 1, 5000))
        t.diff(state(vol=5002))
        assert t.diff(state(vol=5001)) == [Change("mix_vol", 1, 5001)]

    def test_stale_read_before_fl_applied_is_not_echoed(self):
        t = tracker()
        t.apply_remote(Change("mix_vol", 1, 5000))
        assert t.diff(state(vol=100)) == []  # FL not updated yet
        assert t.diff(state(vol=5000)) == []

    def test_real_move_right_after_remote_is_sent_after_confirm_window(self):
        t = tracker()
        t.apply_remote(Change("mix_vol", 1, 5000))
        out = []
        for _ in range(REMOTE_CONFIRM_TICKS):
            out += t.diff(state(vol=6000))
        assert out == [Change("mix_vol", 1, 6000)]

    def test_exact_kinds_have_no_tolerance(self):
        t = tracker()
        t.apply_remote(Change("mix_mute", 1, 1))
        assert t.diff(state(mute=1)) == []
        assert t.diff(state(mute=0)) == [Change("mix_mute", 1, 0)]

    def test_remote_for_unknown_key_is_rejected(self):
        t = tracker()
        assert t.apply_remote(Change("ch_vol", 900, 10)) is False
        assert ("ch_vol", 900) not in [c.key for c in t.snapshot()]

    def test_knows_tells_caller_whether_to_write_to_fl(self):
        t = tracker()
        assert t.knows(VOL)
        assert not t.knows(("ch_vol", 900))
        assert not StateTracker().knows(VOL)

    def test_remote_before_baseline_is_rejected(self):
        t = StateTracker()
        assert t.apply_remote(Change("mix_pan", 4, 8192)) is False

    def test_invalid_remote_raises_protocol_error(self):
        t = tracker()
        with pytest.raises(ProtocolError):
            t.apply_remote(Change("mix_vol", 1, 99999))

    def test_suppressing_flag_blocks_all_output(self):
        t = tracker()
        with t.suppressing():
            assert t.diff(state(vol=200)) == []
        assert t.diff(state(vol=200)) == []

    def test_suppressing_is_released_after_exception_and_nests(self):
        t = tracker()
        with pytest.raises(RuntimeError), t.suppressing():
            with t.suppressing():
                pass
            raise RuntimeError
        assert t.diff(state(vol=300)) == [Change("mix_vol", 1, 300)]


class TestSnapshot:
    def test_snapshot_lists_known_state_sorted(self):
        t = StateTracker()
        t.diff({("mix_vol", 2): 7, ("mix_vol", 1): 3, ("tempo", None): 120000})
        assert t.snapshot() == [
            Change("mix_vol", 1, 3),
            Change("mix_vol", 2, 7),
            Change("tempo", None, 120000),
        ]
