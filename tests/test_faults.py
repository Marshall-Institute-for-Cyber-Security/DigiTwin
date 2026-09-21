"""Bus-level fault injection (digitwin.faults): each primitive in isolation,
then wired into a running Executive, then a snapshot round-trip — the
roadmap's own "done when" bar for P4."""

from __future__ import annotations

import random

import pytest

from digitwin.executive import Executive, ExecutiveMode
from digitwin.faults import (
    DriftFault,
    DropoutFault,
    FrozenFault,
    NoiseFault,
    OffsetFault,
    StuckFault,
)
from digitwin.io import InProcessTransport, IOBus, TransportError
from digitwin.models import PLC_Generic
from digitwin.plc import TagType
from digitwin.snapshot import Snapshot

# --- signal-level faults, in isolation --------------------------------------


def test_stuck_fault_overrides_whatever_the_plant_wrote() -> None:
    io = IOBus()
    io.set("level", 42)
    StuckFault(signal="level", value=999).step(0.1, io)
    assert io.get("level") == 999


def test_stuck_fault_does_nothing_while_inactive() -> None:
    io = IOBus()
    io.set("level", 42)
    StuckFault(signal="level", value=999, active=False).step(0.1, io)
    assert io.get("level") == 42


def test_offset_fault_adds_a_fixed_bias() -> None:
    io = IOBus()
    io.set("temp", 20.0)
    OffsetFault(signal="temp", offset=5.0).step(0.1, io)
    assert io.get("temp") == 25.0


def test_drift_fault_ramps_over_time() -> None:
    io = IOBus()
    fault = DriftFault(signal="temp", rate=1.0)  # 1 unit/s
    for _ in range(10):
        io.set("temp", 20.0)  # the plant re-asserts its true value every tick
        fault.step(0.1, io)
    assert io.get("temp") == pytest.approx(20.0 + 1.0)  # 10 * 0.1s * 1.0/s


def test_drift_fault_holds_its_accumulation_while_inactive_then_resumes() -> None:
    io = IOBus()
    fault = DriftFault(signal="temp", rate=1.0)
    io.set("temp", 20.0)
    fault.step(0.1, io)  # accumulated = 0.1

    fault.active = False
    io.set("temp", 20.0)
    fault.step(0.1, io)  # inactive: untouched, no further accumulation
    assert io.get("temp") == 20.0

    fault.active = True
    io.set("temp", 20.0)
    fault.step(0.1, io)  # resumes from 0.1, not from 0
    assert io.get("temp") == pytest.approx(20.2)


def test_noise_fault_is_deterministic_with_a_seeded_rng() -> None:
    io1, io2 = IOBus(), IOBus()
    io1.set("flow", 10.0)
    io2.set("flow", 10.0)
    NoiseFault(signal="flow", sigma=1.0, rng=random.Random(1)).step(0.1, io1)
    NoiseFault(signal="flow", sigma=1.0, rng=random.Random(1)).step(0.1, io2)

    assert io1.get("flow") == io2.get("flow")
    assert io1.get("flow") != 10.0  # sigma=1.0 essentially never rolls exactly 0


def test_frozen_fault_captures_on_activation_and_holds() -> None:
    io = IOBus()
    fault = FrozenFault(signal="pressure")
    io.set("pressure", 10.0)
    fault.step(0.1, io)  # inactive: no-op
    assert io.get("pressure") == 10.0

    fault.active = True
    io.set("pressure", 15.0)  # the plant's new true value...
    fault.step(0.1, io)  # ...captured as the frozen value, then held
    assert io.get("pressure") == 15.0

    io.set("pressure", 999.0)  # the plant keeps moving...
    fault.step(0.1, io)
    assert io.get("pressure") == 15.0  # ...but the input never updates


def test_frozen_fault_recaptures_on_a_fresh_activation() -> None:
    io = IOBus()
    fault = FrozenFault(signal="pressure")
    io.set("pressure", 10.0)
    fault.active = True
    fault.step(0.1, io)
    assert io.get("pressure") == 10.0

    fault.active = False
    io.set("pressure", 50.0)
    fault.step(0.1, io)
    assert io.get("pressure") == 50.0  # inactive: the true value passes through

    fault.active = True
    fault.step(0.1, io)  # a fresh activation captures 50.0, not the old 10.0
    io.set("pressure", 999.0)
    fault.step(0.1, io)
    assert io.get("pressure") == 50.0


def test_dropout_fault_blocks_reads_and_writes_while_active() -> None:
    class _StubTransport:
        def read_inputs(self) -> dict[str, int]:
            return {"tag": 1}

        def write_outputs(self, outputs: dict[str, int]) -> None:
            pass

    fault = DropoutFault(_StubTransport())
    assert fault.read_inputs() == {"tag": 1}  # inactive: passes through

    fault.active = True
    with pytest.raises(TransportError, match="input read blocked"):
        fault.read_inputs()
    with pytest.raises(TransportError, match="output write blocked"):
        fault.write_outputs({})

    fault.active = False
    assert fault.read_inputs() == {"tag": 1}  # recovers


# --- wired into a running Executive -----------------------------------------


class _ConstantSource:
    """A trivial plant: writes a fixed true value to a bus signal every tick."""

    def __init__(self, signal: str, value: float) -> None:
        self.signal = signal
        self.value = value

    def step(self, dt: float, io: IOBus) -> None:
        io.set(self.signal, self.value)


def _sim_with_fault(fault: object) -> Executive:
    bus = IOBus()
    plc = PLC_Generic("t", lambda _plc: None)
    plc.define_tag("reading", TagType.ANALOG_INPUT, 0, "%IW0.0")
    plant = _ConstantSource("true_value", 50)
    transport = InProcessTransport(bus, plc, inputs={"%IW0.0": "true_value"})
    sim = Executive(plc, plant, bus, transport, dt=0.1, mode=ExecutiveMode.FREE_RUN)
    sim.faults.append(fault)  # type: ignore[arg-type]
    return sim


def test_a_fault_is_what_the_plc_actually_reads() -> None:
    sim = _sim_with_fault(StuckFault(signal="true_value", value=999))
    sim.tick()
    assert sim.plc.read("reading") == 999


def test_with_no_active_fault_the_plc_reads_the_plants_true_value() -> None:
    sim = _sim_with_fault(StuckFault(signal="true_value", value=999, active=False))
    sim.tick()
    assert sim.plc.read("reading") == 50


# --- snapshot: the roadmap's own P4 "done when" bar -------------------------


def test_a_drift_faults_state_survives_a_snapshot_round_trip() -> None:
    baseline = _sim_with_fault(DriftFault(signal="true_value", rate=2.0))
    baseline.run(20)

    sim = _sim_with_fault(DriftFault(signal="true_value", rate=2.0))
    sim.run(10)
    halfway = Snapshot.capture(sim)
    sim.run(5)  # diverge from the eventual comparison point on purpose
    halfway.restore(sim)
    sim.run(10)

    assert sim.plc.read("reading") == baseline.plc.read("reading")

    live_fault, baseline_fault = sim.faults[0], baseline.faults[0]
    assert isinstance(live_fault, DriftFault)
    assert isinstance(baseline_fault, DriftFault)
    assert live_fault._accumulated == pytest.approx(baseline_fault._accumulated)
