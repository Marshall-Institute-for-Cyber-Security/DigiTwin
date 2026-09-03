"""Sensor components: turn continuous plant state into PLC-readable values."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from digitwin.io import IOBus


@dataclass
class AnalogSensor:
    """Samples a continuous bus signal into a scaled integer word.

    Reads ``source`` (engineering units), optionally applies a first-order
    low-pass (``filter_tau`` seconds) and additive Gaussian noise
    (``noise_sigma``, source units), linearly maps ``[in_lo, in_hi]`` onto
    ``[out_lo, out_hi]``, clamps, rounds, and writes ``dest``.

    Deterministic unless ``noise_sigma`` is set; seed ``rng`` for repeatable
    noise.
    """

    source: str
    dest: str
    in_lo: float = 0.0
    in_hi: float = 100.0
    out_lo: int = 0
    out_hi: int = 100
    noise_sigma: float = 0.0
    filter_tau: float = 0.0
    rng: random.Random = field(default_factory=random.Random)
    _filtered: float | None = field(default=None, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        raw = float(io.get(self.source, 0.0))

        if self.filter_tau > 0.0:
            if self._filtered is None:
                self._filtered = raw
            else:
                alpha = dt / (self.filter_tau + dt)
                self._filtered += alpha * (raw - self._filtered)
            value = self._filtered
        else:
            value = raw

        if self.noise_sigma > 0.0:
            value += self.rng.gauss(0.0, self.noise_sigma)

        span_in = self.in_hi - self.in_lo
        frac = 0.0 if span_in == 0.0 else (value - self.in_lo) / span_in
        frac = max(0.0, min(1.0, frac))
        io.set(self.dest, self.out_lo + round(frac * (self.out_hi - self.out_lo)))


@dataclass
class DiscreteSensor:
    """A threshold switch with hysteresis: continuous signal -> bool.

    Turns on when ``source`` rises past ``threshold`` and off only after it
    falls back below ``threshold - hysteresis``, so noise near the setpoint
    doesn't chatter the output.
    """

    source: str
    dest: str
    threshold: float
    hysteresis: float = 0.0
    _state: bool = field(default=False, repr=False)

    def step(self, dt: float, io: IOBus) -> None:
        value = float(io.get(self.source, 0.0))
        if self._state:
            if value < self.threshold - self.hysteresis:
                self._state = False
        elif value >= self.threshold:
            self._state = True
        io.set(self.dest, self._state)
