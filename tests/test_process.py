"""Process-element dynamics, checked against analytic trajectories."""

from __future__ import annotations

import math

from digitwin.io import IOBus
from digitwin.plant import (
    Integrator,
    PIDLoop,
    PipeSegment,
    PlantModel,
    ThermalMass,
    TransportDelay,
)
from digitwin.snapshot import capture_state, restore_state

# --- Integrator -----------------------------------------------------------


def test_integrator_of_a_constant_is_exact() -> None:
    io = IOBus()
    io.set("in", 3.0)
    integ = Integrator(input_signal="in", output_signal="out", gain=2.0)

    dt = 0.1
    for _ in range(20):
        integ.step(dt, io)

    expected = 2.0 * 3.0 * (20 * dt)  # gain * input * elapsed
    assert math.isclose(integ.value, expected, rel_tol=1e-9)
    assert math.isclose(float(io.get("out")), expected, rel_tol=1e-9)


def test_integrator_clamps_to_out_max() -> None:
    io = IOBus()
    io.set("in", 1.0)
    integ = Integrator(input_signal="in", output_signal="out", out_max=5.0)
    for _ in range(1000):
        integ.step(0.1, io)
    assert integ.value == 5.0


def test_integrator_reset_signal_forces_the_accumulator() -> None:
    io = IOBus()
    io.set("in", 1.0)
    integ = Integrator(
        input_signal="in", output_signal="out", reset_signal="hold", reset_value=0.0
    )
    for _ in range(10):
        integ.step(0.1, io)
    assert integ.value > 0.0

    io.set("hold", True)
    integ.step(0.1, io)
    assert math.isclose(integ.value, 0.1)  # reset, then one step of integration


# --- TransportDelay -----------------------------------------------------------


def test_transport_delay_reproduces_the_input_n_steps_later() -> None:
    io = IOBus()
    delay = TransportDelay(input_signal="in", output_signal="out", delay_s=0.5)
    dt = 0.1  # -> 5 slots

    seen: list[float] = []
    for step in range(1, 13):
        io.set("in", float(step))
        delay.step(dt, io)
        seen.append(float(io.get("out")))

    # First five outputs are the fill value; from step 6 on the output is the
    # input from exactly five steps earlier.
    assert seen[:5] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert seen[5:] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_transport_delay_zero_delay_is_pass_through() -> None:
    io = IOBus()
    delay = TransportDelay(input_signal="in", output_signal="out", delay_s=0.0)
    io.set("in", 42.0)
    delay.step(0.1, io)
    assert io.get("out") == 42.0


# --- PipeSegment -----------------------------------------------------------


def test_pipe_flow_is_conductance_times_head_difference() -> None:
    io = IOBus()
    io.set("up", 10.0)
    io.set("down", 2.0)
    pipe = PipeSegment(
        upstream_signal="up",
        downstream_signal="down",
        flow_signal="q",
        conductance=0.5,
    )
    pipe.step(0.1, io)
    assert math.isclose(float(io.get("q")), 0.5 * (10.0 - 2.0))


def test_pipe_valve_throttles_and_reverse_flow_is_blocked_by_default() -> None:
    io = IOBus()
    io.set("up", 10.0)
    io.set("valve", 0.25)
    pipe = PipeSegment(
        upstream_signal="up", flow_signal="q", conductance=1.0, valve_signal="valve"
    )
    pipe.step(0.1, io)
    assert math.isclose(float(io.get("q")), 10.0 * 0.25)

    io.set("up", -5.0)  # would drive flow negative
    pipe.step(0.1, io)
    assert io.get("q") == 0.0

    pipe.allow_reverse = True
    pipe.step(0.1, io)
    assert math.isclose(float(io.get("q")), -5.0 * 0.25)


# --- ThermalMass -----------------------------------------------------------


def test_thermal_mass_cools_toward_ambient_one_euler_step_exactly() -> None:
    io = IOBus()
    io.set("heat", False)
    mass = ThermalMass(
        temp_signal="t",
        heater_signal="heat",
        time_constant_s=10.0,
        ambient=20.0,
        temp=100.0,
    )
    mass.step(0.1, io)
    # dT = dt * -(T - ambient)/tau = 0.1 * -(80/10) = -0.8
    assert math.isclose(mass.temp, 99.2)


def test_thermal_mass_lossless_heating_is_a_linear_ramp() -> None:
    io = IOBus()
    io.set("heat", True)
    mass = ThermalMass(
        temp_signal="t",
        heater_signal="heat",
        heater_power=100.0,
        heat_capacity=10.0,
        time_constant_s=0.0,  # no ambient losses
        temp=0.0,
    )
    dt = 0.1
    for _ in range(50):
        mass.step(dt, io)
    assert math.isclose(mass.temp, (100.0 / 10.0) * (50 * dt))  # rate * elapsed


