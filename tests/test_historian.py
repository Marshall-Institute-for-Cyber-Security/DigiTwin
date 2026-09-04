"""Historian sampling modes, the query API, persistent sinks, and the event
log — including what the executive records on its own."""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path

import pytest

from digitwin.demo import build_demo
from digitwin.events import EventCategory, EventLog, EventSeverity
from digitwin.executive import Executive, ExecutiveMode
from digitwin.historian import (
    CsvSink,
    Historian,
    JsonlSink,
    SampleMode,
    SqliteSink,
)
from digitwin.io import InProcessTransport, IOBus
from digitwin.models import PLC_Generic
from digitwin.plant import NullPlant
from digitwin.plc import PLC


def _historian(sim: Executive) -> Historian:
    historian = sim.historian
    assert historian is not None
    return historian


def _events(sim: Executive) -> EventLog:
    events = sim.events
    assert events is not None
    return events


# --- sampling modes -------------------------------------------------------


def test_on_change_records_the_first_reading_then_only_transitions() -> None:
    historian = Historian(mode=SampleMode.ON_CHANGE)

    historian.record(0.0, {"a": 1, "b": False})
    historian.record(0.1, {"a": 1, "b": False})
    historian.record(0.2, {"a": 2, "b": False})

    assert [(s.timestamp, s.tag, s.value) for s in historian] == [
        (0.0, "a", 1),
        (0.0, "b", False),
        (0.2, "a", 2),
    ]


def test_every_scan_records_every_tag_every_time() -> None:
    historian = Historian(mode=SampleMode.EVERY_SCAN)

    historian.record(0.0, {"a": 1})
    historian.record(0.1, {"a": 1})

    assert len(historian) == 2


def test_periodic_records_at_the_sample_rate_not_every_scan() -> None:
    historian = Historian(mode=SampleMode.PERIODIC, interval_s=0.5)

    for step in range(11):  # 0.0 .. 1.0 s in 0.1 s ticks
        historian.record(round(step * 0.1, 3), {"a": step})

    assert [s.timestamp for s in historian] == [0.0, 0.5, 1.0]


def test_tags_filter_limits_what_is_stored() -> None:
    historian = Historian(tags=frozenset({"wanted"}))

    historian.record(0.0, {"wanted": 1, "ignored": 2})

    assert historian.tag_names() == {"wanted"}


def test_capacity_drops_the_oldest_samples_and_counts_them() -> None:
    historian = Historian(capacity=3, mode=SampleMode.EVERY_SCAN)

    for step in range(5):
        historian.record(float(step), {"a": step})

    assert [s.value for s in historian] == [2, 3, 4]
    assert historian.dropped == 2


def test_a_bit_and_a_word_of_equal_value_are_different_readings() -> None:
    historian = Historian(mode=SampleMode.ON_CHANGE)

    historian.record(0.0, {"a": 1})
    historian.record(0.1, {"a": True})

    assert [s.value for s in historian] == [1, True]


# --- query API ------------------------------------------------------------


def test_query_filters_by_tag_and_window() -> None:
    historian = Historian(mode=SampleMode.EVERY_SCAN)
    for step in range(5):
        historian.record(float(step), {"a": step, "b": -step})

    window = historian.query("a", start=1.0, end=3.0)

    assert [(s.timestamp, s.value) for s in window] == [(1.0, 1), (2.0, 2), (3.0, 3)]


def test_value_at_returns_the_last_sample_at_or_before_a_time() -> None:
    historian = Historian(mode=SampleMode.ON_CHANGE)
    historian.record(0.0, {"level": 0})
    historian.record(1.0, {"level": 10})
    historian.record(2.0, {"level": 20})

    assert historian.value_at("level", 1.5) == 10
    assert historian.value_at("level", 2.0) == 20
    assert historian.value_at("missing", 1.0) is None


def test_snapshot_at_reconstructs_the_recorded_tag_table() -> None:
    historian = Historian(mode=SampleMode.ON_CHANGE)
    historian.record(0.0, {"a": 1, "b": 1})
    historian.record(1.0, {"a": 2, "b": 1})
    historian.record(2.0, {"a": 3, "b": 9})

    assert historian.snapshot_at(1.0) == {"a": 2, "b": 1}


def test_latest_span_and_clear() -> None:
    historian = Historian(mode=SampleMode.EVERY_SCAN)
    historian.record(0.0, {"a": 1})
    historian.record(1.0, {"a": 2})

    latest = historian.latest("a")
    assert latest is not None and latest.value == 2
    assert historian.span() == (0.0, 1.0)

    historian.clear()
    assert len(historian) == 0 and historian.span() is None


# --- sinks ----------------------------------------------------------------


