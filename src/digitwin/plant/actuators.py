"""Actuator components: turn a PLC command into physical motion with dynamics."""

from __future__ import annotations

from dataclasses import dataclass, field

from digitwin.io import IOBus


@dataclass
class Valve:
    """A valve that takes finite time to travel between shut and fully open.

    Reads ``command_signal`` from the bus (a bool, or a 0..1 position setpoint)
    and drives ``position`` toward it at ``1 / travel_time_s`` per second
    (instant if ``travel_time_s`` is 0). ``switch_delay_s`` is a dead time after
    the command *changes* before travel begins — the switching latency of the
    driving output (an electromechanical relay is milliseconds, a transistor
    output far less). Writes the current 0..1 ``position`` to
    ``position_signal`` each step; if ``state_signal`` is set, also writes the
    bool a limit switch would give — true once ``position`` is within
    ``seal_tol`` of fully open.

    Snapshot-safe: ``position``, ``_target`` and ``_delay_left`` are plain
    floats.
    """

    command_signal: str
    position_signal: str
    travel_time_s: float = 2.0
    switch_delay_s: float = 0.0
    state_signal: str | None = None
    seal_tol: float = 0.02
    position: float = 0.0
    _target: float = field(default=0.0, repr=False)
    _delay_left: float = field(default=0.0, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        target = max(0.0, min(1.0, float(io.get(self.command_signal, 0.0))))
        if target != self._target:
            self._target = target
            self._delay_left = self.switch_delay_s
        if self._delay_left > 0.0:
            self._delay_left = max(0.0, self._delay_left - dt)
        elif self.travel_time_s <= 0.0:
            self.position = target
        else:
            reach = dt / self.travel_time_s
            delta = target - self.position
            self.position = target if abs(delta) <= reach else self.position + (
                reach if delta > 0 else -reach
            )
        io.set(self.position_signal, self.position)
        if self.state_signal is not None:
            io.set(self.state_signal, self.position >= 1.0 - self.seal_tol)


@dataclass
class Motor:
    """A motor that ramps up to speed on start and coasts down on stop.

    Reads a bool ``command_signal``. ``speed`` (0..1 of rated) rises linearly
    over ``spin_up_s`` while commanded on and falls over ``coast_s`` while off,
    and is written to ``speed_signal`` each step. If ``running_signal`` is set,
    also writes the feedback contact a PLC reads back: closed once ``speed``
    reaches ``running_threshold``, open again once the motor is fully stopped.

    Snapshot-safe: ``speed`` and ``_running`` are the only state.
    """

    command_signal: str
    speed_signal: str
    spin_up_s: float = 1.0
    coast_s: float = 2.0
    running_signal: str | None = None
    running_threshold: float = 0.9
    speed: float = 0.0
    _running: bool = field(default=False, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        commanded = bool(io.get(self.command_signal, False))
        self.speed = _ramp_speed(
            self.speed, commanded, dt, self.spin_up_s, self.coast_s
        )
        io.set(self.speed_signal, self.speed)
        if self.running_signal is not None:
            self._running = _run_contact(
                self._running, self.speed, self.running_threshold
            )
            io.set(self.running_signal, self._running)


@dataclass
class Pump:
    """A motor whose shaft speed becomes volumetric flow.

    Same spin-up / coast / feedback behaviour as :class:`Motor`, plus it writes
    ``rated_flow * speed`` to ``flow_signal`` — wire that to a tank's inflow or
    a flow element's input. ``speed_signal`` is optional here.

    Snapshot-safe: ``speed`` and ``_running`` are the only state.
    """

    command_signal: str
    flow_signal: str
    rated_flow: float = 10.0
    spin_up_s: float = 1.0
    coast_s: float = 2.0
    running_signal: str | None = None
    running_threshold: float = 0.9
    speed_signal: str | None = None
    speed: float = 0.0
    _running: bool = field(default=False, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        commanded = bool(io.get(self.command_signal, False))
        self.speed = _ramp_speed(
            self.speed, commanded, dt, self.spin_up_s, self.coast_s
        )
        io.set(self.flow_signal, self.rated_flow * self.speed)
        if self.speed_signal is not None:
            io.set(self.speed_signal, self.speed)
        if self.running_signal is not None:
            self._running = _run_contact(
                self._running, self.speed, self.running_threshold
            )
            io.set(self.running_signal, self._running)


@dataclass
class FirstOrderActuator:
    """A generic proportional actuator: its output chases the commanded value
    with a first-order lag, ``dx/dt = (command - x) / tau_s``.

    Models a positioner, a VFD speed ramp, a pneumatic damper — anything that
    tracks a setpoint with a time constant and no device-specific shape. Reads
    ``command_signal`` (engineering units or 0..1), advances ``position`` one
    step toward it — capped at ``max_rate`` units/second if set — clamps to
    ``[out_min, out_max]``, and writes ``position_signal``. A non-positive
    ``tau_s`` makes it instantaneous (still subject to ``max_rate``).

    Snapshot-safe: ``position`` is the only state.
    """

    command_signal: str
    position_signal: str
    tau_s: float = 1.0
    max_rate: float | None = None
    out_min: float = 0.0
    out_max: float = 1.0
    position: float = 0.0

    def step(self, dt: float, io: IOBus) -> None:
        target = float(io.get(self.command_signal, 0.0))
        if self.tau_s > 0.0:
            move = (target - self.position) * (dt / (self.tau_s + dt))
        else:
            move = target - self.position
        if self.max_rate is not None:
            cap = self.max_rate * dt
            move = max(-cap, min(cap, move))
        self.position = max(self.out_min, min(self.out_max, self.position + move))
        io.set(self.position_signal, self.position)


def _ramp_speed(
    speed: float, commanded: bool, dt: float, spin_up_s: float, coast_s: float
) -> float:
    """0..1 speed after one step: linear rise over ``spin_up_s`` when commanded,
    linear fall over ``coast_s`` when not. A non-positive time means instant."""
    if commanded:
        rise = dt / spin_up_s if spin_up_s > 0.0 else 1.0
        return min(1.0, speed + rise)
    fall = dt / coast_s if coast_s > 0.0 else 1.0
    return max(0.0, speed - fall)


def _run_contact(running: bool, speed: float, threshold: float) -> bool:
    """The run-proven feedback contact: latches closed once ``speed`` reaches
    ``threshold``, opens again only once the machine is fully stopped."""
    if speed >= threshold:
        return True
    if speed <= 0.0:
        return False
    return running
