# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

DigiTwin is a small **soft-PLC engine and simulation harness** written in pure
Python (stdlib only for runtime; no third-party runtime deps). The long-term goal
is a proper **digital twin for PLCs**: a simulated controller plus a simulated
physical plant, running in synchronized time, observable, and able to mirror real
hardware.

- **Roadmap / design rationale:** `C:\Users\lambert232\.claude\plans\can-you-take-a-effervescent-clock.md`
  (outside the repo — ask the user if it's missing)
- **Active checklist:** [TODO.md](TODO.md) — phase-ordered, references the roadmap

## Layout

```
src/digitwin/
  __init__.py            public exports
  plc.py                 core engine: Tag, TagType, PLC, three-phase scan
  demo.py                runnable demo: wires tags + program, scans in a loop
  programs/
    __init__.py
    start_stop_tank.py   example scan-cycle program (seal-in start/stop + tank)
```

There are **no tests yet** — adding `pytest` + a `tests/` tree is the first task
in TODO.md (Phase 7 baseline slice). Do it before large refactors.

## Commands

```bash
uv sync                 # create venv, install dev tools
uv run digitwin         # run the demo simulation
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
- Keep `src/digitwin/__init__.py` and `programs/__init__.py` export lists in sync
  with what's public.

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
  detection or memory holds its own state (see `StartStopTankProgram`'s
  `_prev_*` flags) — until Phase 2 introduces real timer/counter objects.

## Direction (so new code lands the right way)

The current `StartStopTankProgram` both runs control logic **and** simulates the
tank physics. That is a known wart. The roadmap's Phase 1 separates them:

- **Control logic** stays in `programs/`.
- **Physical behavior** (tank levels, valve dynamics, sensor noise) moves to a
  new `digitwin/plant/` package behind a `PlantModel` protocol.
- They communicate only through an **I/O bus** (`digitwin/io.py`), whose
  `IOTransport` abstraction is later swapped for OPC UA / Modbus.
- `PLC` will become an abstract base; concrete `PLC_<Vendor>_<Model>` subclasses
  carry a `HardwareProfile` (I/O counts, memory map, address syntax).

When adding features, check TODO.md for which phase it belongs to and follow that
phase's design notes in the roadmap rather than extending the flat structure.
