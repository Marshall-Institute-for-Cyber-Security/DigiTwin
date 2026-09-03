# DigiTwin — Digital Twin Roadmap for PLCs

## Context

DigiTwin today is a competent **soft-PLC engine**, not yet a **digital twin**.
`src/digitwin/plc.py` implements the classic three-phase scan (freeze inputs →
run program → flush outputs) with a typed tag table and native-address metadata.
`src/digitwin/programs/start_stop_tank.py` is one example program, and
`src/digitwin/demo.py` wires tags and scans in a bare `for` loop.

The gap between "soft PLC" and "digital twin" is that a twin must also model the
**physical process** the PLC controls, run in **synchronized time**, expose its
**state for observation**, and be able to **mirror a real PLC**. Right now:

- `StartStopTankProgram` both runs control logic *and* simulates the tank
  physics (level fills/drains inside `__call__`). There is no plant.
- There is no clock — `scan()` is called five times as fast as the CPU allows.
- Tag state is only observable by calling `plc.read()` between scans; no history,
  no events, no snapshots.
- Tags, wiring, and initial values are hardcoded in `demo.py`.
- Nothing connects to, or compares against, a real controller.
- There are no tests.

This document is a **concept roadmap** (design only — no code changes proposed
yet). It is organized as phases; the plant/controller split (Phase 1) is the
foundation everything else builds on, and the I/O layer is designed from the
start so external protocol sync (Phase 6) drops in cleanly.

## Target architecture

```
            ┌────────────────────────────────────────────────┐
            │                  Executive                      │
            │  fixed-interval clock · scaled/real/free-run     │
            │  drives one tick: plant.step() → plc.scan()      │
            └───────────────┬─────────────────┬───────────────┘
                            │                 │
                   ┌────────▼───────┐   ┌─────▼──────────┐
                   │  Plant model   │   │      PLC       │
                   │ tanks, valves, │   │ scan engine   │
                   │ pumps, sensor  │   │ + control      │
                   │ dynamics       │   │   program      │
                   └────────┬───────┘   └─────┬──────────┘
                            │                 │
                    ┌───────▼─────────────────▼────────┐
                    │            I/O bus                │
                    │  sensor tags ← plant, actuator    │
                    │  tags → plant; noise/lag/faults   │
                    │  pluggable transports:            │
                    │  in-proc · OPC UA · Modbus · file │
                    └───────┬──────────────────────────┘
                            │
                    ┌───────▼────────┐   ┌───────────────┐
                    │  Historian     │   │  Shadow/diff  │
                    │ trends, events,│   │ compare twin  │
                    │ snapshots      │   │ vs real PLC   │
                    └───────┬────────┘   └───────────────┘
                            │
                    ┌───────▼────────┐
                    │   HMI / API    │  live web dashboard, REST/WS
                    └────────────────┘
```

Package layout this implies (new modules alongside `plc.py`):

- `digitwin/plant/` — base `PlantModel` protocol + component models
- `digitwin/io.py` — the I/O bus / coupling layer and transport protocol
- `digitwin/hardware.py` — `HardwareProfile`, address-syntax strategies, model registry
- `digitwin/models/` — concrete `PLC` subclasses (`schneider_tm221.py`, `generic.py`)
- `digitwin/executive.py` — the real-time / scaled-time scan loop
- `digitwin/historian.py` — tag trends, event log, snapshot/restore
- `digitwin/scenario.py` — scripted stimulus + assertions
- `digitwin/adapters/` — `opcua.py`, `modbus.py` transports (Phase 6)
- `digitwin/hmi/` — dashboard (Phase 5)
- `digitwin/config.py` — load tags/wiring/plant from YAML

---

## Phase 1 — Split the plant from the controller (foundation)

**Goal:** the PLC program contains only control logic; all physical behavior
moves into a plant model that the PLC can only influence through I/O.

- Define `PlantModel` protocol: `step(dt: float, io: IOBus) -> None`. It reads
  actuator commands from the bus and writes sensor readings back.
- Build small composable component models with real (if simple) dynamics:
  - `Tank(area, inflow_valve, outflow_valve)` — level integrates
    `dLevel/dt = (q_in - q_out) / area`; `q` depends on valve position and
    (for outflow) `sqrt(level)`.
  - `Motor`/`Pump` — start/stop with spin-up ramp, running feedback contact.
  - `DiscreteSensor(threshold, hysteresis)` — e.g. level switch → discrete input.
  - `AnalogSensor(range, noise_sigma, filter_tau)` — scaled word value.
