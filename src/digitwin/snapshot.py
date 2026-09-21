"""Snapshot / restore: freeze the whole twin to JSON and thaw it again.

A snapshot holds everything that makes the next tick deterministic — the tag
table, the PLC's scan bookkeeping, the plant's internal state (tank levels,
sensor filters, RNG state), the control program's instruction blocks (timer
accumulators, counter values, one-shot edges), the I/O bus signals, and the
executive's clock. Restoring one into a running twin rewinds it; restoring the
same snapshot twice and driving each copy differently is the "branch" of
time-travel.

Plant components and instruction blocks are plain dataclasses, so their state
is captured reflectively: primitive attributes are stored, nested objects are
walked, and anything that isn't representable (open files, sockets, callables)
is skipped. A component that needs to control this can implement
``capture_state()`` / ``restore_state()`` and the walker will defer to it.

Deliberately *not* in a snapshot: the historian and event log. They are the
audit trail of the run, and a rewind should not erase what was observed —
after restoring you can see both branches in the trend.
"""

from __future__ import annotations

import json
import random
import types
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from digitwin.io import IOValue
from digitwin.plc import TagValue

if TYPE_CHECKING:
    from digitwin.executive import Executive

SNAPSHOT_VERSION = 1

# What survives a JSON round-trip.
StateValue = int | float | bool | str | None | list["StateValue"] | dict[str, "StateValue"]

_RANDOM_KEY = "__random__"
_MAX_DEPTH = 8
# Attributes of these kinds are code, not state; capturing them is meaningless.
_CODE_TYPES = (
    types.FunctionType,
    types.BuiltinFunctionType,
    types.MethodType,
    types.ModuleType,
    type,
)


@runtime_checkable
class Snapshotable(Protocol):
    """A component that serializes itself instead of being walked."""

    def capture_state(self) -> StateValue: ...

    def restore_state(self, state: StateValue) -> None: ...


def _is_leaf(value: object) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _capturable(value: object, depth: int) -> bool:
    """Whether :func:`capture_state` can represent ``value`` faithfully.

    Anything else is left out of the capture entirely rather than stored as a
    placeholder that would overwrite the live object on restore.
    """
    if _is_leaf(value):
        return True
    if depth >= _MAX_DEPTH or isinstance(value, _CODE_TYPES):
        return False
    if isinstance(value, random.Random | Snapshotable | list | tuple | Mapping):
        return True
    return isinstance(getattr(value, "__dict__", None), dict)


def capture_state(obj: object, _depth: int = 0) -> StateValue:
    """Reflectively capture ``obj``'s serializable state.

    Leaves are returned as-is; sequences and string-keyed mappings are walked;
    any other object contributes its instance attributes. Attributes that
    cannot be represented (callables, file handles, anything past
    ``_MAX_DEPTH``) are omitted rather than guessed at.
    """
    if _is_leaf(obj):
        return obj  # type: ignore[return-value]
    if isinstance(obj, random.Random):
        version, internal, gauss_next = obj.getstate()
        return {_RANDOM_KEY: [version, list(internal), gauss_next]}
    if isinstance(obj, Snapshotable):
        return obj.capture_state()
    if isinstance(obj, list | tuple):
        # Length is preserved so restore can pair items up positionally.
        return [
            capture_state(item, _depth + 1) if _capturable(item, _depth + 1) else None
            for item in obj
        ]
    if isinstance(obj, Mapping):
        return _capture_items(
            ((k, v) for k, v in obj.items() if isinstance(k, str)), _depth
        )
    attributes = getattr(obj, "__dict__", None)
    if isinstance(attributes, dict):
        return _capture_items(attributes.items(), _depth)
    return None


def _capture_items(
    items: Iterable[tuple[str, object]],
    depth: int,
) -> dict[str, StateValue]:
    return {
        key: capture_state(value, depth + 1)
        for key, value in items
        if _capturable(value, depth + 1)
    }


