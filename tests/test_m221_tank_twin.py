"""Behaviour of M221TankTwinProgram against the values SE_Complete.smbp
produces on the real hardware: seal-in latch, and the software tank-level
counter driven by the emulated 1 Hz clock."""

from __future__ import annotations

from examples.m221_lab_twin import build_lab_twin_plc

DT = 0.1
SCANS_PER_TICK = round(1.0 / DT)  # the emulated clock fires once per this many scans


def test_oit_start_seals_in_and_lights_red() -> None:
    plc = build_lab_twin_plc(DT)

    plc.write("oit_start_button", True)
    plc.scan()
    assert plc.read("start_bit") is True

    plc.write("oit_start_button", False)
    plc.scan()
    assert plc.read("start_bit") is True  # held without the OIT bit
    assert plc.read("red_light") is True
    assert plc.read("green_light") is False


def test_oit_stop_drops_the_latch_and_lights_green() -> None:
    plc = build_lab_twin_plc(DT)

    plc.write("oit_start_button", True)
    plc.scan()
    plc.write("oit_start_button", False)
    plc.write("oit_stop_button", True)
    plc.scan()

    assert plc.read("start_bit") is False
    assert plc.read("stop_bit") is True
    assert plc.read("green_light") is True
    assert plc.read("red_light") is False


def test_tank_level_counts_up_once_per_second_while_running() -> None:
    plc = build_lab_twin_plc(DT)
    plc.write("oit_start_button", True)
    plc.scan()
    plc.write("oit_start_button", False)

    for _ in range(SCANS_PER_TICK * 5):
        plc.scan()

    assert plc.read("tank_level") == 5


def test_tank_level_counts_down_once_per_second_while_stopped() -> None:
    plc = build_lab_twin_plc(DT)
    plc.write("oit_start_button", True)
    plc.scan()
    plc.write("oit_start_button", False)
    for _ in range(SCANS_PER_TICK * 10):
        plc.scan()
    assert plc.read("tank_level") == 10

    plc.write("oit_stop_button", True)
    plc.scan()
    plc.write("oit_stop_button", False)
    for _ in range(SCANS_PER_TICK * 4):
        plc.scan()

    assert plc.read("tank_level") == 6


def test_tank_level_clamps_at_100_and_never_goes_negative() -> None:
    plc = build_lab_twin_plc(DT)
    plc.write("oit_start_button", True)
    plc.scan()
    plc.write("oit_start_button", False)

    for _ in range(SCANS_PER_TICK * 200):
        plc.scan()
    assert plc.read("tank_level") == 100

    plc.write("oit_stop_button", True)
    plc.scan()
    plc.write("oit_stop_button", False)
    for _ in range(SCANS_PER_TICK * 200):
        plc.scan()
    assert plc.read("tank_level") == 0


def test_tank_reset_zeroes_the_level() -> None:
    plc = build_lab_twin_plc(DT)
    plc.write("oit_start_button", True)
    plc.scan()
    plc.write("oit_start_button", False)
    for _ in range(SCANS_PER_TICK * 20):
        plc.scan()
    assert plc.read("tank_level") == 20

    plc.write("tank_reset", True)
    plc.scan()
    assert plc.read("tank_level") == 0

    plc.write("tank_reset", False)
    plc.scan()
    assert plc.read("tank_level") == 0
