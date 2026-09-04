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
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from digitwin.adapters.modbus import ModbusSlaveServer
from digitwin.events import EventCategory, EventLog, EventSeverity
from digitwin.historian import Historian
from digitwin.io import IOBus, IOTransport, TransportError
from digitwin.plant.base import PlantModel
from digitwin.plc import PLC, TagType, TagValue
from digitwin.snapshot import SnapshotRecorder


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

    # Observers (Phase 3). All optional: a twin without them behaves the same.
    historian: Historian | None = None
    events: EventLog | None = None
    snapshots: SnapshotRecorder | None = None
    # ModbusSlaveServer isn't a passive observer like the three above — its
    # `accept` map writes into tags — but it's synced from the same once-per-
    # tick hook, never mid-scan. See digitwin.adapters.modbus.
    modbus_slave: ModbusSlaveServer | None = None

    scan_count: int = 0
    elapsed: float = 0.0
    last_scan_s: float = 0.0
    last_jitter_s: float = 0.0
    worst_jitter_s: float = 0.0

    # Transport health (hold-last on read failure, fault event on both edges).
    # Retry policy lives in the transport; by the time TransportError reaches
    # here, this scan's exchange is down. See TransportError.partial_inputs.
    consecutive_read_failures: int = 0
    consecutive_write_failures: int = 0

    _deadline: float | None = field(default=None, repr=False)
    _watchdog_seen: bool = field(default=False, repr=False)

    @property
    def transport_ok(self) -> bool:
        """False while either direction is mid-fault (see the *_failures counters)."""
        return self.consecutive_read_failures == 0 and self.consecutive_write_failures == 0

    def tick(self) -> None:
        self.plant.step(self.dt, self.bus)
        self._transfer_inputs()
        self.plc.scan()
        self._transfer_outputs()

        self.scan_count += 1
        self.elapsed += self.dt
        self.last_scan_s = self.plc.last_scan_duration
        self._observe()
        self._pace()

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.tick()

    def run_for(self, sim_seconds: float) -> None:
        self.run(round(sim_seconds / self.dt))

    def _transfer_inputs(self) -> None:
        try:
            values = self.transport.read_inputs()
        except TransportError as exc:
            # Hold-last: apply whatever this block-oriented read did recover,
            # and leave every other input tag at its previous value.
            self._apply_inputs(exc.partial_inputs)
            self._on_transport_failure(exc, "input read", is_write=False)
            return
        self._apply_inputs(values)
        self._on_transport_recovery(is_write=False)

    def _apply_inputs(self, values: Mapping[str, TagValue]) -> None:
        for tag_name, value in values.items():
            self.plc.tags[tag_name].value = value

    def _transfer_outputs(self) -> None:
        outputs: dict[str, TagValue] = {
            name: tag.value
            for name, tag in self.plc.tags.items()
            if tag.tag_type in (TagType.DISCRETE_OUTPUT, TagType.ANALOG_OUTPUT)
        }
        try:
            self.transport.write_outputs(outputs)
        except TransportError as exc:
            self._on_transport_failure(exc, "output write", is_write=True)
            return
        self._on_transport_recovery(is_write=True)

    def _on_transport_failure(self, exc: TransportError, label: str, *, is_write: bool) -> None:
        """Log once on fault entry, not every scan a sustained fault persists.

        A failed write means the PLC's output_image has diverged from what the
        field device actually holds (the command never arrived) — worse than a
        stale read, so it's always an ERROR; a stale read is a WARNING.
        """
        if is_write:
            was_ok = self.consecutive_write_failures == 0
            self.consecutive_write_failures += 1
        else:
            was_ok = self.consecutive_read_failures == 0
            self.consecutive_read_failures += 1
        if was_ok and self.events is not None:
            self.events.log(
                self.elapsed,
                EventCategory.FAULT,
                f"{label} failed: {exc}",
                severity=EventSeverity.ERROR if is_write else EventSeverity.WARNING,
                source=self.plc.name,
                scan=self.plc.scan_count,
            )

    def _on_transport_recovery(self, *, is_write: bool) -> None:
        failures = (
            self.consecutive_write_failures if is_write else self.consecutive_read_failures
        )
        if failures and self.events is not None:
            self.events.log(
                self.elapsed,
                EventCategory.FAULT,
                f"{'output write' if is_write else 'input read'} recovered",
                severity=EventSeverity.INFO,
                source=self.plc.name,
                scan=self.plc.scan_count,
                failed_scans=failures,
            )
        if is_write:
            self.consecutive_write_failures = 0
        else:
            self.consecutive_read_failures = 0

    def _observe(self) -> None:
        """Feed the Phase 3 observers, after the tick's state has settled.

        Timestamps are simulation seconds (``elapsed``), so a trace is
        identical in every pacing mode.
        """
        if self.events is not None and self.plc.watchdog_tripped != self._watchdog_seen:
            if self.plc.watchdog_tripped:
                self.events.log(
                    self.elapsed,
                    EventCategory.WATCHDOG,
                    f"scan overran the {self.plc.watchdog_s:.3f}s watchdog budget",
                    severity=EventSeverity.ERROR,
                    source=self.plc.name,
                    scan=self.plc.scan_count,
                    scan_duration_s=self.plc.last_scan_duration,
                )
            self._watchdog_seen = self.plc.watchdog_tripped

        if self.historian is not None:
            self.historian.record(
                self.elapsed,
                {name: tag.value for name, tag in self.plc.tags.items()},
            )

        if self.snapshots is not None:
            self.snapshots.maybe_capture(self)

        if self.modbus_slave is not None:
            self.modbus_slave.sync()

    def reset_pacing(self) -> None:
        """Forget the wall-clock cadence origin, so the next tick re-establishes
        it. Snapshot restore calls this: rewinding simulated time says nothing
        about wall time, and a stale deadline would burn the difference in one
        long sleep."""
        self._deadline = None

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
