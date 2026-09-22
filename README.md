# DigiTwin

A small **soft-PLC engine and simulation harness**, written in pure Python
(stdlib only at runtime — no third-party runtime dependencies). It pairs a
scan-cycle-accurate simulated PLC with a simulated physical plant, run in
synchronized time behind a shared I/O bus, so you can develop and test control
logic against realistic process dynamics without real hardware.

The long-term goal is a proper **digital twin for PLCs**: observable (tag
historian, structured event log, snapshot/restore and time-travel), able to
mirror real vendor hardware (addressing, memory map, retention, system bits),
and able to talk the same wire protocols real hardware does (Modbus today).
See [docs/ROADMAP.md](docs/ROADMAP.md) for where that's headed.

## Usage

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                # create the venv and install dev tools
uv run digitwin run examples/tank.toml # load a twin from a project file and scan it
```

With plain pip:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                             # editable install + dev tools
digitwin run examples/tank.toml                     # load and scan a twin
```

This is a standard PEP 621 project, so `pip install .` (or from a built wheel)
works too. `uv.lock` is uv-only; pip ignores it.

## Development

```bash
uv run pytest          # or: pytest
uv run ruff check .    # or: ruff check .
uv run mypy            # or: mypy
```

## Controllers

`PLC` is abstract: it carries the scan engine and firmware behaviour but no
hardware, so you instantiate a concrete model. A model is a subclass holding a
`HardwareProfile` — I/O counts, memory map, retain ranges, address syntax,
system bits, watchdog defaults. Two ship today: `PLC_Generic` (permissive, wide
I/O and memory — what the demo runs on) and `PLC_Schneider_TM221CE16T` (a
Modicon M221: 9 DI, 7 DO, 2 AI, `%M0..511`, `%MW0..7999`). Instantiate one
directly, or look it up by model name with `plc_from_model`:

```python
from digitwin import PLC_Generic, TagType, plc_from_model

def program(plc):
    plc.write_output("lamp", plc.read_input("button"))

plc = PLC_Generic("bench", program)   # or: plc_from_model("TM221CE16T", program)
plc.define_tag("button", TagType.DISCRETE_INPUT, False, "%I0.0")
plc.define_tag("lamp", TagType.DISCRETE_OUTPUT, False, "%Q0.0")

plc.write("button", True)   # stands in for the field wiring
plc.scan()
assert plc.read("lamp") is True
```

The profile is enforced as you define tags. `define_tag` raises `AddressError`
if the address is malformed, addresses the wrong area for the tag type, runs
past the model's channel count or memory range, or is already taken by another
tag — one address backs one tag:

```python
plc = plc_from_model("TM221CE16T", program)
plc.define_tag("sensor", TagType.DISCRETE_INPUT, False, "%I0.9")
# AddressError: '%I0.9' is past the discrete input count (9)
```

The profile also decides retention. `retentive` defaults to whether the address
falls in the model's retain range, because on real hardware retention is a
property of the memory area rather than of the tag: on the TM221 a tag at
`%MW100` survives `cold_start()` and one at `%MW5000` does not. Pass
`retentive=` to override. A tag defined with no native address skips all of
this — convenient in tests, but nothing about it is checked.

Adding a controller means adding a profile, not touching the engine; see
`digitwin/models/`. Note that several of the TM221's system-bit addresses are
marked UNVERIFIED in that module — they exercise the mechanism but have not
been checked against Schneider's system-object reference.

## Writing a program

A program is any callable taking a `PLC` (the argument is positional, so name
it whatever reads best). Read physical inputs from the frozen image with
`read_input`, stage physical outputs with `write_output`, and use `read` /
`write` for internal bits and words — the tags themselves are declared on the
controller with `define_tag`, as above. See `digitwin.programs.start_stop_tank`
for a worked example.

The program contains **only control logic**. Physical behaviour lives in a
plant model (`digitwin.plant`) that the PLC reaches only through the I/O bus
(`digitwin.io`); the `Executive` steps the plant and the PLC together each
tick. A whole twin — controller, tags, plant, wiring, observers — is described
by one TOML project file and loaded with `digitwin.config.load_project`; see
[docs/BUILDING_A_TWIN.md](docs/BUILDING_A_TWIN.md) and `examples/tank.toml`.

For edge detection or timing, hold an instruction block from
`digitwin.instructions` (`TON`, `TOF`, `CTU`, `ONS`) and call it each scan. The
`Executive` runs `FREE_RUN` (as fast as possible), `REAL_TIME`, or `SCALED`
(`scale`× wall-clock); pacing never changes `dt`, so the trajectory is
identical in every mode. `PLC` also models a first-scan bit, retentive tags,
cold/warm/power-cycle restarts, and a program-scan watchdog.

## Design notes

[docs/ROADMAP.md](docs/ROADMAP.md) has the target architecture and the phased
design rationale; [docs/TODO.md](docs/TODO.md) is the working checklist and
tracks known gaps inline under the items they qualify.
[AGENTS.md](AGENTS.md) states the engine invariants to preserve when changing
the scan cycle.
