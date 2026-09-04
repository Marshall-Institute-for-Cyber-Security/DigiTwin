"""ModbusClientTransport + RegisterMap: register-level encode/decode and the
transport's block-grouped read/write, against a fake pymodbus-shaped client.
ModbusSlaveServer's sync() is likewise tested without any real server. Both
also cover pymodbus-import-failure paths. pymodbus is an optional extra
(`uv sync --extra modbus`); tests needing the real package skip via
`pytest.importorskip` when it isn't installed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from digitwin.adapters.modbus import ModbusClientTransport, ModbusSlaveServer, RegisterMap, _blocks
from digitwin.io import TransportError
from digitwin.models import PLC_Generic
from digitwin.plc import PLC, TagType


def _plc() -> PLC:
    plc = PLC_Generic("modbus_test_plc", lambda plc: None)
    plc.define_tag("di_a", TagType.DISCRETE_INPUT, False, "%I0.0")
    plc.define_tag("di_b", TagType.DISCRETE_INPUT, False, "%I0.1")
    plc.define_tag("ai_a", TagType.ANALOG_INPUT, 0, "%IW0.0")
    plc.define_tag("ai_b", TagType.ANALOG_INPUT, 0, "%IW0.1")
    plc.define_tag("ai_wide", TagType.ANALOG_INPUT, 0, "%IW0.2")
    plc.define_tag("do_a", TagType.DISCRETE_OUTPUT, False, "%Q0.0")
    plc.define_tag("do_b", TagType.DISCRETE_OUTPUT, False, "%Q0.1")
    plc.define_tag("aw_a", TagType.ANALOG_OUTPUT, 0, "%QW0.0")
    # No native address: internal tags a slave server might expose/accept,
    # same as an HMI setpoint or computed alarm bit would be.
    plc.define_tag("alarm", TagType.INTERNAL_BIT, False)
    plc.define_tag("setpoint", TagType.WORD, 0)
    return plc


# --- RegisterMap: encode/decode, no client involved -----------------------


def test_bit_kind_round_trips_true_and_false() -> None:
    reg = RegisterMap(kind="coil", address=0)
    assert reg.decode(reg.encode(True)) is True
    assert reg.decode(reg.encode(False)) is False


def test_single_register_scale_and_offset() -> None:
    # 4-20mA transmitter scaled to 0..1000 engineering units over a 0..4095
    # raw count, with a +5 zero offset.
    reg = RegisterMap(kind="input_register", address=10, scale=1000 / 4095, offset=5)
    encoded = reg.encode(505)  # (505 - 5) / (1000/4095) ~= 2047.9 -> round to 2048
    assert encoded == [round((505 - 5) / (1000 / 4095))]
    assert reg.decode(encoded) == 505


def test_32bit_word_order_big_vs_little() -> None:
    big = RegisterMap(kind="holding_register", address=0, length=2, word_order="big")
    little = RegisterMap(kind="holding_register", address=0, length=2, word_order="little")
    value = 0x1234_5678

    assert big.encode(value) == [0x1234, 0x5678]
    assert little.encode(value) == [0x5678, 0x1234]
    assert big.decode(big.encode(value)) == value
    assert little.decode(little.encode(value)) == value


def test_byte_order_swap_within_a_word() -> None:
    reg = RegisterMap(kind="holding_register", address=0, byte_order="little")
    assert reg.encode(0x1234) == [0x3412]
    assert reg.decode([0x3412]) == 0x1234


def test_length_must_be_one_or_two() -> None:
    with pytest.raises(ValueError, match="length"):
        RegisterMap(kind="holding_register", address=0, length=3)


def test_bit_kind_rejects_length_two() -> None:
    with pytest.raises(ValueError, match="single bit"):
        RegisterMap(kind="coil", address=0, length=2)


# --- block grouping ---------------------------------------------------


def test_blocks_merges_contiguous_and_splits_on_a_gap() -> None:
    a = ("a", RegisterMap(kind="input_register", address=0))
    b = ("b", RegisterMap(kind="input_register", address=1))
    c = ("c", RegisterMap(kind="input_register", address=5))  # gap after b

    blocks = _blocks([c, a, b])  # unordered input; grouping sorts by address

    assert [[name for name, _ in block] for block in blocks] == [["a", "b"], ["c"]]


def test_blocks_handles_a_32bit_entry_that_abuts_the_next() -> None:
    wide = ("wide", RegisterMap(kind="input_register", address=0, length=2))
    narrow = ("narrow", RegisterMap(kind="input_register", address=2))

    blocks = _blocks([wide, narrow])

    assert len(blocks) == 1


# --- ModbusClientTransport: wiring + block reads/writes against a fake client


@dataclass
class _Response:
    bits: list[bool] | None = None
    registers: list[int] | None = None
    error: bool = False

    def isError(self) -> bool:
        return self.error

    def __str__(self) -> str:
        return "fake modbus error"


@dataclass
class FakeClient:
    """Enough of pymodbus's sync client surface for the transport to drive."""

    coils: dict[int, bool] = field(default_factory=dict)
    holding: dict[int, int] = field(default_factory=dict)
    discrete: dict[int, bool] = field(default_factory=dict)
    input_regs: dict[int, int] = field(default_factory=dict)
    fail_addresses: set[int] = field(default_factory=set)
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    def read_discrete_inputs(self, address: int, count: int, device_id: int) -> _Response:
        self.calls.append(("read_discrete_inputs", address, count))
        if address in self.fail_addresses:
            return _Response(error=True)
        return _Response(bits=[self.discrete.get(address + i, False) for i in range(count)])

    def read_input_registers(self, address: int, count: int, device_id: int) -> _Response:
        self.calls.append(("read_input_registers", address, count))
        if address in self.fail_addresses:
            return _Response(error=True)
        return _Response(registers=[self.input_regs.get(address + i, 0) for i in range(count)])

    def write_coils(self, address: int, values: list[bool], device_id: int) -> _Response:
        self.calls.append(("write_coils", address, len(values)))
        for i, v in enumerate(values):
            self.coils[address + i] = v
        return _Response()

    def write_registers(self, address: int, values: list[int], device_id: int) -> _Response:
        self.calls.append(("write_registers", address, len(values)))
        for i, v in enumerate(values):
            self.holding[address + i] = v
        return _Response()


