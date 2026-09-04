"""External protocol adapters: swap the transport at the plant<->PLC boundary
for a real network protocol instead of the in-process one.

Each adapter's third-party dependency lives behind an optional extra
(``pip install digitwin[modbus]``) and is imported lazily, on first use — so
importing this package, or the base ``digitwin`` package that re-exports it,
never requires the dependency to be installed.
"""

from __future__ import annotations

from digitwin.adapters.modbus import ModbusClientTransport, ModbusSlaveServer, RegisterMap

__all__ = [
    "ModbusClientTransport",
    "ModbusSlaveServer",
    "RegisterMap",
]