def restore_state(obj: object, state: StateValue) -> None:
    """Push a :func:`capture_state` result back into a live object."""
    if isinstance(obj, Snapshotable):
        obj.restore_state(state)
        return
    if isinstance(state, dict):
        if isinstance(obj, random.Random) and _RANDOM_KEY in state:
            _restore_random(obj, state[_RANDOM_KEY])
            return
        if isinstance(obj, dict):
            for key, value in state.items():
                _restore_slot(obj, key, value, obj.get(key))
            return
        for key, value in state.items():
            if not hasattr(obj, key):
                continue
            _restore_slot(obj, key, value, getattr(obj, key))
    elif isinstance(state, list) and isinstance(obj, list):
        _restore_sequence(obj, state)


def _placeholder(value: StateValue, current: object) -> bool:
    """True when ``value`` is the ``None`` stand-in for something that wasn't
    capturable — writing it would replace a live object with nothing."""
    return value is None and current is not None and not _is_leaf(current)


def _restore_slot(
    owner: object,
    key: str,
    value: StateValue,
    current: object,
) -> None:
    """Write one captured attribute (or mapping entry) back onto ``owner``."""
    if _placeholder(value, current):
        return
    if isinstance(value, dict) and not _is_leaf(current) and current is not None:
        restore_state(current, value)
        return
    if isinstance(value, list) and isinstance(current, list):
        _restore_sequence(current, value)
        return
    if isinstance(owner, dict):
        owner[key] = value
    else:
        setattr(owner, key, value)


def _restore_sequence(current: list[Any], state: Sequence[StateValue]) -> None:
    if len(current) != len(state):
        # Shape changed since capture; the safest reading is a plain replace.
        current[:] = list(state)
        return
    for index, item in enumerate(state):
        if _placeholder(item, current[index]):
            continue
        if _is_leaf(current[index]) or _is_leaf(item):
            current[index] = item
        else:
            restore_state(current[index], item)


def _restore_random(rng: random.Random, state: StateValue) -> None:
    if not isinstance(state, list) or len(state) != 3:
        return
    version, internal, gauss_next = state
    if not isinstance(version, int) or not isinstance(internal, list):
        return
    if not all(isinstance(word, int) for word in internal):
        return
    words = tuple(word for word in internal if isinstance(word, int))
    next_gauss = gauss_next if isinstance(gauss_next, float) else None
    rng.setstate((version, words, next_gauss))


