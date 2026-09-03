"""IEC-style timer and counter instruction blocks.

Each is a small stateful object a program instantiates once (as an attribute)
and calls every scan. Timers advance by ``dt`` seconds; the counter and
one-shot act on the rising edge of their input. They replace the hand-rolled
``_prev_*`` flags a program would otherwise carry for edge detection or
timing.

Timers compare with a nanosecond epsilon and snap ``elapsed`` to ``preset`` on
expiry, so a 2.0 s preset fires on exactly the 20th 0.1 s scan despite binary
floating-point drift.
"""

from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-9


@dataclass
class TON:
    """On-delay timer: ``q`` goes true once ``enable`` has been held for
    ``preset`` seconds, and drops the instant ``enable`` goes false."""

    preset: float
    elapsed: float = 0.0
    q: bool = False

    def __call__(self, enable: bool, dt: float) -> bool:
        if not enable:
            self.elapsed = 0.0
            self.q = False
            return self.q
        self.elapsed = min(self.preset, self.elapsed + dt)
        if self.elapsed + _EPS >= self.preset:
            self.elapsed = self.preset
            self.q = True
        return self.q


@dataclass
class TOF:
    """Off-delay timer: ``q`` goes true immediately with ``enable`` and holds
    for ``preset`` seconds after ``enable`` goes false."""

    preset: float
    elapsed: float = 0.0
    q: bool = False

    def __call__(self, enable: bool, dt: float) -> bool:
        if enable:
            self.elapsed = 0.0
            self.q = True
        elif self.q:
            self.elapsed = min(self.preset, self.elapsed + dt)
            if self.elapsed + _EPS >= self.preset:
                self.elapsed = self.preset
                self.q = False
        return self.q


@dataclass
class CTU:
    """Count-up counter: ``q`` goes true when ``count`` reaches ``preset``.
    Counts on each rising edge of ``cu``; ``reset`` returns ``count`` to zero."""

    preset: int
    count: int = 0
    q: bool = False
    _prev_cu: bool = False

    def __call__(self, cu: bool, *, reset: bool = False) -> bool:
        if reset:
            self.count = 0
        elif cu and not self._prev_cu:
            self.count += 1
        self._prev_cu = cu
        self.q = self.count >= self.preset
        return self.q


@dataclass
class ONS:
    """One-shot / rising-edge detect: true for exactly the one scan on which
    ``signal`` transitions from false to true."""

    _prev: bool = False

    def __call__(self, signal: bool) -> bool:
        edge = signal and not self._prev
        self._prev = signal
        return edge
