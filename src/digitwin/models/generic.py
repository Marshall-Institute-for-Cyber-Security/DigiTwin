"""PLC_Generic — a permissive model that matches the pre-2b demo.

Wide I/O counts and memory so any address the examples use is valid. Use it
when a specific controller's limits don't matter.
"""

from __future__ import annotations

from digitwin.hardware import IEC_DOTTED, HardwareProfile
from digitwin.plc import PLC


class PLC_Generic(PLC):
    profile = HardwareProfile(
        vendor="DigiTwin",
        model="Generic",
        digital_inputs=512,
        digital_outputs=512,
        analog_inputs=64,
        analog_outputs=64,
        memory_bits=(0, 8191),
        memory_words=(0, 8191),
        retentive_bits=(0, 8191),
        retentive_words=(0, 8191),
        address_syntax=IEC_DOTTED,
        first_scan_bit="%S1",
        default_watchdog_ms=1000,
        min_scan_ms=1,
    )