- Rewrite `StartStopTankProgram` to be pure control: latch start/stop, drive
  lights, command the fill/drain valves based on level *read from a sensor tag*.
  Delete the `_prev_fill_condition` physics bookkeeping — level now lives in the
  plant. (Keep edge-detection only where it is genuinely control logic.)
- The demo becomes: `TankPlant` (one `Tank` + two valves + a level transmitter)
  wired to the PLC through the I/O bus, stepped by the executive.

**Why this is the backbone:** without it, the "twin" is just a program talking to
itself. With it, you can swap in a higher-fidelity tank, inject a stuck valve, or
replace the simulated plant with a real one, and the control program is unchanged.

**Validate:** run the demo; the tank should fill on start and drain on stop as
before, but the level is now produced by `Tank.step()`, not the program. Add a
unit test that steps the plant with a fixed valve command and checks the level
trajectory against the analytic solution.

---

## Phase 2 — Real-time executive & scan-semantics fidelity

**Goal:** the twin advances in controlled time and behaves like real PLC firmware.

- `Executive(plc, plant, io, scan_ms, mode)` with modes:
  - `real_time` — sleep to hold the wall-clock scan interval
  - `scaled` — N× faster/slower than real time (for long runs / demos)
  - `free_run` — as fast as possible, for batch scenarios and CI
- Per-tick order: `plant.step(dt)` → `io.transfer_inputs()` → `plc.scan()` →
  `io.transfer_outputs()`. Record actual scan duration and jitter.
- Add PLC firmware realism to `plc.py`:
  - **First-scan bit** (`S1`/`FIRST_SCAN`) set only on scan 1.
  - **Retentive vs non-retentive tags** — non-retentive reset to initial value
    on cold start; retentive survive. Add a `retentive: bool` field to `Tag`.
  - **Cold start / warm start / power cycle** entry points.
  - **Watchdog** — flag if a program scan exceeds a configured budget.
  - **Timers & counters as first-class instruction objects** (`TON`, `TOF`,
    `CTU`, one-shot `ONS`) driven by `dt`, instead of hand-rolled `_prev_*`
    flags in every program.

**Validate:** run the demo in `scaled` mode at 50× and confirm level trajectory
matches `real_time`. Test that a `TON` with a 2 s preset fires after the right
number of 100 ms scans.

---

## Phase 2b — PLC hardware abstraction (vendor/model classes)

**Goal:** `PLC` becomes an abstract base holding only the universal scan engine;
concrete controllers are subclasses that carry a real hardware profile, e.g.:

```python
class PLC_Schneider_TM221CE16T(PLC):
    profile = HardwareProfile(
        vendor="Schneider Electric",
        model="TM221CE16T",
        digital_inputs=9,      # %I0.0 .. %I0.8
        digital_outputs=7,     # %Q0.0 .. %Q0.6 (transistor, sink)
        analog_inputs=2,       # 0-10 V, %IW0.0 .. %IW0.1
        memory_bits=(0, 511),          # %M0..%M511
        memory_words=(0, 7999),        # %MW0..%MW7999
        retentive_bits=(0, 511),       # configurable retain range
        address_syntax=IEC_DOTTED,     # %I0.0 / %Q0.1 / %M12 / %MW3
        first_scan_bit="%S13",
        default_watchdog_ms=250,
        min_scan_ms=1,
    )
```

Design:

- **`HardwareProfile` dataclass** (`digitwin/hardware.py`) — pure catalog data:
  I/O channel counts and electrical type, addressable memory ranges, retentive
  ranges, address syntax, system-bit names, watchdog/scan limits, supported
  instruction set, comms ports. No behavior.
- **`PLC` base** gains, driven by `self.profile`:
  - `define_tag()` validates `native_address` against the profile — rejects
    `%I0.9` on a 9-input model, `%MW9000` past the word range, wrong syntax.
  - Address → physical channel resolution: `%I0.3` → digital input terminal 3,
    so the I/O bus wires the plant to *terminals*, not tag names.
  - Auto-populates system tags (first-scan bit, always-on/always-off, scan-time
    word) with the names this model actually uses.
  - Applies the model's default watchdog and retentive ranges unless overridden.
- **Address-syntax strategies** — small parser/formatter objects
  (`IEC_DOTTED`, `IEC_IX` `%IX0.0`, `AB_TAG` `Local:1:I.Data.0`, `SIEMENS` `I0.0`
  / `DB1.DBX0.0`, `MODICON` `%M` / `4xxxx`). A model names its strategy; the
  parser turns an address string into `(area, byte, bit)` or `(area, index)`.
