"""I/O bus: the coupling layer between a PLC and a plant model.

The plant and the control program never touch each other. They exchange
values only through an :class:`IOBus` — the plant writes sensor readings and
reads actuator commands; a transport moves those values across the PLC
boundary. The in-process transport wires a local plant to a local PLC; later
phases swap it for OPC UA / Modbus without touching either side.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from digitwin.plc import PLC, TagValue

# Bus signals carry the plant's continuous state too, so they are wider than a
# tag value. The transport narrows floats back to ``TagValue`` at the boundary.
IOValue = int | float | bool


class TransportError(Exception):
    """An I/O transport failed to move values across the PLC boundary (timeout,
    dropped connection, protocol fault).

    ``partial_inputs`` carries whichever input values were read successfully
    before the failure. A networked read walks a register map in blocks (one
    round trip per contiguous group), so one block timing out shouldn't
    discard the ones that already came back — the executive applies these and
    holds the last value for everything else. Writes have nothing to apply
    back, so ``partial_inputs`` is always empty there.

    The in-process transport never raises it; the OPC UA / Modbus adapters
    (Phase 6) will."""

    def __init__(
        self, message: str, *, partial_inputs: Mapping[str, TagValue] | None = None
    ) -> None:
        super().__init__(message)
        self.partial_inputs: dict[str, TagValue] = (
            dict(partial_inputs) if partial_inputs else {}
        )


class IOBus:
    """A flat namespace of named signal values shared by plant and transport.

    Direction is a convention, not enforced: the plant calls :meth:`set` for
    sensor readings and :meth:`get` for actuator commands; the transport does
    the mirror image.
    """

    def __init__(self) -> None:
        self._signals: dict[str, IOValue] = {}

    def get(self, name: str, default: IOValue = 0) -> IOValue:
        return self._signals.get(name, default)

    def set(self, name: str, value: IOValue) -> None:
        self._signals[name] = value

    def snapshot(self) -> dict[str, IOValue]:
        """A copy of every signal — for the historian / debugging."""
        return dict(self._signals)

    def load(self, signals: Mapping[str, IOValue]) -> None:
        """Replace every signal with a previously taken :meth:`snapshot` —
        used by snapshot restore, which must rewind the bus with the plant."""
        self._signals = dict(signals)


class IOTransport(Protocol):
    """Moves values across the PLC I/O boundary.

    ``read_inputs`` returns ``{input tag name: value}`` for every wired input;
    ``write_outputs`` accepts ``{output tag name: value}`` for every output the
    PLC produced and forwards the ones it knows. The signature is intentionally
    batch-oriented so an OPC UA / Modbus adapter can implement it with a single
    round-trip — or several, for a register map split across blocks.

    A round-trip that fails outright raises :class:`TransportError`. A read
    that fails partway through raises it too, with ``partial_inputs`` set to
    whatever was read before the failing block, so a scan's worth of good
    reads isn't discarded over one bad one. Retry policy — attempts, timeout —
    belongs to the transport, not the caller: by the time ``TransportError``
    reaches the executive, the exchange is down for this scan.
    """

    def read_inputs(self) -> dict[str, TagValue]: ...

    def write_outputs(self, outputs: dict[str, TagValue]) -> None: ...


@dataclass
class InProcessTransport:
    """Couples a plant and a PLC that share one :class:`IOBus` in this process.

    ``inputs`` maps each physical input channel — a native address, e.g.
    ``"%IW0.0"`` — to the bus signal that feeds it; ``outputs`` maps each
    output channel to the bus signal it drives. Wiring is by address, not tag
    name: a terminal's wiring shouldn't have to change just because a program
    renames the tag sitting on it. ``plc`` resolves each address to its
    owning tag via :meth:`~digitwin.plc.PLC.tag_at`.

    ``plc`` is only required when ``inputs`` or ``outputs`` is non-empty —
    there is nothing to resolve otherwise. A wired channel with no tag
    claiming it is a wiring/program mismatch, not something to silently
    drop — it raises ``ValueError``.
    """

    bus: IOBus
    plc: PLC | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.inputs or self.outputs) and self.plc is None:
            raise ValueError("InProcessTransport needs a plc to resolve addressed channels")

    def read_inputs(self) -> dict[str, TagValue]:
        values: dict[str, TagValue] = {}
        for address, signal in self.inputs.items():
            tag_name = self._resolve(address, signal)
            raw = self.bus.get(signal)
            values[tag_name] = bool(raw) if isinstance(raw, bool) else int(raw)
        return values

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        for address, signal in self.outputs.items():
            tag_name = self._resolve(address, signal)
            self.bus.set(signal, outputs[tag_name])

    def _resolve(self, address: str, signal: str) -> str:
        assert self.plc is not None  # guaranteed by __post_init__ once wired
        tag_name = self.plc.tag_at(address)
        if tag_name is None:
            raise ValueError(
                f"terminal {address!r} is wired to bus signal {signal!r} but "
                f"no tag on {type(self.plc).__name__} claims that address"
            )
        return tag_name
