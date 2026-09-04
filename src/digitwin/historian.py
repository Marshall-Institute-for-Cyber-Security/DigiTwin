"""Historian: a ring buffer of tag samples plus optional persistent sinks.

The historian is a pure observer — it never touches the scan cycle. The
executive hands it the tag table once per tick and it stores
``(timestamp, tag, value)`` samples, either on change (the default, and what a
real historian does) or at a fixed sample rate. Timestamps are *simulation*
seconds, so a trace is reproducible regardless of how fast the run was paced.

Samples live in a bounded buffer; a sink (CSV / SQLite / JSON Lines) can mirror
them to disk as they arrive. Sinks are shared with :mod:`digitwin.events`,
which persists its rows the same way.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import IO, Protocol

from digitwin.plc import TagValue

# One persisted row: flat, primitive, and the same shape for every sink.
RowValue = int | float | bool | str | None
Row = Mapping[str, RowValue]

SAMPLE_FIELDS = ("timestamp", "tag", "value")

_EPS = 1e-9


class RecordSink(Protocol):
    """A persistent destination for historian samples or event rows.

    ``write`` takes a batch — the historian emits one batch per scan, so a
    file or database sink pays its overhead once per scan, not once per tag.
    """

    def write(self, rows: Sequence[Row]) -> None: ...

    def close(self) -> None: ...


def _check_identifiers(names: Sequence[str]) -> None:
    for name in names:
        if not name.isidentifier():
            raise ValueError(f"{name!r} is not a valid SQL identifier")


@dataclass
class CsvSink:
    """Appends rows to a CSV file, writing the header on the first batch.

    The file handle stays open for the sink's lifetime; call :meth:`close`
    (or use the sink as a context manager) when the run finishes.
    """

    path: Path | str
    fields: Sequence[str] = SAMPLE_FIELDS
    _file: IO[str] | None = field(default=None, init=False, repr=False)

    def write(self, rows: Sequence[Row]) -> None:
        if not rows:
            return
        if self._file is None:
            # Held open across batches, so this handle outlives a with-block.
            self._file = open(self.path, "w", newline="", encoding="utf-8")  # noqa: SIM115
            csv.writer(self._file).writerow(self.fields)
        csv.writer(self._file).writerows(
            [[row.get(name) for name in self.fields] for row in rows]
        )
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> CsvSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class SqliteSink:
    """Inserts rows into a SQLite table, created on first write.

    Columns are declared without an affinity, so SQLite keeps ints, floats and
    text as written — a historian holds all three.
    """

    path: Path | str
    table: str = "samples"
    fields: Sequence[str] = SAMPLE_FIELDS
    _conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _check_identifiers([self.table, *self.fields])

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            columns = ", ".join(self.fields)
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table} ({columns})")
        return self._conn

    def write(self, rows: Sequence[Row]) -> None:
        if not rows:
            return
        conn = self._connection()
        columns = ", ".join(self.fields)
        placeholders = ", ".join("?" * len(self.fields))
        conn.executemany(
            f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders})",
            [tuple(row.get(name) for name in self.fields) for row in rows],
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SqliteSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class JsonlSink:
    """Writes one JSON object per line — for rows carrying nested data, as
    event rows do."""

    path: Path | str
    _file: IO[str] | None = field(default=None, init=False, repr=False)

    def write(self, rows: Sequence[Row]) -> None:
        if not rows:
            return
        if self._file is None:
            # Held open across batches, so this handle outlives a with-block.
            self._file = open(self.path, "w", encoding="utf-8")  # noqa: SIM115
        for row in rows:
            self._file.write(json.dumps(dict(row)) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SampleMode(Enum):
    """When the historian takes a sample of a tag."""

    ON_CHANGE = "on_change"    # only when the value differs from the stored one
    PERIODIC = "periodic"      # every tag, once per `interval_s` of sim time
    EVERY_SCAN = "every_scan"  # every tag, every scan


@dataclass(frozen=True)
class Sample:
    timestamp: float
    tag: str
    value: TagValue

    def as_row(self) -> dict[str, RowValue]:
        return {"timestamp": self.timestamp, "tag": self.tag, "value": self.value}


@dataclass
class Historian:
    """Bounded trend store for tag values.

    ``tags`` limits what is recorded (``None`` records everything the caller
    passes). Once ``capacity`` samples are held the oldest are dropped and
    :attr:`dropped` counts how many — a wrapped buffer can no longer
    reconstruct the early tag table, so a long run wants a sink as well.
    """

    capacity: int = 100_000
    mode: SampleMode = SampleMode.ON_CHANGE
    interval_s: float = 1.0
    tags: frozenset[str] | None = None
    sink: RecordSink | None = None

    samples: list[Sample] = field(default_factory=list, init=False, repr=False)
    dropped: int = field(default=0, init=False)
    _last: dict[str, TagValue] = field(default_factory=dict, init=False, repr=False)
    _next_periodic: float | None = field(default=None, init=False, repr=False)

    def record(self, timestamp: float, values: Mapping[str, TagValue]) -> list[Sample]:
        """Take a sample of ``values`` at ``timestamp``; returns what was stored."""
        if self.mode is SampleMode.PERIODIC:
            if self._next_periodic is None:
                self._next_periodic = timestamp
            if timestamp + _EPS < self._next_periodic:
                return []
            self._next_periodic += self.interval_s

        new: list[Sample] = []
        for name, value in values.items():
            if self.tags is not None and name not in self.tags:
                continue
            if self.mode is SampleMode.ON_CHANGE and self._unchanged(name, value):
                continue
            self._last[name] = value
            new.append(Sample(timestamp, name, value))

        if new:
            self._append(new)
            if self.sink is not None:
                self.sink.write([sample.as_row() for sample in new])
        return new

    def _unchanged(self, name: str, value: TagValue) -> bool:
        if name not in self._last:
            return False
        previous = self._last[name]
        # `True == 1` in Python but a bit and a word are not the same reading,
        # so a type change counts as a change.
        return previous == value and type(previous) is type(value)

    def _append(self, new: Sequence[Sample]) -> None:
        self.samples.extend(new)
        overflow = len(self.samples) - self.capacity
        if overflow > 0:
            del self.samples[:overflow]
            self.dropped += overflow

    # --- query API ---------------------------------------------------------

    def query(
        self,
        tag: str | None = None,
        *,
        start: float | None = None,
        end: float | None = None,
    ) -> list[Sample]:
        """Samples for one tag (or all), within an inclusive time window."""
        return [
            sample
            for sample in self.samples
            if (tag is None or sample.tag == tag)
            and (start is None or sample.timestamp >= start)
            and (end is None or sample.timestamp <= end)
        ]

    def series(
        self,
        tag: str,
        *,
        start: float | None = None,
        end: float | None = None,
    ) -> list[tuple[float, TagValue]]:
        """A trend for one tag as ``(timestamp, value)`` pairs."""
        return [(s.timestamp, s.value) for s in self.query(tag, start=start, end=end)]

    def latest(self, tag: str) -> Sample | None:
        """The most recent sample of ``tag``, or ``None`` if it has none."""
        for sample in reversed(self.samples):
            if sample.tag == tag:
                return sample
        return None

    def value_at(self, tag: str, timestamp: float) -> TagValue | None:
        """The value ``tag`` held at ``timestamp`` — the last sample at or
        before it, since on-change recording only stores transitions."""
        found: TagValue | None = None
        for sample in self.samples:
            if sample.timestamp > timestamp:
                break
            if sample.tag == tag:
                found = sample.value
        return found

    def snapshot_at(self, timestamp: float) -> dict[str, TagValue]:
        """Reconstruct the whole recorded tag table as of ``timestamp``."""
        table: dict[str, TagValue] = {}
        for sample in self.samples:
            if sample.timestamp > timestamp:
                break
            table[sample.tag] = sample.value
        return table

    def tag_names(self) -> set[str]:
        return {sample.tag for sample in self.samples}

    def span(self) -> tuple[float, float] | None:
        """``(first, last)`` timestamp held, or ``None`` when empty."""
        if not self.samples:
            return None
        return self.samples[0].timestamp, self.samples[-1].timestamp

    def clear(self) -> None:
        """Drop every stored sample; the change detector restarts too, so the
        next :meth:`record` writes a full tag table again."""
        self.samples.clear()
        self._last.clear()
        self._next_periodic = None
        self.dropped = 0

    def close(self) -> None:
        if self.sink is not None:
            self.sink.close()

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self) -> Iterator[Sample]:
        return iter(self.samples)
