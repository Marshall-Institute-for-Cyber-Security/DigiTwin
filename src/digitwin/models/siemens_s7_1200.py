"""PLC_Siemens_S7_1200_CPU1214C — Siemens SIMATIC S7-1200 CPU 1214C DC/DC/DC.

14 digital inputs (%I0.0..%I1.5, 24V DC), 10 transistor outputs
(%Q0.0..%Q1.1), 2 onboard analog inputs (%IW64, %IW66 — TIA Portal's
default address for this CPU with no signal boards/expansion modules
attached). 8192 bytes (65536 bits) of bit memory (%M0.0..%M8191.7,
%MW0..%MW8190).

Verified against Siemens' official CPU 1214C datasheet (part 6ES7214-1AG40-
0XB0) and the S7-1200 System Manual:

- digital_inputs=14, digital_outputs=10, analog_inputs=2, analog_outputs=0
  — confirmed, matches the DC/DC/DC variant's catalog data. (This CPU has
  no onboard analog output; 1215C/1217C add 2, not modeled here.)
- Onboard analog inputs at %IW64/%IW66 — confirmed as TIA Portal's default
  hardware-configuration address for this CPU's two channels with nothing
  else installed; see the SIEMENS syntax's own docstring for why that base
  is baked into the syntax rather than kept generic.
- Bit memory = 8192 bytes (%M0.0..%M8191.7); word memory %MW0..%MW8190 —
  confirmed.
- default_watchdog_ms=150, min_scan_ms=1 — confirmed ("Cycle time
  monitoring", default 150 ms, adjustable range 1-6000 ms in CPU
  properties).
- first_scan_bit="%M1.0", always_on_bit="%M1.2", always_off_bit="%M1.3" —
  NOT fixed hardware addresses the way the M221's %S13 is. These are
  ordinary memory bits inside whichever byte a TIA Portal project
  designates as the "System memory byte" in CPU properties; %MB1 is the
  overwhelmingly common convention (and TIA Portal's own suggested
  default) the first time that option is enabled, but a real project could
  pick any byte. Treat this as "the standard convention," not "the only
  possible address" — flagged here rather than silently presented as fixed
  hardware.
- scan_time_word=None — UNVERIFIED as a fixed address, because it isn't
  one. Unlike the M221's %SW30, the S7-1200 doesn't expose last-scan-time
  through a plain memory address; it's read via OB1's start-info temp
  variables or a system function, neither of which is a native_address
  this engine's model can represent. Left unset rather than inventing a
  plausible-looking word.
- retentive_bits=None, retentive_words=None — deliberately NOT modeled as
  a fixed range (contrast schneider_tm221.py, which does approximate one).
  Real S7-1200 retention is a per-project TIA Portal configuration (a
  "Retentive Memory" byte-count starting at %MB0, up to a combined 10 KB
  budget shared with data blocks, or per-tag Retain flags) — there's no
  fixed hardware range to point at the way the M221 happens to have one.
  A tag needing retention on this profile should pass `retentive=True`
  explicitly to `define_tag`.
- Clock memory (the self-toggling pulse byte, conventionally %MB0) is not
  modeled — no engine field for a self-toggling system bit exists yet; see
  docs/INTERPRETER_DESIGN.md's "system-bit providers" item for where that
  would eventually plug in.
"""

from __future__ import annotations

from digitwin.hardware import SIEMENS, HardwareProfile
from digitwin.plc import PLC


class PLC_Siemens_S7_1200_CPU1214C(PLC):
    profile = HardwareProfile(
        vendor="Siemens",
        model="S7-1200_CPU1214C",
        digital_inputs=14,
        digital_outputs=10,
        analog_inputs=2,
        analog_outputs=0,
        memory_bits=(0, 65535),
        memory_words=(0, 8190),
        retentive_bits=None,
        retentive_words=None,
        address_syntax=SIEMENS,
        first_scan_bit="%M1.0",
        always_on_bit="%M1.2",
        always_off_bit="%M1.3",
        scan_time_word=None,
        default_watchdog_ms=150,
        min_scan_ms=1,
    )
