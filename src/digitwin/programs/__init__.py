"""Scan-cycle programs that run on a :class:`digitwin.plc.PLC`, plus a name
registry so a project file can reference one by string."""

from __future__ import annotations

from collections.abc import Callable

from digitwin.plc import PLC, Program
from digitwin.programs.m221_tank_twin import M221TankTwinProgram
from digitwin.programs.start_stop_tank import StartStopTankProgram


def _noop(plc: PLC) -> None:
    """A program that does nothing — for plant-only twins and wiring smoke tests."""


# name -> factory(dt) -> Program. The factory takes the executive's ``dt`` so a
# program that needs the scan period (e.g. M221TankTwinProgram's emulated 1 Hz
# clock) gets it; factories that don't need it just ignore the argument.
_REGISTRY: dict[str, Callable[[float], Program]] = {
    "noop": lambda _dt: _noop,
    "start_stop_tank": lambda _dt: StartStopTankProgram(),
    "m221_tank_twin": lambda dt: M221TankTwinProgram(dt),
}


def program_from_name(name: str, *, dt: float) -> Program:
    """Look up a registered program by name, building it for scan period ``dt``."""
    try:
        return _REGISTRY[name](dt)
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown program {name!r}; known: {known}") from None


__all__ = [
    "M221TankTwinProgram",
    "StartStopTankProgram",
    "program_from_name",
]
