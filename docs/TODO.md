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
- [x] Engineering units / range on `Tag` — landed in P2 (`Tag.units` /
      `eng_low` / `eng_high`, advisory metadata; `[[tags]]` keys)
- [ ] `opcua` transport option in `[transport]` — deferred with the adapter
- [ ] **Validate:** a second reviewer builds an unfamiliar twin from
      `BUILDING_A_TWIN.md` alone

## P2 — Plant component library  *(landed 2026-09-10)*

- [x] Actuators — `Motor` / `Pump` (spin-up ramp + running feedback), `Valve`
      (travel time + `switch_delay_s` relay-vs-transistor dead time),
      `FirstOrderActuator` (generic lag + rate limit) — `plant/actuators.py`
- [x] Process elements — `PipeSegment` / flow link, `ThermalMass`,
      `Integrator`, `TransportDelay`, `PIDLoop` (`plant/process.py`, registered
      in `_PLANT_COMPONENTS`, analytic-trajectory tests in `test_process.py`)
- [x] Per-channel analog quantisation / resolution (`resolution_bits`) +
      `scale` / `offset` calibration on `AnalogSensor`; `Tag.units` / `eng_low`
      / `eng_high` metadata (subsumes the P1 tag-units item)
- [x] Each component: dataclass, `step(dt, io)`, snapshot-safe, unit test vs an
      analytic / reference trajectory (incl. a snapshot round-trip test)
- [x] Register every component in `digitwin.config._PLANT_COMPONENTS`
- [x] `docs/WRITING_A_COMPONENT.md`
- [x] **Validate:** `examples/heated_tank.toml` — a heated, stirred tank
      (Pump + Tank + PIDLoop + ThermalMass + 2×AnalogSensor, `noop` program)
      settles level and temperature with no new physics code; covered by
      `test_config.py::test_heated_tank_project_runs_entirely_from_library_components`

## P3 — Second twin as proof of generality

- [x] Second real vendor `HardwareProfile` — Siemens S7-1200 CPU 1214C
      (`models/siemens_s7_1200.py`), fields datasheet-checked against the
      official part datasheet (6ES7214-1AG40-0XB0) and the S7-1200 System
      Manual, or explicitly left unset with a documented reason where no
      fixed hardware fact exists (`retentive_bits`/`retentive_words`,
      `scan_time_word` — see the module docstring). `first_scan_bit` /
      `always_on_bit` / `always_off_bit` are flagged as the standard TIA
      Portal "system memory byte" convention (`%MB1`), not immutable
      hardware, unlike the M221's fixed `%S13`.
