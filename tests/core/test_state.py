from flmp_core.messages import Change
from flmp_core.state import StateTracker


def tracker() -> StateTracker:
    return StateTracker(tolerance={"mix_vol": 2, "ch_vol": 2})


class TestDiff:
    def test_first_observation_is_baseline_not_change(self):
        t = tracker()
        assert t.diff({("mix_vol", 1): 100}) == []

    def test_reports_changed_values_only(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100, ("mix_mute", 1): 0})
        changes = t.diff({("mix_vol", 1): 101, ("mix_mute", 1): 0})
        assert changes == [Change("mix_vol", 1, 101)]

    def test_one_step_local_moves_are_not_lost(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        assert t.diff({("mix_vol", 1): 101}) == [Change("mix_vol", 1, 101)]
        assert t.diff({("mix_vol", 1): 102}) == [Change("mix_vol", 1, 102)]

    def test_new_key_after_baseline_is_reported(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        assert t.diff({("mix_vol", 1): 100, ("mix_vol", 2): 5}) == [Change("mix_vol", 2, 5)]

    def test_output_is_sorted_and_deterministic(self):
        t = tracker()
        t.diff({("mix_vol", 2): 0, ("mix_vol", 1): 0})
        out = t.diff({("mix_vol", 2): 9, ("mix_vol", 1): 9})
        assert [c.index for c in out] == [1, 2]


class TestAntiLoop:
    def test_remote_value_is_never_echoed(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        t.apply_remote(Change("mix_vol", 1, 5000))
        assert t.diff({("mix_vol", 1): 5000}) == []

    def test_remote_echo_within_tolerance_is_absorbed(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        t.apply_remote(Change("mix_vol", 1, 5000))
        # FL may round the value it was given.
        assert t.diff({("mix_vol", 1): 5002}) == []
        assert t.diff({("mix_vol", 1): 4999}) == []

    def test_genuine_local_move_after_remote_is_sent(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        t.apply_remote(Change("mix_vol", 1, 5000))
        assert t.diff({("mix_vol", 1): 6000}) == [Change("mix_vol", 1, 6000)]

    def test_suppression_ends_once_user_moves_away(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        t.apply_remote(Change("mix_vol", 1, 5000))
        t.diff({("mix_vol", 1): 6000})
        # Coming back near the old remote value is a real local move now.
        assert t.diff({("mix_vol", 1): 5001}) == [Change("mix_vol", 1, 5001)]

    def test_exact_kinds_have_no_tolerance(self):
        t = tracker()
        t.diff({("mix_mute", 1): 0})
        t.apply_remote(Change("mix_mute", 1, 1))
        assert t.diff({("mix_mute", 1): 0}) == [Change("mix_mute", 1, 0)]

    def test_remote_before_baseline_is_remembered(self):
        t = tracker()
        t.apply_remote(Change("mix_pan", 4, 8192))
        assert t.diff({("mix_pan", 4): 8192}) == []

    def test_suppressing_flag_blocks_all_output(self):
        t = tracker()
        t.diff({("mix_vol", 1): 100})
        with t.suppressing():
            assert t.diff({("mix_vol", 1): 200}) == []
        # The value seen while suppressing becomes the new baseline.
        assert t.diff({("mix_vol", 1): 200}) == []


class TestSnapshot:
    def test_snapshot_lists_known_state_sorted(self):
        t = tracker()
        t.diff({("mix_vol", 2): 7, ("mix_vol", 1): 3, ("tempo", None): 120000})
        assert t.snapshot() == [
            Change("mix_vol", 1, 3),
            Change("mix_vol", 2, 7),
            Change("tempo", None, 120000),
        ]

    def test_reset_forgets_everything(self):
        t = tracker()
        t.diff({("mix_vol", 1): 3})
        t.reset()
        assert t.snapshot() == []
