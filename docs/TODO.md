# DigiTwin — Digital Twin Build Checklist

Working checklist for turning the soft-PLC engine into a proper PLC digital twin.
Full rationale, architecture diagram, and per-phase design notes live in the
roadmap document: `docs/ROADMAP.md`

Order follows the roadmap's **Suggested sequencing** table.

---

## Phase 1 — Split the plant from the controller  _(foundation)_

- [x] Add `PlantModel` protocol: `step(dt, io) -> None` (`digitwin/plant/base.py`) — also `CompositePlant`
- [x] `Tank` component — level integrates `(q_in - q_out) / area`, outflow `∝ √level` (`digitwin/plant/tank.py`)
- [ ] `Motor` / `Pump` component — start/stop with spin-up ramp + running feedback
- [x] `DiscreteSensor` — threshold + hysteresis → discrete input (`digitwin/plant/sensors.py`)
- [x] `AnalogSensor` — range, noise sigma, filter tau → scaled word (`digitwin/plant/sensors.py`)
- [x] `digitwin/io.py` — I/O bus + `IOTransport` protocol + in-process transport
  - [x] Keep the protocol **synchronous** (`def read_inputs` / `def write_outputs`) — matches the executive; `pymodbus` sync client fits directly, the `asyncua` adapter wraps its own loop
  - [ ] `read_inputs()` reports staleness or raises `TransportError` — networked reads can time out (Phase 7 comms-dropout reuses this)
- [x] Strip physics out of `StartStopTankProgram`; make it pure control logic
- [x] Rebuild `demo.py` as tank plant + PLC wired through the I/O bus, stepped by a minimal `Executive`
- [x] **Validate:** demo still fills on start / drains on stop; level comes from `Tank.step()`
- [x] Test: step plant with fixed valve command, check level vs analytic solution (`tests/test_plant.py`)


## Phase 2 — Real-time executive & scan-semantics fidelity

- [x] `Executive` — `ExecutiveMode` `FREE_RUN` / `REAL_TIME` / `SCALED` (+ `scale`), `run_for(seconds)`
- [x] Per-tick order: `plant.step` → `_transfer_inputs` → `plc.scan` → `_transfer_outputs`
- [x] Record actual scan duration (`plc.last_scan_duration`) + jitter (`last_jitter_s` / `worst_jitter_s`)
- [x] First-scan bit (`plc.first_scan`, true only on scan 1; re-armed by a restart)
- [x] `retentive: bool` on `Tag` (+ `initial_value`); non-retentive reset to initial on cold start
- [x] Cold start / warm start / power cycle entry points (`PLC.cold_start` / `warm_start` / `power_cycle`)
- [x] Watchdog — `PLC(watchdog_s=…)` latches `watchdog_tripped` when a program scan runs over budget
- [x] Timer / counter instruction objects: `TON`, `TOF`, `CTU`, `ONS` (`digitwin/instructions.py`)
- [x] **Validate:** `SCALED` 50× matches `FREE_RUN` trajectory; `TON` 2 s preset fires on the 20th 0.1 s scan

## Phase 2b — PLC hardware abstraction (vendor/model classes)  _(in progress)_

- [x] `HardwareProfile` dataclass (`digitwin/hardware.py`) — I/O counts, memory + retentive ranges, `address_syntax`, system bits, `default_watchdog_ms` / `min_scan_ms`
  - [ ] `instruction_set` field still unadded (feeds the _(later)_ capability gating below)
  - [ ] `min_scan_ms` is catalog data only — nothing reads it; the executive never clamps `dt` against it
  - [ ] no `output_type` (relay vs transistor) or comms-port fields yet — the roadmap's profile sketch lists both
- [x] Split `PLC` into abstract base — scan engine + firmware only, `ClassVar profile`, `watchdog_s` defaults from `profile.default_watchdog_ms`. Note: `PLC` declares no abstract *members*, so `ABC` isn't what stops instantiation — the runtime `TypeError` in `__init__` is (mypy does not see `PLC` as abstract)
- [x] `define_tag()` validates `native_address` against the profile — syntax, area vs tag type, index range; raises `AddressError`
  - [ ] validation is opt-in: a tag with `native_address=None` is never checked, so profile limits are bypassable
  - [ ] `%S` / `%SW` indices are not range-checked (`hardware.py::_range_check` ends before them)