- [x] The one `AddressSyntax` that vendor needs — `SIEMENS`
      (`hardware.py::_Siemens`), against the existing `AddressSyntax`
      protocol, registered in `ADDRESS_SYNTAXES`. Covers `%I`/`%Q` (byte.bit),
      `%M` (byte.bit), `%MW` (byte offset), `%IW` (onboard analog, based at
      `%IW64` per TIA Portal's default). `%QW` deliberately not implemented —
      this CPU has no onboard analog output and no profile needs it yet.
      Known, documented limitation: this engine's `AddressArea` model can't
      represent `%M`/`%MW` byte-overlap (real on Siemens hardware, unlike the
      M221), and fixing that would mean touching `plc.py`'s address-claim
      mechanism, which P3 must not do — see `_Siemens`'s docstring and
      `test_s7_1200_does_not_detect_real_byte_overlap_between_m_and_mw`,
      which locks the current (documented) behavior in on purpose.
      17 new tests in `test_hardware.py` (`test_s7_1200_*`), full suite green,
      `mypy --strict` and `ruff` clean.
- [x] A second plant that is not a level process — a conveyor motor
      (`programs/motor_conveyor.py::MotorConveyorProgram`): seal-in
      start/stop plus a `TON` run-proving timer that latches a fault (and
      drops the run command) if the motor's running-feedback contact never
      seals in within `prove_time_s` — a different control shape from the
      tank twins' fill/drain interlock, and the first shipped program to
      exercise a timer block on new hardware. Built entirely from stock
      `digitwin.plant` (`Motor` + `AnalogSensor`), no new physics code, same
      "assembly, not physics" bar `heated_tank.toml` set for P2.
- [x] Its project file in the P1 schema — `examples/motor_conveyor.toml`,
      on the real `S7-1200_CPU1214C` model (not an inline profile). Belt
      speed is reported 0-27648 at 10-bit resolution — Siemens' real "S7
      normalized" convention for the onboard analog input, not an arbitrary
      scale. `test_motor_conveyor_project_runs_entirely_from_library_
      components` in `test_config.py` loads it through `load_project` and
      confirms it settles (full speed, no fault) and stops cleanly; 7 more
      tests in `test_motor_conveyor.py` cover the program in isolation
      (latch/seal-in, stop-dominates, proving-within-time, proving-timeout,
      fault-clears-only-on-stop, feedback-loss-while-running). Every timing
      assertion was verified against a scratch run before being written
      down, not derived by inspection alone.
- [x] PR description lists every framework change the twin forced: **none**
      beyond `hardware.py` (the new `SIEMENS` syntax), `models/` (the new
      profile), and `programs/` (the new program) — target met.
- [x] **Validate:** the twin runs from its file (`uv run digitwin run
      examples/motor_conveyor.toml` — 100 scans, 10.0s simulated, historian
      recording); `plc.py` / `executive.py` / `io.py` / `historian.py` /
      `events.py` are untouched (confirmed by diff, not just assertion).

**P3 is complete.** Full suite: 231 passed, 3 skipped (unrelated, pre-existing);
`mypy --strict` and `ruff check .` both clean.

## P4 — Framework-level fault injection (primitive only)

- [x] `digitwin/faults.py` — `StuckFault` / `OffsetFault` / `DriftFault` /
      `NoiseFault` / `FrozenFault` (bus-level, same `step(dt, io)` shape as a
      `PlantModel` component) + `DropoutFault` (wraps an `IOTransport`,
      raises `TransportError` while active — reuses the executive's existing
      hold-last / fault-event handling instead of a second failure path).
      `FaultModel` protocol for the five signal-level faults.
- [x] Injected via the I/O bus: `Executive.faults: list[FaultModel]`, ticked
      in `tick()` after `plant.step()` and before `_transfer_inputs()` — the
      plant and program never see a fault, only the perturbed signal.
      `DropoutFault` attaches by replacing `Executive.transport` instead
      (documented in `faults.py`'s module docstring — it has no signal to
      perturb, it makes the exchange itself fail).
- [x] Snapshot-safe: `Snapshot` gained a `faults` field, captured/restored
      through the same reflective `capture_state`/`restore_state` machinery
      already used for `plant`/`program` — no bespoke serialization needed,
      since every signal fault is a plain dataclass. `DropoutFault` is
      deliberately *not* snapshotted (it lives on `Executive.transport`,
      never part of a snapshot's scope, same as the executive's own
      transport-failure counters).
- [x] `[[faults]]` array-of-tables in the project schema (`config.py`'s
      `_build_faults`), same shape as `[[plant.components]]`. A
      `type = "Dropout"` entry wraps the just-built transport instead of
      joining the list — one uniform syntax, two attachment points under
      the hood, documented rather than papered over.
      `examples/motor_conveyor.toml` ships a real (inactive-by-default)
      `[[faults]]` entry demonstrating it: flip `active = true` and
      `MotorConveyorProgram`'s proving-timer fault trips from config alone,
      no code changes — verified for real, not just asserted in a test.
- [x] **Not** here: scenario DSL, curated fault scenarios, assertion tooling —
      none added.
- [x] **Validate:** each fault is a one-liner to attach
      (`sim.faults.append(StuckFault(...))` / `sim.transport =
      DropoutFault(sim.transport)`); `test_faults.py`'s
      `test_a_drift_faults_state_survives_a_snapshot_round_trip` proves a
      snapshot taken mid-fault (a `DriftFault`'s accumulated bias) restores
      to the same faulted trajectory as an uninterrupted run, the same
      pattern `test_snapshot.py`'s own headline case already uses.

**P4 is complete.** 15 new tests (`test_faults.py` + 3 in `test_config.py`);
full suite 246 passed, 3 skipped (unrelated, pre-existing); `mypy --strict`
and `ruff check .` both clean.

## P5 — Regression / golden-trace harness

- [x] Golden historian traces for every shipped example twin
      (`tests/test_golden_traces.py` + `tests/golden/*.json`), run in
      `FREE_RUN`. Each twin gets a fresh, uniform `Historian(mode=
      EVERY_SCAN)` attached regardless of what its own project file
      declares, run through its own established stimulus (the same scripts
      `test_config.py`'s hardcoded-vs-config regression tests already use,
      where one exists — `tank`/`tank_inline_profile` share the fill/drain
      script, `m221_lab_twin` its own, `motor_conveyor` its start/stop
      script; `heated_tank` has no controller, so it's `sim.run(3000)`
      settling on its own), and diffed against the checked-in JSON.
      Deliberately excludes `scan_time_ms` (wall-clock derived), same as
      the existing hardcoded-vs-config regression tests.
      Diffed in CI too, not just locally: **`.github/workflows/ci.yml`**
      (new) runs ruff, `mypy --strict`, and the full test suite (with the
      `modbus` extra installed, so the real-`pymodbus` socket tests run
      too — verified locally first: 254 passed, 0 skipped, vs. 251 passed
      / 3 skipped without the extra) on every push/PR.
- [x] Stand the harness up once P1 lands; add a trace per twin as it
      arrives — landed after P3 (`motor_conveyor`), so all five shipped,
      documented example twins are covered. `examples/pump_tank_bench.toml`
      is deliberately **not** covered: it isn't in `AGENTS.md`'s documented
      example list and belongs to `examples/tester.py`, itself still a
      scratch script — add its trace if/when it's promoted to a real,
      documented example.
- [x] Backfill: demo-on-`PLC_Generic` identical-output baseline — covered
      by the `tank` / `tank_inline_profile` golden traces (both `Generic`,
      one inline-profile); second-vendor datasheet sanity check — covered
      by P3 (Siemens S7-1200) plus the new `motor_conveyor` golden trace.
- [x] **Validate:** changing a program's logic fails exactly the traces
      that touch it. Verified for real, not just claimed: introduced a
      one-line logic inversion into `MotorConveyorProgram` (`if not
      timed_out:` instead of `if timed_out:`), ran the golden-trace suite,
      confirmed **exactly** `test_motor_conveyor_golden_trace` failed (at
      the first tick `motor_run` diverges) while the other four traces
      stayed green, then reverted — `git diff` on the file came back empty,
      confirming a clean revert before anything was committed.

**P5 is complete.** 5 new tests (`test_golden_traces.py`) + a new CI
workflow; full suite (with `modbus` installed) 254 passed, 0 skipped;
`mypy --strict` and `ruff check .` both clean.

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
