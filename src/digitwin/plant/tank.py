"""Tank component: level integrates net inflow over the tank's area."""

from __future__ import annotations

import math
from dataclasses import dataclass

from digitwin.io import IOBus


@dataclass
class Tank:
    """A tank with an on/off fill valve and a gravity drain valve.

    ``dLevel/dt = (q_in - q_out) / area`` where ``q_in`` is ``fill_rate`` while
    the fill valve is open and ``q_out`` is ``drain_coeff * sqrt(level)``
    (Torricelli) while the drain valve is open. Level is clamped to
    ``[level_min, level_max]`` and integrated with explicit Euler, which is
    accurate enough at PLC scan rates.

    The valve command and level signal names are the bus keys this component
    reads and writes; wire the control program's outputs to the same names.
    """

    area: float = 1.0
    fill_rate: float = 10.0
    drain_coeff: float = 2.0
    level_min: float = 0.0
    level_max: float = 100.0
    level: float = 0.0

    fill_valve_signal: str = "fill_valve"
    drain_valve_signal: str = "drain_valve"
    level_signal: str = "tank_level_true"

    def step(self, dt: float, io: IOBus) -> None:
        q_in = self.fill_rate if io.get(self.fill_valve_signal, False) else 0.0
        q_out = (
            self.drain_coeff * math.sqrt(max(self.level, 0.0))
            if io.get(self.drain_valve_signal, False)
            else 0.0
        )
        self.level += dt * (q_in - q_out) / self.area
        self.level = max(self.level_min, min(self.level_max, self.level))
        io.set(self.level_signal, self.level)