- [x] One tag per address — a second tag claiming an address already in use raises `AddressError`; unaddressed tags never collide
- [x] Retentive ranges are applied — `define_tag`'s `retentive` defaults to `profile.is_retentive(native_address)`, so `%M5` on the TM221 survives a cold start with no caller involvement; pass `retentive=` to override. `PLC_Generic` deliberately declares no retain range
- [x] `AddressArea` / `ParsedAddress` — address string → `(area, linear index)`
- [ ] Address → physical channel resolution (I/O bus wires to terminals, not tag names) — bus + `InProcessTransport` still wire by tag name
- [x] Auto-populate model-specific system tags (first-scan, always-on/off, scan-time word) — `PLC._define_system_tags()` reads `profile.first_scan_bit` / `always_on_bit` / `always_off_bit` / `scan_time_word` and defines+syncs them each scan (`FIRST_SCAN_TAG`, `ALWAYS_ON_TAG`, `ALWAYS_OFF_TAG`, `SCAN_TIME_MS_TAG` in `plc.py`); `plc.first_scan` bare bool kept for internal engine use, tag mirrors it for programs
- [ ] Analog I/O never crosses the scan boundary — `ANALOG_INPUT` maps onto `TagType.WORD`, but the input image freezes only `DISCRETE_INPUT`, so a `%IW0.0` tag has to be read live with `plc.read()`; symmetrically `Executive._transfer_outputs` ships only `DISCRETE_OUTPUT`, so a `%QW` output never reaches the plant. The demo dodges this by putting `tank_level` at `%MW0`. Probably wants `TagType.ANALOG_INPUT` / `ANALOG_OUTPUT` rather than a patched freeze list
- [ ] Address-syntax strategies — _partial:_ `AddressSyntax` protocol + `IEC_DOTTED` done; `IEC_IX`, `AB_TAG`, `SIEMENS`, `MODICON` not started
- [x] `digitwin/models/generic.py` — `PLC_Generic` (permissive; demo + engine tests now run on it)
- [x] `digitwin/models/schneider_tm221.py` — `PLC_Schneider_TM221CE16T` (9 DI, 7 DO, 2 AI, `%M0..511`, `%MW0..7999`)
- [x] Model registry + `plc_from_model("TM221CE16T", program)` factory (`digitwin/models/__init__.py`)
- [ ] _(later)_ capability gating — check program instructions against `profile.instruction_set`
- [ ] _(later)_ per-model fidelity knobs — relay vs transistor switching delay, analog quantization, jitter band
- [x] **Validate:** `PLC_Schneider_TM221CE16T` accepts `%I0.8` / `%Q0.6`, raises on `%I0.9` / `%Q0.7` / `%QX0.0` — covered in `tests/test_hardware.py` (60 tests total)
- [ ] **Validate:** demo rebuilt on `PLC_Generic` gives identical historian output to Phase 2 — demo runs on `PLC_Generic` and the suite is green; the historian now exists and `tests/test_historian.py` proves the trace is pacing-mode-invariant, but there is no stored Phase 2 baseline to diff against. Folds into the Phase 7 golden-trace item
- [ ] **Verify the TM221 profile against the M221 system-object reference** — `always_on_bit="%S20"`, `always_off_bit="%S21"`, `scan_time_word="%SW10"` and `retentive_words=(0, 1999)` were filled in to exercise the mechanism and are marked UNVERIFIED in the module. `tests/test_hardware.py` now asserts them, so fix profile and assertions in one commit
- [ ] Sanity-check `HardwareProfile` fields against a second-vendor datasheet (S7-1200 or Micro850)

## Phase 3 — Observability (historian, events, snapshots)  _(landed)_

All four observers are optional fields on `Executive` and are fed from
`Executive._observe()` after the tick settles, timestamped in **simulation**
seconds — so a trace is identical in every pacing mode and a twin without them
behaves the same.

- [x] Historian (`digitwin/historian.py`) — bounded `(timestamp, tag, value)` store; `SampleMode.ON_CHANGE` (default) / `PERIODIC` (`interval_s`) / `EVERY_SCAN`, optional `tags` filter, `capacity` bound with a `dropped` counter
  - [x] Query API: `query` / `series` / `latest` / `value_at` / `snapshot_at` / `tag_names` / `span`
- [x] Optional persistent sink — `CsvSink`, `SqliteSink`, `JsonlSink` behind a `RecordSink` protocol, batched one write per scan and shared with the event log
  - [ ] no Parquet sink: it needs `pyarrow` and the base install stays stdlib-only — revisit as an optional extra alongside the Phase 6 ones
- [x] Structured event log (`digitwin/events.py`) — `Event` with `EventCategory` (MODE / ALARM / WATCHDOG / FAULT / OPERATOR / SYSTEM), `EventSeverity`, `scan`, and free-form `**data`; filtered by `query()`
  - [x] Watchdog trips are logged by the executive itself, once on the edge
  - [x] Operator actions: `demo.press()` writes the button *and* logs the action
  - [ ] Restarts are not logged — `PLC.cold_start` / `warm_start` / `power_cycle` don't know about the event log, and the executive can't see them; either the PLC takes an optional log or the restart entry points move behind the executive
  - [ ] Alarms are caller-supplied; there is no alarm-condition monitor (limits, deadband, ack) yet
