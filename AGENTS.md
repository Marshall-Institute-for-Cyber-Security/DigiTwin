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
  plc.py                 core engine: Tag, TagType, PLC, three-phase scan
  io.py                  I/O bus + IOTransport protocol + in-process transport
  executive.py           fixed-dt tick loop: plant.step -> transfer -> plc.scan
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
  test_plant.py          component dynamics / scaling / hysteresis
  test_executive.py      demo integration: control drives valves, plant owns level
```

Phase 1 (plant/controller split) is landed; `pytest` is wired up but coverage is
still thin. Backfilling engine tests (scan phasing, seal-in latch) and the rest
of Phase 2 is the current work — see [docs/TODO.md](docs/TODO.md).

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
2. **Program scan** — the program runs against that frozen image.
3. **Output scan** — staged outputs in `output_image` are flushed to tags at the
   end.

Therefore, in program code:

- Read physical inputs with `plc.read_input(name)` — **never** `plc.read()` for a
  discrete input (that would bypass the frozen image).
- Stage physical outputs with `plc.write_output(name, value)` — flushed at end of
  scan, not immediately.
- Use `plc.read()` / `plc.write()` only for internal bits (`INTERNAL_BIT`) and
  words (`WORD`).
- Programs are callables `(plc: PLC) -> None`. A program that needs edge
  detection or memory holds its own instance state until Phase 2 introduces real
  timer/counter objects. (`StartStopTankProgram` is now stateless — its old
  `_prev_*` physics bookkeeping moved to the plant.)

## Direction (so new code lands the right way)

Phase 1 split control from physics; keep them apart:

- **Control logic** lives in `programs/` — reads sensor tags, commands actuator
  tags, nothing else.
- **Physical behavior** (tank levels, valve dynamics, sensor noise) lives in
  `digitwin/plant/` behind the `PlantModel` protocol (`step(dt, io)`).
- They communicate **only** through the I/O bus (`digitwin/io.py`); its
  synchronous `IOTransport` abstraction is later swapped for OPC UA (`asyncua`,
  adapter owns its loop) or Modbus (`pymodbus`, sync) — see the Phase 6 roadmap
  notes for how the client-transport and slave-server roles differ. Never let a
  program import from `plant/` or vice versa.
- Still ahead (Phase 2b): `PLC` becomes an abstract base; concrete
  `PLC_<Vendor>_<Model>` subclasses carry a `HardwareProfile` (I/O counts,
  memory map, address syntax).

When adding features, check [docs/TODO.md](docs/TODO.md) for which phase it
belongs to and follow that phase's design notes in the roadmap rather than
extending the flat structure.
