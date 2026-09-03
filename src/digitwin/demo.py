"""Runnable demo: a tank plant wired to a PLC through the I/O bus.

The control program latches start/stop and commands the valves; the tank
level is now integrated by :class:`digitwin.plant.tank.Tank` and read back
through an analog level transmitter. The executive steps both each tick.
"""

from __future__ import annotations

from digitwin.executive import Executive, ExecutiveMode
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


def build_demo(mode: ExecutiveMode = ExecutiveMode.FREE_RUN, scale: float = 1.0) -> Executive:
    """Assemble the tank plant, the PLC, and the executive that steps them."""
    plc = build_demo_plc()

    tank = Tank(area=2.0, fill_rate=20.0, drain_coeff=3.0, level_max=100.0)
    transmitter = AnalogSensor(source=tank.level_signal, dest="tank_level")
    plant = CompositePlant([tank, transmitter])

    bus = IOBus()
    transport = InProcessTransport(bus, inputs=INPUT_WIRING, outputs=OUTPUT_WIRING)
    return Executive(plc, plant, bus, transport, dt=0.1, mode=mode, scale=scale)


def _report(sim: Executive) -> None:
    plc = sim.plc
    print(
        f"t={sim.elapsed:4.1f}s  level={int(plc.read('tank_level')):3d}  "
        f"green={int(plc.read('green_light'))} red={int(plc.read('red_light'))}  "
        f"fill={int(plc.read('fill_valve'))} drain={int(plc.read('drain_valve'))}  "
        f"scan={sim.last_scan_s * 1e6:4.0f}us"
    )


def main() -> None:
    sim = build_demo()

    sim.plc.write("start_button", True)  # press start
    for _ in range(5):
        sim.tick()
    sim.plc.write("start_button", False)  # release; seal-in holds it running

    for step in range(1, 121):
        sim.tick()
        if step == 45:
            sim.plc.write("stop_button", True)  # press stop
        elif step == 50:
            sim.plc.write("stop_button", False)
        if step % 15 == 0:
            _report(sim)

    print(f"scans={sim.plc.scan_count}  watchdog_tripped={sim.plc.watchdog_tripped}")


if __name__ == "__main__":
    main()