- [x] Snapshot / restore — full twin state to JSON (`digitwin/snapshot.py`): tag values, scan count / first-scan / watchdog, program instruction state (timer accumulators, counter values, one-shot edges), plant state including sensor RNG, bus signals, executive clock
  - [x] `capture_state` / `restore_state` walk plain objects reflectively and skip what they can't represent, so an uncapturable attribute is never clobbered on restore; a component can override with `capture_state()` / `restore_state()` (the `Snapshotable` protocol)
  - [ ] Wall-clock fields (`last_scan_duration`, jitter) are deliberately *not* restored — they measure the host, not the twin
  - [ ] The historian and event log are deliberately *not* snapshotted: a rewind is a new branch in the audit trail, not a hole in it
- [x] Time-travel: snapshot every N scans, restore + branch — `SnapshotRecorder(every=, keep=)` on the executive, with `at()` / `rewind()` to the nearest kept point
- [x] Recorded-I/O replay (`digitwin/replay.py`) — `RecordingTransport` wraps any transport and stores the per-scan input frame *and* the outputs; `ReplayTransport` feeds them back into a `NullPlant` run (`build_replay`); `diff_outputs` reports every disagreement, the same comparison Phase 6's divergence detector will run live
  - [x] `RecordingTransport(plc=...)` also captures discrete inputs driven outside the bus (field-wired buttons, HMI, tests) — without it a replay silently loses them
  - [ ] Frames are keyed by tag name, so replay inherits the tag-name wiring gap below
- [x] **Validate:** run 100 scans, snapshot at 50, restore, run 50 more, diff tag table vs uninterrupted run (`tests/test_snapshot.py`)

## Phase 4 — Config-driven definition

- [ ] `digitwin/config.py` — load YAML/TOML project (tags, wiring, plant tree, executive settings)
- [ ] `build_demo_plc()` → `load_project(path)`
- [ ] `examples/tank.yaml`
- [ ] Config references PLC model by string (`plc: {model: TM221CE16T}`)
- [ ] **Validate:** `tank.yaml` produces byte-identical historian output to the hardcoded demo

## Phase 5 — HMI / visualization

- [ ] Small web app (FastAPI+WS, or stdlib `http.server`+SSE to stay dependency-free)
- [ ] Live tag values pushed each scan
- [ ] Trend charts from the historian
- [ ] On-screen operator buttons → `oit_*` internal bits → event log
- [ ] Schematic view: tank with animated level, valve states, lights
- [ ] Fault-injection controls (wire to Phase 7)
- [ ] **Validate:** press on-screen Start, watch tank fill in real time + trend update

## Phase 6 — External synchronization (twin of a real PLC)

- [ ] Confirm `IOTransport` protocol is adapter-ready — sync signature checked against `asyncua` (async) and `pymodbus` (sync)
- [ ] `digitwin/adapters/opcua.py` — map tags to OPC UA nodes (client + server); `asyncua`, adapter owns the event loop
- [ ] `digitwin/adapters/modbus.py` — `pymodbus`, imported lazily inside the module:
  - [ ] `ModbusClientTransport(IOTransport)` — twin as master; `read_inputs` ← discrete inputs / input registers, `write_outputs` → coils / holding registers; sync `ModbusTcpClient`
  - [ ] `ModbusSlaveServer` — twin as slave for external SCADA/HMI; side window on the tag table, synced once per scan, **not** an `IOTransport`
  - [ ] tag→register map: 16-bit zero-based registers, float / int32 = 2 registers with configurable word+byte order, scale/offset on analog values
- [ ] Add `opcua` / `modbus` optional-dependency extras to `pyproject.toml` (`digitwin[modbus]`)
- [ ] Topology: virtual commissioning (real PLC logic, DigiTwin is the plant)
- [ ] Topology: shadow mode (real PLC + twin get same field inputs, compare outputs)
- [ ] Topology: predictive (twin fed live inputs, runs faster than real time)
- [ ] Divergence detector — log tags disagreeing beyond tolerance for > 1 scan
- [ ] **Validate:** OPC UA adapter vs OpenPLC running same ladder — zero divergence, then inject a logic diff and confirm exactly that tag flags

## Phase 7 — Scenario harness, fault injection, full testing

- [ ] `digitwin/scenario.py` — timed steps + assertions, runs in `free_run`, usable as pytest cases
- [ ] Fault library — sensor stuck/drift/noise, actuator stuck/slow/reversed, wire break, comms dropout (injected via I/O bus)
- [ ] Golden historian traces for demo scenarios; CI diffs them in free-run
- [x] Add `pytest` to dev deps (`pyproject.toml`), create `tests/`
- [x] Unit tests for current engine: scan phasing, seal-in latch, edge detection, hardware profiles, observability (`test_engine.py`, `test_start_stop_tank.py`, `test_instructions.py`, `test_plant.py`, `test_executive.py`, `test_hardware.py`, `test_historian.py`, `test_snapshot.py`, `test_replay.py` — 104 tests)
- [x] `mypy --strict` covers `tests/` as well as `src/` (`pyproject.toml`), so test-side type claims are checked too
- [ ] **Validate:** `uv run pytest` green; tank scenario passes; flipping seal-in logic fails exactly one scenario
