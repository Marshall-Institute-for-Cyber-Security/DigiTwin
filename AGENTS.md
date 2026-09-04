# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

DigiTwin is a small **soft-PLC engine and simulation harness** written in pure
Python (stdlib only for runtime; no third-party runtime deps). The long-term goal
is a proper **digital twin for PLCs**: a simulated controller plus a simulated
physical plant, running in synchronized time, observable, and able to mirror real
hardware.

- **Roadmap / design rationale:** [docs/ROADMAP.md](docs/ROADMAP.md)
- **Active checklist:** [docs/TODO.md](docs/TODO.md) — phase-ordered, references the roadmap

## Layout

```
src/digitwin/
  __init__.py            public exports
  plc.py                 core engine: abstract PLC, three-phase scan + firmware
                         (first-scan bit, retentive tags, restarts, watchdog)
  hardware.py            HardwareProfile (catalog data) + address syntax:
                         AddressArea / ParsedAddress / AddressSyntax / IEC_DOTTED
  instructions.py        IEC timer/counter blocks: TON, TOF, CTU, ONS
  io.py                  I/O bus + IOTransport protocol + in-process transport
  executive.py           timed tick loop: FREE_RUN / REAL_TIME / SCALED modes
                         + the optional Phase 3 observers
  historian.py           tag trend store (on-change / periodic) + CSV/SQLite/
                         JSONL sinks behind a RecordSink protocol
  events.py              structured event log: categories, severity, audit trail
  snapshot.py            capture/restore full twin state to JSON; SnapshotRecorder
                         for time-travel (snapshot every N scans, rewind, branch)
  replay.py              recorded-I/O: RecordingTransport / ReplayTransport /
                         diff_outputs — re-run a captured run with no plant
  demo.py                runnable demo: tank plant wired to a PLC via the bus
  models/
    __init__.py          model registry + plc_from_model(name, program)
    generic.py           PLC_Generic — permissive profile, used by demo + tests
    schneider_tm221.py   PLC_Schneider_TM221CE16T — 9 DI / 7 DO / 2 AI
  programs/
    __init__.py
    start_stop_tank.py   example control program (seal-in start/stop, valves)
  plant/
    __init__.py
    base.py              PlantModel protocol + CompositePlant + NullPlant
    tank.py              Tank: level integrates (q_in - q_out) / area
    sensors.py           AnalogSensor (scaled word), DiscreteSensor (hysteresis)
docs/
  ROADMAP.md             concept roadmap: target architecture + phased design
  TODO.md                phase-ordered build checklist
tests/
  test_engine.py         scan phasing, first-scan, retentive, restarts, watchdog
  test_hardware.py       address validation, retain ranges, system tags, registry
  test_instructions.py   TON / TOF / CTU / ONS
  test_plant.py          component dynamics / scaling / hysteresis
  test_executive.py      demo integration + pacing modes
  test_start_stop_tank.py  seal-in latch behaviour
  test_historian.py      sampling modes, query API, sinks, event log
  test_snapshot.py       capture/restore, JSON round-trip, time-travel
  test_replay.py         record + replay, divergence diff
```

Phases 1, 2 and 3 are landed (plant/controller split; timed executive, firmware
realism, timer/counter blocks; historian, event log, snapshot/restore and
recorded-I/O replay). Phase 2b (PLC hardware abstraction) is mostly
landed: `HardwareProfile`, the abstract `PLC` base, vendor/model subclasses, the
model registry, address validation, and profile-driven retention. Still open in
2b are terminal-level I/O wiring, analog tags in the scan images, and the other
address syntaxes — see [docs/TODO.md](docs/TODO.md), which tracks the known gaps
inline under each landed item.

## Commands

```bash
uv sync                 # create venv, install dev tools
uv run digitwin         # run the demo simulation
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run mypy             # type-check (strict, src/ + tests/)
```

Plain-pip equivalent: `pip install -e ".[dev]"`, then `digitwin` / `ruff check .`
/ `mypy`. Python 3.12+.

Windows note: the shell here is PowerShell; the Bash tool is also available for
POSIX scripts.

## Conventions

