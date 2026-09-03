"""Hardware profiles: what a given PLC model physically has.

A HardwareProfile is pure catalog data - I/O channel counts, addressable
memory ranges, the native address syntax, the first-scan system bit, and
watchdog / scan-time limits. It carries no behvior. Concrete PLC subclasses
(digitwin/model/) each hold one, and PLC.define_tag validates every
native_addresses against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Protocol

from digitwin.plc import TagType


class AddressError(ValueError):
    """A native address is malformed or out of range for the harware profile."""

class AddressArea(Enum):
    DISCRETE_INPUT = "discrete input"   # %I
    DISCRETE_OUTPUT = "discrete output" # %Q
    ANALOG_INPUT = "analog input"       # %IW
    ANALOG_OUTPUT = "analog output"     # %QW
    MEMORY_BIT = "memory bit"           # %M
    MEMORY_WORD = "memory word"         # %MW
    SYSTEM_BIT = "system bit"           # %S
    SYSTEM_WORD = "system word"         # %SW

@dataclass(frozen=True)
class ParsedAddress:
    """A native address string broken into an area and a linear index."""

    area: AddressArea
    index: int
    raw: str

class AddressSyntax(Protocol):
    name: str
    def parse(self, address: str) -> ParsedAddress: ...

    def format(self, parsed: ParsedAddress) -> str: ...

class _IecDotted:
    """Schneider M221 style: %I0.3 %Q0.1 %IW0.0 %M12 %MW7 %S13 %SW4
    
    Embedded I/O only (module 0). The number after the dot is the channel
    index; %M / %MW / %S / %SW take a flat index.
    """

    name = "IEC_DOTTED"

    _PATTERNS: ClassVar[list[tuple[re.Pattern[str], AddressArea]]] = [
        (re.compile(r"%I0\.(\d+)$"), AddressArea.DISCRETE_INPUT),
        (re.compile(r"%Q0\.(\d+)$"), AddressArea.DISCRETE_OUTPUT),
        (re.compile(r"%IW0\.(\d+)$"), AddressArea.ANALOG_INPUT),
        (re.compile(r"%QW0\.(\d+)$"), AddressArea.ANALOG_OUTPUT),
        (re.compile(r"%M(\d+)$"), AddressArea.MEMORY_BIT),
        (re.compile(r"%MW(\d+)$"), AddressArea.MEMORY_WORD),
        (re.compile(r"%S(\d+)$"), AddressArea.SYSTEM_BIT),
        (re.compile(r"%SW(\d+)$"), AddressArea.SYSTEM_WORD),
    ]

    _FORMATS: ClassVar[dict[AddressArea, str]] = {
        AddressArea.DISCRETE_INPUT: "%I0.{i}",
        AddressArea.DISCRETE_OUTPUT: "%Q0.{i}",
        AddressArea.ANALOG_INPUT: "%IW0.{i}",
        AddressArea.ANALOG_OUTPUT: "%QW0.{i}",
        AddressArea.MEMORY_BIT: "%M{i}",
        AddressArea.MEMORY_WORD: "%MW{i}",
        AddressArea.SYSTEM_BIT: "%S{i}",
        AddressArea.SYSTEM_WORD: "%SW{i}",
    }

    def parse(self, address: str) -> ParsedAddress:
        for pattern, area in self._PATTERNS:
            match = pattern.fullmatch(address)
            if match is not None:
                return ParsedAddress(area=area, index=int(match.group(1)), raw=address)
        raise AddressError(f"{address!r} is not valid IEC_DOTTED syntax")

    def format(self, parsed: ParsedAddress) -> str:
        return self._FORMATS[parsed.area].format(i=parsed.index)

IEC_DOTTED: AddressSyntax = _IecDotted()

_AREA_FOR_TYPE: dict[TagType, set[AddressArea]] = {
    TagType.DISCRETE_INPUT: {AddressArea.DISCRETE_INPUT},
    TagType.DISCRETE_OUTPUT: {AddressArea.DISCRETE_OUTPUT},
    TagType.INTERNAL_BIT: {AddressArea.MEMORY_BIT, AddressArea.SYSTEM_BIT},
    TagType.WORD: {
        AddressArea.MEMORY_WORD,
        AddressArea.ANALOG_INPUT,
        AddressArea.ANALOG_OUTPUT,
        AddressArea.SYSTEM_WORD,
    },
}

@dataclass(frozen=True)
class HardwareProfile:
    vendor: str
    model: str
    digital_inputs: int
    digital_outputs: int
    analog_inputs: int = 0
    analog_outputs: int = 0
    memory_bits: tuple[int, int] = (0, 0)      # inclusive %M range
    memory_words: tuple[int, int] = (0, 0)     # inclusive %MW range
    retentive_bits: tuple[int, int] | None = None
    retentive_words: tuple[int, int] | None = None
    address_syntax: AddressSyntax = IEC_DOTTED
    first_scan_bit: str = "%S13"
    default_watchdog_ms: int = 250
    min_scan_ms: int = 1

    def validate_address(self, address: str, tag_type: TagType) -> ParsedAddress:
        """Parse `address`, confirm its area suits `tag_type`, and range-check
        the index. Raises AddressError on any failure; returns the ParsedAddress."""
        parsed = self.address_syntax.parse(address)
        if parsed.area not in _AREA_FOR_TYPE[tag_type]:
            raise AddressError(
                f"{address!r} ({parsed.area.value}) can't address a "
                f"{tag_type.value} tag"
            )
        self._range_check(parsed)
        return parsed

    def is_retentive(self, address: str) -> bool:
        """Whether `address` falls in the model's retain range (bits or words)."""
        parsed = self.address_syntax.parse(address)
        if parsed.area is AddressArea.MEMORY_BIT and self.retentive_bits is not None:
            lo, hi = self.retentive_bits
            return lo <= parsed.index <= hi
        if parsed.area is AddressArea.MEMORY_WORD and self.retentive_words is not None:
            lo, hi = self.retentive_words
            return lo <= parsed.index <= hi
        return False

    def _range_check(self, parsed: ParsedAddress) -> None:
        counts = {
            AddressArea.DISCRETE_INPUT: self.digital_inputs,
            AddressArea.DISCRETE_OUTPUT: self.digital_outputs,
            AddressArea.ANALOG_INPUT: self.analog_inputs,
            AddressArea.ANALOG_OUTPUT: self.analog_outputs,
        }
        ranges = {
            AddressArea.MEMORY_BIT: self.memory_bits,
            AddressArea.MEMORY_WORD: self.memory_words,
        }
        if parsed.area in counts:
            limit = counts[parsed.area]
            if not 0 <= parsed.index < limit:
                raise AddressError(
                    f"{parsed.raw!r} is past the {parsed.area.value} count ({limit})"
                )
        elif parsed.area in ranges:
            lo, hi = ranges[parsed.area]
            if not lo <= parsed.index <= hi:
                raise AddressError(
                    f"{parsed.raw!r} is outside {parsed.area.value}s %{lo}..%{hi}"
                )
        # SYSTEM_BIT / SYSTEM_WORD: not range-checked in the minimal cut
