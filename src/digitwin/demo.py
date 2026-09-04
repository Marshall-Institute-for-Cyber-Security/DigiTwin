"""Runnable demo: a tank plant wired to a PLC through the I/O bus.

The control program latches start/stop and commands the valves; the tank
level is now integrated by :class:`digitwin.plant.tank.Tank` and read back
through an analog level transmitter. The executive steps both each tick, and
the Phase 3 observers — a historian and an event log — watch the run.
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
    ("tank_level", TagType.WORD, 0, "%MW0"),
    ("tank_fill_permitted", TagType.INTERNAL_BIT, True, "%M11"),
    ("tank_drain_permitted", TagType.INTERNAL_BIT, False, "%M10"),
]

# PLC tag <-> bus signal wiring for the in-process transport.
INPUT_WIRING = {"tank_level": "tank_level"}
OUTPUT_WIRING = {"fill_valve": "fill_valve", "drain_valve": "drain_valve"}


# Retentive: the latch state should survive a power cycle like a real seal-in.
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
    """Assemble the tank plant, the PLC, and the executive that steps them.

    ``observe`` attaches the historian and event log; they are pure observers,
    so the simulation is identical either way.
    """
    plc = build_demo_plc()

    tank = Tank(area=2.0, fill_rate=20.0, drain_coeff=3.0, level_max=100.0)
    transmitter = AnalogSensor(source=tank.level_signal, dest="tank_level")
    plant = CompositePlant([tank, transmitter])

    bus = IOBus()
    transport = InProcessTransport(bus, inputs=INPUT_WIRING, outputs=OUTPUT_WIRING)
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
    """Drive a demo pushbutton and log it as an operator action."""
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


def _report(sim: Executive) -> None:
    plc = sim.plc
    print(
        f"t={sim.elapsed:4.1f}s  level={int(plc.read('tank_level')):3d}  "
        f"green={int(plc.read('green_light'))} red={int(plc.read('red_light'))}  "
        f"fill={int(plc.read('fill_valve'))} drain={int(plc.read('drain_valve'))}  "
        f"scan={sim.last_scan_s * 1e6:4.0f}us"
    )


def _observability_report(sim: Executive) -> None:
    """What the Phase 3 observers saw: trend extremes and the audit trail."""
    historian = sim.historian
    if historian is not None:
        level = historian.series("tank_level")
        span = historian.span()
        window = f", t={span[0]:.1f}..{span[1]:.1f}s" if span else ""
        print(
            f"historian: {len(historian)} samples across "
            f"{len(historian.tag_names())} tags{window}"
        )
        if level:
            peak_t, peak = max(level, key=lambda point: point[1])
            print(f"  tank_level: {len(level)} changes, peak {peak} at t={peak_t:.1f}s")
            print(f"  level at t=5.0s was {historian.value_at('tank_level', 5.0)}")

    events = sim.events
    if events is not None:
        print(f"events: {len(events)}")
        for event in events:
            print(
                f"  t={event.timestamp:5.1f}s  {event.category.value:<8} "
                f"{event.message}"
            )


def main() -> None:
    sim = build_demo()

    press(sim, "start_button", True)
    for _ in range(5):
        sim.tick()
    press(sim, "start_button", False)  # release; seal-in holds it running

    for step in range(1, 121):
        sim.tick()
        if step == 45:
            press(sim, "stop_button", True)
        elif step == 50:
            press(sim, "stop_button", False)
        if step % 15 == 0:
            _report(sim)

    print(f"scans={sim.plc.scan_count}  watchdog_tripped={sim.plc.watchdog_tripped}")
    _observability_report(sim)


if __name__ == "__main__":
    main()