def _wired(plc: PLC, client: FakeClient) -> ModbusClientTransport:
    transport = ModbusClientTransport(
        host="127.0.0.1",
        plc=plc,
        inputs={
            "%I0.0": RegisterMap(kind="discrete_input", address=0),
            "%I0.1": RegisterMap(kind="discrete_input", address=1),
            "%IW0.0": RegisterMap(kind="input_register", address=0),
            "%IW0.1": RegisterMap(kind="input_register", address=1),
        },
        outputs={
            "%Q0.0": RegisterMap(kind="coil", address=0),
            "%Q0.1": RegisterMap(kind="coil", address=1),
            "%QW0.0": RegisterMap(kind="holding_register", address=0),
        },
    )
    transport._client = client  # bypass connect(): no real pymodbus needed
    return transport


def test_read_inputs_resolves_addresses_to_tag_names_in_one_round_trip_per_kind() -> None:
    plc = _plc()
    client = FakeClient(discrete={0: True, 1: False}, input_regs={0: 42, 1: 7})
    transport = _wired(plc, client)

    values = transport.read_inputs()

    assert values == {"di_a": True, "di_b": False, "ai_a": 42, "ai_b": 7}
    # One round trip for the discrete_input block, one for input_register.
    assert [call[0] for call in client.calls] == ["read_discrete_inputs", "read_input_registers"]
    assert client.calls[0][2] == 2  # count spans both di_a/di_b in one block
    assert client.calls[1][2] == 2


def test_read_inputs_raises_when_a_mapped_address_has_no_tag() -> None:
    # A wiring/program mismatch, not something to silently drop -- same as
    # InProcessTransport (see digitwin/io.py, digitwin/plc.py::PLC.tag_at).
    plc = PLC_Generic("unwired_plc", lambda plc: None)
    plc.define_tag("di_a", TagType.DISCRETE_INPUT, False, "%I0.0")
    # %I0.1 deliberately left undefined -> no tag claims it.
    plc.define_tag("ai_a", TagType.ANALOG_INPUT, 0, "%IW0.0")
    plc.define_tag("ai_b", TagType.ANALOG_INPUT, 0, "%IW0.1")
    client = FakeClient(discrete={0: True})
    transport = _wired(plc, client)

    with pytest.raises(ValueError, match="no tag on PLC_Generic claims"):
        transport.read_inputs()


