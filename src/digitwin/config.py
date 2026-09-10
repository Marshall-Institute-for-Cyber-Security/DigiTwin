"""Load a twin from a project file instead of hand-wiring it in Python.

A project is one TOML file describing the controller, its tags, the plant
component tree, the plant<->PLC coupling (in-process or Modbus), any Modbus
slave window, the executive settings, and which observers to attach.
:func:`load_project` turns it into a ready-to-run
:class:`~digitwin.executive.Executive`.

The controller is named one of two ways:

* ``[plc] model = "TM221CE16T"`` — a name from the ``models/`` registry. The
  normal path: those profiles are datasheet-cited, reviewed, and unit-tested.
* ``[plc.profile]`` (an inline table) or ``[plc] profile_file = "dev.toml"`` —
  for a device not yet in the catalog. The loader builds a
  :class:`~digitwin.hardware.HardwareProfile` and an anonymous ``PLC`` subclass
  around it, and emits a :class:`ConfigWarning` because an inline profile is
  not catalog-reviewed. Promote it to a ``models/`` subclass once it settles.

Every structural problem — unknown model, bad address, dangling wiring
reference, wrong component parameter, undefined Modbus tag — raises
:class:`ConfigError` at load time with a located message, never a stack trace
mid-run.
"""

from __future__ import annotations

import tomllib
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from digitwin.adapters.modbus import ModbusClientTransport, ModbusSlaveServer, RegisterMap
from digitwin.events import EventLog
from digitwin.executive import Executive, ExecutiveMode
from digitwin.hardware import HardwareProfile
from digitwin.historian import CsvSink, Historian, JsonlSink, RecordSink, SampleMode, SqliteSink
from digitwin.io import InProcessTransport, IOBus, IOTransport
from digitwin.models import plc_from_model
from digitwin.plant import (
    AnalogSensor,
    CompositePlant,
    DiscreteSensor,
    FirstOrderActuator,
    Integrator,
    Motor,
    NullPlant,
    PIDLoop,
    PipeSegment,
    PlantModel,
    Pump,
    Tank,
    ThermalMass,
    TransportDelay,
    Valve,
)
from digitwin.plc import PLC, Program, TagType
from digitwin.programs import program_from_name

CONFIG_VERSION = 1


class ConfigError(ValueError):
    """A project file is malformed or points at something that does not exist.
    Raised at load time, with a message that says which section and key."""


class ConfigWarning(UserWarning):
    """The project loads, but leans on something unreviewed — today only an
    inline hardware profile that is not in the ``models/`` catalog."""


# Plant component type name -> class. Each is a dataclass whose fields are the
# project-file parameters; ``step(dt, io)`` makes it a PlantModel.
_PLANT_COMPONENTS: dict[str, type] = {
    "Tank": Tank,
    "Valve": Valve,
    "Motor": Motor,
    "Pump": Pump,
    "FirstOrderActuator": FirstOrderActuator,
    "Integrator": Integrator,
    "TransportDelay": TransportDelay,
    "PipeSegment": PipeSegment,
    "ThermalMass": ThermalMass,
    "PIDLoop": PIDLoop,
    "AnalogSensor": AnalogSensor,
    "DiscreteSensor": DiscreteSensor,
}

_SINKS: dict[str, type] = {"csv": CsvSink, "sqlite": SqliteSink, "jsonl": JsonlSink}


