# DigiTwin — Build Checklist

Working checklist for the framework. Rationale, architecture, and per-priority
design notes live in `docs/ROADMAP.md`; this file tracks what is done and what
is next, in that document's priority order (P1–P5).

`docs/BUILDING_A_TWIN.md` is the how-to for creating a twin from a project file.

---

## Landed (framework substrate)

- [x] **Plant / controller / time / observation separated** and enforced by the
      `AGENTS.md` invariants. A program is a pure `(PLC) -> None` callable.
- [x] **Controller engine** — three-phase scan, first-scan bit, retentive tags,
      cold/warm/power-cycle restarts, program-scan watchdog, `TON/TOF/CTU/ONS`.
- [x] **Hardware abstraction** — abstract `PLC`; `HardwareProfile` (I/O counts,
      memory map, retain ranges, address syntax, system bits, watchdog / scan
      limits); `define_tag` validates every `native_address` and claims its
      terminal; retention defaults from the profile; `tag_at()` resolves an
      address to its tag. Model registry + `plc_from_model`. Profiles:
      `PLC_Generic`, `PLC_Schneider_TM221CE16T`.
- [x] **Analog I/O across the scan boundary** as distinct tag types.
- [x] **Timed executive** — `FREE_RUN` / `REAL_TIME` / `SCALED`, jitter
      tracking, `min_scan_ms` enforced, hold-last + fault events on transport
      failure.
- [x] **Observability** — historian (on-change / periodic / every-scan, query
      API, CSV/SQLite/JSONL sinks), structured event log, full snapshot/restore
      (`Snapshotable` override), `SnapshotRecorder` time-travel, recorded-I/O
      replay + `diff_outputs`.
- [x] **Modbus TCP** — `ModbusClientTransport` (master, wired by address,
      block-batched, partial-read recovery), `ModbusSlaveServer` (slave side
      window, synced once per tick), `RegisterMap` (16/32-bit, word/byte order,
      scale/offset). Tested against a real `pymodbus` over a socket.
- [x] **Engineering baseline** — `mypy --strict` (src + tests), ruff, zero
      runtime deps, Python 3.12+.
- [x] **Real-hardware validation** — the M221 twin replaced a physical Cyberhive
      ICS Wall M221 with the real Maple Systems HMI unmodified; behaviour
      matched (see ROADMAP *Validation history*).

---

## P1 — Config / authoring layer

- [x] `HardwareProfile.from_mapping()` / `to_mapping()` + address-syntax name
      registry (`ADDRESS_SYNTAXES`, `address_syntax_by_name`) — `hardware.py`
- [x] `program_from_name(name, *, dt)` registry — `programs/__init__.py`
      (`noop`, `start_stop_tank`, `m221_tank_twin`)
- [x] `digitwin/config.py` — `load_project(path) -> Executive`, TOML via stdlib
      `tomllib`, `ConfigError` with located messages, `ConfigWarning` for inline
      profiles
  - [x] `[plc]` — `model` **or** inline `[plc.profile]` / `profile_file`;
        anonymous `PLC` subclass for the inline path
  - [x] `[executive]`, `[[tags]]`, `[plant.components]` (component registry),
        `[wiring]`
  - [x] `[transport]` — `in_process` (default) or `modbus_client` with
        `RegisterMap` tables
  - [x] `[modbus.slave_server]` — `publish` / `accept` maps, attached as
        `Executive.modbus_slave`
  - [x] `[observability]` — historian mode / capacity / tags / sink, event log
        capacity / sink; CSV/SQLite/JSONL sinks
- [x] `examples/tank.toml` ↔ `tests/reference.py::build_demo()` — byte-identical
      historian trend (`tests/test_config.py`)
- [x] `examples/tank_inline_profile.toml` — inline-profile twin loads, runs,
      warns, matches the demo trend
- [x] `examples/m221_lab_twin.toml` ↔ `examples.m221_lab_twin.build_lab_twin()`
      — identical tag table after the reference script; Modbus slave window
      built from config
- [x] `digitwin/__init__.py` exports `load_project`, `ConfigError`,
      `ConfigWarning`
