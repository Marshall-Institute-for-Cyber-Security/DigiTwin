# DigiTwin — Roadmap

## Purpose

DigiTwin is a **framework for building digital twins of controlled systems**: a
simulated controller (soft PLC) plus a simulated physical process, coupled
through an I/O layer, advancing in synchronized time, observable, and able to
stand in for real hardware over field protocols.

The water/tank material in this repo is a **validation vehicle**, not the point.
It proved the concept against physical lab devices (see *Validation history*
below) and now serves as the reference example. The framework itself carries no
water-specific assumptions.

Downstream research — dataset generation, attack-path analysis, pre-deployment
test pipelines — builds *on* this framework and is **explicitly out of scope
here**. This document is about one thing: making twin creation reliable and
repeatable. Anything that only matters to a specific research application belongs
in that application's repo, not this one.

---

## Architecture

```
            ┌────────────────────────────────────────────────┐
            │                  Executive                      │
            │  fixed-interval clock · scaled/real/free-run     │
            │  drives one tick: plant.step() → plc.scan()      │
            └───────────────┬─────────────────┬───────────────┘
                            │                 │
                   ┌────────▼───────┐   ┌─────▼──────────┐
                   │  Plant model   │   │      PLC       │
                   │ components +   │   │ scan engine +  │
                   │ dynamics       │   │ control program│
                   └────────┬───────┘   └─────┬──────────┘
                            │                 │
                    ┌───────▼─────────────────▼────────┐
                    │            I/O bus               │
                    │  sensor tags ← plant · actuator  │
                    │  tags → plant · noise/lag/faults │
                    │  transports: in-proc · Modbus ·  │
                    │  OPC UA (later) · file replay     │
                    └───────┬──────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │  Observability │  historian · event log ·
                    │                │  snapshot/restore · replay
                    └────────────────┘
```

### Layer invariants (see `AGENTS.md` for the full statement)

- **Control logic** lives only in `programs/` — reads sensor tags, commands
  actuator tags, holds `instructions` blocks for timing. Nothing else.
- **Physical behaviour** lives only in `plant/`, behind `PlantModel.step(dt, io)`.
- **Time** belongs to the executive. Instructions and the plant receive `dt`;
  they never sleep or read the wall clock.
- **Observation is passive.** Attaching a historian / event log / recorder must
  not change the simulation. Timestamps are simulation seconds.
- **Control and plant communicate only through the I/O bus** (`io.py`).
- **Hardware identity lives in `models/`.** Add a controller by adding a
  `HardwareProfile`, not by widening the engine.

### Package layout

```
digitwin/
  plc.py          landed  abstract PLC, three-phase scan, firmware realism
  hardware.py     landed  HardwareProfile, address-syntax strategy, IEC_DOTTED
  instructions.py landed  TON / TOF / CTU / ONS
  io.py           landed  IOBus, IOTransport, in-process transport
  executive.py    landed  timed loop, FREE_RUN / REAL_TIME / SCALED
  historian.py    landed  trend store + CSV/SQLite/JSONL sinks
  events.py       landed  structured event log
  snapshot.py     landed  capture/restore, SnapshotRecorder (time-travel)
  replay.py       landed  recorded-I/O capture + replay + diff
  models/         landed  PLC_Generic, PLC_Schneider_TM221CE16T, registry
  plant/          landed  PlantModel, CompositePlant, Tank, Valve, Motor, Pump,
                          AnalogSensor, DiscreteSensor  — expanding (P2)
  programs/       landed  registry + example control programs
  adapters/
    modbus.py     landed  Modbus TCP master transport + slave server
    opcua.py      deferred
  config.py       landed  (P1)  load a twin from a TOML project file
  cli.py          landed  (P1)  `digitwin run PROJECT.toml`
  faults.py       PLANNED (P4)  bus-level signal perturbation
```

The tank twin lives as data (`examples/tank.toml`); its hand-wired Python
builder is `tests/reference.py`, a regression anchor that the framework never
imports or ships.

---

## Status ledger — what is landed

- **Plant / controller / time / observation are separated** and enforced by the
  invariants above. A program is a pure callable `(PLC) -> None`.
- **Controller engine**: three-phase scan, first-scan bit, retentive tags,
  cold/warm/power-cycle restarts, program-scan watchdog, `TON/TOF/CTU/ONS`.
