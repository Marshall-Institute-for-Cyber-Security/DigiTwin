"""Bus-level fault injection: perturb a named signal or interrupt a
transport, transparently to the plant and the control program.

This is framework infrastructure only, per the roadmap's P4 scope note: a
timed scenario DSL, a curated library of application-specific fault
scenarios, and assertion tooling all belong to whatever consumes this
framework, not here. What's here is just the primitives — each one a small,
independently-testable way to perturb a signal or a transport, "on" until
told otherwise.

Five of the six are signal-level: :class:`StuckFault`, :class:`OffsetFault`,
:class:`DriftFault`, :class:`NoiseFault`, :class:`FrozenFault`. Each is a
plain dataclass sharing the same ``step(dt, io)`` shape as a
:class:`~digitwin.plant.base.PlantModel` component (see :class:`FaultModel`).
Attach one to a running twin's ``Executive.faults`` list and it runs every
tick, after the plant and before the PLC's input read — the same "bus-level
hook, neither side aware" position as any other signal transform in this
framework. Toggle ``.active`` at any time; a one-liner either way:

    sim.faults.append(StuckFault(signal="tank_level", value=50))
    ...
    sim.faults[0].active = False

:class:`DropoutFault` is different in kind: it doesn't touch a bus signal at
all. It wraps a twin's real :class:`~digitwin.io.IOTransport` and, while
active, raises :class:`~digitwin.io.TransportError` instead of moving values
— reusing the executive's existing hold-last / fault-event handling
(``Executive._on_transport_failure``) rather than adding a second failure
path. Attach it by replacing the transport, not by appending to a list:

    sim.transport = DropoutFault(sim.transport)
    sim.transport.active = True   # comms gap starts now

Each signal fault is a plain dataclass with only leaf-typed attributes (plus
:class:`NoiseFault`'s `random.Random`, which the reflective snapshot walker
already special-cases), so ``Executive.faults`` snapshots and restores for
free through the same mechanism that already captures ``plant`` and
``program`` — see ``Snapshot``'s ``faults`` field. ``DropoutFault`` is *not*
snapshotted: it lives on ``Executive.transport``, which was never part of a
snapshot's scope (transports are connections, not simulated state — the
executive's own transport-failure counters aren't snapshotted either, for
the same reason).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from digitwin.io import IOBus, IOTransport, IOValue, TransportError
from digitwin.plc import TagValue


class FaultModel(Protocol):
    """A bus-level fault: same shape as a PlantModel component. Ticked after
    the plant and before the PLC's input read, so it can perturb a signal
    the plant just wrote before the transport carries it across."""

    def step(self, dt: float, io: IOBus) -> None: ...


@dataclass
class StuckFault:
    """Holds ``signal`` at a fixed ``value``, ignoring whatever the plant
    writes — a jammed sensor, a valve wedged open/shut, a relay welded
    closed. Unlike :class:`FrozenFault`, the stuck value is chosen by the
    caller, not captured from the signal's own history."""

    signal: str
    value: IOValue
    active: bool = True

    def step(self, dt: float, io: IOBus) -> None:
        if self.active:
            io.set(self.signal, self.value)


@dataclass
class OffsetFault:
    """Adds a fixed ``offset`` to whatever the plant writes to ``signal`` —
    a miscalibrated transmitter, a zero-shifted sensor."""

    signal: str
    offset: float
    active: bool = True

    def step(self, dt: float, io: IOBus) -> None:
        if self.active:
            io.set(self.signal, float(io.get(self.signal, 0.0)) + self.offset)


@dataclass
class DriftFault:
    """Ramps a growing bias onto ``signal`` at ``rate`` signal-units per
    second while active — a slowly failing sensor, not a step change. The
    accumulated bias holds (does not reset) while inactive, so pausing and
    resuming a drift picks up where it left off, matching a real degrading
    instrument rather than a scripted event."""

    signal: str
    rate: float
    active: bool = True
    _accumulated: float = field(default=0.0, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        if self.active:
            self._accumulated += self.rate * dt
            io.set(self.signal, float(io.get(self.signal, 0.0)) + self._accumulated)


@dataclass
class NoiseFault:
    """Adds Gaussian noise (``sigma``, signal units) to ``signal`` each
    step — a noisy transmitter or a flaky field wire. Distinct from
    :class:`~digitwin.plant.sensors.AnalogSensor`'s own ``noise_sigma``:
    that models a transmitter's designed-in measurement noise; this is an
    injected fault layered on top of whatever the plant and any sensor
    already produced.

    Seed ``rng`` for repeatable noise, same convention as ``AnalogSensor``.
    """

    signal: str
    sigma: float
    active: bool = True
    rng: random.Random = field(default_factory=random.Random)

    def step(self, dt: float, io: IOBus) -> None:
        if self.active:
            io.set(self.signal, float(io.get(self.signal, 0.0)) + self.rng.gauss(0.0, self.sigma))


@dataclass
class FrozenFault:
    """Freezes ``signal`` at whatever value it held the instant the fault
    went active — an input that never updates again (a comms hang, a stuck
    read buffer), as opposed to :class:`StuckFault`'s caller-chosen value.
    Re-captures the frozen value each time ``active`` transitions from
    false to true, so toggling it off and back on freezes a fresh value
    rather than replaying the first one."""

    signal: str
    active: bool = False
    _frozen_value: IOValue | None = field(default=None, repr=False)
    _was_active: bool = field(default=False, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        if self.active and not self._was_active:
            self._frozen_value = io.get(self.signal)
        if self.active and self._frozen_value is not None:
            io.set(self.signal, self._frozen_value)
        self._was_active = self.active


class DropoutFault:
    """Wraps a real :class:`~digitwin.io.IOTransport` and, while ``active``,
    raises :class:`~digitwin.io.TransportError` instead of moving values —
    a comms gap (a pulled cable, a radio link down, a PLC offline). See the
    module docstring for why this is a transport wrapper rather than a
    ``Executive.faults`` entry: it has nothing to write to the bus, it makes
    the exchange itself fail, the way a real dropout does.

    Not a dataclass — mutable, single-purpose wrapper state (``active``)
    plus a delegate that a dataclass's ``__eq__``/``__repr__`` would rather
    not try to render.
    """

    def __init__(self, wrapped: IOTransport, *, active: bool = False) -> None:
        self.wrapped = wrapped
        self.active = active

    def read_inputs(self) -> dict[str, TagValue]:
        if self.active:
            raise TransportError("dropout fault active: input read blocked")
        return self.wrapped.read_inputs()

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        if self.active:
            raise TransportError("dropout fault active: output write blocked")
        self.wrapped.write_outputs(outputs)
