# Building a twin

A twin is one TOML **project file**. `digitwin.config.load_project(path)` reads
it and returns a ready-to-run `Executive`. Nothing about a twin needs to be
written in Python unless you are adding a *new* control program, plant
component, or controller model to the shared library.

```python
from digitwin.config import load_project

sim = load_project("my_twin.toml")
sim.run(600)                       # 600 ticks in free-run
print(sim.plc.read("tank_level"))
```

## The smallest project

```toml
version = 1
name = "bench"

[plc]
model = "Generic"                  # a name from digitwin.models
program = "noop"                   # a name from digitwin.programs

[executive]
dt = 0.1                           # seconds of simulated time per tick
```

That loads and ticks — it just has no I/O and does nothing. A real twin fills in
tags, a plant, wiring, and observers.

## Sections

### `[plc]` — the controller

Exactly **one** of these three:

| Key | Use |
|---|---|
| `model = "TM221CE16T"` | A name from the `models/` registry. The normal path: catalogued, datasheet-cited, unit-tested. |
| `[plc.profile]` (inline table) | A device not yet catalogued. The loader builds a `HardwareProfile` and an anonymous `PLC` subclass, and **warns** (`ConfigWarning`) that it is not catalogue-reviewed. |
| `profile_file = "dev.toml"` | Same as inline, but the profile table lives in its own file (top-level keys, or under `[profile]`). Path is relative to the project file. |

Other `[plc]` keys: `name` (default `"plc"`), `program` (required, a
`digitwin.programs` name), `watchdog_s` (default: the profile's).

An inline profile mirrors `HardwareProfile`'s fields; inclusive ranges are
`[lo, hi]` lists and `address_syntax` is a strategy *name* (only `"IEC_DOTTED"`
is implemented today). Add `verified = true` or `datasheet = "…"` to record
provenance — the load-time warning still fires, but says so.

```toml
[plc.profile]
vendor = "Acme"
model = "PLC-9000"
digital_inputs = 16
digital_outputs = 16
memory_bits = [0, 255]
memory_words = [0, 255]
address_syntax = "IEC_DOTTED"
first_scan_bit = "%S1"
verified = false
```

**Promotion path:** once an inline profile is stable and checked against a
datasheet, move it into `src/digitwin/models/<vendor>_<model>.py` as a `PLC`
subclass and switch the project to `model:`.

### `[executive]` — time

`dt` (default `0.1`), `mode` (`"free_run"` | `"real_time"` | `"scaled"`,
default `free_run`), `scale` (for `scaled`, default `1.0`). Pacing never
changes `dt`, so the trajectory is identical in every mode.

### `[[tags]]` — the tag table

One array entry per tag:

```toml
[[tags]]
name = "start_button"
type = "discrete_input"      # discrete_input|discrete_output|analog_input|analog_output|internal_bit|word
address = "%I0.0"            # required for the four physical types; validated against the profile
initial = false             # default 0
retentive = true            # optional; default = whatever the profile's retain ranges say
```

### `[plant.components]` — the physics

An array of components, stepped in order. Put sources (tanks, motors) before the
sensors that read them. Omit the section for a controller-only twin
(`NullPlant`).

```toml
[[plant.components]]
type = "Tank"               # a name from digitwin.plant
area = 2.0
fill_rate = 20.0

[[plant.components]]
type = "AnalogSensor"
source = "tank_level_true"  # bus signal the tank writes
dest = "tank_level"         # bus signal feeding the level input
```

Each component's parameters are its dataclass fields; an unknown field fails at
load.

### `[wiring]` — in-process plant ↔ PLC coupling

Maps a **native address** (a physical terminal) to the **bus signal** on the
plant side. Address, not tag name, so renaming a tag never touches wiring.

```toml
[wiring.inputs]
"%IW0.0" = "tank_level"

[wiring.outputs]
"%Q0.2" = "fill_valve"
"%Q0.3" = "drain_valve"
```

### `[transport]` — replace the boundary (optional)

Default is the in-process transport driven by `[wiring]`. To make the twin a
Modbus **master** polling a remote device instead:

```toml
[transport]
type = "modbus_client"
host = "192.168.0.10"
port = 502
unit_id = 1

[transport.inputs]
"%IW0.0" = { kind = "input_register", address = 0, scale = 0.1 }

[transport.outputs]
"%Q0.0" = { kind = "coil", address = 0 }
```

`kind` is `discrete_input`/`input_register` for inputs,
`coil`/`holding_register` for outputs. `length = 2` spans two registers for a
32-bit value; `word_order`/`byte_order` (`"big"`/`"little"`) and `scale`/`offset`
handle the rest. Requires `pip install digitwin[modbus]` to actually connect.

### `[modbus.slave_server]` — an operator window (optional)

Exposes twin tags to an external SCADA/HMI. **Not** on the scan path — synced
once per tick. Keyed by **tag name**.

```toml
[modbus.slave_server]
host = "0.0.0.0"
port = 502

[modbus.slave_server.publish]              # HMI reads these
start_bit = { kind = "coil", address = 1 }
tank_level = { kind = "holding_register", address = 0 }

[modbus.slave_server.accept]               # HMI may write these
oit_start_button = { kind = "coil", address = 3 }
```

`load_project` builds it; you start it: `sim.modbus_slave.start()`.

### `[observability]`

```toml
[observability]
events = true                              # bool, or a table with `capacity`

[observability.historian]
mode = "on_change"                         # on_change (default) | periodic | every_scan
capacity = 100000
sink = { type = "csv", path = "trend.csv" }   # csv | sqlite | jsonl; path relative to the project
```

`historian = true` / `events = true` is shorthand for the defaults.

## What's available

| Registry | Where | List it |
|---|---|---|
| Controller models | `digitwin/models/__init__.py` | `digitwin.models._REGISTRY.keys()` |
| Control programs | `digitwin/programs/__init__.py` | `digitwin.programs._REGISTRY.keys()` |
| Plant components | `digitwin/config.py` | `digitwin.config._PLANT_COMPONENTS.keys()` |
| Historian sinks | `digitwin/config.py` | `digitwin.config._SINKS.keys()` |

Adding to a registry is a one-line entry plus the class/function it points at.
Programs take a `dt` factory argument (see `program_from_name`).

## Validating a twin

Each shipped example has a hand-wired Python builder and a project file that
must agree:

- `examples/tank.toml` ↔ `tests/reference.py::build_demo()`
- `examples/m221_lab_twin.toml` ↔ `examples/m221_lab_twin.py::build_lab_twin()`

`tests/test_config.py` runs each twin both ways and asserts identical
historian / tag output. Do the same for a new twin: build it once by hand, once
from a file, run the same stimulus, diff the trend. (`scan_time_ms` is
wall-clock derived — exclude it.)

## Known fidelity limits (carried from the water twin)

- **No real-time scan-cycle guarantee.** The executive holds a wall-clock
  *interval* between ticks; it does not model sub-millisecond scan jitter,
  interrupt priorities, or I/O-update timing within a scan.
- **No firmware quirks.** Vendor-specific startup behaviour, forcing tables,
  online-edit semantics, and communication-loss handling beyond hold-last are
  not modelled.
- **Retention is an address range**, not the real explicit-backup mechanism
  some controllers (e.g. the M221) actually use.
- **Physics is only as good as the component.** Explicit-Euler integration at
  scan rates; no CFD/FEA. Co-simulate an FMU for higher fidelity.
- **One address syntax.** `IEC_DOTTED` only until a second vendor needs another.