def load_project(path: str | Path) -> Executive:
    """Build an :class:`Executive` from the TOML project at ``path``."""
    path = Path(path)
    project_dir = path.parent
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read project file {path}: {exc}") from exc
    try:
        cfg: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    version = cfg.get("version")
    if version != CONFIG_VERSION:
        raise ConfigError(
            f"project 'version' is {version!r}, this build supports {CONFIG_VERSION}"
        )

    exec_cfg = _table(cfg, "executive")
    dt = _as_float(exec_cfg.get("dt", 0.1), "[executive] dt")
    mode = _enum(ExecutiveMode, exec_cfg.get("mode", "free_run"), "[executive] mode")
    scale = _as_float(exec_cfg.get("scale", 1.0), "[executive] scale")

    plc = _build_plc(cfg, project_dir=project_dir, dt=dt)
    _define_tags(plc, cfg)
    plant = _build_plant(cfg)

    bus = IOBus()
    transport = _build_transport(cfg, plc=plc, bus=bus)

    obs = _table(cfg, "observability")
    return Executive(
        plc,
        plant,
        bus,
        transport,
        dt=dt,
        mode=mode,
        scale=scale,
        historian=_historian_from(obs.get("historian"), project_dir=project_dir),
        events=_events_from(obs.get("events"), project_dir=project_dir),
        modbus_slave=_build_modbus_slave(cfg, plc=plc),
    )


# --- controller -------------------------------------------------------------


def _build_plc(cfg: Mapping[str, Any], *, project_dir: Path, dt: float) -> PLC:
    plc_cfg = _table(cfg, "plc", required=True)

    program_name = plc_cfg.get("program")
    if not program_name:
        raise ConfigError("[plc] needs a 'program'")
    try:
        program: Program = program_from_name(str(program_name), dt=dt)
    except ValueError as exc:
        raise ConfigError(f"[plc] {exc}") from exc

    name = str(plc_cfg.get("name", "plc"))
    watchdog_s = plc_cfg.get("watchdog_s")
    if watchdog_s is not None:
        watchdog_s = _as_float(watchdog_s, "[plc] watchdog_s")

    sources = [key for key in ("model", "profile", "profile_file") if key in plc_cfg]
    if len(sources) != 1:
        raise ConfigError(
            "[plc] needs exactly one of 'model', 'profile', or 'profile_file' "
            f"(found: {', '.join(sources) or 'none'})"
        )

    if sources[0] == "model":
        try:
            return plc_from_model(
                str(plc_cfg["model"]), program, name=name, watchdog_s=watchdog_s
            )
        except ValueError as exc:
            raise ConfigError(f"[plc] {exc}") from exc

    if sources[0] == "profile":
        raw = plc_cfg["profile"]
        if not isinstance(raw, Mapping):
            raise ConfigError("[plc.profile] must be a table")
        profile_data = dict(raw)
    else:
        profile_data = _load_profile_file(project_dir / str(plc_cfg["profile_file"]))

    verified = bool(profile_data.pop("verified", False))
    datasheet = profile_data.pop("datasheet", None)
    try:
        profile = HardwareProfile.from_mapping(profile_data)
    except ValueError as exc:
        raise ConfigError(f"[plc.profile] {exc}") from exc

    _warn_inline_profile(profile, verified=verified, datasheet=datasheet)
    cls_name = f"PLC_{profile.vendor}_{profile.model}".replace(" ", "_")
    plc_cls: type[PLC] = type(cls_name, (PLC,), {"profile": profile})
    return plc_cls(name, program, watchdog_s=watchdog_s)


def _load_profile_file(path: Path) -> dict[str, Any]:
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read profile_file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"profile_file {path}: invalid TOML: {exc}") from exc
    inner = doc.get("profile", doc)
    if not isinstance(inner, Mapping):
        raise ConfigError(f"profile_file {path}: [profile] must be a table")
    return dict(inner)


def _warn_inline_profile(
    profile: HardwareProfile, *, verified: bool, datasheet: Any
) -> None:
    if datasheet:
        note = f" (author cites {datasheet!r})"
    elif verified:
        note = " (author set verified = true)"
    else:
        note = ""
    warnings.warn(
        f"[plc] uses an inline hardware profile for {profile.vendor} "
        f"{profile.model!r}{note}: it is not in the models/ catalog, so it is "
        f"not datasheet-reviewed or unit-tested. Promote it to a models/ "
        f"subclass once it is stable.",
        ConfigWarning,
        stacklevel=3,
    )


# --- tags -----------------------------------------------------------------


