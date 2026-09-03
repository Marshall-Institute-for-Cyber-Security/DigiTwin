"""Runnable demo: wire up a PLC with the start/stop tank program and scan it."""

from __future__ import annotations

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
    ("tank_level", TagType.WORD, 0, "%MW0"),
    ("tank_fill_permitted", TagType.INTERNAL_BIT, True, "%M11"),
    ("tank_drain_permitted", TagType.INTERNAL_BIT, False, "%M10"),
]


def build_demo_plc() -> PLC:
    plc = PLC("demo_plc", StartStopTankProgram())
    for name, tag_type, initial_value, native_address in DEMO_TAGS:
        plc.define_tag(name, tag_type, initial_value, native_address=native_address)
    return plc


def main() -> None:
    plc = build_demo_plc()
    plc.write("start_button", True)  # simulate pressing start

    for i in range(5):
        plc.scan()
        print(
            f"{i} level: {plc.read('tank_level')} "
            f"green: {plc.read('green_light')} red: {plc.read('red_light')}"
        )


if __name__ == "__main__":
    main()
