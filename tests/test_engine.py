"""Characterization tests for the three-phase scan engine in ``plc.py``.

These pin the current semantics before Phase 2 changes them (first-scan bit,
retentive reset, cold start). If a change here is intentional, update the
test in the same commit and say why.
"""

from __future__ import annotations

import pytest

from digitwin.plc import PLC, TagType, TagValue


def test_input_image_contains_only_discrete_inputs() -> None:
    frozen: dict[str, TagValue] = {}

    def program(plc: PLC) -> None:
        frozen.update(plc.input_image)

    plc = PLC("t", program)
    plc.define_tag("di", TagType.DISCRETE_INPUT, True)
    plc.define_tag("do", TagType.DISCRETE_OUTPUT, True)
    plc.define_tag("bit", TagType.INTERNAL_BIT, True)
    plc.define_tag("word", TagType.WORD, 5)

    plc.scan()

    assert set(frozen) == {"di"}


def test_discrete_input_is_frozen_for_the_whole_scan() -> None:
    seen: list[TagValue] = []

    def program(plc: PLC) -> None:
        seen.append(plc.read_input("btn"))
        plc.tags["btn"].value = True  # hardware toggles mid-scan
        seen.append(plc.read_input("btn"))  # program still sees the frozen image

    plc = PLC("t", program)
    plc.define_tag("btn", TagType.DISCRETE_INPUT, False)

    plc.scan()
    assert seen == [False, False]

    plc.scan()
    assert seen[2:] == [True, True]  # next scan re-freezes with the new value


def test_outputs_are_staged_and_flushed_only_at_end_of_scan() -> None:
    during_scan: list[TagValue] = []

    def program(plc: PLC) -> None:
        plc.write_output("lamp", True)
        during_scan.append(plc.read("lamp"))  # tag not updated yet

    plc = PLC("t", program)
    plc.define_tag("lamp", TagType.DISCRETE_OUTPUT, False)

    plc.scan()

    assert during_scan == [False]
    assert plc.read("lamp") is True  # flushed after the program returns


def test_output_holds_its_last_value_when_not_restaged() -> None:
    scan_no = 0

    def program(plc: PLC) -> None:
        nonlocal scan_no
        scan_no += 1
        if scan_no == 1:
            plc.write_output("lamp", True)
        # scan 2 stages nothing

    plc = PLC("t", program)
    plc.define_tag("lamp", TagType.DISCRETE_OUTPUT, False)

    plc.scan()
    assert plc.read("lamp") is True
    plc.scan()
    assert plc.read("lamp") is True  # output scan only writes what was staged


def test_read_and_write_are_immediate_for_internal_bits_and_words() -> None:
    mid: dict[str, TagValue] = {}

    def program(plc: PLC) -> None:
        plc.write("count", plc.read("count") + 1)
        plc.write("flag", True)
        mid["flag"] = plc.read("flag")
        mid["count"] = plc.read("count")

    plc = PLC("t", program)
    plc.define_tag("count", TagType.WORD, 0)
    plc.define_tag("flag", TagType.INTERNAL_BIT, False)

    plc.scan()

    assert mid == {"flag": True, "count": 1}
    plc.scan()
    assert plc.read("count") == 2


def test_full_cycle_reads_input_and_drives_output() -> None:
    def passthrough(plc: PLC) -> None:
        plc.write_output("out", plc.read_input("in"))

    plc = PLC("t", passthrough)
    plc.define_tag("in", TagType.DISCRETE_INPUT, False)
    plc.define_tag("out", TagType.DISCRETE_OUTPUT, False)

    plc.tags["in"].value = True
    plc.scan()
    assert plc.read("out") is True

    plc.tags["in"].value = False
    plc.scan()
    assert plc.read("out") is False


def test_read_input_rejects_non_discrete_input_tags() -> None:
    errors: list[str] = []

    def program(plc: PLC) -> None:
        try:
            plc.read_input("bit")
        except KeyError:
            errors.append("bit")

    plc = PLC("t", program)
    plc.define_tag("bit", TagType.INTERNAL_BIT, True)

    plc.scan()
    assert errors == ["bit"]


def test_define_tag_rejects_duplicate_names() -> None:
    plc = PLC("t", lambda _plc: None)
    plc.define_tag("x", TagType.INTERNAL_BIT)

    with pytest.raises(ValueError, match="already exists"):
        plc.define_tag("x", TagType.WORD)
