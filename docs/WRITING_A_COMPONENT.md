# Writing a plant component

A plant component is one piece of simulated physics the executive advances once
per tick. Standing up a new process should be *wiring components together* — the
library in `digitwin/plant/` already covers tanks, actuators, and generic
dynamics. Write a new component only when no combination of the existing ones
produces the behaviour you need.

If you are assembling a twin from components that already exist, you want
[BUILDING_A_TWIN.md](BUILDING_A_TWIN.md) instead.

## The contract

```python
from digitwin.plant.base import PlantModel   # a Protocol, for reference only
```

A component is **a dataclass with one method**:

```python
def step(self, dt: float, io: IOBus) -> None: ...
```

- `dt` — seconds of simulated time this tick. It comes from the executive; the
  component never sleeps, never reads the wall clock, never looks at a pacing
  mode.
- `io` — the I/O bus. `io.get(name, default)` reads a signal, `io.set(name,
  value)` writes one. Values are `int | float | bool`.

`step` reads the actuator commands it cares about, advances its internal state
by `dt`, and writes its sensor outputs. That is the whole interface — there is
no `reset`, no `__init__` beyond the dataclass, no lifecycle.

### Rules

1. **Only the bus.** A component never imports from `digitwin/programs/`, never
   touches a `PLC` or a `Tag`. Control and physics meet only on the bus. The
   control program reaching the other side of that bus is equally forbidden
   from importing `digitwin/plant/`.
2. **Signal names are constructor parameters.** Don't hard-code bus keys. Take
   `inflow_signal: str`, `level_signal: str`, etc. so a twin can wire two
   instances of your component without a collision. Give optional outputs a
   `str | None = None` parameter and skip the `io.set` when it's `None`.
3. **Integrate explicitly.** `state += dt * rate` (forward Euler) is the house
   style — it is accurate enough at PLC scan rates and keeps `step` readable.
   For a first-order lag use the stable form `x += (dt / (tau + dt)) * (target
   - x)`, as `AnalogSensor` and `FirstOrderActuator` do.
4. **Clamp what's physical.** A level can't go negative, a valve position is
   `0..1`, a 0..1 command that arrives as `1.7` should be treated as `1.0`.
5. **Declaration order is your friend.** `CompositePlant` steps components in
   the order the project file lists them, within one tick. Put a source (tank,
   pump) before the sensor that reads it so the reading reflects this tick's
   physics. A controller-in-the-loop element (`PIDLoop`) placed before the
   process it drives simply acts on last tick's measurement — normal sampled
   control, not a bug.

## Snapshot safety

Snapshot/restore walks a component reflectively: primitive attributes are
saved, nested dataclasses / lists / dicts / `random.Random` are recursed,
everything else (open files, sockets, callables) is skipped. So:

- **Keep state in plain fields** — `float`, `int`, `bool`, `str`, `None`, or a
  `list` of those. `TransportDelay` holds its FIFO as a `list[float]`; a
  `collections.deque` would be dropped on capture.
- Use `field(default=..., repr=False)` for internal state that isn't a tuning
  parameter (`_filtered`, `_running`, `_delay_left`).
- If your state genuinely can't be expressed that way, implement
  `capture_state(self) -> StateValue` / `restore_state(self, state)` and the
  walker defers to you. None of the shipped components need this.

There is a round-trip test for the process elements in
[`tests/test_process.py`](../tests/test_process.py) —
`test_process_elements_survive_a_snapshot_round_trip`. Add your component to it.

## Testing

Every component ships a unit test that checks its dynamics against an **analytic
or reference trajectory**, not just "the number moved":

- constant-rate integration is exact under Euler — assert the closed form
  (`test_integrator_of_a_constant_is_exact`)
- a first-order lag has a known one-step value and settles at its target
  (`test_first_order_actuator_tracks_its_command_with_a_lag`)
- a threshold/hysteresis element is a truth table
  (`test_discrete_sensor_has_hysteresis`)

Put the test in `tests/test_<area>.py` (`test_actuators.py`, `test_process.py`,
`test_plant.py`).

## Registering it

1. Export it from `digitwin/plant/__init__.py` (add to the imports **and**
   `__all__`) and from `digitwin/__init__.py` (`AGENTS.md` asks these stay in
   sync).
2. Add it to `_PLANT_COMPONENTS` in `digitwin/config.py` under the name a
   project file will use (`"ThermalMass"`). The loader instantiates
   `Component(**params)` straight from the `[[plant.components]]` table, so the
   dataclass field names *are* the project-file keys. A bad key or value fails
   at load with a located `ConfigError`.
3. If it introduces a new tuning field on `Tag` or the engine, note it in
   `docs/TODO.md`.

## A worked example

A lump of thermal mass a heater drives, with first-order loss to ambient
([`plant/process.py`](../src/digitwin/plant/process.py) has the shipped
version):

```python
from dataclasses import dataclass
from digitwin.io import IOBus


@dataclass
class ThermalMass:
    temp_signal: str
    heater_signal: str
    heater_power: float = 1000.0
    heat_capacity: float = 4184.0
    time_constant_s: float = 60.0
    ambient: float = 20.0
    ambient_signal: str | None = None
    temp: float = 20.0

    def step(self, dt: float, io: IOBus) -> None:
        drive = max(0.0, min(1.0, float(io.get(self.heater_signal, 0.0))))
        ambient = (
            float(io.get(self.ambient_signal, self.ambient))
            if self.ambient_signal is not None
            else self.ambient
        )
        loss = (self.temp - ambient) / self.time_constant_s if self.time_constant_s > 0 else 0.0
        self.temp += dt * (drive * self.heater_power / self.heat_capacity - loss)
        io.set(self.temp_signal, self.temp)
```

Signal names are parameters (rule 2); the command is clamped `0..1` (rule 4);
Euler integration (rule 3); `temp` is a plain float, so snapshot just works.
[`examples/heated_tank.toml`](../examples/heated_tank.toml) wires it to a
`Pump`, a `Tank`, a `PIDLoop`, and two `AnalogSensor`s — a complete heated,
stirred tank with no physics code beyond the library.
