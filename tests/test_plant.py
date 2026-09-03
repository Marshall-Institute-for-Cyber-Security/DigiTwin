"""Plant component tests: dynamics, scaling, hysteresis."""

from __future__ import annotations

import math

from digitwin.io import IOBus
from digitwin.plant import AnalogSensor, DiscreteSensor, Tank


def test_tank_fills_linearly_matches_analytic_solution() -> None:
    # Fill valve open, drain valve closed => dLevel/dt = fill_rate / area (constant),
    # for which explicit Euler is exact.
    bus = IOBus()
    bus.set("fill_valve", True)
    bus.set("drain_valve", False)
    tank = Tank(area=2.0, fill_rate=10.0, level=0.0)

    dt = 0.1
    for _ in range(20):
        tank.step(dt, bus)

    expected = (10.0 / 2.0) * (20 * dt)  # rate * elapsed
    assert math.isclose(tank.level, expected, rel_tol=1e-9)
    assert math.isclose(float(bus.get("tank_level_true")), expected, rel_tol=1e-9)


def test_tank_drains_monotonically_and_never_goes_negative() -> None:
    bus = IOBus()
    bus.set("fill_valve", False)
    bus.set("drain_valve", True)
    tank = Tank(area=1.0, drain_coeff=3.0, level=50.0)

    previous = tank.level
    for _ in range(500):
        tank.step(0.1, bus)
        assert tank.level <= previous
        assert tank.level >= 0.0
        previous = tank.level


def test_tank_level_clamps_to_max() -> None:
    bus = IOBus()
    bus.set("fill_valve", True)
    tank = Tank(area=1.0, fill_rate=100.0, level_max=100.0, level=0.0)

    for _ in range(100):
        tank.step(0.1, bus)

    assert tank.level == 100.0


def test_analog_sensor_scales_and_rounds() -> None:
    bus = IOBus()
    bus.set("level_true", 25.0)
    sensor = AnalogSensor(source="level_true", dest="level_word", out_hi=4000)

    sensor.step(0.1, bus)

    assert bus.get("level_word") == 1000


def test_analog_sensor_clamps_out_of_range_input() -> None:
    bus = IOBus()
    sensor = AnalogSensor(source="src", dest="dst")

    bus.set("src", 150.0)
    sensor.step(0.1, bus)
    assert bus.get("dst") == 100

    bus.set("src", -20.0)
    sensor.step(0.1, bus)
    assert bus.get("dst") == 0


def test_discrete_sensor_has_hysteresis() -> None:
    bus = IOBus()
    switch = DiscreteSensor(source="lvl", dest="hi", threshold=80.0, hysteresis=5.0)

    for value, expected in [(70.0, False), (85.0, True), (78.0, True), (74.0, False)]:
        bus.set("lvl", value)
        switch.step(0.1, bus)
        assert bus.get("hi") is expected