- **Style:** ruff with `E, F, I, UP, B, SIM`; line length 100. `from __future__
  import annotations` at the top of every module.
- **Typing:** mypy `strict` must pass over `src/` **and** `tests/`. Full
  annotations on all public functions.
- **Docstrings:** one-line module docstring; short class/function docstrings that
  explain intent, matching the terse style already in `plc.py`.
- **No runtime dependencies** without discussing it first. Dev-only tools and
  future protocol adapters (`opcua`, `modbus`) go in optional-dependency extras,
  not the base install.
- Keep the export lists in `src/digitwin/__init__.py`, `programs/__init__.py`,
  and `plant/__init__.py` in sync with what's public.

## Engine invariants — do not break these

The PLC models the classic **three-phase scan cycle** (`PLC.scan()` in
`plc.py`). Preserve the semantics:

1. **Input scan** — physical inputs (`TagType.DISCRETE_INPUT`) are frozen into
   `input_image` at the start of the scan.
2. **Program scan** — the program runs against that frozen image (timed; a scan
   over `watchdog_s` latches `plc.watchdog_tripped`).
3. **Output scan** — staged outputs in `output_image` are flushed to tags at the
   end.

`plc.first_scan` is true only on scan 1 (re-armed by `cold_start` / `warm_start`
/ `power_cycle`). `cold_start` resets non-retentive tags to `initial_value`;
retentive tags survive it. Keep these semantics.

A tag's retentiveness comes from the **hardware profile**, not the caller:
`define_tag` defaults `retentive` to `profile.is_retentive(native_address)`,
because on real hardware retention is a property of the memory area. Pass
`retentive=` only to override that deliberately. An address backs at most one
tag — a second claim raises `AddressError`.

Therefore, in program code:

- Read physical inputs with `plc.read_input(name)` — **never** `plc.read()` for a
  discrete input (that would bypass the frozen image).
- Stage physical outputs with `plc.write_output(name, value)` — flushed at end of
  scan, not immediately.
- Use `plc.read()` / `plc.write()` only for internal bits (`INTERNAL_BIT`) and
  words (`WORD`).
- Programs are callables `(plc: PLC) -> None`. For edge detection or timing, hold
  a `digitwin.instructions` block (`TON`, `TOF`, `CTU`, `ONS`) as instance state
  and call it each scan — don't hand-roll `_prev_*` flags.

## Direction (so new code lands the right way)

Control, physics, and timing are separate layers; keep them apart:

- **Control logic** lives in `programs/` — reads sensor tags, commands actuator
  tags, holds `instructions` blocks for timing. Nothing else.
- **Physical behavior** (tank levels, valve dynamics, sensor noise) lives in
  `digitwin/plant/` behind the `PlantModel` protocol (`step(dt, io)`).
- **Time** is the executive's: it owns `dt` and the pacing mode. Instructions
  and the plant receive `dt`; they never sleep or look at the wall clock.
- **Observation is passive.** The historian, event log and snapshot recorder
  are optional `Executive` fields fed once per tick from `_observe()`; nothing
  in the engine may depend on them, and attaching them must not change the
  simulation. Observability timestamps are simulation seconds, never wall
  clock, so a trace is reproducible and pacing-mode-invariant.
- Control and plant communicate **only** through the I/O bus (`digitwin/io.py`);
  its synchronous `IOTransport` abstraction is later swapped for OPC UA
  (`asyncua`, adapter owns its loop) or Modbus (`pymodbus`, sync) — see the
  Phase 6 roadmap notes for the client-transport vs slave-server split. Never
  let a program import from `plant/` or vice versa.
- **Hardware identity** lives in `models/`: `PLC` is abstract, and a concrete
  `PLC_<Vendor>_<Model>` carries a `HardwareProfile` (I/O counts, memory map,
  retain ranges, address syntax, system bits). Add a controller by adding a
  profile, not by widening the engine. Profile fields you can't source from a
  datasheet must be marked UNVERIFIED in the module, as `schneider_tm221.py`
  does — a plausible-looking invented address is worse than an obvious gap.

When adding features, check [docs/TODO.md](docs/TODO.md) for which phase it
belongs to and follow that phase's design notes in the roadmap rather than
extending the flat structure.
