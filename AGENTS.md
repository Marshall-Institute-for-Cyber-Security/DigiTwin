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
  plc.py                 core engine: three-phase scan + firmware (first-scan
                         bit, retentive tags, restarts, watchdog)
  instructions.py        IEC timer/counter blocks: TON, TOF, CTU, ONS
  io.py                  I/O bus + IOTransport protocol + in-process transport
  executive.py           timed tick loop: FREE_RUN / REAL_TIME / SCALED modes
  demo.py                runnable demo: tank plant wired to a PLC via the bus
  programs/
    __init__.py
    start_stop_tank.py   example control program (seal-in start/stop, valves)
  plant/
    __init__.py
    base.py              PlantModel protocol + CompositePlant container
    tank.py              Tank: level integrates (q_in - q_out) / area
    sensors.py           AnalogSensor (scaled word), DiscreteSensor (hysteresis)
docs/
  ROADMAP.md             concept roadmap: target architecture + phased design
  TODO.md                phase-ordered build checklist
tests/
  test_engine.py         scan phasing, first-scan, retentive, restarts, watchdog
  test_instructions.py   TON / TOF / CTU / ONS
  test_plant.py          component dynamics / scaling / hysteresis
  test_executive.py      demo integration + pacing modes
  test_start_stop_tank.py  seal-in latch behaviour
```

Phases 1 and 2 are landed (plant/controller split; timed executive, firmware
realism, timer/counter blocks). Phase 2b (PLC hardware abstraction —
`HardwareProfile`, vendor/model subclasses) is the current work — see
[docs/TODO.md](docs/TODO.md).

## Commands

```bash
uv sync                 # create venv, install dev tools
uv run digitwin         # run the demo simulation
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run mypy             # type-check (strict, src/ only)
```

Plain-pip equivalent: `pip install -e ".[dev]"`, then `digitwin` / `ruff check .`
/ `mypy`. Python 3.12+.

Windows note: the shell here is PowerShell; the Bash tool is also available for
POSIX scripts.

## Conventions

- **Style:** ruff with `E, F, I, UP, B, SIM`; line length 100. `from __future__
  import annotations` at the top of every module.
- **Typing:** mypy `strict` must pass. Full annotations on all public functions.
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
`retentive=True` tags survive it. Keep these semantics.

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
- Control and plant communicate **only** through the I/O bus (`digitwin/io.py`);
  its synchronous `IOTransport` abstraction is later swapped for OPC UA
  (`asyncua`, adapter owns its loop) or Modbus (`pymodbus`, sync) — see the
  Phase 6 roadmap notes for the client-transport vs slave-server split. Never
  let a program import from `plant/` or vice versa.
- Still ahead (Phase 2b): `PLC` becomes an abstract base; concrete
  `PLC_<Vendor>_<Model>` subclasses carry a `HardwareProfile` (I/O counts,
  memory map, address syntax).

When adding features, check [docs/TODO.md](docs/TODO.md) for which phase it
belongs to and follow that phase's design notes in the roadmap rather than
extending the flat structure.
