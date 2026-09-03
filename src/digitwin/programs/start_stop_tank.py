"""Seal-in start/stop control with a filling/draining tank level."""

from __future__ import annotations

from digitwin.plc import PLC

TANK_LEVEL_MIN = 0
TANK_LEVEL_MAX = 99


class StartStopTankProgram:
    """Latches start/stop from either physical or OIT buttons, drives the
    indicator lights, and steps a tank level up while running / down while
    stopped (one unit per rising edge of the fill/drain condition).
    """

    def __init__(self) -> None:
        self._prev_fill_condition = False
        self._prev_drain_condition = False

    def __call__(self, plc: PLC) -> None:
        start_pressed = plc.read_input("start_button") or plc.read("oit_start_button")
        stop_pressed = plc.read_input("stop_button") or plc.read("oit_stop_button")

        # Seal-in latch: stop dominates when both are pressed.
        if start_pressed:
            plc.write("start_bit", True)
            plc.write("stop_bit", False)
        if stop_pressed:
            plc.write("stop_bit", True)
            plc.write("start_bit", False)

        start_bit = plc.read("start_bit")
        stop_bit = plc.read("stop_bit")

        plc.write_output("green_light", stop_bit)
        plc.write_output("red_light", start_bit)

        # Tank level: fills while running, drains while stopped.
        level = plc.read("tank_level")
        fill_permitted = level <= TANK_LEVEL_MAX
        drain_permitted = level >= TANK_LEVEL_MIN + 1
        plc.write("tank_fill_permitted", fill_permitted)
        plc.write("tank_drain_permitted", drain_permitted)

        fill_condition = bool(start_bit and fill_permitted)
        drain_condition = bool(stop_bit and drain_permitted)

        if fill_condition and not self._prev_fill_condition:
            level += 1
        if drain_condition and not self._prev_drain_condition:
            level -= 1
        plc.write("tank_level", max(TANK_LEVEL_MIN, min(TANK_LEVEL_MAX, level)))

        self._prev_fill_condition = fill_condition
        self._prev_drain_condition = drain_condition