- **Hardware abstraction**: `PLC` is abstract; a concrete model carries a
  `HardwareProfile` (I/O counts, memory map, retain ranges, address syntax,
  system bits, watchdog / scan limits). `define_tag` validates every
  `native_address` against the profile and claims its terminal exclusively;
  retention defaults from the profile. `tag_at()` resolves an address to its
  tag, so transports wire by terminal, not tag name. Model registry +
  `plc_from_model(name, program)`. Three profiles: `PLC_Generic`,
  `TM221CE16T`, `S7-1200_CPU1214C`.
- **Analog I/O crosses the scan boundary** as its own tag types, frozen /
  flushed alongside discrete channels.
- **Timed executive**: FREE_RUN / REAL_TIME / SCALED, jitter tracking,
  `min_scan_ms` enforced, hold-last + fault events on transport failure.
- **Observability**: historian (on-change / periodic / every-scan, query API,
  persistent sinks), structured event log, full snapshot/restore (reflective
  state walk, `Snapshotable` override), `SnapshotRecorder` time-travel,
  recorded-I/O replay with `diff_outputs`.
- **Modbus TCP**: `ModbusClientTransport` (twin as master, wired by address,
  block-batched, partial-read recovery) and `ModbusSlaveServer` (twin as slave,
  side window on the tag table, synced once per tick). `RegisterMap` with
  16/32-bit, word/byte order, scale/offset. Tested against a real `pymodbus`
  install over an actual socket.
- **Config / authoring layer** (P1, landed 2026-09-09): `load_project(path)`
  reads a TOML project — controller (`model:` or inline `profile:`), tags,
  plant component tree, in-process or Modbus coupling, Modbus slave window,
  executive, observers — into an `Executive`. `ConfigError` locates every
  structural fault at load time. `examples/tank.toml` and
  `examples/m221_lab_twin.toml` reproduce their hand-wired builders exactly.
  See `docs/BUILDING_A_TWIN.md`.
- **Plant component library** (P2, landed 2026-09-10): the actuator set
  (`Motor`, `Pump`, `Valve` + `switch_delay_s`, `FirstOrderActuator`), process
  elements (`Integrator`, `TransportDelay`, `PipeSegment`, `ThermalMass`,
  `PIDLoop`), `AnalogSensor` `scale`/`offset`/`resolution_bits`, `Tag`
  engineering-units metadata. All registered in `_PLANT_COMPONENTS`, each with
  an analytic-trajectory test plus a snapshot round-trip. `examples/heated_
  tank.toml` builds a heated stirred tank from library parts only.
  See `docs/WRITING_A_COMPONENT.md`.
- **Second vendor, proof of generality** (P3, landed 2026-09-21): Siemens
  S7-1200 CPU 1214C (`models/siemens_s7_1200.py`) + its `SIEMENS` address
  syntax, and a conveyor-motor twin (`examples/motor_conveyor.toml`,
  `programs/motor_conveyor.py`) that is not a level process, assembled
  entirely from the P2 library. Required zero changes to `plc.py` /
  `executive.py` / `io.py` / `historian.py` / `events.py`. See the P3
  section below for detail.
- **Fault injection** (P4, landed 2026-09-21): `digitwin/faults.py` — five
  bus-level signal faults (`StuckFault`/`OffsetFault`/`DriftFault`/
  `NoiseFault`/`FrozenFault`) plus `DropoutFault` (transport-level, reuses
  `TransportError`). `Executive.faults`, ticked between the plant and the
  input transfer; snapshot-safe via the existing reflective capture/restore;
  `[[faults]]` in the project schema. See the P4 section below for detail.
- **Engineering baseline**: `mypy --strict` over `src/` + `tests/`, ruff
  (`E,F,I,UP,B,SIM`), 246 tests (3 skipped, unrelated), zero runtime
  dependencies, Python 3.12+.

---

## The work, in priority order

Each priority has a validation step that runs without the priorities after it.

### P1 — Config / authoring layer  *(substantially landed 2026-09-09)*

Landed: `digitwin/config.py`, the two-path controller, all sections through
`[modbus.slave_server]` and `[observability]` sinks, both example twins as
project files with byte-identical regression tests, `docs/BUILDING_A_TWIN.md`.
Remaining: engineering units on `Tag` (moved to P2), an `opcua` transport
option (with the adapter), and a fresh-eyes build-from-doc check. Detail in
`docs/TODO.md`.

