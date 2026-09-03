"""Plant model protocol and a container that steps components in order."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from digitwin.io import IOBus


class PlantModel(Protocol):
    """A piece of simulated physics advanced one time step at a time.

    ``step`` reads actuator commands from the bus and writes sensor readings
    back. It must not touch the PLC or its tags directly.
    """

    def step(self, dt: float, io: IOBus) -> None: ...


@dataclass
class CompositePlant:
    """A plant assembled from components, stepped in declaration order.

    Order matters: put source components (tanks, motors) before the sensors
    that read their state so a reading reflects the same tick's physics.
    """

    components: list[PlantModel] = field(default_factory=list)

    def step(self, dt: float, io: IOBus) -> None:
        for component in self.components:
            component.step(dt, io)