def test_thermal_mass_settles_at_ambient() -> None:
    io = IOBus()
    io.set("heat", False)
    mass = ThermalMass(
        temp_signal="t", heater_signal="heat", time_constant_s=5.0, ambient=20.0, temp=90.0
    )
    for _ in range(5000):
        mass.step(0.1, io)
    assert math.isclose(mass.temp, 20.0, abs_tol=1e-6)


# --- PIDLoop -----------------------------------------------------------


def test_pid_proportional_only_tracks_error_and_clamps() -> None:
    io = IOBus()
    io.set("sp", 10.0)
    io.set("pv", 0.0)
    pid = PIDLoop(
        setpoint_signal="sp", process_signal="pv", output_signal="cv", kp=2.0, out_max=100.0
    )
    pid.step(0.1, io)
    assert math.isclose(float(io.get("cv")), 20.0)

    pid.kp = 100.0
    pid.step(0.1, io)
    assert io.get("cv") == 100.0  # clamped, not 1000


def test_pid_integral_only_ramps_at_ki_times_error() -> None:
    io = IOBus()
    io.set("sp", 1.0)
    io.set("pv", 0.0)
    pid = PIDLoop(
        setpoint_signal="sp",
        process_signal="pv",
        output_signal="cv",
        kp=0.0,
        ki=1.0,
        out_max=1e9,
    )
    for _ in range(10):
        pid.step(0.1, io)
    assert math.isclose(float(io.get("cv")), 1.0 * 1.0 * (10 * 0.1))


def test_pid_clamp_anti_windup_releases_immediately_on_sign_change() -> None:
    io = IOBus()
    io.set("sp", 10.0)
    io.set("pv", 0.0)
    pid = PIDLoop(
        setpoint_signal="sp",
        process_signal="pv",
        output_signal="cv",
        kp=0.0,
        ki=1.0,
        out_max=5.0,
    )
    for _ in range(30):  # drive hard into the ceiling; integral must not wind past 5.0
        pid.step(0.1, io)
    assert io.get("cv") == 5.0
    assert math.isclose(pid._integral, 5.0)

    io.set("pv", 20.0)  # error flips to -10
    pid.step(0.1, io)
    # integral: 5.0 + 1.0 * (-10) * 0.1 = 4.0 -> output tracks it the very next step
    assert math.isclose(float(io.get("cv")), 4.0)


# --- snapshot safety -----------------------------------------------------------


def test_process_elements_survive_a_snapshot_round_trip() -> None:
    io = IOBus()
    io.set("in", 2.0)
    io.set("sp", 5.0)
    io.set("pv", 1.0)
    live: list[PlantModel] = [
        Integrator(input_signal="in", output_signal="i_out"),
        TransportDelay(input_signal="in", output_signal="d_out", delay_s=0.3),
        ThermalMass(temp_signal="t", heater_signal="in", temp=50.0),
        PIDLoop(setpoint_signal="sp", process_signal="pv", output_signal="cv", ki=0.5),
    ]
    for _ in range(7):
        for component in live:
            component.step(0.1, io)

    frozen = [capture_state(component) for component in live]

    # Run the live copies further so their state diverges from the snapshot.
    for _ in range(5):
        for component in live:
            component.step(0.1, io)

    thawed: list[PlantModel] = [
        Integrator(input_signal="in", output_signal="i_out"),
        TransportDelay(input_signal="in", output_signal="d_out", delay_s=0.3),
        ThermalMass(temp_signal="t", heater_signal="in", temp=50.0),
        PIDLoop(setpoint_signal="sp", process_signal="pv", output_signal="cv", ki=0.5),
    ]
    for component, state in zip(thawed, frozen, strict=True):
        restore_state(component, state)

    # Driving both sets identically from the restore point must agree tick for tick.
    io_a, io_b = IOBus(), IOBus()
    for io_x in (io_a, io_b):
        io_x.set("in", 2.0)
        io_x.set("sp", 5.0)
        io_x.set("pv", 1.0)
    # rebuild the live set from its own frozen state so both start level
    for component, state in zip(live, frozen, strict=True):
        restore_state(component, state)
    for _ in range(6):
        for component in live:
            component.step(0.1, io_a)
        for component in thawed:
            component.step(0.1, io_b)
    assert io_a._signals == io_b._signals
