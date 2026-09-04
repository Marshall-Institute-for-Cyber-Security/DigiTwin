"""PLC_Generic — a permissive model that matches the pre-2b demo.

Wide I/O counts and memory so any address the examples use is valid. Use it
when a specific controller's limits don't matter.

It declares no retain ranges: an invented controller shouldn't imply where a
real one keeps its retentive memory, so tags on this model are non-retentive
unless ``define_tag(..., retentive=True)`` says otherwise. Concrete vendor
models set the real ranges and get retention for free.
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
        retentive_bits=None,
        retentive_words=None,
        address_syntax=IEC_DOTTED,
        first_scan_bit="%S1",
        always_on_bit="%S2",
        always_off_bit="%S3",
        scan_time_word="%SW0",
        default_watchdog_ms=1000,
        min_scan_ms=1,
    )