**Goal:** a twin is a project file plus a documented procedure, not a bespoke
Python module. Before this, every twin was hand-wired in Python with tag lists,
wiring dicts, plant assembly and register maps as literals — nothing stopped a
mistake and nothing was reusable.

**Scope**

- `digitwin/config.py` — `load_project(path) -> Executive`. One file describes:
  - `plc:` — the controller, by **either** path:
    - `model:` — a name from the `models/` registry. The normal path for a
      catalogued device: curated, datasheet-cited, reviewed, reused across
      twins.
    - inline `profile:` table (or `profile_file:`) — for a device **not yet
      catalogued**. The loader builds a `HardwareProfile` and an anonymous
      `PLC` subclass around it. An inline profile carries a provenance field
      (`verified = false` or a datasheet reference) and the loader notes at
      load time that it is not catalogue-reviewed. It may only name an
      `address_syntax` that is already implemented in code. **Promotion
      path:** once stable and datasheet-checked, an inline profile graduates
      to a `models/` subclass and the project switches to `model:`. Backed by
      `HardwareProfile.from_mapping()` / `to_mapping()`.
  - `tags:` — name, type, native address, initial value, retentive override,
    units / engineering range / scaling.
  - `plant:` — component tree with parameters (references the P2 library).
  - `wiring:` — which sensor tag each plant signal drives, which plant signal
    each actuator tag drives — keyed by native address, matching the transport
    convention already in place.
  - `executive:` — `dt`, mode, scale.
  - `modbus:` (optional) — client-transport and/or slave-server register maps,
    replacing hand-built `RegisterMap` objects.
- **Format:** prefer `tomllib` (stdlib, read-only load is all a project file
  needs) to keep the zero-dependency runtime. Fall back to YAML only if nesting
  ergonomics genuinely require it, and then as an optional extra.
- **Validation with clear errors** — unknown model, bad address, dangling
  wiring reference, plant component / parameter mismatch all fail at load with a
  located message, not a stack trace mid-run. Schema is versioned.
- Re-express the tank twin and the M221 lab twin as project files; keep the
  Python builders as thin wrappers or delete them.
- `docs/BUILDING_A_TWIN.md` — the repeatable procedure end to end.

**Done when:** the tank and M221 twins loaded from their project files produce
**byte-identical historian output** to the current hardcoded versions, a twin
whose controller is an inline `profile:` (no `models/` entry) loads and runs,
and a malformed field in each section produces a clear load-time error (tested).

### P2 — Plant component library  *(landed 2026-09-10)*

**Goal:** enough composable, tested primitives that standing up a new process is
wiring components together, not deriving dynamics from scratch.

Landed: the actuator set (`Motor`, `Pump`, `Valve` with a `switch_delay_s`
relay/transistor dead time, `FirstOrderActuator`), the process elements
(`Integrator`, `TransportDelay`, `PipeSegment`, `ThermalMass`, `PIDLoop`),
`AnalogSensor` signal conditioning (`scale`/`offset` calibration,
`resolution_bits` quantisation) and `Tag.units` / `eng_low` / `eng_high`
metadata. Each is registered in `_PLANT_COMPONENTS` and tested against an
analytic trajectory; there is a snapshot round-trip test. `docs/WRITING_A_
COMPONENT.md` documents the contract. `examples/heated_tank.toml` is the
validation twin — a heated, stirred tank (pump + tank + PID + thermal mass +
two sensors, `noop` program) that settles level and temperature with no bespoke
physics code.

**Scope (as delivered)**

- **Actuators:** `Motor` / `Pump` (start/stop with spin-up ramp, running-feedback
  contact), `Valve` (finite travel time; relay-vs-transistor switching delay as
  a model-driven knob), a generic first-order actuator.
- **Process elements:** `PipeSegment` / simple flow link, `ThermalMass`
  (heating/cooling toward ambient with a time constant), generic `Integrator`,
  `TransportDelay` (dead time), a `PIDLoop` element for cascaded control.
- **Signal conditioning:** per-channel analog quantisation / resolution and
  scale/offset on `AnalogSensor` (currently raw engineering-unit ints).