def test_csv_sink_writes_a_header_and_one_row_per_sample(tmp_path: Path) -> None:
    path = tmp_path / "trend.csv"
    with CsvSink(path) as sink:
        historian = Historian(mode=SampleMode.EVERY_SCAN, sink=sink)
        historian.record(0.0, {"a": 1})
        historian.record(0.1, {"a": 2})

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["timestamp", "tag", "value"]
    assert [row[1:] for row in rows[1:]] == [["a", "1"], ["a", "2"]]


def test_sqlite_sink_round_trips_samples(tmp_path: Path) -> None:
    path = tmp_path / "trend.sqlite"
    with SqliteSink(path) as sink:
        historian = Historian(mode=SampleMode.EVERY_SCAN, sink=sink)
        historian.record(0.0, {"level": 5})
        historian.record(0.1, {"level": 7})

    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT timestamp, tag, value FROM samples").fetchall()

    assert rows == [(0.0, "level", 5), (0.1, "level", 7)]


def test_sqlite_sink_rejects_an_injected_table_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="identifier"):
        SqliteSink(tmp_path / "x.sqlite", table="samples; DROP TABLE samples")


def test_jsonl_sink_writes_one_object_per_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with JsonlSink(path) as sink:
        log = EventLog(sink=sink)
        log.log(1.0, EventCategory.ALARM, "high level", tag="tank_level", value=99)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["message"] == "high level"
    assert json.loads(rows[0]["data"]) == {"tag": "tank_level", "value": 99}


# --- event log ------------------------------------------------------------


def test_event_log_query_filters_by_category_severity_and_window() -> None:
    log = EventLog()
    log.log(0.0, EventCategory.OPERATOR, "start pressed")
    log.log(1.0, EventCategory.ALARM, "high level", severity=EventSeverity.ERROR)
    log.log(2.0, EventCategory.ALARM, "cleared")

    assert len(log.query(category=EventCategory.ALARM)) == 2
    assert len(log.query(severity=EventSeverity.ERROR)) == 1
    assert len(log.query(start=1.5)) == 1


def test_event_log_keeps_extra_keywords_as_structured_data() -> None:
    log = EventLog()

    event = log.log(0.0, EventCategory.FAULT, "valve stuck", valve="fill", scan=7)

    assert event.data == {"valve": "fill"}
    assert event.scan == 7


def test_event_log_capacity_drops_the_oldest() -> None:
    log = EventLog(capacity=2)
    for step in range(4):
        log.log(float(step), EventCategory.SYSTEM, f"e{step}")

    assert [event.message for event in log] == ["e2", "e3"]
    assert log.dropped == 2


# --- executive integration ------------------------------------------------


def test_executive_records_the_tag_table_in_simulation_time() -> None:
    sim = build_demo()
    sim.plc.write("start_button", True)

    sim.run(10)

    historian = _historian(sim)
    assert historian.span() == pytest.approx((0.1, 1.0))
    assert "tank_level" in historian.tag_names()
    assert historian.latest("red_light") is not None


def test_the_trend_is_identical_in_free_run_and_scaled_time() -> None:
    def trace(mode: ExecutiveMode, scale: float) -> list[tuple[float, str, object]]:
        sim = build_demo(mode, scale)
        sim.plc.write("start_button", True)
        sim.run(20)
        return [(s.timestamp, s.tag, s.value) for s in _historian(sim)]

    assert trace(ExecutiveMode.SCALED, 50.0) == trace(ExecutiveMode.FREE_RUN, 1.0)


def test_observers_do_not_change_the_simulation() -> None:
    def level_after(observe: bool) -> float:
        sim = build_demo(observe=observe)
        sim.plc.write("start_button", True)
        sim.run(30)
        return float(sim.plc.read("tank_level"))

    assert level_after(observe=True) == level_after(observe=False)


def test_executive_logs_a_watchdog_trip_once_on_the_edge() -> None:
    def slow_program(plc: PLC) -> None:
        time.sleep(0.005)

    plc = PLC_Generic("slow", slow_program, watchdog_s=0.001)
    bus = IOBus()
    events = EventLog()
    sim = Executive(plc, NullPlant(), bus, InProcessTransport(bus), events=events)

    sim.run(3)

    trips = events.query(category=EventCategory.WATCHDOG)
    assert len(trips) == 1
    assert trips[0].severity is EventSeverity.ERROR
    assert trips[0].scan == 1


def test_demo_press_logs_an_operator_action() -> None:
    from digitwin.demo import press

    sim = build_demo()
    press(sim, "start_button", True)

    actions = _events(sim).query(category=EventCategory.OPERATOR)
    assert len(actions) == 1
    assert actions[0].data == {"tag": "start_button", "value": True}