def _define_tags(plc: PLC, cfg: Mapping[str, Any]) -> None:
    for i, spec in enumerate(_array(cfg, "tags")):
        try:
            name = spec["name"]
            type_str = spec["type"]
        except KeyError as exc:
            raise ConfigError(f"[[tags]] #{i} is missing {exc}") from None
        tag_type = _enum(TagType, type_str, f"[[tags]] {name!r} type")
        try:
            plc.define_tag(
                str(name),
                tag_type,
                spec.get("initial", 0),
                spec.get("address"),
                retentive=spec.get("retentive"),
                units=spec.get("units"),
                eng_low=spec.get("eng_low"),
                eng_high=spec.get("eng_high"),
            )
        except ValueError as exc:  # AddressError included
            raise ConfigError(f"[[tags]] {name!r}: {exc}") from exc


# --- plant --------------------------------------------------------------------


def _build_plant(cfg: Mapping[str, Any]) -> PlantModel:
    specs = _array(cfg, "plant.components")
    if not specs:
        return NullPlant()
    components: list[PlantModel] = []
    for i, spec in enumerate(specs):
        params = dict(spec)
        type_name = params.pop("type", None)
        if not type_name:
            raise ConfigError(f"[[plant.components]] #{i} needs a 'type'")
        try:
            component_cls = _PLANT_COMPONENTS[str(type_name)]
        except KeyError:
            known = ", ".join(sorted(_PLANT_COMPONENTS))
            raise ConfigError(
                f"[[plant.components]] #{i}: unknown component {type_name!r}; known: {known}"
            ) from None
        try:
            components.append(component_cls(**params))
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"[[plant.components]] #{i} ({type_name}): {exc}"
            ) from exc
    return CompositePlant(components)


# --- coupling: transport + optional Modbus slave window ------------------


def _build_transport(cfg: Mapping[str, Any], *, plc: PLC, bus: IOBus) -> IOTransport:
    tcfg = _table(cfg, "transport")
    kind = str(tcfg.get("type", "in_process"))

    if kind == "in_process":
        wiring = _table(cfg, "wiring")
        return InProcessTransport(
            bus,
            plc,
            inputs=_addr_map(wiring.get("inputs", {}), "inputs"),
            outputs=_addr_map(wiring.get("outputs", {}), "outputs"),
        )

    if kind == "modbus_client":
        host = tcfg.get("host")
        if not host:
            raise ConfigError("[transport] type = 'modbus_client' needs a 'host'")
        inputs = _register_maps(tcfg.get("inputs", {}), "[transport.inputs]")
        outputs = _register_maps(tcfg.get("outputs", {}), "[transport.outputs]")
        kwargs = {k: tcfg[k] for k in ("port", "unit_id", "timeout") if k in tcfg}
        try:
            return ModbusClientTransport(
                str(host), plc, inputs=inputs, outputs=outputs, **kwargs
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"[transport] {exc}") from exc

    raise ConfigError(
        f"[transport] unknown type {kind!r}; known: in_process, modbus_client"
    )


def _build_modbus_slave(cfg: Mapping[str, Any], *, plc: PLC) -> ModbusSlaveServer | None:
    scfg = _table(cfg, "modbus").get("slave_server")
    if scfg is None:
        return None
    if not isinstance(scfg, Mapping):
        raise ConfigError("[modbus.slave_server] must be a table")
    publish = _register_maps(scfg.get("publish", {}), "[modbus.slave_server.publish]")
    accept = _register_maps(scfg.get("accept", {}), "[modbus.slave_server.accept]")
    kwargs = {k: scfg[k] for k in ("host", "port", "unit_id") if k in scfg}
    try:
        return ModbusSlaveServer(plc=plc, publish=publish, accept=accept, **kwargs)
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigError(f"[modbus.slave_server] {exc}") from exc


def _register_maps(raw: Any, label: str) -> dict[str, RegisterMap]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{label} must be a table of key = {{ kind, address, ... }}")
    return {str(k): _register_map(v, f"{label} {k!r}") for k, v in raw.items()}