- **Contract:** each component is a dataclass with `step(dt, io)`, is snapshot-
  safe (plain fields or a `Snapshotable` override), and ships a unit test
  against an analytic or reference trajectory where one exists.
- `docs/WRITING_A_COMPONENT.md` — the `PlantModel` contract and bus-signal
  conventions for contributors.

**Done when:** a non-trivial process (e.g. a heated, stirred tank with a pump
and a PID loop) is built entirely from library components with no new physics
code, and each component's dynamics are covered by a test.

### P3 — Second twin as proof of generality *(landed 2026-09-21)*

**Goal:** exercise the abstraction on a device **materially different from the
tank**. Generality was asserted by design discipline before this; now
demonstrated.

**Landed:**

- A second real vendor `HardwareProfile` — **Siemens S7-1200 CPU 1214C**
  (`models/siemens_s7_1200.py`), fields checked against the official part
  datasheet (6ES7214-1AG40-0XB0) and the S7-1200 System Manual. Two facts
  left deliberately unset rather than approximated — `retentive_bits` /
  `retentive_words` (S7-1200 retention is a per-project TIA Portal
  configuration, not a fixed hardware range the way the M221 happens to
  have one) and `scan_time_word` (no fixed address exposes it on this CPU,
  unlike the M221's `%SW30`) — each with the reasoning written into the
  module docstring, per this document's own "an obvious gap beats a
  plausible-looking invented fact" standard.
- The **one** address-syntax strategy this vendor needs — `SIEMENS`
  (`hardware.py::_Siemens`) — `%I`/`%Q`/`%M` (byte.bit), `%MW` (byte
  offset), `%IW` (onboard analog, based at the TIA-Portal-default `%IW64`).
  `%QW` and DB access intentionally not implemented — no profile needs them
  yet. One known, documented engine-model limitation surfaced by this
  vendor: `%M`/`%MW` physically overlap on real Siemens hardware (unlike
  the M221), which this engine's non-overlapping `AddressArea` model can't
  represent without touching `plc.py`'s address-claim mechanism — flagged
  in `_Siemens`'s docstring and locked in by a test
  (`test_s7_1200_does_not_detect_real_byte_overlap_between_m_and_mw`)
  rather than left as a silent gap.
- A second plant that is **not** a level process — a conveyor motor
  (`programs/motor_conveyor.py::MotorConveyorProgram`), assembled entirely
  from the P2 library (`Motor` + `AnalogSensor`, no new physics code).
  Control shape is a classic industrial motor-start pattern (seal-in
  start/stop plus a `TON` run-proving timer that latches a fault if the
  running-feedback contact never seals in) — genuinely different from the
  tank twins' fill/drain interlock, and the first shipped program to
  exercise a timer block on new hardware.
- Its project file in the P1 schema — `examples/motor_conveyor.toml`, on
  the real `S7-1200_CPU1214C` model.
- **Framework changes required: none beyond `hardware.py`, `models/`, and
  `programs/`** — `plc.py` / `executive.py` / `io.py` / `historian.py` /
  `events.py` are all untouched, confirmed by diff. The abstraction held.

**Done when:** the second twin runs from a project file, its profile is
datasheet-checked, and the list of required framework changes is captured in the
PR description. **Met** — see `docs/TODO.md`'s P3 section for the full
verification (231 tests passed, `mypy --strict` and `ruff` clean, the CLI
running the new example directly).

### P4 — Framework-level fault injection  *(primitive only, landed 2026-09-21)*

**Goal:** the platform can perturb any signal, because a reliable twin framework
should be able to inject a fault regardless of what the fault is *for*. This is
infrastructure, not the research-facing fault catalogue.

**Landed**

- `digitwin/faults.py` — bus-level hooks: `StuckFault` (hold a value),
  `OffsetFault`, `DriftFault` (ramp, holds its accumulation while inactive
  and resumes rather than resetting), `NoiseFault`, `FrozenFault` (captures
  and holds whatever the signal read at activation — an input that never
  updates again). `DropoutFault` is the sixth (comms gap) but architecturally
  different: it wraps an `IOTransport` and raises `TransportError` while
  active, reusing the executive's existing hold-last / fault-event handling
  rather than a second failure path — attached by replacing
  `Executive.transport`, not by joining the fault list.
- Injected through the I/O bus: a new `Executive.faults` list, ticked once
  per tick after `plant.step()` and before the input transfer — so neither
  the plant nor the program is aware, they only ever see the (possibly
  perturbed) bus signal.
- Snapshot-safe: every signal fault is a plain dataclass, so `Snapshot`'s
  existing reflective `capture_state`/`restore_state` machinery (already used
  for `plant`/`program`) captures and restores fault state with no
  fault-specific serialization code.
- `[[faults]]` in the project schema, same array-of-tables shape as
  `[[plant.components]]`; `examples/motor_conveyor.toml` ships a real,
  inactive-by-default entry as a working demonstration.
- **Not** in scope, and nothing added: a timed scenario DSL, a curated
  library of application-specific fault scenarios, assertion tooling. Those
  belong to whatever consumes the framework.

**Done when:** each fault type is a one-liner to attach to a running twin, is
covered by a test, and a snapshot taken during a fault restores to the same
faulted state. **Met** — see `docs/TODO.md`'s P4 section for the full
verification (15 new tests, full suite 246 passed / 3 skipped, `mypy --strict`
and `ruff` clean).

### P5 — Regression / golden-trace harness

**Goal:** behaviour is locked, so refactors and new twins can't drift silently.
The current roadmap admits it never stored the "identical output" baselines it
keeps promising.

**Scope**

- Golden historian traces for every shipped example twin, run in `FREE_RUN` and
  diffed in CI.
- Stand the harness up early (once P1 lands) and add a trace per twin as it
  arrives.
- Backfill the two open validation items: demo-on-`PLC_Generic` identical-output
  baseline, and the second-vendor datasheet sanity check (folds into P3).

**Done when:** `uv run pytest` includes a golden-trace diff for each example
twin, and deliberately changing a program's logic fails exactly the traces that
touch it.

---

## Deferred (not on the reliability path)

| Item | Why deferred |
|---|---|
| HMI / live dashboard | Real demo value, but watching a twin is not what makes twin *creation* reliable. Revisit after P1–P3. |
| Scenario DSL + curated fault library | Belongs with the research that consumes the framework, not in it. P4 ships only the injection primitive. |
| OPC UA adapter (`adapters/opcua.py`) | Modbus TCP covers the current need. Build when a target twin actually speaks OPC UA. Keep checking the sync `IOTransport` signature stays adapter-ready. |
| Connection-topology framing (virtual commissioning / shadow / predictive) and a live divergence detector | Application patterns, not framework mechanism — the transport swaps that enable them already exist. `diff_outputs` covers the offline case today. |
| Deferred `HardwareProfile` catalogue fields (`instruction_set`, `output_type`, comms ports) and the other three address syntaxes | No consumer yet. Add one when P3's second vendor forces it — not speculatively. |
| TM221 `retentive_words` exact model | Documented approximation; the real M221 uses explicit program-triggered backup. Only worth doing if a twin depends on that mechanism. |

---

## Validation history

- **2026-09-04 — hardware replacement, physical M221.** `examples/m221_lab_twin.py`
  serving `ModbusSlaveServer` at the M221's native addresses was swapped in for
  the physical Cyberhive ICS Wall M221, with the real Maple Systems HMI
  connected against it unmodified. The twin matched the physical PLC's behaviour
  exactly.
- **Modbus server / register / coil behaviour** was checked with FactoryIO
  before the hardware test.

This is the evidence that the concept works. P3 extends it from "one twin
matched one real device" to "the framework builds a second, different twin
without structural change."

---

## Roadmap self-check

1. Each priority's validation step runs without the priorities after it — check
   that dependency direction holds (P2 needs P1's loader only for its final
   check; P3 needs P1 + P2; P5 needs P1).
2. The P1 schema must express P3's second-vendor profile reference and a
   non-level plant **without a schema change**. Sanity-check the schema against
   both before calling P1 done.
3. The example twins stay runnable (`uv run digitwin`) and produce identical
   historian traces before and after P1 (from file) and P5 (golden diff).
4. Adding the second vendor in P3 must not touch `plc.py`, `executive.py`,
   `io.py`, `historian.py`, or `events.py`. If it does, that file was not as
   generic as this document claims — fix the abstraction, don't special-case.
