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

- [ ] `Executive(plc, plant, io, scan_ms, mode)` — modes `real_time` / `scaled` / `free_run`
- [ ] Per-tick order: `plant.step` → `io.transfer_inputs` → `plc.scan` → `io.transfer_outputs`
- [ ] Record actual scan duration + jitter
- [ ] First-scan bit (set only on scan 1)
- [ ] `retentive: bool` on `Tag`; non-retentive reset to initial on cold start
- [ ] Cold start / warm start / power cycle entry points
- [ ] Watchdog — flag program scans over budget
- [ ] Timer / counter instruction objects: `TON`, `TOF`, `CTU`, `ONS` (driven by `dt`)
- [ ] **Validate:** `scaled` 50× matches `real_time` trajectory; `TON` 2 s preset fires at right scan count

## Phase 2b — PLC hardware abstraction (vendor/model classes)

- [ ] `HardwareProfile` dataclass (`digitwin/hardware.py`) — I/O counts, memory ranges, retentive ranges, system-bit names, watchdog/scan limits, instruction set
- [ ] Split `PLC` into abstract base (scan engine only) + `self.profile`
- [ ] `define_tag()` validates `native_address` against the profile
- [ ] Address → physical channel resolution (I/O bus wires to terminals, not tag names)
- [ ] Auto-populate model-specific system tags (first-scan, always-on/off, scan-time word)
- [ ] Address-syntax strategies: `IEC_DOTTED`, `IEC_IX`, `AB_TAG`, `SIEMENS`, `MODICON`
- [ ] `digitwin/models/generic.py` — `PLC_Generic` matching today's demo
- [ ] `digitwin/models/schneider_tm221.py` — `PLC_Schneider_TM221CE16T` (9 DI, 7 DO, 2 AI)
- [ ] Model registry + `plc_from_model("TM221CE16T", program)` factory
- [ ] _(later)_ capability gating — check program instructions against `profile.instruction_set`
- [ ] _(later)_ per-model fidelity knobs — relay vs transistor switching delay, analog quantization, jitter band
- [ ] **Validate:** `PLC_Schneider_TM221CE16T` accepts `%I0.8` / `%Q0.6`, raises on `%I0.9` / `%Q0.7` / `%QX0.0`
- [ ] **Validate:** demo rebuilt on `PLC_Generic` gives identical historian output to Phase 2
- [ ] Sanity-check `HardwareProfile` fields against a second-vendor datasheet (S7-1200 or Micro850)

## Phase 3 — Observability (historian, events, snapshots)

- [ ] Historian — ring buffer of `(timestamp, tag, value)` on change / at sample rate + query API
- [ ] Optional persistent sink (CSV / SQLite / Parquet)
- [ ] Structured event log — mode changes, alarms, watchdog trips, faults, operator actions
- [ ] Snapshot / restore — full twin state to JSON (tags, plant state, timer accumulators, scan count)
- [ ] Time-travel: snapshot every N scans, restore + branch
- [ ] Recorded-I/O replay — persist input image per scan, replay with no plant
- [ ] **Validate:** run 100 scans, snapshot at 50, restore, run 50 more, diff tag table vs uninterrupted run

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
- [ ] Add `pytest` to dev deps (`pyproject.toml`), create `tests/`
- [ ] Unit tests for current engine: scan phasing, seal-in latch, edge detection
- [ ] **Validate:** `uv run pytest` green; tank scenario passes; flipping seal-in logic fails exactly one scenario
