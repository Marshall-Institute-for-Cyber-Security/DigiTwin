"""PLC_Schneider_TM221CE16T — Schneider Electric Modicon M221, 16 I/O transistor.

9 digital inputs (%I0.0..%I0.8), 7 transistor outputs (%Q0.0..%Q0.6),
2 analog inputs (%IW0.0..%IW0.1). Memory %M0..%M511, %MW0..%MW7999.

Verified against Schneider's Modicon M221 Logic Controller Programming Guide
(EIO0000003297.04), "System Objects" chapter, System Bits / System Words
description tables:

- first_scan_bit="%S13" ("First cycle in RUNNING state" — set for exactly one
  scan after STOP->RUN) — confirmed correct.
- scan_time_word="%SW30" ("Last scan time", ms) — corrected; was "%SW10",
  which the reference doesn't define for this purpose.
- always_on_bit / always_off_bit — removed. The M221's full system-bit table
  (%S0..%S123) has no constant-TRUE or constant-FALSE bit; %S20 and %S21
  (previously used as placeholders) are real, unrelated functions — "Index
  overflow" and "Grafcet initialization" respectively. A model with no such
  bit simply doesn't get an always_on/always_off tag (see PLC._define_system_tags).

Still a simplification: retentive_words=(0, 1999) models retention as an
address range, the way this engine's retentive mechanism works. The real M221
doesn't retain %MW automatically at all — retention is an explicit,
program-triggered backup/restore of a caller-chosen word count (system bits
%S90/%S93/%S94 + count in %SW148), not a fixed hardware range. This range is
therefore a deliberate approximation, not a verified catalog fact; modeling
the real explicit-backup mechanism is future work, not a quick number fix.
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
        retentive_words=(0, 1999),  # approximation; see module docstring
        address_syntax=IEC_DOTTED,
        first_scan_bit="%S13",  # "First cycle in RUNNING state" — confirmed
        always_on_bit=None,     # no such bit on real M221 hardware
        always_off_bit=None,    # no such bit on real M221 hardware
        scan_time_word="%SW30",  # "Last scan time" (ms) — confirmed
        default_watchdog_ms=250,
        min_scan_ms=1,
    )