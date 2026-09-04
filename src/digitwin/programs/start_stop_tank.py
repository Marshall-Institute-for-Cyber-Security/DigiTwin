"""Seal-in start/stop control that commands a tank's fill and drain valves."""

from __future__ import annotations

from digitwin.plc import PLC

TANK_LEVEL_MIN = 0
TANK_LEVEL_MAX = 99


class StartStopTankProgram:
    """Pure control logic — no physics.

    Latches a run command from either the physical or the OIT start/stop
    buttons (stop dominates), drives the indicator lights, and commands the
    fill valve while running / the drain valve while stopped. The tank level
    is produced by the plant model and read back from the ``tank_level``
    sensor tag; the program only uses it for the high/low interlocks.
    """

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

        running = bool(plc.read("start_bit"))
        stopped = bool(plc.read("stop_bit"))

        plc.write_output("green_light", stopped)
        plc.write_output("red_light", running)

        # Interlocks from the level transmitter; the plant owns the real limits.
        level = plc.read_input("tank_level")
        fill_permitted = level < TANK_LEVEL_MAX
        drain_permitted = level > TANK_LEVEL_MIN
        plc.write("tank_fill_permitted", fill_permitted)
        plc.write("tank_drain_permitted", drain_permitted)

        plc.write_output("fill_valve", running and fill_permitted)
        plc.write_output("drain_valve", stopped and drain_permitted)
