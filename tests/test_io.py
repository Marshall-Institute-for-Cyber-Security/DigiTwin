"""Characterization tests for the I/O bus and the in-process transport:
terminal-based wiring (digitwin/io.py) and PLC.tag_at (digitwin/plc.py).

If a change here is intentional, update the test in the same commit and say
why.
"""

from __future__ import annotations

import pytest

from digitwin.hardware import AddressError
from digitwin.io import InProcessTransport, IOBus
from digitwin.models import PLC_Generic
from digitwin.plc import TagType


def _plc() -> PLC_Generic:
    return PLC_Generic("t", lambda _plc: None)


# --- PLC.tag_at ---------------------------------------------------------


def test_tag_at_resolves_a_defined_terminal() -> None:
    plc = _plc()
    plc.define_tag("start_button", TagType.DISCRETE_INPUT, False, "%I0.0")

    assert plc.tag_at("%I0.0") == "start_button"


def test_tag_at_returns_none_for_an_unwired_terminal() -> None:
    plc = _plc()

    assert plc.tag_at("%I0.5") is None


def test_tag_at_raises_on_malformed_syntax() -> None:
    plc = _plc()
    with pytest.raises(AddressError):
        plc.tag_at("%QX0.0")


# --- InProcessTransport: wired by terminal, not tag name ----------------


def test_transport_resolves_the_tag_currently_at_a_terminal() -> None:
    plc = _plc()
    plc.define_tag("level_sensor", TagType.ANALOG_INPUT, 0, "%IW0.0")
    bus = IOBus()
    bus.set("tank_level", 42)
    transport = InProcessTransport(bus, plc, inputs={"%IW0.0": "tank_level"})

    assert transport.read_inputs() == {"level_sensor": 42}


def test_transport_wiring_survives_a_tag_rename() -> None:
    # The whole point: field wiring is keyed by terminal, so renaming the tag
    # at that terminal doesn't require touching INPUT_WIRING/OUTPUT_WIRING.
    plc = _plc()
    plc.define_tag("renamed_sensor", TagType.ANALOG_INPUT, 0, "%IW0.0")
    bus = IOBus()
    bus.set("tank_level", 7)
    transport = InProcessTransport(bus, plc, inputs={"%IW0.0": "tank_level"})

    assert transport.read_inputs() == {"renamed_sensor": 7}


def test_transport_writes_the_output_tag_at_the_wired_terminal() -> None:
    plc = _plc()
    plc.define_tag("fill_valve", TagType.DISCRETE_OUTPUT, False, "%Q0.0")
    bus = IOBus()
    transport = InProcessTransport(bus, plc, outputs={"%Q0.0": "fill_valve_signal"})

    transport.write_outputs({"fill_valve": True})

    assert bus.get("fill_valve_signal") is True


def test_transport_raises_when_a_wired_terminal_has_no_tag() -> None:
    plc = _plc()  # no tag defined at %IW0.0
    bus = IOBus()
    transport = InProcessTransport(bus, plc, inputs={"%IW0.0": "tank_level"})

    with pytest.raises(ValueError, match="no tag on PLC_Generic claims"):
        transport.read_inputs()


def test_transport_needs_a_plc_to_resolve_addressed_channels() -> None:
    bus = IOBus()
    with pytest.raises(ValueError, match="needs a plc"):
        InProcessTransport(bus, inputs={"%IW0.0": "tank_level"})