@dataclass
class Snapshot:
    """A full twin state at one instant, JSON-serializable."""

    scan_count: int
    elapsed: float
    ticks: int = 0
    first_scan: bool = False
    watchdog_tripped: bool = False
    tags: dict[str, TagValue] = field(default_factory=dict)
    bus: dict[str, IOValue] = field(default_factory=dict)
    program: StateValue = None
    plant: StateValue = None
    # Phase 4 fault objects (digitwin.faults) — plain dataclasses, captured
    # the same reflective way as `plant`/`program`. Not included:
    # DropoutFault, which lives on `Executive.transport`, never part of a
    # snapshot's scope (see faults.py's module docstring).
    faults: StateValue = None
    version: int = SNAPSHOT_VERSION

    @classmethod
    def capture(cls, sim: Executive) -> Snapshot:
        """Freeze an executive and everything it drives."""
        plc = sim.plc
        return cls(
            scan_count=plc.scan_count,
            elapsed=sim.elapsed,
            ticks=sim.scan_count,
            first_scan=plc.first_scan,
            watchdog_tripped=plc.watchdog_tripped,
            tags={name: tag.value for name, tag in plc.tags.items()},
            bus=sim.bus.snapshot(),
            program=capture_state(plc.program),
            plant=capture_state(sim.plant),
            faults=capture_state(sim.faults),
        )

    def restore(self, sim: Executive) -> None:
        """Rewind ``sim`` to this state.

        The twin must be the same shape it was captured from — a tag in the
        snapshot that the PLC no longer defines is an error, not something to
        silently skip. ``last_scan_duration`` is not restored: it measures wall
        clock, not simulated state.
        """
        plc = sim.plc
        missing = sorted(set(self.tags) - set(plc.tags))
        if missing:
            raise KeyError(f"snapshot has tags this PLC does not define: {missing}")
        for name, value in self.tags.items():
            plc.tags[name].value = value
        plc.scan_count = self.scan_count
        plc.first_scan = self.first_scan
        plc.watchdog_tripped = self.watchdog_tripped
        plc.input_image = {}
        plc.output_image = {}

        restore_state(plc.program, self.program)
        restore_state(sim.plant, self.plant)
        restore_state(sim.faults, self.faults)
        sim.bus.load(self.bus)

        sim.elapsed = self.elapsed
        sim.scan_count = self.ticks
        sim.reset_pacing()

    # --- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "scan_count": self.scan_count,
            "ticks": self.ticks,
            "elapsed": self.elapsed,
            "first_scan": self.first_scan,
            "watchdog_tripped": self.watchdog_tripped,
            "tags": dict(self.tags),
            "bus": dict(self.bus),
            "program": self.program,
            "plant": self.plant,
            "faults": self.faults,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Snapshot:
        version = int(data.get("version", SNAPSHOT_VERSION))
        if version != SNAPSHOT_VERSION:
            raise ValueError(
                f"snapshot version {version} != supported {SNAPSHOT_VERSION}"
            )
        return cls(
            scan_count=int(data["scan_count"]),
            elapsed=float(data["elapsed"]),
            ticks=int(data.get("ticks", data["scan_count"])),
            first_scan=bool(data.get("first_scan", False)),
            watchdog_tripped=bool(data.get("watchdog_tripped", False)),
            tags=dict(data.get("tags", {})),
            bus=dict(data.get("bus", {})),
            program=data.get("program"),
            plant=data.get("plant"),
            faults=data.get("faults"),
            version=version,
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> Snapshot:
        return cls.from_dict(json.loads(text))

    def save(self, path: Path | str, *, indent: int | None = 2) -> None:
        Path(path).write_text(self.to_json(indent=indent), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> Snapshot:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


@dataclass
class SnapshotRecorder:
    """Takes a snapshot every ``every`` scans and keeps the last ``keep``.

    Hang one on the executive and it captures as the run goes, so you can
    rewind to any kept point afterwards:

        sim.snapshots = SnapshotRecorder(every=50)
        sim.run(200)
        sim.snapshots.rewind(sim, 100)   # back to scan 100, ready to branch
    """

    every: int = 50
    keep: int = 100
    snapshots: list[Snapshot] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.every < 1:
            raise ValueError("every must be at least 1 scan")

    def maybe_capture(self, sim: Executive) -> Snapshot | None:
        """Capture if this tick lands on the interval; called by the executive."""
        if sim.scan_count % self.every != 0:
            return None
        return self.capture(sim)

    def capture(self, sim: Executive) -> Snapshot:
        snapshot = Snapshot.capture(sim)
        self.snapshots.append(snapshot)
        overflow = len(self.snapshots) - self.keep
        if overflow > 0:
            del self.snapshots[:overflow]
        return snapshot

    def at(self, ticks: int) -> Snapshot:
        """The newest kept snapshot at or before executive tick ``ticks``."""
        for snapshot in reversed(self.snapshots):
            if snapshot.ticks <= ticks:
                return snapshot
        raise KeyError(f"no snapshot kept at or before tick {ticks}")

    def rewind(self, sim: Executive, ticks: int) -> Snapshot:
        """Restore ``sim`` to the newest kept snapshot at or before ``ticks``."""
        snapshot = self.at(ticks)
        snapshot.restore(sim)
        return snapshot

    def __len__(self) -> int:
        return len(self.snapshots)
