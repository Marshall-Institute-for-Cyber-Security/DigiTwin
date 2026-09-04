"""PLC_Schneider_TM221CE16T — Schneider Electric Modicon M221, 16 I/O transistor.

9 digital inputs (%I0.0..%I0.8), 7 transistor outputs (%Q0.0..%Q0.6),
2 analog inputs (%IW0.0..%IW0.1). Memory %M0..%M511, %MW0..%MW7999.

UNVERIFIED against the M221 system-object reference: always_on_bit,
always_off_bit, scan_time_word, and retentive_words. %S13 (first cycle) and
the I/O counts are from the catalog and are trusted. The others were filled in
to exercise the profile mechanism and may not be the addresses the real
controller uses — tests/test_hardware.py now asserts them, so correct the
profile and those assertions together. See docs/TODO.md, Phase 2b.
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
        retentive_words=(0, 1999),  # UNVERIFIED; M221 retain range is configurable
        address_syntax=IEC_DOTTED,
        first_scan_bit="%S13",       # first cycle after RUN — from the reference
        always_on_bit="%S20",        # UNVERIFIED (see module docstring)
        always_off_bit="%S21",       # UNVERIFIED
        scan_time_word="%SW10",      # UNVERIFIED
        default_watchdog_ms=250,
        min_scan_ms=1,
    )