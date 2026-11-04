from flmp_relay.lww import OwnWrites

K = ("mix_vol", 1)


def test_foreign_change_passes_when_nothing_in_flight():
    assert not OwnWrites().blocks(K, now=0.0)


def test_foreign_change_blocked_until_own_echo():
    w = OwnWrites()
    w.sent(K, now=0.0)
    w.sent(K, now=0.0)
    assert w.blocks(K, now=0.1)
    w.echoed(K)
    assert w.blocks(K, now=0.1)
    w.echoed(K)
    assert not w.blocks(K, now=0.1)


def test_keys_are_independent():
    w = OwnWrites()
    w.sent(K, now=0.0)
    assert not w.blocks(("mix_vol", 2), now=0.0)


def test_unexpected_echo_does_not_underflow():
    w = OwnWrites()
    w.echoed(K)
    w.sent(K, now=0.0)
    assert w.blocks(K, now=0.0)


def test_lost_echo_expires():
    # The server may drop or reject a write (rate limit, validation): never block forever.
    w = OwnWrites(ttl=2.0)
    w.sent(K, now=0.0)
    assert w.blocks(K, now=1.9)
    assert not w.blocks(K, now=2.1)


def test_reset_clears_in_flight():
    w = OwnWrites()
    w.sent(K, now=0.0)
    w.reset()
    assert not w.blocks(K, now=0.0)
