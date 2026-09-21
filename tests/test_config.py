"""Project-file loading: a twin built from TOML must match the hand-wired
demo bit for bit, the inline-profile path must work and warn, and every
structural mistake must fail at load with a located message."""

from __future__ import annotations

from pathlib import Path

import pytest
from reference import build_demo

from digitwin.adapters.modbus import ModbusClientTransport, ModbusSlaveServer
from digitwin.config import ConfigError, ConfigWarning, load_project
from digitwin.executive import Executive, ExecutiveMode
from digitwin.historian import Sample
from digitwin.plc import TagValue

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _run_demo_script(sim: Executive) -> None:
    """The same stimulus sequence for every twin under test."""
    sim.plc.write("start_button", True)
    sim.run(5)
    sim.plc.write("start_button", False)
    sim.run(60)
    sim.plc.write("stop_button", True)
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(60)


def _trend(sim: Executive) -> list[Sample]:
    historian = sim.historian
    assert historian is not None
    # scan_time_ms is wall-clock derived, so it is the one tag that is not
    # reproducible between two runs — exclude it from the equality check.
    return [s for s in historian.samples if s.tag != "scan_time_ms"]


def _write(tmp_path: Path, body: str) -> Path:
    project = tmp_path / "project.toml"
    project.write_text(body, encoding="utf-8")
    return project


def test_tank_project_reproduces_the_hardcoded_demo_trend() -> None:
    from_file = load_project(EXAMPLES / "tank.toml")
    hardcoded = build_demo()

    _run_demo_script(from_file)
    _run_demo_script(hardcoded)

    assert _trend(from_file) == _trend(hardcoded)
    assert len(_trend(from_file)) > 0


def test_inline_profile_project_reproduces_the_demo_and_warns() -> None:
    with pytest.warns(ConfigWarning, match="not in the models/ catalog"):
        from_file = load_project(EXAMPLES / "tank_inline_profile.toml")
    hardcoded = build_demo()

    _run_demo_script(from_file)
    _run_demo_script(hardcoded)

    assert _trend(from_file) == _trend(hardcoded)
    assert type(from_file.plc).__name__ == "PLC_DigiTwin_Generic"


_MINIMAL = """
version = 1
[plc]
model = "Generic"
program = "noop"
[executive]
dt = 0.1
"""


def test_wrong_version_is_rejected(tmp_path: Path) -> None:
    project = _write(tmp_path, _MINIMAL.replace("version = 1", "version = 2"))
    with pytest.raises(ConfigError, match="version"):
        load_project(project)


def test_unknown_model_is_rejected(tmp_path: Path) -> None:
    project = _write(tmp_path, _MINIMAL.replace('"Generic"', '"NoSuchPLC"'))
    with pytest.raises(ConfigError, match="unknown PLC model"):
        load_project(project)


def test_unknown_program_is_rejected(tmp_path: Path) -> None:
    project = _write(tmp_path, _MINIMAL.replace('"noop"', '"nope"'))
    with pytest.raises(ConfigError, match="unknown program"):
        load_project(project)


def test_model_and_profile_together_are_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[plc.profile]\nvendor = "x"\nmodel = "y"\n'
        "digital_inputs = 1\ndigital_outputs = 1\n",
    )
    with pytest.raises(ConfigError, match="exactly one of"):
        load_project(project)


def test_bad_tag_address_fails_at_load(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[[tags]]\nname = "sensor"\ntype = "discrete_input"\n'
        'address = "%Q0.0"\n',  # output area for an input tag
    )
    with pytest.raises(ConfigError, match=r"\[\[tags\]\] 'sensor'"):
        load_project(project)


def test_unknown_tag_type_is_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[[tags]]\nname = "x"\ntype = "bogus"\naddress = "%M0"\n',
    )
    with pytest.raises(ConfigError, match="unknown 'bogus'"):
        load_project(project)


def test_unknown_plant_component_is_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path, _MINIMAL + '\n[[plant.components]]\ntype = "Reactor"\n'
    )
    with pytest.raises(ConfigError, match="unknown component 'Reactor'"):
        load_project(project)


def test_bad_plant_component_parameter_is_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[[plant.components]]\ntype = "Tank"\nnot_a_field = 1\n',
    )
    with pytest.raises(ConfigError, match="Tank"):
        load_project(project)


def test_invalid_component_value_is_rejected_with_a_located_message(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[[plant.components]]\ntype = "AnalogSensor"\n'
        'source = "s"\ndest = "d"\nresolution_bits = 0\n',
    )
    with pytest.raises(ConfigError, match="AnalogSensor.*resolution_bits"):
        load_project(project)


def test_tag_units_and_engineering_range_land_on_the_tag(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL + '\n[[tags]]\nname = "flow"\ntype = "analog_input"\n'
        'address = "%IW0.0"\nunits = "m3/h"\neng_low = 0.0\neng_high = 250.0\n',
    )
    sim = load_project(project)
    tag = sim.plc.tags["flow"]
    assert (tag.units, tag.eng_low, tag.eng_high) == ("m3/h", 0.0, 250.0)