- [x] `docs/BUILDING_A_TWIN.md` — end-to-end procedure
- [x] pytest `pythonpath = ["."]` so tests can `import examples.*`
      (fixed a pre-existing collection failure)
- [x] Retire `src/digitwin/demo.py` — CLI is now `digitwin run PROJECT.toml`
      (`digitwin/cli.py`); the hand-wired tank builder moved to
      `tests/reference.py` (not shipped, not imported by the framework); the
      shipped package is framework-only
- [ ] Engineering units / range on `Tag` — **moved to P2** (needs an engine
      field; folds into per-channel analog scaling)
- [ ] `opcua` transport option in `[transport]` — deferred with the adapter
- [ ] **Validate:** a second reviewer builds an unfamiliar twin from
      `BUILDING_A_TWIN.md` alone

## P2 — Plant component library

- [ ] Actuators — `Motor` / `Pump` (spin-up ramp + running feedback), `Valve`
      (travel time; relay-vs-transistor delay knob), generic first-order actuator
- [ ] Process elements — `PipeSegment` / flow link, `ThermalMass`,
      `Integrator`, `TransportDelay`, `PIDLoop`
- [ ] Per-channel analog quantisation / resolution + scale/offset on
      `AnalogSensor` (subsumes the P1 tag-units item)
- [ ] Each component: dataclass, `step(dt, io)`, snapshot-safe, unit test vs an
      analytic / reference trajectory
- [ ] Register every component in `digitwin.config._PLANT_COMPONENTS`
- [ ] `docs/WRITING_A_COMPONENT.md`
- [ ] **Validate:** a heated stirred tank + pump + PID loop built entirely from
      library components, no new physics code

## P3 — Second twin as proof of generality

- [ ] Second real vendor `HardwareProfile` (S7-1200 or Micro850), every field
      datasheet-checked or marked `UNVERIFIED`
- [ ] The one `AddressSyntax` that vendor needs (`SIEMENS` or `AB_TAG`), against
      the existing protocol; register it in `ADDRESS_SYNTAXES`
- [ ] A second plant that is not a level process (thermal or motor/conveyor)
- [ ] Its project file in the P1 schema
- [ ] PR description lists every framework change the twin forced (target: only
      the new profile + syntax + components)
- [ ] **Validate:** the twin runs from its file; `plc.py` / `executive.py` /
      `io.py` / `historian.py` / `events.py` are untouched

## P4 — Framework-level fault injection (primitive only)

- [ ] `digitwin/faults.py` — bus-level `stuck` / `offset` / `drift` / `noise` /
      `frozen` / `dropout` (dropout reuses the `TransportError` path)
- [ ] Injected via the I/O bus; plant and program unaware
- [ ] Snapshot-safe (a faulted run captures and replays)
- [ ] `[faults]` section in the project schema
- [ ] **Not** here: scenario DSL, curated fault scenarios, assertion tooling
- [ ] **Validate:** each fault is a one-liner to attach; snapshot during a fault
      restores to the faulted state

## P5 — Regression / golden-trace harness

- [ ] Golden historian traces for every shipped example twin, diffed in
      `FREE_RUN` in CI
- [ ] Stand the harness up once P1 lands; add a trace per twin as it arrives
- [ ] Backfill: demo-on-`PLC_Generic` identical-output baseline; second-vendor
      datasheet sanity check (folds into P3)
- [ ] **Validate:** changing a program's logic fails exactly the traces that
      touch it

---

## Deferred (see ROADMAP *Deferred* table for why)

- [ ] HMI / live dashboard
- [ ] Scenario DSL + curated fault library
- [ ] `digitwin/adapters/opcua.py` (+ `[transport] type = "opcua"`)
- [ ] Connection-topology framing (virtual commissioning / shadow / predictive),
      live divergence detector
- [ ] `HardwareProfile` catalogue fields `instruction_set` / `output_type` /
      comms ports; address syntaxes beyond the one P3 adds
- [ ] Exact TM221 `%MW` retention (explicit program-triggered backup)
- [ ] Parquet historian sink (needs `pyarrow`)
