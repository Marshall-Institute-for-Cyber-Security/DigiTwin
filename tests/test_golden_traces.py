"""Golden-trace regression harness (P5).

Each shipped example twin is run through its own established stimulus (the
same scripts test_config.py's own hardcoded-vs-config regression tests already
use, where one exists) with a uniform, every-scan historian attached — not
whatever `[observability]` a project file happens to declare, so every twin is
captured the same way regardless of its own settings — and the resulting trace
is compared against a checked-in JSON fixture under tests/golden/.

A deliberate behavior change should fail exactly the traces that exercise it
and no others; see docs/TODO.md's P5 section for the one-off verification of
that claim (a program mutated, the matching trace failing, everything else
staying green).

Scope note: examples/pump_tank_bench.toml is not covered here. It isn't in
AGENTS.md's documented example list and belongs to examples/tester.py, which
is itself still a scratch script, not a shipped, documented twin. Add a trace
for it once (if) it's promoted to a real example.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from digitwin.config import load_project
from digitwin.executive import Executive, ExecutiveMode
from digitwin.historian import Historian, SampleMode

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
GOLDEN = Path(__file__).resolve().parent / "golden"

# Tags excluded from every trace: wall-clock derived, not reproducible run to
# run (same exclusion test_config.py's own regression tests already apply).
_NON_REPRODUCIBLE_TAGS = frozenset({"scan_time_ms"})


def _instrument(sim: Executive) -> None:
    """Attach a fresh, uniform historian regardless of what the project file
    itself declares — every twin's golden trace is captured the same way."""
    sim.mode = ExecutiveMode.FREE_RUN  # never sleep in a test, whatever the file says
    sim.historian = Historian(mode=SampleMode.EVERY_SCAN)


def _canonical_trace(sim: Executive) -> list[dict[str, Any]]:
    historian = sim.historian
    assert historian is not None
    return [
        {"timestamp": s.timestamp, "tag": s.tag, "value": s.value}
        for s in historian.samples
        if s.tag not in _NON_REPRODUCIBLE_TAGS
    ]


def _assert_matches_golden(name: str, trace: list[dict[str, Any]]) -> None:
    path = GOLDEN / f"{name}.json"
    expected = json.loads(path.read_text(encoding="utf-8"))
    if trace == expected:
        return
    if len(trace) != len(expected):
        raise AssertionError(
            f"{name}: golden trace has {len(expected)} samples, this run has "
            f"{len(trace)} — behavior changed how much it records, not just "
            f"what it recorded"
        )
    for i, (got, want) in enumerate(zip(trace, expected, strict=True)):
        if got != want:
            raise AssertionError(
                f"{name}: first divergence at sample {i}: got {got!r}, "
                f"golden has {want!r}"
            )


# --- per-twin stimulus, matching each twin's own established test script ---


def _run_tank_script(sim: Executive) -> None:
    sim.plc.write("start_button", True)
    sim.run(5)
    sim.plc.write("start_button", False)
    sim.run(60)
    sim.plc.write("stop_button", True)
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(60)


def _run_m221_script(sim: Executive) -> None:
    sim.plc.write("oit_start_button", True)
    sim.tick()
    sim.plc.write("oit_start_button", False)
    for step in range(1, 1101):
        sim.tick()
        if step == 700:
            sim.plc.write("oit_stop_button", True)
            sim.tick()
            sim.plc.write("oit_stop_button", False)


def _run_motor_conveyor_script(sim: Executive) -> None:
    sim.plc.write("start_button", True)
    sim.run(45)
    sim.plc.write("start_button", False)
    sim.run(20)
    sim.plc.write("stop_button", True)
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(40)


# --- one test per shipped, documented example twin -------------------------


def test_tank_golden_trace() -> None:
    sim = load_project(EXAMPLES / "tank.toml")
    _instrument(sim)
    _run_tank_script(sim)
    _assert_matches_golden("tank", _canonical_trace(sim))


def test_tank_inline_profile_golden_trace() -> None:
    sim = load_project(EXAMPLES / "tank_inline_profile.toml")
    _instrument(sim)
    _run_tank_script(sim)
    _assert_matches_golden("tank_inline_profile", _canonical_trace(sim))


def test_heated_tank_golden_trace() -> None:
    sim = load_project(EXAMPLES / "heated_tank.toml")
    _instrument(sim)
    sim.run(3000)  # noop program: physics settling is the whole story
    _assert_matches_golden("heated_tank", _canonical_trace(sim))


def test_m221_lab_twin_golden_trace() -> None:
    sim = load_project(EXAMPLES / "m221_lab_twin.toml")
    _instrument(sim)
    _run_m221_script(sim)
    _assert_matches_golden("m221_lab_twin", _canonical_trace(sim))


def test_motor_conveyor_golden_trace() -> None:
    sim = load_project(EXAMPLES / "motor_conveyor.toml")
    _instrument(sim)
    _run_motor_conveyor_script(sim)
    _assert_matches_golden("motor_conveyor", _canonical_trace(sim))