def test_a_failed_block_raises_transporterror_with_partial_inputs_from_the_other_block() -> None:
    plc = _plc()
    client = FakeClient(discrete={0: True, 1: False}, fail_addresses={0})
    transport = _wired(plc, client)

    with pytest.raises(TransportError) as exc_info:
        transport.read_inputs()

    # discrete_input block failed; input_register block (read after it) never ran.
    assert exc_info.value.partial_inputs == {}


def test_a_later_block_failing_keeps_the_earlier_blocks_successful_reads() -> None:
    """discrete_input is read before input_register (see _INPUT_KINDS); make
    only the register block fail and check the discrete reads survive into
    partial_inputs."""
    plc = _plc()
    client = FakeClient(discrete={0: True, 1: False}, input_regs={0: 1, 1: 2})

    def failing_read(address: int, count: int, device_id: int) -> _Response:
        return _Response(error=True)

    client.read_input_registers = failing_read  # type: ignore[method-assign]
    transport = _wired(plc, client)

    with pytest.raises(TransportError) as exc_info:
        transport.read_inputs()

    assert exc_info.value.partial_inputs == {"di_a": True, "di_b": False}


def test_write_outputs_batches_adjacent_coils_and_resolves_by_tag_name() -> None:
    plc = _plc()
    client = FakeClient()
    transport = _wired(plc, client)

    transport.write_outputs({"do_a": True, "do_b": False, "aw_a": 123})

    assert client.coils == {0: True, 1: False}
    assert client.holding == {0: 123}
    assert [call[0] for call in client.calls] == ["write_coils", "write_registers"]
    assert client.calls[0][2] == 2  # do_a + do_b in one round trip


def test_write_outputs_leaves_zero_for_a_tag_missing_from_the_output_dict() -> None:
    plc = _plc()
    client = FakeClient()
    transport = _wired(plc, client)

    transport.write_outputs({"do_a": True})  # do_b omitted

    assert client.coils == {0: True, 1: False}


def test_construction_rejects_a_read_only_kind_wired_as_an_output() -> None:
    plc = _plc()
    with pytest.raises(ValueError, match="not a writable kind"):
        ModbusClientTransport(
            host="x",
            plc=plc,
            outputs={"%Q0.0": RegisterMap(kind="discrete_input", address=0)},
        )


def test_construction_rejects_a_write_only_kind_wired_as_an_input() -> None:
    plc = _plc()
    with pytest.raises(ValueError, match="not a readable kind"):
        ModbusClientTransport(
            host="x",
            plc=plc,
            inputs={"%I0.0": RegisterMap(kind="coil", address=0)},
        )


def test_connect_raises_transporterror_when_pymodbus_is_missing_or_refuses() -> None:
    plc = _plc()
    transport = ModbusClientTransport(host="127.0.0.1", plc=plc, port=1)

    with pytest.raises((ImportError, TransportError)):
        transport.connect()


# --- ModbusSlaveServer: tag-table <-> wire sync, no server/network involved


def test_sync_publishes_tag_values_by_name_not_address() -> None:
    plc = _plc()
    plc.tags["alarm"].value = True
    plc.tags["ai_a"].value = 42
    server = ModbusSlaveServer(
        plc=plc,
        publish={
            "alarm": RegisterMap(kind="discrete_input", address=0),
            "ai_a": RegisterMap(kind="input_register", address=0),
        },
    )

    server.sync()

    assert server._store.read("discrete_input", 0, 1) == [1]
    assert server._store.read("input_register", 0, 1) == [42]


def test_sync_absorbs_a_remote_write_into_the_tag_table() -> None:
    plc = _plc()
    server = ModbusSlaveServer(
        plc=plc,
        accept={
            "do_a": RegisterMap(kind="coil", address=0),
            "setpoint": RegisterMap(kind="holding_register", address=0),
        },
    )
    # Simulate a remote master having written these before this tick's sync.
    server._store.write("coil", 0, [1])
    server._store.write("holding_register", 0, [77])

    server.sync()

    assert plc.tags["do_a"].value is True
    assert plc.tags["setpoint"].value == 77


def test_sync_round_trips_a_32bit_value_through_accept() -> None:
    plc = _plc()
    reg = RegisterMap(kind="holding_register", address=0, length=2, word_order="big")
    server = ModbusSlaveServer(plc=plc, accept={"setpoint": reg})
    server._store.write("holding_register", 0, reg.encode(0x1234_5678))

    server.sync()

    assert plc.tags["setpoint"].value == 0x1234_5678


