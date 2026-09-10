"""Actuator component dynamics."""

from __future__ import annotations

import math

from digitwin.io import IOBus
from digitwin.plant import FirstOrderActuator, Motor, Pump, Valve


def test_valve_travels_at_a_constant_rate_and_clamps_open() -> None:
    io = IOBus()
    io.set("cmd", True)                 # command fully open
    valve = Valve(command_signal="cmd", position_signal="pos", travel_time_s=2.0)

    for _ in range (10):                # 10 * 0.1 s = 1.0 S = half travel
        valve.step(0.1, io)
    assert abs(io.get("pos") - 0.5) < 1e-9

    for _ in range(20):                 # well past full travel
        valve.step(0.1, io)
    assert io.get("pos") == 1.0         # clamped, not overshooting


def test_valve_reports_sealed_state_only_near_full_open() -> None:
    io = IOBus()
    io.set("cmd", 1.0)
    valve = Valve(
        command_signal="cmd",
        position_signal="pos",
        state_signal="open",
        travel_time_s=1.0,
    )

    valve.step(0.5, io)
    assert io.get("open") is False      # half open
    for _ in range(10):
        valve.step(0.1, io)
    assert io.get("open") is True


def test_zero_travel_time_is_instant() -> None:
    io = IOBus()
    io.set("cmd", True)
    valve = Valve(command_signal="cmd", position_signal="pos", travel_time_s=0.0)
    valve.step(0.1, io)
    assert io.get("pos") == 1.0


def test_valve_switch_delay_holds_travel_until_the_dead_time_elapses() -> None:
    io = IOBus()
    io.set("cmd", True)
    valve = Valve(
        command_signal="cmd",
        position_signal="pos",
        travel_time_s=1.0,
        switch_delay_s=0.3,
    )

    for _ in range(3):                  # 0.3 s of switching dead time
        valve.step(0.1, io)
    assert io.get("pos") == 0.0         # commanded, but not moving yet

    for _ in range(5):                  # now travelling: 0.5 s of a 1.0 s stroke
        valve.step(0.1, io)
    assert abs(io.get("pos") - 0.5) < 1e-9


def test_first_order_actuator_tracks_its_command_with_a_lag() -> None:
    io = IOBus()
    io.set("cmd", 1.0)
    act = FirstOrderActuator(
        command_signal="cmd", position_signal="pos", tau_s=0.9, out_max=1.0
    )

    act.step(0.1, io)
    assert math.isclose(act.position, 0.1)   # alpha = dt / (tau + dt) = 0.1

    for _ in range(200):
        act.step(0.1, io)
    assert math.isclose(act.position, 1.0, abs_tol=1e-6)   # settles at the command


def test_first_order_actuator_rate_limit_caps_the_step() -> None:
    io = IOBus()
    io.set("cmd", 10.0)
    act = FirstOrderActuator(
        command_signal="cmd",
        position_signal="pos",
        tau_s=0.0,               # would jump straight to the command...
        max_rate=2.0,            # ...but no faster than 2.0 units/s
        out_max=10.0,
    )
    act.step(0.1, io)
    assert math.isclose(act.position, 0.2)


def test_motor_spins_up_and_clamps_at_rated_speed() -> None:
    io = IOBus()
    io.set("run", True)
    motor = Motor(command_signal="run", speed_signal="spd", spin_up_s=1.0)

    for _ in range(5):
        motor.step(0.1, io)
    assert abs(io.get("spd") - 0.5) < 1e-9

    for _ in range(10):
        motor.step(0.1, io)
    assert io.get("spd") == 1.0


def test_motor_running_contact_latches_at_threshold_and_clears_when_stopped() -> None:
    io = IOBus()
    io.set("run", True)
    motor = Motor(
        command_signal="run",
        speed_signal="spd",
        running_signal="proven",
        spin_up_s=1.0,
        coast_s=1.0,
        running_threshold=0.9,
    )

    for _ in range(8):                      # speed 0.8 — up, not yet proven
        motor.step(0.1, io)
    assert io.get("proven") is False

    for _ in range(4):                      # past 0.9
        motor.step(0.1, io)
    assert io.get("proven") is True

    io.set("run", False)
    motor.step(0.1, io)                     # coasting, still turning
    assert io.get("proven") is True

    for _ in range(20):                     # fully stopped
        motor.step(0.1, io)
    assert io.get("spd") == 0.0
    assert io.get("proven") is False


def test_pump_flow_is_rated_flow_times_speed() -> None:
    io = IOBus()
    io.set("run", True)
    pump = Pump(command_signal="run", flow_signal="q", rated_flow=20.0, spin_up_s=1.0)

    for _ in range(5):
        pump.step(0.1, io)
    assert abs(io.get("q") - 10.0) < 1e-9   # 0.5 * 20.0

    for _ in range(10):
        pump.step(0.1, io)
    assert io.get("q") == 20.0