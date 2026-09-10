"""Executive / demo integration: control drives valves, plant owns the level,
and the pacing modes don't change the simulation maths."""

from __future__ import annotations

import time

import pytest
from reference import build_demo, build_demo_plc

from digitwin.events import EventCategory, EventLog, EventSeverity
from digitwin.executive import Executive, ExecutiveMode
from digitwin.io import InProcessTransport, IOBus, TransportError
from digitwin.models import PLC_Generic
from digitwin.plant import CompositePlant, NullPlant, Tank
from digitwin.plc import PLC, TagType, TagValue


def _tank(sim: Executive) -> Tank:
    plant = sim.plant
    assert isinstance(plant, CompositePlant)
    return next(c for c in plant.components if isinstance(c, Tank))


def test_executive_rejects_a_dt_faster_than_the_plcs_min_scan_time() -> None:
    plc = build_demo_plc()  # PLC_Generic, min_scan_ms=1
    bus = IOBus()
    transport = InProcessTransport(bus, plc)

    with pytest.raises(ValueError, match="faster than"):
        Executive(plc, CompositePlant([]), bus, transport, dt=0.0001)  # 0.1 ms


def test_tank_fills_on_start_and_level_comes_from_the_plant() -> None:
    sim = build_demo()
    sim.plc.write("start_button", True)

    sim.run(30)

    assert _tank(sim).level > 0.0
    assert sim.plc.read("red_light") is True
    assert sim.plc.read("green_light") is False
    # The reported word tracks the plant's integrated level, not the program.
    assert sim.plc.read("tank_level") == round(_tank(sim).level)


def test_tank_drains_after_stop() -> None:
    sim = build_demo()
    sim.plc.write("start_button", True)
    sim.run(40)
    sim.plc.write("start_button", False)
    peak = _tank(sim).level

    sim.plc.write("stop_button", True)
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(40)

    assert _tank(sim).level < peak
    assert sim.plc.read("green_light") is True
    assert sim.plc.read("red_light") is False


def test_program_never_writes_the_level_tag() -> None:
    sim = build_demo()
    sim.plc.write("start_button", True)

    # A bare program scan (no transport transfer) must leave tank_level alone.
    sim.plc.scan()
    assert sim.plc.read("tank_level") == 0


def test_scaled_mode_produces_the_same_trajectory_as_free_run() -> None:
    def level_after_20(mode: ExecutiveMode, scale: float) -> float:
        sim = build_demo()
        sim.mode = mode
        sim.scale = scale
        sim.plc.write("start_button", True)
        sim.run(20)
        return _tank(sim).level

    baseline = level_after_20(ExecutiveMode.FREE_RUN, 1.0)
    scaled = level_after_20(ExecutiveMode.SCALED, 50.0)

    assert scaled == baseline


def test_real_time_mode_actually_spends_wall_time() -> None:
    sim = build_demo()
    sim.mode = ExecutiveMode.REAL_TIME
    sim.dt = 0.01

    started = time.perf_counter()
    sim.run(12)
    wall = time.perf_counter() - started

    assert wall >= 0.05  # 12 * 10 ms, minus the first (unpaced) tick and slack


def test_free_run_mode_does_not_sleep() -> None:
    sim = build_demo()
    sim.dt = 0.01

    started = time.perf_counter()
    sim.run(200)

    assert time.perf_counter() - started < 0.5  # 200 * 10 ms if it were paced


class _CapturingTransport:
    """Records exactly what the executive hands to write_outputs."""

    def __init__(self) -> None:
        self.received: dict[str, TagValue] = {}

    def read_inputs(self) -> dict[str, TagValue]:
        return {}

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        self.received.update(outputs)


def test_analog_output_reaches_the_transport_alongside_discrete_outputs() -> None:
    def program(plc: PLC) -> None:
        plc.write_output("do", True)
        plc.write_output("ao", 77)

    plc = PLC_Generic("t", program)
    plc.define_tag("do", TagType.DISCRETE_OUTPUT, False, "%Q0.0")
    plc.define_tag("ao", TagType.ANALOG_OUTPUT, 0, "%QW0.0")

    transport = _CapturingTransport()
    sim = Executive(plc, NullPlant(), IOBus(), transport)

    sim.tick()

    assert transport.received == {"do": True, "ao": 77}


class _ScriptedTransport:
    """Test double: each call to read/write pops the next scripted outcome —
    a value dict for success, or a ``TransportError`` to raise."""

    def __init__(self) -> None:
        self.read_script: list[dict[str, TagValue] | TransportError] = []
        self.write_script: list[TransportError | None] = []
        self.write_calls: list[dict[str, TagValue]] = []

    def read_inputs(self) -> dict[str, TagValue]:
        outcome = self.read_script.pop(0)
        if isinstance(outcome, TransportError):
            raise outcome
        return outcome

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        self.write_calls.append(dict(outputs))
        outcome = self.write_script.pop(0) if self.write_script else None
        if outcome is not None:
            raise outcome


def test_read_failure_holds_last_value_and_applies_partial_inputs() -> None:
    plc = PLC_Generic("t", lambda plc: None)
    plc.define_tag("di_a", TagType.DISCRETE_INPUT, False, "%I0.0")
    plc.define_tag("di_b", TagType.DISCRETE_INPUT, False, "%I0.1")

    transport = _ScriptedTransport()
    transport.read_script = [
        {"di_a": True, "di_b": True},
        TransportError("block B timed out", partial_inputs={"di_a": False}),
    ]
    sim = Executive(plc, NullPlant(), IOBus(), transport)

    sim.tick()
    assert plc.read_input("di_a") is True
    assert plc.read_input("di_b") is True

    sim.tick()
    # di_a got a fresh value from the surviving partial read; di_b, absent
    # from the failed block, held its last value.
    assert plc.read_input("di_a") is False
    assert plc.read_input("di_b") is True


def test_read_failure_logs_fault_once_then_recovery_event() -> None:
    plc = PLC_Generic("t", lambda plc: None)
    transport = _ScriptedTransport()
    transport.read_script = [
        TransportError("timeout"),
        TransportError("timeout"),
        {},
    ]
    events = EventLog()
    sim = Executive(plc, NullPlant(), IOBus(), transport, events=events)

    sim.run(3)

    faults = events.query(category=EventCategory.FAULT)
    assert [e.severity for e in faults] == [EventSeverity.WARNING, EventSeverity.INFO]
    assert faults[0].message == "input read failed: timeout"
    assert faults[1].message == "input read recovered"
    assert faults[1].data["failed_scans"] == 2
    assert sim.consecutive_read_failures == 0


def test_write_failure_logs_error_severity_and_diverges_from_read_faults() -> None:
    plc = PLC_Generic("t", lambda plc: None)
    transport = _ScriptedTransport()
    transport.read_script = [{}]
    transport.write_script = [TransportError("write timeout")]
    events = EventLog()
    sim = Executive(plc, NullPlant(), IOBus(), transport, events=events)

    sim.tick()

    faults = events.query(category=EventCategory.FAULT)
    assert len(faults) == 1
    assert faults[0].severity is EventSeverity.ERROR
    assert faults[0].message == "output write failed: write timeout"
    assert sim.consecutive_write_failures == 1


def test_transport_ok_reflects_either_direction_faulting() -> None:
    plc = PLC_Generic("t", lambda plc: None)
    transport = _ScriptedTransport()
    transport.read_script = [TransportError("timeout"), {}]
    sim = Executive(plc, NullPlant(), IOBus(), transport)

    sim.tick()
    assert sim.transport_ok is False

    sim.tick()
    assert sim.transport_ok is True