def test_construction_rejects_a_write_only_kind_in_publish() -> None:
    plc = _plc()
    with pytest.raises(ValueError, match="not a readable kind"):
        ModbusSlaveServer(plc=plc, publish={"alarm": RegisterMap(kind="coil", address=0)})


def test_construction_rejects_a_read_only_kind_in_accept() -> None:
    plc = _plc()
    reg = RegisterMap(kind="input_register", address=0)
    with pytest.raises(ValueError, match="not a writable kind"):
        ModbusSlaveServer(plc=plc, accept={"setpoint": reg})


def test_construction_rejects_an_undefined_tag_name() -> None:
    plc = _plc()
    with pytest.raises(KeyError):
        ModbusSlaveServer(
            plc=plc, publish={"no_such_tag": RegisterMap(kind="discrete_input", address=0)}
        )


def test_slave_start_raises_importerror_when_pymodbus_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plc = _plc()
    server = ModbusSlaveServer(plc=plc)

    def _boom() -> Any:
        raise ImportError("ModbusSlaveServer requires pymodbus")

    monkeypatch.setattr("digitwin.adapters.modbus._import_server_pieces", _boom)
    with pytest.raises(ImportError):
        server.start()


# --- Live round trip against a real pymodbus server + client. Requires the
# `modbus` extra (`uv sync --extra modbus`); skipped otherwise, same as every
# other pymodbus-optional test here.


def test_slave_server_serves_a_real_pymodbus_client_end_to_end() -> None:
    pytest.importorskip("pymodbus")
    import socket
    import time

    from pymodbus.client import ModbusTcpClient

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    plc = _plc()
    plc.tags["ai_a"].value = 4242
    server = ModbusSlaveServer(
        plc=plc,
        publish={"ai_a": RegisterMap(kind="input_register", address=0)},
        accept={"setpoint": RegisterMap(kind="holding_register", address=0)},
        host="127.0.0.1",
        port=port,
    )
    server.sync()
    server.start()
    client: Any = ModbusTcpClient("127.0.0.1", port=port, timeout=1.0)
    try:
        deadline = time.monotonic() + 3.0
        while not client.connect():
            if time.monotonic() > deadline:
                pytest.fail("real pymodbus server never accepted a connection")
            time.sleep(0.05)

        read = client.read_input_registers(address=0, count=1, device_id=1)
        assert not read.isError()
        assert read.registers == [4242]

        write = client.write_registers(address=0, values=[777], device_id=1)
        assert not write.isError()
    finally:
        client.close()
        server.stop()

    server.sync()
    assert plc.tags["setpoint"].value == 777


def test_client_and_slave_server_interoperate_over_a_real_socket() -> None:
    """ModbusSlaveServer stands in for a remote field device; ModbusClientTransport
    polls it exactly as it would poll real hardware — exercises both bit
    (coil/discrete_input) and word (holding/input register) kinds, both
    directions, over an actual TCP round trip."""
    pytest.importorskip("pymodbus")
    import socket
    import time

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    remote_plc = _plc()
    remote_plc.tags["di_a"].value = True
    remote_plc.tags["ai_a"].value = 55
    server = ModbusSlaveServer(
        plc=remote_plc,
        publish={
            "di_a": RegisterMap(kind="discrete_input", address=0),
            "ai_a": RegisterMap(kind="input_register", address=0),
        },
        accept={
            "do_a": RegisterMap(kind="coil", address=0),
            "aw_a": RegisterMap(kind="holding_register", address=0),
        },
        host="127.0.0.1",
        port=port,
    )
    server.sync()
    server.start()

    our_plc = _plc()
    transport = ModbusClientTransport(
        host="127.0.0.1",
        port=port,
        plc=our_plc,
        inputs={
            "%I0.0": RegisterMap(kind="discrete_input", address=0),
            "%IW0.0": RegisterMap(kind="input_register", address=0),
        },
        outputs={
            "%Q0.0": RegisterMap(kind="coil", address=0),
            "%QW0.0": RegisterMap(kind="holding_register", address=0),
        },
    )
    try:
        deadline = time.monotonic() + 3.0
        while True:
            try:
                values = transport.read_inputs()
                break
            except TransportError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

        assert values == {"di_a": True, "ai_a": 55}

        transport.write_outputs({"do_a": True, "aw_a": 999})
    finally:
        transport.close()
        server.stop()

    server.sync()
    assert remote_plc.tags["do_a"].value is True
    assert remote_plc.tags["aw_a"].value == 999
