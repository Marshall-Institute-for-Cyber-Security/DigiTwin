"""PLC_Schneider_TM221CE16T — Schneider Electric Modicon M221, 16 I/O transistor.

9 digital inputs (%I0.0..%I0.8), 7 transistor outputs (%Q0.0..%Q0.6),
2 analog inputs (%IW0.0..%IW0.1). Memory %M0..%M511, %MW0..%MW7999.
"""

from __future__ import annotations

from digitwin.hardware import IEC_DOTTED, HardwareProfile
from digitwin.plc import PLC


class PLC_Schneider_TM221CE16T(PLC):
    profile = HardwareProfile(
        vendor="Schneider Electric",
        model="TM221CE16T",
        digital_inputs=9,
        digital_outputs=7,
        analog_inputs=2,
        analog_outputs=0,
        memory_bits=(0, 511),
        memory_words=(0, 7999),
        retentive_bits=(0, 511),
        retentive_words=(0, 1999),
        address_syntax=IEC_DOTTED,
        first_scan_bit="%S13",
        default_watchdog_ms=250,
        min_scan_ms=1,
    )