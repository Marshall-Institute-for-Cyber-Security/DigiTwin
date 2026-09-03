"""Concrete PLC models and a name -> class registry."""
from __future__ import annotations

from digitwin.models.generic import PLC_Generic
from digitwin.models.schneider_tm221 import PLC_Schneider_TM221CE16T
from digitwin.plc import PLC, Program

_REGISTRY: dict[str, type[PLC]] = {
    cls.profile.model: cls for cls in (PLC_Generic, PLC_Schneider_TM221CE16T)
}

def plc_from_model(
    model: str,
    program: Program,
    *,
    name: str | None = None,
    watchdog_s: float | None = None,
) -> PLC:
    """Build a PLC for a registered model name, e.g. plc_from_model("TM221CE16T", prog)"""
    try:
        cls = _REGISTRY[model]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown PLC model {model!r}; known: {known}") from None
    return cls(name or model, program, watchdog_s=watchdog_s)

__all__ = [
    "PLC_Generic",
    "PLC_Schneider_TM221CE16T",
    "plc_from_model",
]