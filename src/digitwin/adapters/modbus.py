"""Modbus TCP: two distinct roles, do not conflate them.

``ModbusClientTransport`` is the twin as Modbus *master* — an ``IOTransport``
that polls a remote slave owning the real I/O (or an external plant
simulation). It sits on the plant<->PLC boundary exactly like
:class:`~digitwin.io.InProcessTransport`, wired by native PLC address rather
than tag name.

``ModbusSlaveServer`` is the twin as Modbus *slave* — a side window onto the
tag table for an external SCADA/HMI (or a real master) to read and write.
It is **not** an ``IOTransport`` and is never on the scan path: it's synced
once per tick from ``Executive._observe()``, same as the historian.

Both require ``pymodbus`` (``pip install digitwin[modbus]``). The dependency
is imported lazily — on first :meth:`ModbusClientTransport.connect` or
:meth:`ModbusSlaveServer.start` — so importing this module, or the base
``digitwin`` package that re-exports it, never requires pymodbus to be
installed.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from digitwin.io import TransportError
from digitwin.plc import PLC, TagValue

RegisterKind = Literal["coil", "discrete_input", "holding_register", "input_register"]
WordOrder = Literal["big", "little"]

_INPUT_KINDS: tuple[RegisterKind, ...] = ("discrete_input", "input_register")
_OUTPUT_KINDS: tuple[RegisterKind, ...] = ("coil", "holding_register")
_BIT_KINDS: tuple[RegisterKind, ...] = ("coil", "discrete_input")


def _import_client() -> Any:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as exc:
        raise ImportError(
            "ModbusClientTransport requires pymodbus - install with "
            "`pip install digitwin[modbus]`"
        ) from exc
    return ModbusTcpClient


def _swap_bytes(word: int) -> int:
    return ((word & 0xFF) << 8) | ((word >> 8) & 0xFF)


@dataclass(frozen=True)
class RegisterMap:
    """Where one PLC channel sits in the remote's Modbus map.

    ``coil`` / ``discrete_input`` are single bits — no scaling applies.
    ``holding_register`` / ``input_register`` are 16-bit words; ``length=2``
    spans a pair of registers combined into a 32-bit value. ``word_order``
    picks which register carries the high half; ``byte_order`` swaps the two
    bytes *within* each register (some devices transmit byte-swapped words).
    ``scale`` / ``offset`` convert the raw 16/32-bit count to the PLC's
    engineering-unit int: ``tag_value = round(raw * scale + offset)`` on
    read, inverted on write.
    """

    kind: RegisterKind
    address: int
    length: int = 1
    word_order: WordOrder = "big"
    byte_order: WordOrder = "big"
    scale: float = 1.0
    offset: float = 0.0

    def __post_init__(self) -> None:
        if self.length not in (1, 2):
            raise ValueError("RegisterMap.length must be 1 or 2 (16- or 32-bit)")
        if self.kind in _BIT_KINDS and self.length != 1:
            raise ValueError(f"{self.kind} is a single bit; length must be 1")

    def encode(self, value: TagValue) -> list[int]:
        """A PLC tag value -> the words to write on the wire."""
        if self.kind in _BIT_KINDS:
            return [1 if value else 0]
        raw = round((float(value) - self.offset) / self.scale)
        raw &= 0xFFFFFFFF if self.length == 2 else 0xFFFF
        if self.length == 1:
            words = [raw]
        elif self.word_order == "big":
            words = [(raw >> 16) & 0xFFFF, raw & 0xFFFF]
        else:
            words = [raw & 0xFFFF, (raw >> 16) & 0xFFFF]
        return [_swap_bytes(w) if self.byte_order == "little" else w for w in words]

    def decode(self, words: list[int]) -> TagValue:
        """The words read off the wire -> a PLC tag value."""
        if self.kind in _BIT_KINDS:
            return bool(words[0])
        unswapped = [_swap_bytes(w) if self.byte_order == "little" else w for w in words]
        if self.length == 1:
            raw = unswapped[0]
        elif self.word_order == "big":
            hi, lo = unswapped
            raw = (hi << 16) | lo
        else:
            lo, hi = unswapped
            raw = (hi << 16) | lo
        return round(raw * self.scale + self.offset)


def _blocks(entries: list[tuple[str, RegisterMap]]) -> list[list[tuple[str, RegisterMap]]]:
    """Group same-kind entries into contiguous address runs, so a read or
    write costs one round trip per contiguous group instead of one per tag."""
    ordered = sorted(entries, key=lambda entry: entry[1].address)
    blocks: list[list[tuple[str, RegisterMap]]] = []
    next_free = -1
    for entry in ordered:
        reg = entry[1]
        if blocks and reg.address <= next_free:
            blocks[-1].append(entry)
        else:
            blocks.append([entry])
        next_free = max(next_free, reg.address + reg.length)
    return blocks


@dataclass
class ModbusClientTransport:
    """The twin as a Modbus TCP master, polling one remote slave.

    Wiring is by native PLC address, the same convention as
    :class:`~digitwin.io.InProcessTransport`: ``inputs`` maps each address the
    PLC reads (``RegisterMap.kind`` ``discrete_input`` / ``input_register``)
    to its slot in the slave's map; ``outputs`` maps each address the PLC
    drives (``coil`` / ``holding_register``). ``plc`` resolves each address to
    its owning tag via :meth:`~digitwin.plc.PLC.tag_at`; an address with no
    tag claiming it is dropped before the round trip, same as
    ``InProcessTransport``.
    """

    host: str
    plc: PLC
    inputs: dict[str, RegisterMap] = field(default_factory=dict)
    outputs: dict[str, RegisterMap] = field(default_factory=dict)
    port: int = 502
    unit_id: int = 1
    timeout: float = 3.0
    _client: Any = field(default=None, repr=False, init=False, compare=False)

    def __post_init__(self) -> None:
        for address, reg in self.inputs.items():
            if reg.kind not in _INPUT_KINDS:
                raise ValueError(f"input {address!r} maps to {reg.kind!r}, not a readable kind")
        for address, reg in self.outputs.items():
            if reg.kind not in _OUTPUT_KINDS:
                raise ValueError(f"output {address!r} maps to {reg.kind!r}, not a writable kind")

    def connect(self) -> None:
        """Open the TCP connection. Idempotent; ``read_inputs`` /
        ``write_outputs`` call it automatically. Raises TransportError if the
        slave refuses the connection."""
        if self._client is not None:
            return
        client = _import_client()(self.host, port=self.port, timeout=self.timeout)
        if not client.connect():
            raise TransportError(f"could not connect to Modbus slave at {self.host}:{self.port}")
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def read_inputs(self) -> dict[str, TagValue]:
        self.connect()
        raw: dict[str, TagValue] = {}
        for kind in _INPUT_KINDS:
            for block in _blocks(self._live_entries(self.inputs, kind)):
                try:
                    raw.update(self._read_block(block, kind))
                except TransportError as exc:
                    raise TransportError(str(exc), partial_inputs=raw) from exc
        return raw

    def write_outputs(self, outputs: Mapping[str, TagValue]) -> None:
        self.connect()
        for kind in _OUTPUT_KINDS:
            for block in _blocks(self._live_entries(self.outputs, kind)):
                self._write_block(block, kind, outputs)

    def _live_entries(
        self, channels: dict[str, RegisterMap], kind: RegisterKind
    ) -> list[tuple[str, RegisterMap]]:
        """(tag name, map) for every channel of `kind` whose address is
        currently claimed by a tag. Downstream code keys by tag name, like
        every other transport; an unclaimed address is dropped here, same as
        ``InProcessTransport``."""
        live = []
        for address, reg in channels.items():
            if reg.kind != kind:
                continue
            tag_name = self.plc.tag_at(address)
            if tag_name is not None:
                live.append((tag_name, reg))
        return live

    def _read_block(
        self, block: list[tuple[str, RegisterMap]], kind: RegisterKind
    ) -> dict[str, TagValue]:
        assert self._client is not None
        start = min(reg.address for _, reg in block)
        count = max(reg.address + reg.length for _, reg in block) - start
        read = (
            self._client.read_discrete_inputs
            if kind == "discrete_input"
            else self._client.read_input_registers
        )
        response = read(address=start, count=count, device_id=self.unit_id)
        if response.isError():
            raise TransportError(f"Modbus read of {count} {kind}(s) at {start} failed: {response}")
        wire = response.bits if kind == "discrete_input" else response.registers
        values: dict[str, TagValue] = {}
        for tag_name, reg in block:
            offset = reg.address - start
            words = (
                [1 if wire[offset] else 0]
                if kind == "discrete_input"
                else list(wire[offset : offset + reg.length])
            )
            values[tag_name] = reg.decode(words)
        return values

    def _write_block(
        self,
        block: list[tuple[str, RegisterMap]],
        kind: RegisterKind,
        outputs: Mapping[str, TagValue],
    ) -> None:
        assert self._client is not None
        start = min(reg.address for _, reg in block)
        span = max(reg.address + reg.length for _, reg in block) - start
        words = [0] * span
        for tag_name, reg in block:
            value = outputs.get(tag_name)
            if value is None:
                continue
            offset = reg.address - start
            words[offset : offset + reg.length] = reg.encode(value)
        write = self._client.write_coils if kind == "coil" else self._client.write_registers
        values: list[int] | list[bool] = [bool(w) for w in words] if kind == "coil" else words
        response = write(address=start, values=values, device_id=self.unit_id)
        if response.isError():
            raise TransportError(f"Modbus write of {span} {kind}(s) at {start} failed: {response}")


class _SlaveStore:
    """Thread-safe in-memory register file, shared by :meth:`ModbusSlaveServer.sync`
    and the running server's data blocks. Kept separate from pymodbus's own
    datastore types so the tag<->register sync logic is unit-testable without
    pymodbus installed — nothing here touches the network."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tables: dict[RegisterKind, dict[int, int]] = {
            "coil": {},
            "discrete_input": {},
            "holding_register": {},
            "input_register": {},
        }

    def write(self, kind: RegisterKind, address: int, words: list[int]) -> None:
        with self._lock:
            table = self._tables[kind]
            for i, word in enumerate(words):
                table[address + i] = word

    def read(self, kind: RegisterKind, address: int, count: int) -> list[int]:
        with self._lock:
            table = self._tables[kind]
            return [table.get(address + i, 0) for i in range(count)]


