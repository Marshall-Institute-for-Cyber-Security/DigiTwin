"""Process elements: generic dynamics you wire between actuators and sensors.

None of these carry any water- or thermal-specific meaning on their own — a
``ThermalMass`` is a first-order lag with a driven input, an ``Integrator`` is
an accumulator, a ``PipeSegment`` is a conductance between two nodes. Compose
them (in declaration order, sources before sinks) to build a process without
writing new physics.

Every component here is a plain dataclass whose fields are the project-file
parameters and whose ``step(dt, io)`` makes it a :class:`~digitwin.plant.base.
PlantModel`. State is held in a handful of plain float / list attributes so the
reflective snapshot walker captures it without a ``capture_state`` override.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from digitwin.io import IOBus


@dataclass
class Integrator:
    """Accumulates a bus signal over time: ``dOut/dt = gain * in``.

    Reads ``input_signal`` each step, integrates with explicit Euler, clamps the
    accumulator to ``[out_min, out_max]``, and writes ``output_signal``. If
    ``reset_signal`` is wired and truthy, the accumulator is forced to
    ``reset_value`` before the step (a jog/hold input on a real integrator).
    Snapshot-safe: ``value`` is the only state.
    """

    input_signal: str
    output_signal: str
    gain: float = 1.0
    out_min: float = float("-inf")
    out_max: float = float("inf")
    reset_signal: str | None = None
    reset_value: float = 0.0
    value: float = 0.0

    def step(self, dt: float, io: IOBus) -> None:
        if self.reset_signal is not None and io.get(self.reset_signal, False):
            self.value = self.reset_value
        self.value += dt * self.gain * float(io.get(self.input_signal, 0.0))
        self.value = max(self.out_min, min(self.out_max, self.value))
        io.set(self.output_signal, self.value)


@dataclass
class TransportDelay:
    """Pure dead time: ``output_signal`` is the value ``input_signal`` carried
    ``delay_s`` seconds ago.

    The input is sampled once per step into a FIFO sized from the first step's
    ``dt`` (``round(delay_s / dt)`` slots). Until the FIFO fills, the output
    holds ``initial``. A non-positive ``delay_s`` is a pass-through. Snapshot-
    safe: ``_buffer`` is a plain list of floats.
    """

    input_signal: str
    output_signal: str
    delay_s: float
    initial: float = 0.0
    _buffer: list[float] = field(default_factory=list, repr=False)
    _slots: int = field(default=-1, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        if self._slots < 0:
            self._slots = round(self.delay_s / dt) if self.delay_s > 0.0 and dt > 0.0 else 0
        sample = float(io.get(self.input_signal, self.initial))
        if self._slots == 0:
            io.set(self.output_signal, sample)
            return
        self._buffer.append(sample)
        out = self._buffer.pop(0) if len(self._buffer) > self._slots else self.initial
        io.set(self.output_signal, out)


@dataclass
class PipeSegment:
    """A flow link between two nodes: ``q = conductance * (upstream - downstream)``.

    ``upstream_signal`` and ``downstream_signal`` are levels or pressures in
    consistent units; ``downstream_signal`` may be omitted (treated as
    ``downstream_default``, e.g. an open drain to atmosphere). A wired 0..1
    ``valve_signal`` throttles the flow linearly. Flow is clamped to
    ``[0, max_flow]`` unless ``allow_reverse`` is set. Writes ``flow_signal``;
    stateless.
    """

    upstream_signal: str
    flow_signal: str
    downstream_signal: str | None = None
    downstream_default: float = 0.0
    conductance: float = 1.0
    valve_signal: str | None = None
    max_flow: float = float("inf")
    allow_reverse: bool = False

    def step(self, dt: float, io: IOBus) -> None:
        upstream = float(io.get(self.upstream_signal, 0.0))
        downstream = (
            float(io.get(self.downstream_signal, self.downstream_default))
            if self.downstream_signal is not None
            else self.downstream_default
        )
        flow = self.conductance * (upstream - downstream)
        if self.valve_signal is not None:
            flow *= max(0.0, min(1.0, float(io.get(self.valve_signal, 0.0))))
        if not self.allow_reverse:
            flow = max(0.0, flow)
        io.set(self.flow_signal, max(-self.max_flow, min(self.max_flow, flow)))


@dataclass
class ThermalMass:
    """A lump of thermal mass a heater drives and ambient losses pull back.

    ``dT/dt = q_in / heat_capacity - (T - ambient) / time_constant_s`` where
    ``q_in`` is ``heater_power`` scaled by the 0..1 (or bool) ``heater_signal``.
    ``ambient`` is a constant unless ``ambient_signal`` is wired. Integrated with
    explicit Euler; writes ``temp_signal``. With the heater off, ``temp`` decays
    toward ambient with the given time constant. Snapshot-safe: ``temp`` is the
    only state.
    """

    temp_signal: str
    heater_signal: str
    heater_power: float = 1000.0
    heat_capacity: float = 4184.0
    time_constant_s: float = 60.0
    ambient: float = 20.0
    ambient_signal: str | None = None
    temp: float = 20.0

    def step(self, dt: float, io: IOBus) -> None:
        drive = max(0.0, min(1.0, float(io.get(self.heater_signal, 0.0))))
        ambient = (
            float(io.get(self.ambient_signal, self.ambient))
            if self.ambient_signal is not None
            else self.ambient
        )
        loss = (
            (self.temp - ambient) / self.time_constant_s
            if self.time_constant_s > 0.0
            else 0.0
        )
        self.temp += dt * (drive * self.heater_power / self.heat_capacity - loss)
        io.set(self.temp_signal, self.temp)


@dataclass
class PIDLoop:
    """A PID controller as a plant component — for an inner/cascaded loop the
    PLC program doesn't close itself.

    Each step: ``error = setpoint - process``; the output is
    ``kp*error + ki*∫error dt - kd*d(process)/dt`` with the derivative taken on
    the measurement so a setpoint step doesn't kick it. The output is clamped to
    ``[out_min, out_max]`` and the integral is held whenever accepting it would
    drive further into that clamp (clamp anti-windup). Writes ``output_signal``.
    Snapshot-safe: ``_integral`` and ``_prev_process`` are the only state.
    """

    setpoint_signal: str
    process_signal: str
    output_signal: str
    kp: float = 1.0
    ki: float = 0.0
    kd: float = 0.0
    out_min: float = 0.0
    out_max: float = 100.0
    _integral: float = field(default=0.0, repr=False)
    _prev_process: float | None = field(default=None, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        setpoint = float(io.get(self.setpoint_signal, 0.0))
        process = float(io.get(self.process_signal, 0.0))
        error = setpoint - process
        if self._prev_process is None:
            self._prev_process = process
        derivative = (process - self._prev_process) / dt if dt > 0.0 else 0.0

        candidate = self._integral + self.ki * error * dt
        raw = self.kp * error + candidate - self.kd * derivative
        output = max(self.out_min, min(self.out_max, raw))
        if not (raw > self.out_max and error > 0.0) and not (
            raw < self.out_min and error < 0.0
        ):
            self._integral = candidate

        self._prev_process = process
        io.set(self.output_signal, output)
