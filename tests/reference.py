"""Hand-wired reference twin, used only by the test suite.

The same start/stop tank twin as ``examples/tank.toml``, assembled in Python
instead of loaded from the project file. It lives here — not in
``src/digitwin/`` — because the framework must not ship or depend on a demo.
Its jobs now:

* the regression anchor for ``digitwin.config`` — ``load_project(
  "examples/tank.toml")`` must reproduce ``build_demo()`` bit for bit;
* a realistic, non-trivial twin for the executive / historian / snapshot /
  replay tests to exercise machinery against.

When the P5 golden-trace harness lands, the committed trace becomes the anchor
and this module can go too.
"""

from __future__ import annotations

from digitwin.events import EventCategory, EventLog
from digitwin.executive import Executive, ExecutiveMode
from digitwin.historian import Historian, SampleMode
from digitwin.io import InProcessTransport, IOBus
from digitwin.models import PLC_Generic
from digitwin.plant import AnalogSensor, CompositePlant, Tank
from digitwin.plc import PLC, TagType, TagValue
from digitwin.programs import StartStopTankProgram

# name, type, initial value, native address
TagSpec = tuple[str, TagType, TagValue, str]

DEMO_TAGS: list[TagSpec] = [
    ("start_button", TagType.DISCRETE_INPUT, False, "%I0.0"),
    ("stop_button", TagType.DISCRETE_INPUT, False, "%I0.1"),
    ("oit_start_button", TagType.INTERNAL_BIT, False, "%M3"),
    ("oit_stop_button", TagType.INTERNAL_BIT, False, "%M4"),
    ("start_bit", TagType.INTERNAL_BIT, False, "%M1"),
    ("stop_bit", TagType.INTERNAL_BIT, True, "%M0"),
    ("green_light", TagType.DISCRETE_OUTPUT, False, "%Q0.0"),
    ("red_light", TagType.DISCRETE_OUTPUT, False, "%Q0.1"),
    ("fill_valve", TagType.DISCRETE_OUTPUT, False, "%Q0.2"),
    ("drain_valve", TagType.DISCRETE_OUTPUT, False, "%Q0.3"),
    ("tank_level", TagType.ANALOG_INPUT, 0, "%IW0.0"),
    ("tank_fill_permitted", TagType.INTERNAL_BIT, True, "%M11"),
    ("tank_drain_permitted", TagType.INTERNAL_BIT, False, "%M10"),
]

INPUT_WIRING = {"%IW0.0": "tank_level"}
OUTPUT_WIRING = {"%Q0.2": "fill_valve", "%Q0.3": "drain_valve"}

# The seal-in latch should survive a power cycle like a real one.
RETENTIVE_TAGS = {"start_bit", "stop_bit"}


def build_demo_plc() -> PLC:
    plc = PLC_Generic("demo_plc", StartStopTankProgram(), watchdog_s=0.05)
    for name, tag_type, initial_value, native_address in DEMO_TAGS:
        plc.define_tag(
            name,
            tag_type,
            initial_value,
            native_address=native_address,
            retentive=name in RETENTIVE_TAGS,
        )
    return plc


def build_demo(
    mode: ExecutiveMode = ExecutiveMode.FREE_RUN,
    scale: float = 1.0,
    *,
    observe: bool = True,
) -> Executive:
    """The tank plant, the PLC, and the executive that steps them.

    ``observe`` attaches the historian and event log; they are pure observers,
    so the simulation is identical either way.
    """
    plc = build_demo_plc()

    tank = Tank(area=2.0, fill_rate=20.0, drain_coeff=3.0, level_max=100.0)
    transmitter = AnalogSensor(source=tank.level_signal, dest="tank_level")
    plant = CompositePlant([tank, transmitter])

    bus = IOBus()
    transport = InProcessTransport(bus, plc, inputs=INPUT_WIRING, outputs=OUTPUT_WIRING)
    return Executive(
        plc,
        plant,
        bus,
        transport,
        dt=0.1,
        mode=mode,
        scale=scale,
        historian=Historian(mode=SampleMode.ON_CHANGE) if observe else None,
        events=EventLog() if observe else None,
    )


def press(sim: Executive, button: str, pressed: bool) -> None:
    """Drive a pushbutton and log it as an operator action."""
    sim.plc.write(button, pressed)
    if sim.events is not None:
        sim.events.log(
            sim.elapsed,
            EventCategory.OPERATOR,
            f"{button} {'pressed' if pressed else 'released'}",
            source="demo",
            scan=sim.plc.scan_count,
            tag=button,
            value=pressed,
        )
