"""Seal-in start/stop plus a software tank-level counter — translated 1:1 from
the real M221 lab program (``SE_Complete.smbp``, POUs "Main" and "Tank Fill
Drain Logic") for the hardware-replacement PoC in docs/ROADMAP.md Phase 4.5.

Unlike :class:`digitwin.programs.start_stop_tank.StartStopTankProgram`, the
real rig has no fill/drain valves and no level transmitter — %MW0 ("tank
level") is a pure ladder-logic counter (``FB_COUNTER0``) that steps by 1 once
a second while running/stopped and the corresponding limit isn't reached. This
program reproduces that counter, not physics, so it runs against
:class:`digitwin.plant.NullPlant`.

Two approximations, both because the engine has no equivalent primitive yet:

- The real %S6 is a free-running 1 Hz system clock bit (0.5 s on / 0.5 s off);
  ``PLC``/``HardwareProfile`` has no notion of a self-toggling system bit
  (only ``first_scan_bit`` and ``scan_time_word`` are engine-managed). Here
  it's a private, self-resetting :class:`~digitwin.instructions.TON` that
  fires a single-scan pulse once a second — behaviourally equivalent for this
  ladder's rising-edge counting (see the module's own comment below), except
  the phase at start/stop can differ from real hardware by up to ~1 s.
- ``%M5`` (`tank_reset` here) is read by the ladder (it's the counter's
  ``INIT_COUNT`` input, rungs 1 of "Tank Fill Drain Logic") but never written
  by any rung in this file — on the real PLC it's presumably forced by the
  OIT or a watch list. Its comment field in the project was blank, so the
  name/purpose here is inferred from behaviour, not confirmed; update it once
  the real register/tag map is in hand.
"""

from __future__ import annotations

from digitwin.instructions import ONS, TON
from digitwin.plc import PLC

TANK_LEVEL_MIN = 0
TANK_LEVEL_MAX = 99


class M221TankTwinProgram:
    """Pure control logic, no physics — see module docstring.

    ``scan_dt`` must match the :class:`~digitwin.executive.Executive`'s ``dt``;
    it only feeds the emulated 1 Hz clock, not the scan cycle itself.
    """

    def __init__(self, scan_dt: float) -> None:
        self._scan_dt = scan_dt
        self._clock_tick = TON(preset=1.0)
        self._count_up_edge = ONS()  # stands in for the ladder's RISING0 block
        self._count_down_edge = ONS()  # stands in for the ladder's RISING1 block

    def __call__(self, plc: PLC) -> None:
        # --- POU "Main": seal-in start/stop latch (rungs 1-4), lights (5-6) ---
        start_pressed = plc.read_input("start_button") or plc.read("oit_start_button")
        stop_pressed = plc.read_input("stop_button") or plc.read("oit_stop_button")

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

        # --- POU "Tank Fill Drain Logic" ---
        tick = self._clock_tick(True, self._scan_dt)
        if tick:
            self._clock_tick.elapsed = 0.0
            self._clock_tick.q = False

        fill_permitted = bool(plc.read("tank_fill"))
        drain_permitted = bool(plc.read("tank_drain"))
        reset_count = bool(plc.read("tank_reset"))

        count_up = self._count_up_edge(tick and running and fill_permitted)
        count_down = self._count_down_edge(tick and stopped and drain_permitted)

        level = int(plc.read("tank_level"))
        if reset_count:
            level = 0
        else:
            if count_up:
                level += 1
            if count_down:
                level -= 1
        plc.write("tank_level", level)

        plc.write("tank_drain", level >= TANK_LEVEL_MIN + 1)
        plc.write("tank_fill", level <= TANK_LEVEL_MAX)