- **Model registry** — `plc_from_model("TM221CE16T", program)` factory so Phase 4
  YAML can just say `plc: {model: TM221CE16T}`. Register via decorator or a dict.
- **Capability gating (optional, later)** — `profile.instruction_set` lets a
  program (or a future ladder loader) be checked against what the target
  actually supports; flag use of an instruction the model lacks.
- **Fidelity knobs per model** — e.g. output type (relay vs transistor →
  switching delay in the plant coupling), analog resolution/quantization, scan
  jitter band. These feed the executive and I/O bus, not the program.

Start minimal: `HardwareProfile` + address validation + the `PLC` base split +
two concrete classes (the TM221CE16T and one generic `PLC_Generic` matching
today's demo). Everything else (registry, syntax strategies, capability gating)
layers on without touching control programs.

**Why now (2b, not later):** the base/subclass split and `define_tag` validation
are cheap to do right after Phase 2's firmware work and expensive to retrofit
once programs, config, and adapters all assume the flat `PLC`. The vendor
*catalog* can then grow one model at a time forever.

**Validate:** instantiate `PLC_Schneider_TM221CE16T`, assert `define_tag` accepts
`%I0.8` and `%Q0.6` but raises on `%I0.9`, `%Q0.7`, and `%QX0.0`. The demo,
rebuilt on `PLC_Generic`, produces identical historian output to Phase 2.

---

## Phase 3 — Observability (historian, events, snapshots)

**Goal:** you can see what the twin did, replay it, and branch from any point.

- **Historian** — ring buffer (and optional Parquet/CSV/SQLite sink) of
  `(timestamp, tag, value)` on change or at a sample rate. Query API for trends.
- **Event log** — structured events: mode changes, alarms, watchdog trips, faults
  injected, operator actions. This is the twin's audit trail.
- **Snapshot / restore** — serialize the full twin state (all tag values, plant
  internal state, timer accumulators, scan count) to JSON; reload to resume or to
  fork a what-if run. Enables "time-travel": snapshot every N scans, restore to
  scan 4000, change one input, run forward.
- **Recorded-I/O replay** — persist the input image each scan; replay it into the
  twin later with no plant, to reproduce a field incident bit-for-bit.

**Validate:** run 100 scans, snapshot at 50, restore, run 50 more, diff the tag
table against the uninterrupted run — should be identical.

---

## Phase 4 — Config-driven definition

**Goal:** a twin is described by data files, not by editing `demo.py`.

- `config.py` loads a YAML/TOML project:
  - `tags:` — name, type, native address, initial value, retentive, units,
    scaling, engineering range
  - `wiring:` — which sensor tag each plant output drives, which plant input each
    actuator tag drives
  - `plant:` — component tree with parameters
  - `executive:` — scan_ms, mode, scale
- `build_demo_plc()` becomes `load_project(path)`. Ship `examples/tank.yaml`.

**Validate:** the existing demo, expressed as `tank.yaml`, produces byte-identical
historian output to the hardcoded version.

---

## Phase 5 — HMI / visualization

**Goal:** a live operator-style view, because a twin you can't watch isn't much
of a twin.

- Small ASGI app (FastAPI + WebSocket, or stdlib `http.server` + SSE to stay
  dependency-free) exposing:
  - live tag values, pushed each scan
  - trend charts from the historian
  - buttons that write to `oit_*` internal bits (operator actions → event log)
  - fault-injection controls (Phase 7)
- A schematic view of the demo: tank with animated level, valve states, lights.
- Reuse: the HMI is a pure historian/IO consumer — no engine changes.

**Validate:** open the dashboard, press the on-screen Start, watch the tank fill
in real time and the trend update.

---

## Phase 6 — External synchronization (the "twin" of a real PLC)

**Goal:** the simulated controller and/or plant can be replaced by, or compared
against, real equipment over standard protocols.

- **Transport abstraction in `io.py`** (designed for in Phase 1): an `IOTransport`
  protocol with `read_inputs()` / `write_outputs()`. In-process transport couples
  plant↔PLC; other transports move the boundary.
- **Adapters** (`digitwin/adapters/`):
  - `opcua` — map tags to OPC UA nodes (client and/or server). `asyncua` lib.
  - `modbus` — map tags to coils/registers. `pymodbus` lib.
  - These are optional extras in `pyproject.toml` (`[project.optional-dependencies]`).
- **Three connection topologies**, all just transport swaps:
  1. *Virtual commissioning* — real PLC runs the logic, DigiTwin is the plant:
     real PLC outputs → plant actuators, plant sensors → real PLC inputs.
  2. *Shadow mode* — real PLC and DigiTwin both receive the same field inputs;
     compare their outputs.
  3. *Predictive* — DigiTwin fed live field inputs, runs faster-than-real-time to
     forecast state ahead of the real process.
- **Divergence detector** — when a real reference is connected, log any tag where
  twin and real disagree beyond tolerance for longer than one scan. This is the
  payoff: it catches model drift, logic-download mismatches, and sensor faults.

**Validate:** point the OPC UA adapter at a free soft-PLC (OpenPLC) running the
same ladder; run shadow mode; confirm zero divergence, then introduce a
deliberate logic difference and confirm the detector flags exactly that tag.

---

## Phase 7 — Scenario harness, fault injection, and testing

**Goal:** the twin is used to *prove things*, not just to run.

- `scenario.py` — a script of timed steps and assertions:
  ```
  at 0s     press start
  at 0s     assert red_light == on within 1 scan
  at 10s    assert tank_level >= 40
  at 12s    inject fault: fill_valve stuck_open
  at 20s    assert alarm "high_level" active
  ```
  Runs in `free_run` mode; usable directly as pytest cases.
- **Fault library** — sensor stuck / drift / noise burst, actuator stuck / slow /
  reversed, wire break (input frozen), comms dropout. Injected via the I/O bus so
  neither plant nor program needs to know.
- **Regression suite** — golden historian traces for the demo scenarios; CI runs
  them in free-run and diffs.
- Backfill unit tests for the current engine (scan phasing, seal-in latch, edge
  detection) as the first PRs — there are none today.

**Validate:** `uv run pytest` green; a scenario file for the tank demo passes,
and flipping the seal-in logic in the program makes exactly one scenario fail.

---

## Suggested sequencing

| Order | Phase | Rationale |
|------|-------|-----------|
| 1 | Phase 1 plant split + Phase 7 baseline tests | Nothing else is meaningful without a plant; tests lock current behavior first. |
| 2 | Phase 2 executive + firmware realism | Gives time-based behavior; unblocks timers/counters. |
| 2b | Phase 2b PLC base/subclass split + `HardwareProfile` | Cheap now, expensive to retrofit; do it before config/adapters assume a flat `PLC`. Vendor catalog grows later. |
| 3 | Phase 3 historian + snapshots | Needed to debug everything after. |
| 4 | Phase 4 config | Quality-of-life; makes multiple twins practical. |
| 5 | Phase 5 HMI | High-visibility demo value once there's something to watch. |
| 6 | Phase 6 external sync | The headline "twin" capability; rests on the Phase 1 I/O abstraction. |
| 7 | Phase 7 full scenario/fault harness | Continuous, but matures last. |

## Files this roadmap would touch first (Phase 1, when implemented)

- `src/digitwin/plant/__init__.py`, `src/digitwin/plant/base.py`,
  `src/digitwin/plant/tank.py` — new
- `src/digitwin/io.py` — new (I/O bus + `IOTransport` protocol, in-process impl)
- `src/digitwin/executive.py` — new (minimal fixed-`dt` loop to start)
- `src/digitwin/programs/start_stop_tank.py` — strip physics, keep control only
- `src/digitwin/demo.py` — assemble plant + PLC + executive
- `src/digitwin/plc.py` — add `retentive` to `Tag`, first-scan bit, cold-start
- `tests/` — new; engine + plant unit tests
- `pyproject.toml` — add `pytest` to dev deps; later `opcua`/`modbus` extras

## Verification of the roadmap itself

This is a design document, so "verification" means the phasing holds together:

1. Each phase lists a concrete validation step that can run without the later
   phases — check that dependency direction is respected.
2. Phase 1's `IOTransport` protocol must be expressive enough for Phase 6's OPC UA
   adapter — sanity-check the signature against `asyncua` before committing to it.
3. The demo must stay runnable (`uv run digitwin`) and produce equivalent output
   after Phase 1, Phase 2 (scaled mode), Phase 2b (rebuilt on `PLC_Generic`), and
   Phase 4 (from YAML).
4. The `HardwareProfile` fields must be sufficient to describe a second real model
   from a different vendor (e.g. a Siemens S7-1200 or AB Micro850) without schema
   changes — sanity-check against one datasheet before finalizing the dataclass.