def _register_map(spec: Any, where: str) -> RegisterMap:
    if not isinstance(spec, Mapping):
        raise ConfigError(f"{where} must be a table with at least 'kind' and 'address'")
    try:
        return RegisterMap(**dict(spec))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from exc


# --- observers --------------------------------------------------------------


def _historian_from(spec: Any, *, project_dir: Path) -> Historian | None:
    if spec is None or spec is False:
        return None
    if spec is True:
        return Historian()
    if isinstance(spec, Mapping):
        params = dict(spec)
        kwargs: dict[str, Any] = {}
        if "mode" in params:
            kwargs["mode"] = _enum(
                SampleMode, params.pop("mode"), "[observability.historian] mode"
            )
        for key in ("capacity", "interval_s"):
            if key in params:
                kwargs[key] = params.pop(key)
        if "tags" in params:
            kwargs["tags"] = frozenset(params.pop("tags"))
        if "sink" in params:
            kwargs["sink"] = _sink_from(
                params.pop("sink"), where="[observability.historian.sink]",
                project_dir=project_dir,
            )
        if params:
            raise ConfigError(
                f"[observability.historian] unknown key(s): {', '.join(sorted(params))}"
            )
        return Historian(**kwargs)
    raise ConfigError("[observability] historian must be a boolean or a table")


def _events_from(spec: Any, *, project_dir: Path) -> EventLog | None:
    if spec is None or spec is False:
        return None
    if spec is True:
        return EventLog()
    if isinstance(spec, Mapping):
        params = dict(spec)
        kwargs: dict[str, Any] = {}
        if "capacity" in params:
            kwargs["capacity"] = params.pop("capacity")
        if "sink" in params:
            kwargs["sink"] = _sink_from(
                params.pop("sink"), where="[observability.events.sink]",
                project_dir=project_dir,
            )
        if params:
            raise ConfigError(
                f"[observability.events] unknown key(s): {', '.join(sorted(params))}"
            )
        return EventLog(**kwargs)
    raise ConfigError("[observability] events must be a boolean or a table")


def _sink_from(spec: Any, *, where: str, project_dir: Path) -> RecordSink:
    if not isinstance(spec, Mapping):
        raise ConfigError(f"{where} must be a table with a 'type'")
    params = dict(spec)
    kind = params.pop("type", None)
    if not kind:
        raise ConfigError(f"{where} needs a 'type'")
    try:
        sink_cls = _SINKS[str(kind)]
    except KeyError:
        raise ConfigError(
            f"{where}: unknown sink type {kind!r}; known: {', '.join(sorted(_SINKS))}"
        ) from None
    if "path" in params:
        params["path"] = str(project_dir / str(params["path"]))
    try:
        sink: RecordSink = sink_cls(**params)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where} ({kind}): {exc}") from exc
    return sink


# --- small helpers --------------------------------------------------------


def _table(
    data: Mapping[str, Any], key: str, *, required: bool = False
) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        if required:
            raise ConfigError(f"missing required section [{key}]")
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{key}] must be a table")
    return dict(value)


def _array(data: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    container: Any = data
    *path, leaf = key.split(".")
    for step in path:
        container = container.get(step, {}) if isinstance(container, Mapping) else {}
    value = container.get(leaf, []) if isinstance(container, Mapping) else []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ConfigError(f"[[{key}]] must be an array of tables")
    return [dict(item) for item in value]


def _addr_map(raw: Any, label: str) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"[wiring] {label} must be a table of  address = signal")
    return {str(k): str(v) for k, v in raw.items()}


def _as_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{label} must be a number, got {value!r}") from None


def _enum(enum_cls: Any, value: Any, label: str) -> Any:
    try:
        return enum_cls(str(value))
    except ValueError:
        known = ", ".join(member.value for member in enum_cls)
        raise ConfigError(f"{label}: unknown {str(value)!r}; known: {known}") from None
