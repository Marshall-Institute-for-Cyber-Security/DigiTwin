"""Snapshot / restore and time-travel.

The headline case is the roadmap's Phase 3 validation: run 100 scans,
snapshot at 50, restore, run 50 more, and the tag table must match the
uninterrupted run.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from reference import build_demo

from digitwin.executive import Executive
from digitwin.instructions import TON
from digitwin.io import InProcessTransport, IOBus
from digitwin.models import PLC_Generic
from digitwin.plant import AnalogSensor, CompositePlant, NullPlant, Tank
from digitwin.plc import PLC, TagType, TagValue
from digitwin.snapshot import Snapshot, SnapshotRecorder, capture_state, restore_state


def _tags(sim: Executive) -> dict[str, TagValue]:
    return {name: tag.value for name, tag in sim.plc.tags.items()}


def _tank(sim: Executive) -> Tank:
    plant = sim.plant
    assert isinstance(plant, CompositePlant)
    return next(c for c in plant.components if isinstance(c, Tank))


def _running_demo(observe: bool = False) -> Executive:
    sim = build_demo(observe=observe)
    sim.plc.write("start_button", True)
    return sim


# --- the Phase 3 validation ----------------------------------------------


def test_restore_at_scan_50_reproduces_the_uninterrupted_100_scan_run() -> None:
    baseline = _running_demo()
    baseline.run(100)

    sim = _running_demo()
    sim.run(50)
    halfway = Snapshot.capture(sim)
    sim.run(50)
    straight_through = _tags(sim)

    halfway.restore(sim)
    sim.run(50)

    assert _tags(sim) == straight_through == _tags(baseline)
    assert sim.plc.scan_count == baseline.plc.scan_count == 100
    assert sim.elapsed == pytest.approx(baseline.elapsed)


def test_restore_rewinds_the_plant_and_the_bus_too() -> None:
    sim = _running_demo()
    sim.run(50)
    halfway = Snapshot.capture(sim)
    level_at_50 = _tank(sim).level

    sim.run(50)
    assert _tank(sim).level != pytest.approx(level_at_50)

    halfway.restore(sim)

    assert _tank(sim).level == pytest.approx(level_at_50)
    assert sim.bus.snapshot() == halfway.bus
    assert sim.scan_count == 50


def test_a_branch_diverges_from_the_point_it_was_restored_to() -> None:
    sim = _running_demo()
    sim.run(50)
    halfway = Snapshot.capture(sim)
    sim.run(50)
    kept_filling = float(sim.plc.read("tank_level"))

    halfway.restore(sim)
    sim.plc.write("stop_button", True)  # branch: stop instead of keep filling
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(49)

    assert float(sim.plc.read("tank_level")) < kept_filling


# --- serialization --------------------------------------------------------


def test_a_snapshot_round_trips_through_json_into_a_fresh_twin(
    tmp_path: Path,
) -> None:
    source = _running_demo()
    source.run(50)
    path = tmp_path / "twin.json"
    Snapshot.capture(source).save(path)
    source.run(50)

    revived = _running_demo()
    Snapshot.load(path).restore(revived)
    revived.run(50)

    assert _tags(revived) == _tags(source)
    assert _tank(revived).level == pytest.approx(_tank(source).level)


def test_a_snapshot_from_a_future_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        Snapshot.from_json('{"version": 99, "scan_count": 0, "elapsed": 0.0}')


def test_restoring_tags_the_plc_does_not_define_is_an_error() -> None:
    sim = _running_demo()
    snapshot = Snapshot.capture(sim)
    snapshot.tags["not_a_tag"] = 1

    with pytest.raises(KeyError, match="not_a_tag"):
        snapshot.restore(sim)


# --- what gets captured ---------------------------------------------------


def test_instruction_block_state_survives_a_restore() -> None:
    class TimedProgram:
        def __init__(self) -> None:
            self.timer = TON(preset=2.0)

        def __call__(self, plc: PLC) -> None:
            plc.write("done", self.timer(True, 0.1))

    program = TimedProgram()
    plc = PLC_Generic("timed", program)
    plc.define_tag("done", TagType.INTERNAL_BIT, False)
    bus = IOBus()
    sim = Executive(plc, NullPlant(), bus, InProcessTransport(bus), dt=0.1)

    sim.run(10)
    half_wound = Snapshot.capture(sim)
    assert program.timer.elapsed == pytest.approx(1.0)

    sim.run(10)
    assert plc.read("done") is True

    half_wound.restore(sim)
    assert program.timer.elapsed == pytest.approx(1.0)
    assert program.timer.q is False
    sim.run(9)
    assert plc.read("done") is False  # the last 0.1 s of the preset is still owed
    sim.run(1)
    assert plc.read("done") is True


def test_sensor_rng_state_is_captured_so_a_noisy_branch_repeats() -> None:
    sensor = AnalogSensor(
        source="raw", dest="scaled", noise_sigma=1.0, rng=random.Random(7)
    )
    bus = IOBus()
    bus.set("raw", 50.0)

    def five_readings() -> list[object]:
        readings: list[object] = []
        for _ in range(5):
            sensor.step(0.1, bus)
            readings.append(bus.get("scaled"))
        return readings

    state = capture_state(sensor)
    first = five_readings()

    restore_state(sensor, state)

    assert five_readings() == first


def test_capture_skips_what_it_cannot_represent() -> None:
    class Thing:
        def __init__(self) -> None:
            self.level = 1.5
            self.callback = print
            self.handle = object()

    state = capture_state(Thing())
    assert state == {"level": 1.5}


def test_restore_does_not_clobber_uncaptured_attributes() -> None:
    class Thing:
        def __init__(self) -> None:
            self.level = 1.5
            self.handle = object()

    thing = Thing()
    original_handle = thing.handle
    state = capture_state(thing)
    thing.level = 9.9

    restore_state(thing, state)

    assert thing.level == 1.5
    assert thing.handle is original_handle


# --- time travel ----------------------------------------------------------


def test_the_recorder_captures_every_n_scans_and_rewinds_to_the_nearest() -> None:
    sim = _running_demo()
    sim.snapshots = SnapshotRecorder(every=25)

    sim.run(100)
    assert [snap.ticks for snap in sim.snapshots.snapshots] == [25, 50, 75, 100]

    at_50 = float(sim.snapshots.at(50).tags["tank_level"])
    sim.snapshots.rewind(sim, 60)  # nearest kept snapshot at or before 60

    assert sim.scan_count == 50
    assert float(sim.plc.read("tank_level")) == at_50


def test_the_recorder_keeps_only_the_most_recent_snapshots() -> None:
    sim = _running_demo()
    sim.snapshots = SnapshotRecorder(every=10, keep=3)

    sim.run(100)

    assert len(sim.snapshots) == 3
    assert [snap.ticks for snap in sim.snapshots.snapshots] == [80, 90, 100]
    with pytest.raises(KeyError, match="no snapshot"):
        sim.snapshots.at(50)


def test_snapshots_do_not_erase_the_audit_trail() -> None:
    sim = build_demo()
    sim.plc.write("start_button", True)
    sim.run(50)
    halfway = Snapshot.capture(sim)
    sim.run(50)
    historian = sim.historian
    assert historian is not None
    before = len(historian)

    halfway.restore(sim)
    sim.run(10)

    # The rewind is a new branch in the trend, not a hole in it.
    assert len(historian) > before
