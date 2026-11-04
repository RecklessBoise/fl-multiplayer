from flmp_relay.lww import OwnWrites

K = ("mix_vol", 1)


def test_foreign_change_passes_when_nothing_in_flight():
    w = OwnWrites()
    assert not w.blocks(K)


def test_foreign_change_blocked_until_own_echo():
    w = OwnWrites()
    w.sent(K)
    w.sent(K)
    assert w.blocks(K)
    w.echoed(K)
    assert w.blocks(K)
    w.echoed(K)
    assert not w.blocks(K)


def test_keys_are_independent():
    w = OwnWrites()
    w.sent(K)
    assert not w.blocks(("mix_vol", 2))


def test_unexpected_echo_does_not_underflow():
    w = OwnWrites()
    w.echoed(K)
    w.sent(K)
    assert w.blocks(K)


def test_reset_clears_in_flight():
    w = OwnWrites()
    w.sent(K)
    w.reset()
    assert not w.blocks(K)