def _import_server_pieces() -> tuple[Any, Any, Any, Any]:
    try:
        from pymodbus.server import StartAsyncTcpServer
        from pymodbus.simulator import DataType, SimData, SimDevice
    except ImportError as exc:
        raise ImportError(
            "ModbusSlaveServer requires pymodbus - install with `pip install digitwin[modbus]`"
        ) from exc
    return DataType, SimData, SimDevice, StartAsyncTcpServer


# Modbus function code -> which of our four kinds it addresses. pymodbus's own
# internal mapping (SimRuntime._fx_mapper); duplicated here because the
# SimDevice.action callback below only gets the raw function code.
_FX_KIND: dict[int, RegisterKind] = {
    1: "coil",
    5: "coil",
    15: "coil",
    2: "discrete_input",
    3: "holding_register",
    6: "holding_register",
    16: "holding_register",
    22: "holding_register",
    23: "holding_register",
    4: "input_register",
}


def _make_action(store: _SlaveStore) -> Any:
    """Build the ``SimDevice.action`` callback: pymodbus's live extension hook,
    invoked on every register access with the block's real mutable register
    list. This is what actually keeps the running server in sync with
    `_SlaveStore` — the deprecated ``ModbusDeviceContext``/data-block classes
    deep-copy their data once at construction and can't be updated live.

    For coil/discrete_input, `current_registers` is bit-packed 16-per-word and
    `address`/`count` are register-granular, but `address`/`set_values` on a
    *write* stay bit-granular (pymodbus applies packing only for reads) — so
    writes need no unpacking at all, only reads do.
    """
    from pymodbus.simulator.simutils import SimUtils

    async def action(
        func_code: int,
        start_address: int,
        address: int,
        count: int,
        current_registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> None:
        kind = _FX_KIND.get(func_code)
        if kind is None:
            return None
        if kind in _BIT_KINDS:
            if set_values is None:
                reg_offset = address // 16 - start_address
                fresh = store.read(kind, (address // 16) * 16, count * 16)
                current_registers[reg_offset : reg_offset + count] = SimUtils.bitsToRegisters(
                    [bool(v) for v in fresh]
                )
            else:
                store.write(kind, address, [1 if v else 0 for v in set_values])
        else:
            offset = address - start_address
            if set_values is None:
                current_registers[offset : offset + count] = store.read(kind, address, count)
            else:
                store.write(kind, address, [int(v) for v in set_values])
        return None

    return action


def _addresses(channels: dict[str, RegisterMap], kind: RegisterKind) -> list[int]:
    """Every individual address `kind` occupies across `channels` — a 2-word
    holding/input register expands to both of its addresses."""
    addresses: set[int] = set()
    for reg in channels.values():
        if reg.kind == kind:
            addresses.update(range(reg.address, reg.address + reg.length))
    return sorted(addresses)


def _build_device(
    publish: dict[str, RegisterMap],
    accept: dict[str, RegisterMap],
    unit_id: int,
    store: _SlaveStore,
) -> Any:
    """A SimDevice covering exactly the configured addresses, one SimData per
    address (sidesteps SimData's `count=` replication semantics entirely).
    Initial values are placeholders — `action` overwrites them from `store` on
    first access — a kind with nothing configured gets one dummy address so
    SimDevice's non-shared blocks (which must each be non-empty) don't error."""
    DataType, SimData, SimDevice, _ = _import_server_pieces()

    def bit_block(addresses: list[int]) -> list[Any]:
        if not addresses:
            return [SimData(address=0, count=1, values=False, datatype=DataType.BITS)]
        return [
            SimData(address=a, count=1, values=False, datatype=DataType.BITS) for a in addresses
        ]

    def word_block(addresses: list[int]) -> list[Any]:
        if not addresses:
            return [SimData(address=0, count=1, values=0, datatype=DataType.REGISTERS)]
        return [
            SimData(address=a, count=1, values=0, datatype=DataType.REGISTERS) for a in addresses
        ]

    co = bit_block(_addresses(accept, "coil"))
    di = bit_block(_addresses(publish, "discrete_input"))
    hr = word_block(_addresses(accept, "holding_register"))
    ir = word_block(_addresses(publish, "input_register"))

    return SimDevice(
        id=unit_id,
        simdata=(co, di, hr, ir),
        use_bit_addressing=True,
        action=_make_action(store),
    )


@dataclass
class ModbusSlaveServer:
    """The twin as a Modbus TCP slave: a side window onto the tag table for
    an external SCADA/HMI (or a real master) to poll and command.

    Not an ``IOTransport`` and not on the scan path — :meth:`sync` is meant to
    be called once per tick, from ``Executive._observe()``, after that tick's
    scan has already settled (same slot the historian and event log use).

    ``publish`` exposes tags for read-only access on the wire (``discrete_input``
    / ``input_register`` kinds); ``accept`` lets the remote master overwrite
    tags (``coil`` / ``holding_register`` kinds). Both are keyed by **tag
    name**, not native address — unlike ``ModbusClientTransport``, this isn't
    wiring a physical terminal, it's an operator window onto whichever tags
    (physical or internal) the model chooses to expose.

    Network start/stop (:meth:`start` / :meth:`stop`) runs pymodbus's asyncio
    server on a background thread with its own event loop, backed by a
    ``SimDevice`` whose ``action`` callback bridges every register access to
    `_SlaveStore` live (see :func:`_make_action`) — pymodbus 3.11+'s
    supported way to back a server with external state; the older
    ``ModbusDeviceContext``/data-block classes only support a static,
    one-shot snapshot. The sync logic in :meth:`sync` is independent of the
    network and needs no running server to test (see ``tests/test_modbus.py``,
    which covers it without calling ``start``).
    """

    plc: PLC
    publish: dict[str, RegisterMap] = field(default_factory=dict)
    accept: dict[str, RegisterMap] = field(default_factory=dict)
    host: str = "0.0.0.0"
    port: int = 502
    unit_id: int = 1
    _store: _SlaveStore = field(default_factory=_SlaveStore, repr=False, init=False, compare=False)
    _loop: Any = field(default=None, repr=False, init=False, compare=False)
    _thread: Any = field(default=None, repr=False, init=False, compare=False)

    def __post_init__(self) -> None:
        for tag_name, reg in self.publish.items():
            if reg.kind not in _INPUT_KINDS:
                raise ValueError(
                    f"published tag {tag_name!r} maps to {reg.kind!r}, not a readable kind"
                )
            if tag_name not in self.plc.tags:
                raise KeyError(f"published tag {tag_name!r} is not defined on {self.plc.name!r}")
        for tag_name, reg in self.accept.items():
            if reg.kind not in _OUTPUT_KINDS:
                raise ValueError(
                    f"accepted tag {tag_name!r} maps to {reg.kind!r}, not a writable kind"
                )
            if tag_name not in self.plc.tags:
                raise KeyError(f"accepted tag {tag_name!r} is not defined on {self.plc.name!r}")

    def sync(self) -> None:
        """Publish current tag values onto the wire, then absorb whatever the
        remote master last wrote back onto the tag table. Call once per tick,
        after the scan — never mid-scan, so a remote write can't land between
        the input freeze and the output flush."""
        for tag_name, reg in self.publish.items():
            words = reg.encode(self.plc.tags[tag_name].value)
            self._store.write(reg.kind, reg.address, words)
        for tag_name, reg in self.accept.items():
            words = self._store.read(reg.kind, reg.address, reg.length)
            self.plc.tags[tag_name].value = reg.decode(words)

    def start(self) -> None:
        """Start the background TCP server thread. Idempotent."""
        if self._thread is not None:
            return
        device = _build_device(self.publish, self.accept, self.unit_id, self._store)
        _, _, _, start_async_server = _import_server_pieces()
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            address = (self.host, self.port)
            loop.run_until_complete(start_async_server(context=device, address=address))

        self._loop = loop
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server thread and its event loop. Idempotent.

        Uses pymodbus's own ``ServerStop()`` rather than just stopping the
        loop: ``serve_forever()`` never completes on its own, and abruptly
        stopping the loop out from under it leaves the listening socket's
        pending accept cancelled mid-flight instead of closed cleanly.
        ``ServerStop`` tracks the single running server through a pymodbus-
        global, so only one ``ModbusSlaveServer`` can be live at a time per
        process.
        """
        if self._thread is not None:
            from pymodbus.server import ServerStop

            ServerStop()
            self._thread.join(timeout=2.0)
        self._loop = None
        self._thread = None
