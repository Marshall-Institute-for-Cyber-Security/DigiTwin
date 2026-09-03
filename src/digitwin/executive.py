"""Executive: the tick loop that advances plant and PLC together.

Phase 1 keeps this deliberately minimal — a fixed ``dt``, run as fast as the
CPU allows. Phase 2 adds real-time / scaled / free-run modes, jitter
recording, and firmware realism (first-scan bit, watchdog, cold start).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from digitwin.io import IOBus, IOTransport
from digitwin.plant.base import PlantModel
from digitwin.plc import PLC, TagType, TagValue


@dataclass
class Executive:
    """Drives one tick: ``plant.step`` -> transfer inputs -> ``plc.scan`` ->
    transfer outputs.
    """

    plc: PLC
    plant: PlantModel
    bus: IOBus
    transport: IOTransport
    dt: float = 0.1
    scan_count: int = 0
    elapsed: float = field(default=0.0)

    def tick(self) -> None:
        self.plant.step(self.dt, self.bus)

        for tag_name, value in self.transport.read_inputs().items():
            self.plc.tags[tag_name].value = value

        self.plc.scan()

        outputs: dict[str, TagValue] = {
            name: tag.value
            for name, tag in self.plc.tags.items()
            if tag.tag_type == TagType.DISCRETE_OUTPUT
        }
        self.transport.write_outputs(outputs)

        self.scan_count += 1
        self.elapsed += self.dt

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.tick()
