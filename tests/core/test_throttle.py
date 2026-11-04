import pytest
from flmp_core.messages import Change
from flmp_core.throttle import Throttle


def vol(v: int, i: int = 1) -> Change:
    return Change("mix_vol", i, v)


def test_first_message_goes_through():
    th = Throttle(max_per_second=30)
    assert th.offer(vol(1), now=0.0) == [vol(1)]


def test_burst_is_held_and_latest_value_wins():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.0)
    assert th.offer(vol(2), now=0.01) == []
    assert th.offer(vol(3), now=0.02) == []
    assert th.flush(now=0.02) == []
    assert th.flush(now=1 / 30) == [vol(3)]
    assert th.flush(now=1.0) == []


def test_trailing_value_is_never_lost():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.0)
    th.offer(vol(9), now=0.001)
    assert th.flush(now=10.0) == [vol(9)]


def test_rate_never_exceeds_limit():
    th = Throttle(max_per_second=30)
    sent = []
    t = 0.0
    for v in range(1000):  # 1000 moves over one second
        sent += th.offer(vol(v % 16384), now=t)
        sent += th.flush(now=t)
        t += 0.001
    assert len(sent) <= 31


def test_keys_are_independent():
    th = Throttle(max_per_second=30)
    assert th.offer(vol(1, i=1), now=0.0) == [vol(1, i=1)]
    assert th.offer(vol(1, i=2), now=0.0) == [vol(1, i=2)]


def test_unthrottled_kinds_pass_immediately():
    th = Throttle(max_per_second=30)
    play = Change("play", None, 0)
    stop = Change("stop", None, 0)
    assert th.offer(play, now=0.0) == [play]
    assert th.offer(stop, now=0.001) == [stop]
    assert th.offer(play, now=0.002) == [play]


def test_next_deadline():
    th = Throttle(max_per_second=20)
    assert th.next_deadline() is None
    th.offer(vol(1), now=0.0)
    th.offer(vol(2), now=0.01)
    assert th.next_deadline() == pytest.approx(0.05)


def test_invalid_rate():
    with pytest.raises(ValueError):
        Throttle(max_per_second=0)


def test_flush_at_next_deadline_always_sends():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.1)
    th.offer(vol(2), now=0.11)
    assert th.flush(now=th.next_deadline()) == [vol(2)]


def test_discard_drops_pending_value():
    # A remote value applied locally must not be overwritten by a stale pending drag.
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.0)
    th.offer(vol(2), now=0.01)
    th.discard(vol(0).key)
    assert th.flush(now=1.0) == []


def test_move_back_after_discard_is_sent():
    # Sent X, remote Y applied, user moves back to X: peers must get X.
    th = Throttle(max_per_second=30)
    th.offer(vol(5), now=0.0)
    th.discard(vol(0).key)
    assert th.offer(vol(5), now=0.01) == [vol(5)]


def test_clear_drops_everything():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.0)
    th.offer(vol(2), now=0.01)
    th.clear()
    assert th.flush(now=1.0) == []
    assert th.offer(vol(3), now=1.0) == [vol(3)]


def test_pending_equal_to_last_sent_is_dropped():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=0.0)
    th.offer(vol(2), now=0.01)
    th.offer(vol(1), now=0.02)
    assert th.flush(now=1.0) == []


def test_clock_going_backwards_does_not_stick():
    th = Throttle(max_per_second=30)
    th.offer(vol(1), now=100.0)
    assert th.offer(vol(2), now=5.0) == [vol(2)]


def test_nan_time_is_rejected():
    th = Throttle(max_per_second=30)
    with pytest.raises(ValueError):
        th.offer(vol(1), now=float("nan"))
