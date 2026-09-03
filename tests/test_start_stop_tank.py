"""Seal-in start/stop latch behaviour of StartStopTankProgram."""

from __future__ import annotations

from digitwin.demo import build_demo_plc
from digitwin.plc import PLC


def _press(plc: PLC, **buttons: bool) -> None:
    for name, state in buttons.items():
        plc.tags[name].value = state


def test_start_latches_and_seals_in_after_release() -> None:
    plc = build_demo_plc()

    _press(plc, start_button=True)
    plc.scan()
    assert plc.read("start_bit") is True
    assert plc.read("red_light") is True

    _press(plc, start_button=False)
    plc.scan()
    assert plc.read("start_bit") is True  # held without the button


def test_stop_dominates_when_both_buttons_are_pressed() -> None:
    plc = build_demo_plc()

    _press(plc, start_button=True, stop_button=True)
    plc.scan()

    assert plc.read("start_bit") is False
    assert plc.read("stop_bit") is True
    assert plc.read("green_light") is True


def test_stop_drops_the_latch_after_running() -> None:
    plc = build_demo_plc()

    _press(plc, start_button=True)
    plc.scan()

    _press(plc, start_button=False, stop_button=True)
    plc.scan()

    assert plc.read("start_bit") is False
    assert plc.read("stop_bit") is True
    assert plc.read("red_light") is False


def test_oit_buttons_act_like_the_physical_buttons() -> None:
    plc = build_demo_plc()

    plc.write("oit_start_button", True)
    plc.scan()
    assert plc.read("start_bit") is True

    plc.write("oit_start_button", False)
    plc.write("oit_stop_button", True)
    plc.scan()
    assert plc.read("stop_bit") is True
    assert plc.read("start_bit") is False
