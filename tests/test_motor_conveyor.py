"""Seal-in start/stop + run-proving-timer behaviour of MotorConveyorProgram."""

from __future__ import annotations

from digitwin.models import PLC_Siemens_S7_1200_CPU1214C
from digitwin.plc import PLC, TagType
from digitwin.programs.motor_conveyor import MotorConveyorProgram

DT = 0.1


def _plc(prove_time_s: float = 5.0) -> PLC:
    plc = PLC_Siemens_S7_1200_CPU1214C("t", MotorConveyorProgram(DT, prove_time_s=prove_time_s))
    plc.define_tag("start_button", TagType.DISCRETE_INPUT, False, "%I0.0")
    plc.define_tag("stop_button", TagType.DISCRETE_INPUT, False, "%I0.1")
    plc.define_tag("running_feedback", TagType.DISCRETE_INPUT, False, "%I0.2")
    plc.define_tag("motor_run", TagType.DISCRETE_OUTPUT, False, "%Q0.0")
    plc.define_tag("fault_lamp", TagType.DISCRETE_OUTPUT, False, "%Q0.1")
    plc.define_tag("run_latch", TagType.INTERNAL_BIT, False, "%M0.0")
    plc.define_tag("fault_latch", TagType.INTERNAL_BIT, False, "%M0.1")
    return plc


def _press(plc: PLC, **buttons: bool) -> None:
    for name, state in buttons.items():
        plc.tags[name].value = state


def test_start_latches_and_commands_the_motor() -> None:
    plc = _plc()
    _press(plc, start_button=True)
    plc.scan()

    assert plc.read("run_latch") is True
    assert plc.tags["motor_run"].value is True


def test_start_seals_in_after_release() -> None:
    plc = _plc()
    _press(plc, start_button=True)
    plc.scan()
    _press(plc, start_button=False)
    plc.scan()

    assert plc.read("run_latch") is True
    assert plc.tags["motor_run"].value is True


def test_stop_dominates_and_drops_the_command() -> None:
    plc = _plc()
    _press(plc, start_button=True, stop_button=True)
    plc.scan()

    assert plc.read("run_latch") is False
    assert plc.tags["motor_run"].value is False


def test_proving_within_time_never_faults() -> None:
    plc = _plc(prove_time_s=5.0)
    _press(plc, start_button=True)
    plc.scan()  # scan 1: run latched; no feedback yet, proving timer starts
    _press(plc, running_feedback=True)  # a plant that proves well within time
    for _ in range(10):  # far short of the 5.0s preset
        plc.scan()

    assert plc.read("fault_latch") is False
    assert plc.tags["fault_lamp"].value is False
    assert plc.tags["motor_run"].value is True


def test_a_proving_timeout_latches_a_fault_and_drops_the_command() -> None:
    # running_feedback never arrives (a stuck contactor, a broken feedback
    # wire) — the proving timer keeps accumulating from scan 1 with no reset.
    plc = _plc(prove_time_s=5.0)
    _press(plc, start_button=True)
    plc.scan()  # call 1
    _press(plc, start_button=False)  # released; latch already sealed in

    for _ in range(48):  # calls 2..49 -> 49 total, elapsed 4.9s: not yet
        plc.scan()
    assert plc.read("fault_latch") is False

    plc.scan()  # call 50: elapsed hits the 5.0s preset exactly
    assert plc.read("fault_latch") is True
    assert plc.tags["motor_run"].value is False
    assert plc.tags["fault_lamp"].value is True


def test_only_a_fresh_stop_press_clears_a_latched_fault() -> None:
    plc = _plc(prove_time_s=5.0)
    _press(plc, start_button=True)
    for _ in range(50):
        plc.scan()
    assert plc.read("fault_latch") is True

    _press(plc, start_button=True)  # start again — must NOT clear the fault
    plc.scan()
    assert plc.read("fault_latch") is True
    assert plc.tags["motor_run"].value is False

    _press(plc, start_button=False, stop_button=True)
    plc.scan()
    assert plc.read("fault_latch") is False

    _press(plc, stop_button=False)
    plc.scan()
    assert plc.tags["motor_run"].value is False  # stopped, not auto-restarted


def test_running_feedback_loss_after_proven_running_faults_again() -> None:
    # Once proven, dropping running_feedback re-enables the proving timer
    # (run_cmd stays true, feedback now false) — a real "lost feedback while
    # running" fault, distinct from a slow-start fault.
    plc = _plc(prove_time_s=5.0)
    _press(plc, start_button=True)
    plc.scan()
    _press(plc, start_button=False, running_feedback=True)
    plc.scan()
    assert plc.read("fault_latch") is False

    _press(plc, running_feedback=False)  # feedback lost mid-run
    for _ in range(50):
        plc.scan()

    assert plc.read("fault_latch") is True
    assert plc.tags["motor_run"].value is False
