"""Executive / demo integration: control drives valves, plant owns the level."""

from __future__ import annotations

from digitwin.demo import build_demo
from digitwin.executive import Executive
from digitwin.plant import CompositePlant, Tank


def _tank(sim: Executive) -> Tank:
    plant = sim.plant
    assert isinstance(plant, CompositePlant)
    return next(c for c in plant.components if isinstance(c, Tank))


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
