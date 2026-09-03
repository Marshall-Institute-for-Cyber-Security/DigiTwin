"""Timer and counter instruction blocks: TON, TOF, CTU, ONS."""

from __future__ import annotations

from digitwin.instructions import CTU, ONS, TOF, TON


def test_ton_fires_after_the_preset_elapses() -> None:
    ton = TON(preset=2.0)
    dt = 0.1

    for _ in range(19):
        assert ton(True, dt) is False  # 0.1 .. 1.9 s
    assert ton(True, dt) is True  # 20th scan -> 2.0 s
    assert ton.elapsed == 2.0


def test_ton_resets_the_instant_enable_drops() -> None:
    ton = TON(preset=1.0)
    for _ in range(5):
        ton(True, 0.1)

    assert ton(False, 0.1) is False
    assert ton.elapsed == 0.0


def test_tof_holds_q_for_the_preset_after_enable_drops() -> None:
    tof = TOF(preset=1.0)

    assert tof(True, 0.1) is True  # true immediately
    assert tof(False, 0.1) is True  # still held
    for _ in range(8):
        assert tof(False, 0.1) is True
    assert tof(False, 0.1) is False  # 1.0 s after the drop


def test_ctu_counts_rising_edges_and_latches_at_preset() -> None:
    ctu = CTU(preset=3)
    edges = [True, False, True, False, True]

    results = [ctu(level) for level in edges]

    assert ctu.count == 3
    assert results[-1] is True
    assert ctu(True) is True  # held high, no extra count
    assert ctu.count == 3


def test_ctu_reset_returns_the_count_to_zero() -> None:
    ctu = CTU(preset=2)
    ctu(True)
    ctu(False)
    ctu(True)
    assert ctu.count == 2

    assert ctu(False, reset=True) is False
    assert ctu.count == 0


def test_ons_is_true_for_one_call_per_rising_edge() -> None:
    ons = ONS()

    assert ons(False) is False
    assert ons(True) is True
    assert ons(True) is False
    assert ons(False) is False
    assert ons(True) is True
