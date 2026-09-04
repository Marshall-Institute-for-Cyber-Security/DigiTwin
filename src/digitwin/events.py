"""Structured event log: the twin's audit trail.

Where the historian answers "what value did this tag hold?", the event log
answers "what happened, and why?" — restarts and mode changes, watchdog trips,
alarms, injected faults, operator actions. Events are discrete and typed, they
carry arbitrary structured ``data``, and they share the historian's sinks so a
run can persist both streams the same way.

The executive logs the events it can see on its own (start, restart, watchdog
trip, snapshot / restore); programs and the HMI log alarms and operator
actions through :meth:`EventLog.log`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from digitwin.historian import RecordSink, RowValue

EVENT_FIELDS = ("timestamp", "scan", "category", "severity", "source", "message", "data")


class EventCategory(Enum):
    """What kind of thing happened."""

    MODE = "mode"          # run/stop, cold or warm restart, executive start
    ALARM = "alarm"        # a process condition the operator must see
    WATCHDOG = "watchdog"  # a scan overran its budget
    FAULT = "fault"        # an injected fault (Phase 7) or a transport failure
    OPERATOR = "operator"  # a human (or the HMI) acted on the twin
    SYSTEM = "system"      # snapshot, restore, replay — the harness itself


class EventSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    """One thing that happened, at one simulation timestamp."""

    timestamp: float
    category: EventCategory
    message: str
    severity: EventSeverity = EventSeverity.INFO
    source: str = ""
    scan: int | None = None
    data: dict[str, RowValue] = field(default_factory=dict)

    def as_row(self) -> dict[str, RowValue]:
        """Flatten for a sink; ``data`` is carried as a JSON string so the row
        stays one flat record in CSV and SQLite alike."""
        return {
            "timestamp": self.timestamp,
            "scan": self.scan,
            "category": self.category.value,
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "data": json.dumps(self.data),
        }


@dataclass
class EventLog:
    """Bounded log of :class:`Event` records with an optional sink.

    Like the historian this is an observer: nothing in the engine depends on
    it, and a twin without one behaves identically.
    """

    capacity: int = 10_000
    sink: RecordSink | None = None

    events: list[Event] = field(default_factory=list, init=False, repr=False)
    dropped: int = field(default=0, init=False)

    def log(
        self,
        timestamp: float,
        category: EventCategory,
        message: str,
        *,
        severity: EventSeverity = EventSeverity.INFO,
        source: str = "",
        scan: int | None = None,
        **data: RowValue,
    ) -> Event:
        """Record an event; extra keyword arguments become its ``data``."""
        event = Event(
            timestamp=timestamp,
            category=category,
            message=message,
            severity=severity,
            source=source,
            scan=scan,
            data=dict(data),
        )
        self.events.append(event)
        overflow = len(self.events) - self.capacity
        if overflow > 0:
            del self.events[:overflow]
            self.dropped += overflow
        if self.sink is not None:
            self.sink.write([event.as_row()])
        return event

    def query(
        self,
        *,
        category: EventCategory | None = None,
        severity: EventSeverity | None = None,
        source: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> list[Event]:
        """Events matching every filter given, in the order they happened."""
        return [
            event
            for event in self.events
            if (category is None or event.category is category)
            and (severity is None or event.severity is severity)
            and (source is None or event.source == source)
            and (start is None or event.timestamp >= start)
            and (end is None or event.timestamp <= end)
        ]

    def latest(self) -> Event | None:
        return self.events[-1] if self.events else None

    def clear(self) -> None:
        self.events.clear()
        self.dropped = 0

    def close(self) -> None:
        if self.sink is not None:
            self.sink.close()

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)
