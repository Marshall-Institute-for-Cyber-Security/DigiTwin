"""Seal-in start/stop with a run-proving timer, for a conveyor motor."""

from __future__ import annotations

from digitwin.instructions import TON
from digitwin.plc import PLC

DEFAULT_PROVE_TIME_S = 5.0


class MotorConveyorProgram:
    """Pure control logic — no physics.

    A classic industrial motor-start pattern, distinct from the tank twins'
    fill/drain interlock: start/stop pushbuttons latch a run command (stop
    dominates and also clears any fault), the latch drives the motor, and a
    TON timer gives the motor ``prove_time_s`` seconds to seal in its
    running-feedback contact before latching a fault that drops the run
    command and lights a fault lamp. Only a fresh stop press clears a fault —
    pressing start again while faulted does nothing.

    The proving timer needs the scan period, which the engine doesn't yet
    hand a program at scan time (see docs/INTERPRETER_DESIGN.md finding #2)
    — so, like ``M221TankTwinProgram``, ``dt`` is supplied at construction
    and must match the executive's configured ``dt``.
    """

    def __init__(self, dt: float, *, prove_time_s: float = DEFAULT_PROVE_TIME_S) -> None:
        self._dt = dt
        self._proving = TON(preset=prove_time_s)

    def __call__(self, plc: PLC) -> None:
        start_pressed = plc.read_input("start_button")
        stop_pressed = plc.read_input("stop_button")
        running_feedback = plc.read_input("running_feedback")

        if stop_pressed:
            plc.write("run_latch", False)
            plc.write("fault_latch", False)
        elif start_pressed:
            plc.write("run_latch", True)

        fault = bool(plc.read("fault_latch"))
        run_cmd = bool(plc.read("run_latch")) and not fault

        # Proving timer: counts only while commanded to run and not yet
        # proven running — resets the instant the feedback contact seals in.
        proving_enable = run_cmd and not running_feedback
        timed_out = self._proving(proving_enable, self._dt)
        if timed_out:
            plc.write("fault_latch", True)
            plc.write("run_latch", False)
            run_cmd = False
            fault = True

        plc.write_output("motor_run", run_cmd)
        plc.write_output("fault_lamp", fault)
