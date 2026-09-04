"""I/O bus: the coupling layer between a PLC and a plant model.

The plant and the control program never touch each other. They exchange
values only through an :class:`IOBus` — the plant writes sensor readings and
reads actuator commands; a transport moves those values across the PLC
boundary. The in-process transport wires a local plant to a local PLC; later
phases swap it for OPC UA / Modbus without touching either side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from digitwin.plc import PLC, TagValue

# Bus signals carry the plant's continuous state too, so they are wider than a
# tag value. The transport narrows floats back to ``TagValue`` at the boundary.
IOValue = int | float | bool


class TransportError(Exception):
    """An I/O transport failed to move values across the PLC boundary (timeout,
    dropped connection, protocol fault). The in-process transport never raises
    it; the OPC UA / Modbus adapters (Phase 6) will."""


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


class IOTransport(Protocol):
    """Moves values across the PLC I/O boundary.

    ``read_inputs`` returns ``{input tag name: value}`` for every wired input;
    ``write_outputs`` accepts ``{output tag name: value}`` for every output the
    PLC produced and forwards the ones it knows. The signature is intentionally
    batch-oriented so an OPC UA / Modbus adapter can implement it with a single
    round-trip. A networked implementation raises :class:`TransportError` when a
    round-trip fails.
    """

    def read_inputs(self) -> dict[str, TagValue]: ...

    def write_outputs(self, outputs: dict[str, TagValue]) -> None: ...


@dataclass
class InProcessTransport:
    """Couples a plant and a PLC that share one :class:`IOBus` in this process.

    ``inputs`` and ``outputs`` map a **physical terminal** (a native address,
    e.g. ``"%IW0.0"``) to the bus signal wired there — field wiring connects
    to a terminal, not to whatever name a program happens to give the tag at
    that address. ``plc`` resolves each terminal to its current tag via
    :meth:`PLC.tag_name_at`, so swapping which tag a program puts at an
    address doesn't require touching this wiring.

    A wired terminal with no tag defined there is a wiring/program mismatch,
    not something to silently drop — it raises ``ValueError``.
    """

    plc: PLC
    bus: IOBus
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    def read_inputs(self) -> dict[str, TagValue]:
        values: dict[str, TagValue] = {}
        for native_address, signal in self.inputs.items():
            tag_name = self._resolve(native_address, signal)
            raw = self.bus.get(signal)
            values[tag_name] = bool(raw) if isinstance(raw, bool) else int(raw)
        return values

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        for native_address, signal in self.outputs.items():
            tag_name = self._resolve(native_address, signal)
            self.bus.set(signal, outputs[tag_name])

    def _resolve(self, native_address: str, signal: str) -> str:
        tag_name = self.plc.tag_name_at(native_address)
        if tag_name is None:
            raise ValueError(
                f"terminal {native_address!r} is wired to bus signal {signal!r} "
                f"but no tag on {type(self.plc).__name__} claims that address"
            )
        return tag_name
