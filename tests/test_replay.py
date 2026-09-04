"""Recorded-I/O replay: a run captured at the I/O boundary replays bit for bit
with no plant attached."""

from __future__ import annotations

from pathlib import Path

import pytest

from digitwin.demo import build_demo, build_demo_plc
from digitwin.io import TransportError
from digitwin.plant import NullPlant
from digitwin.plc import PLC, Program
from digitwin.replay import (
    IORecording,
    RecordingTransport,
    ReplayTransport,
    build_replay,
    diff_outputs,
)


def _recorded_demo_run(ticks: int = 60) -> IORecording:
    """Run the demo with a recorder in the I/O path and return the recording."""
    sim = build_demo(observe=False)
    recorder = RecordingTransport(sim.transport, plc=sim.plc)
    sim.transport = recorder

    sim.plc.write("start_button", True)
    sim.run(5)
    sim.plc.write("start_button", False)
    sim.run(ticks - 5)
    return recorder.recording


def test_recording_captures_one_input_and_output_frame_per_scan() -> None:
    recording = _recorded_demo_run(20)

    assert len(recording) == 20
    assert len(recording.outputs) == 20
    assert set(recording.inputs[0]) == {"start_button", "stop_button", "tank_level"}
    assert "fill_valve" in recording.outputs[0]


def test_recording_leaves_the_live_run_unchanged() -> None:
    plain = build_demo(observe=False)
    plain.plc.write("start_button", True)
    plain.run(30)

    recorded = build_demo(observe=False)
    recorded.transport = RecordingTransport(recorded.transport)
    recorded.plc.write("start_button", True)
    recorded.run(30)

    assert recorded.plc.read("tank_level") == plain.plc.read("tank_level")


def test_replay_reproduces_the_recorded_outputs_with_no_plant() -> None:
    recording = _recorded_demo_run()

    sim = build_replay(build_demo_plc(), recording)
    sim.run(len(recording))

    transport = sim.transport
    assert isinstance(transport, ReplayTransport)
    assert isinstance(sim.plant, NullPlant)
    assert diff_outputs(recording, transport.outputs) == []


class BrokenLight:
    """The demo's control logic with one deliberate difference: the green
    light is lit whether or not the tank is stopped."""

    def __init__(self, inner: Program) -> None:
        self.inner = inner

    def __call__(self, plc: PLC) -> None:
        self.inner(plc)
        plc.write_output("green_light", True)


def test_replay_flags_the_one_tag_a_changed_program_disagrees_on() -> None:
    recording = _recorded_demo_run()

    plc = build_demo_plc()
    plc.program = BrokenLight(plc.program)
    sim = build_replay(plc, recording)
    sim.run(len(recording))

    transport = sim.transport
    assert isinstance(transport, ReplayTransport)
    differences = diff_outputs(recording, transport.outputs)
    assert differences
    assert {tag for _, tag, _, _ in differences} == {"green_light"}


def test_an_exhausted_recording_reports_a_transport_failure() -> None:
    recording = _recorded_demo_run(10)
    sim = build_replay(build_demo_plc(), recording)

    sim.run(10)

    assert isinstance(sim.transport, ReplayTransport)
    assert sim.transport.exhausted
    with pytest.raises(TransportError, match="exhausted"):
        sim.tick()


def test_hold_last_keeps_feeding_the_final_frame() -> None:
    recording = _recorded_demo_run(10)
    transport = ReplayTransport(recording, hold_last=True)
    for _ in range(10):
        transport.read_inputs()

    assert transport.read_inputs() == recording.inputs[-1]


def test_rewind_replays_the_recording_from_the_start() -> None:
    recording = _recorded_demo_run(10)
    transport = ReplayTransport(recording)
    first = [transport.read_inputs() for _ in range(10)]

    transport.rewind()

    assert [transport.read_inputs() for _ in range(10)] == first
    assert transport.outputs == []


def test_a_recording_round_trips_through_json(tmp_path: Path) -> None:
    recording = _recorded_demo_run(20)
    path = tmp_path / "run.json"
    recording.save(path)

    loaded = IORecording.load(path)

    assert loaded.dt == recording.dt
    assert loaded.inputs == recording.inputs
    assert loaded.outputs == recording.outputs

    sim = build_replay(build_demo_plc(), loaded)
    sim.run(len(loaded))
    transport = sim.transport
    assert isinstance(transport, ReplayTransport)
    assert diff_outputs(loaded, transport.outputs) == []


def test_a_recording_from_a_future_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        IORecording.from_json('{"version": 99, "dt": 0.1}')
