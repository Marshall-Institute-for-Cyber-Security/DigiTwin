"""Executive: the timed loop that advances plant and PLC together.

Per tick: ``plant.step(dt)`` -> transfer inputs -> ``plc.scan()`` -> transfer
outputs. Pacing modes:

* ``FREE_RUN``  — no sleeping; for tests and batch scenarios.
* ``REAL_TIME`` — hold each tick to ``dt`` wall-clock seconds.
* ``SCALED``    — hold each tick to ``dt / scale`` wall seconds; ``scale`` > 1
  runs faster than real time, < 1 slower.

Pacing only inserts sleeps between ticks — it never changes ``dt``, so the
simulation maths is identical in every mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from digitwin.io import IOBus, IOTransport
from digitwin.plant.base import PlantModel
from digitwin.plc import PLC, TagType, TagValue


class ExecutiveMode(Enum):
    FREE_RUN = "free_run"
    REAL_TIME = "real_time"
    SCALED = "scaled"


@dataclass
class Executive:
    plc: PLC
    plant: PlantModel
    bus: IOBus
    transport: IOTransport
    dt: float = 0.1
    mode: ExecutiveMode = ExecutiveMode.FREE_RUN
    scale: float = 1.0

    scan_count: int = 0
    elapsed: float = 0.0
    last_scan_s: float = 0.0
    last_jitter_s: float = 0.0
    worst_jitter_s: float = 0.0

    _deadline: float | None = field(default=None, repr=False)

    def tick(self) -> None:
        self.plant.step(self.dt, self.bus)
        self._transfer_inputs()
        self.plc.scan()
        self._transfer_outputs()

        self.scan_count += 1
        self.elapsed += self.dt
        self.last_scan_s = self.plc.last_scan_duration
        self._pace()

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.tick()

    def run_for(self, sim_seconds: float) -> None:
        self.run(round(sim_seconds / self.dt))

    def _transfer_inputs(self) -> None:
        for tag_name, value in self.transport.read_inputs().items():
            self.plc.tags[tag_name].value = value

    def _transfer_outputs(self) -> None:
        outputs: dict[str, TagValue] = {
            name: tag.value
            for name, tag in self.plc.tags.items()
            if tag.tag_type in (TagType.DISCRETE_OUTPUT, TagType.ANALOG_OUTPUT)
        }
        self.transport.write_outputs(outputs)

    def _wall_interval(self) -> float:
        if self.mode is ExecutiveMode.REAL_TIME:
            return self.dt
        if self.mode is ExecutiveMode.SCALED:
            return self.dt / self.scale
        return 0.0

    def _pace(self) -> None:
        interval = self._wall_interval()
        if interval <= 0.0:
            return
        now = time.perf_counter()
        deadline = self._deadline
        if deadline is None:
            self._deadline = now  # establish the cadence origin; don't sleep yet
            return
        deadline += interval
        slack = deadline - now
        if slack > 0:
            time.sleep(slack)
            self.last_jitter_s = 0.0
        else:
            self.last_jitter_s = -slack  # ran long; how far past the deadline
            deadline = now  # resync so the clock doesn't spiral
        self._deadline = deadline
        self.worst_jitter_s = max(self.worst_jitter_s, self.last_jitter_s)