def test_heated_tank_project_runs_entirely_from_library_components() -> None:
    sim = load_project(EXAMPLES / "heated_tank.toml")
    sim.run(3000)  # 300 s: pump spun up, level and temperature settled

    assert abs(float(sim.bus.get("tank_temp_true")) - 60.0) < 0.5   # PID holds setpoint
    assert abs(float(sim.bus.get("feed_flow")) - 8.0) < 1e-6        # pump proven up
    assert float(sim.bus.get("tank_level_true")) > 5.0              # tank filled and balanced
    assert sim.plc.read("tank_temp") == 600                         # 60 degC -> 600 counts


def test_motor_conveyor_project_runs_entirely_from_library_components() -> None:
    sim = load_project(EXAMPLES / "motor_conveyor.toml")
    sim.plc.write("start_button", True)
    sim.run(45)
    sim.plc.write("start_button", False)
    sim.run(20)

    assert sim.plc.read("fault_latch") is False
    assert sim.plc.tags["motor_run"].value is True
    assert sim.plc.read("belt_speed") == 27648             # full speed, full-scale counts
    assert abs(float(sim.bus.get("belt_speed_true")) - 1.0) < 1e-9

    sim.plc.write("stop_button", True)
    sim.run(1)
    sim.plc.write("stop_button", False)
    sim.run(40)
    assert sim.plc.tags["motor_run"].value is False
    assert sim.plc.read("belt_speed") == 0


def test_missing_plc_section_is_rejected(tmp_path: Path) -> None:
    project = _write(tmp_path, "version = 1\n[executive]\ndt = 0.1\n")
    with pytest.raises(ConfigError, match=r"missing required section \[plc\]"):
        load_project(project)


def test_profile_file_round_trips_through_to_mapping(tmp_path: Path) -> None:
    from digitwin.hardware import HardwareProfile
    from digitwin.models import PLC_Schneider_TM221CE16T

    original = PLC_Schneider_TM221CE16T.profile
    rebuilt = HardwareProfile.from_mapping(original.to_mapping())
    assert rebuilt == original


# --- Modbus + M221 lab twin ------------------------------------------------


def _tag_table(sim: Executive) -> dict[str, TagValue]:
    return {
        name: tag.value
        for name, tag in sim.plc.tags.items()
        if name != "scan_time_ms"  # wall-clock derived, not reproducible
    }


def _run_m221_script(sim: Executive) -> None:
    sim.plc.write("oit_start_button", True)
    sim.tick()
    sim.plc.write("oit_start_button", False)
    for step in range(1, 1101):
        sim.tick()
        if step == 700:
            sim.plc.write("oit_stop_button", True)
            sim.tick()
            sim.plc.write("oit_stop_button", False)


def test_m221_project_reproduces_the_hardcoded_lab_twin() -> None:
    from examples.m221_lab_twin import build_lab_twin

    from_file = load_project(EXAMPLES / "m221_lab_twin.toml")
    from_file.mode = ExecutiveMode.FREE_RUN  # the file says real_time; don't sleep in tests
    hardcoded = build_lab_twin()

    _run_m221_script(from_file)
    _run_m221_script(hardcoded)

    assert _tag_table(from_file) == _tag_table(hardcoded)


def test_m221_project_builds_the_modbus_slave_window() -> None:
    sim = load_project(EXAMPLES / "m221_lab_twin.toml")
    slave = sim.modbus_slave
    assert isinstance(slave, ModbusSlaveServer)
    assert set(slave.publish) == {"start_bit", "tank_level"}
    assert set(slave.accept) == {"oit_start_button", "oit_stop_button"}
    assert slave.publish["tank_level"].kind == "holding_register"
    assert slave.accept["oit_start_button"].address == 3


def test_modbus_client_transport_from_config(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL
        + """
[transport]
type = "modbus_client"
host = "10.0.0.5"
unit_id = 2

[transport.inputs]
"%I0.0" = { kind = "discrete_input", address = 0 }

[[tags]]
name = "sensor"
type = "discrete_input"
address = "%I0.0"
""",
    )
    sim = load_project(project)
    assert isinstance(sim.transport, ModbusClientTransport)
    assert sim.transport.host == "10.0.0.5"
    assert sim.transport.unit_id == 2
    assert sim.transport.inputs["%I0.0"].kind == "discrete_input"


def test_modbus_slave_with_undefined_tag_is_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path,
        _MINIMAL
        + '\n[modbus.slave_server.publish]\nghost = { kind = "coil", address = 0 }\n',
    )
    with pytest.raises(ConfigError, match="ghost"):
        load_project(project)


def test_unknown_transport_type_is_rejected(tmp_path: Path) -> None:
    project = _write(
        tmp_path, _MINIMAL + '\n[transport]\ntype = "carrier_pigeon"\n'
    )
    with pytest.raises(ConfigError, match="unknown type 'carrier_pigeon'"):
        load_project(project)


def test_historian_sink_is_built_and_written(tmp_path: Path) -> None:
    out = tmp_path / "trend.csv"
    project = _write(
        tmp_path,
        _MINIMAL
        + f"""
[observability.historian]
mode = "every_scan"
sink = {{ type = "csv", path = "{out.name}" }}
""",
    )
    sim = load_project(project)
    sim.run(3)
    assert sim.historian is not None
    sim.historian.close()
    assert out.exists() and out.read_text(encoding="utf-8").count("\n") > 3
