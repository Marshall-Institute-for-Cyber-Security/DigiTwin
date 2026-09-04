"""Scan-cycle programs that run on a :class:`digitwin.plc.PLC`."""

from digitwin.programs.m221_tank_twin import M221TankTwinProgram
from digitwin.programs.start_stop_tank import StartStopTankProgram

__all__ = ["M221TankTwinProgram", "StartStopTankProgram"]
