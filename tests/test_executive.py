"""Executive / demo integration: control drives valves, plant owns the level,
and the pacing modes don't change the simulation maths."""

from __future__ import annotations

import time

import pytest

from digitwin.demo import build_demo, build_demo_plc
from digitwin.executive import Executive, ExecutiveMode
from digitwin.io import InProcessTransport, IOBus
from digitwin.plant import CompositePlant, Tank


def _tank(sim: Executive) -> Tank:
    plant = sim.plant
    assert isinstance(plant, CompositePlant)
    return next(c for c in plant.components if isinstance(c, Tank))


def test_executive_rejects_a_dt_faster_than_the_plcs_min_scan_time() -> None:
    plc = build_demo_plc()  # PLC_Generic, min_scan_ms=1
    bus = IOBus()
    transport = InProcessTransport(plc, bus)

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
