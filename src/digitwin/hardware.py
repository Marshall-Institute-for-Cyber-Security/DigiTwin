"""Hardware profiles: what a given PLC model physically has.

A HardwareProfile is pure catalog data - I/O channel counts, addressable
memory ranges, the native address syntax, the system bits, and watchdog /
scan-time limits. It carries no behavior of its own. Concrete PLC subclasses
(digitwin/models/) each hold one; PLC.define_tag validates every
native_address against it and takes each tag's retentive default from the
retain ranges below.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, ClassVar, Protocol

from digitwin.plc import TagType


class AddressError(ValueError):
    """A native address is malformed, out of range for the hardware profile,
    or already claimed by another tag."""

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

# Address-syntax strategies, by the name a HardwareProfile (or a config file)
# uses to reference one. Only strategies implemented in code appear here; an
# inline profile in a project file may not name one that is missing.
ADDRESS_SYNTAXES: dict[str, AddressSyntax] = {IEC_DOTTED.name: IEC_DOTTED}


def address_syntax_by_name(name: str) -> AddressSyntax:
    """Resolve an ``address_syntax`` name to its strategy, or raise with the
    list of what is implemented."""
    try:
        return ADDRESS_SYNTAXES[name]
    except KeyError:
        known = ", ".join(sorted(ADDRESS_SYNTAXES))
        raise AddressError(
            f"unknown address_syntax {name!r}; implemented: {known}"
        ) from None


_AREA_FOR_TYPE: dict[TagType, set[AddressArea]] = {
    TagType.DISCRETE_INPUT: {AddressArea.DISCRETE_INPUT},
    TagType.DISCRETE_OUTPUT: {AddressArea.DISCRETE_OUTPUT},
    TagType.ANALOG_INPUT: {AddressArea.ANALOG_INPUT},
    TagType.ANALOG_OUTPUT: {AddressArea.ANALOG_OUTPUT},
    TagType.INTERNAL_BIT: {AddressArea.MEMORY_BIT, AddressArea.SYSTEM_BIT},
    TagType.WORD: {AddressArea.MEMORY_WORD, AddressArea.SYSTEM_WORD},
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
    # Inclusive %S / %SW ranges. Optional — a model that doesn't populate
    # these simply skips range-checking that area, same as before this field
    # existed; see the Phase 2b item in docs/TODO.md for why the TM221
    # leaves them unset rather than guessing a boundary.
    system_bits: tuple[int, int] | None = None
    system_words: tuple[int, int] | None = None
    address_syntax: AddressSyntax = IEC_DOTTED
    first_scan_bit: str = "%S13"
    always_on_bit: str | None = None
    always_off_bit: str | None = None
    scan_time_word: str | None = None
    default_watchdog_ms: int = 250
    # Catalog only: the executive does not yet clamp dt against this. See the
    # Phase 2b fidelity-knob item in docs/TODO.md.
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
        if self.system_bits is not None:
            ranges[AddressArea.SYSTEM_BIT] = self.system_bits
        if self.system_words is not None:
            ranges[AddressArea.SYSTEM_WORD] = self.system_words
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
        # SYSTEM_BIT / SYSTEM_WORD with no declared range (system_bits /
        # system_words left None): not checked, same as an unset memory range.

    # --- (de)serialization for config-file profiles ----------------------

    _TUPLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "memory_bits",
        "memory_words",
        "retentive_bits",
        "retentive_words",
        "system_bits",
        "system_words",
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> HardwareProfile:
        """Build a profile from a plain mapping (a project file's ``profile:``
        table). ``address_syntax`` is a strategy *name* resolved against
        :data:`ADDRESS_SYNTAXES`; the inclusive-range fields accept a
        ``[lo, hi]`` list. Unknown keys and missing required keys both raise
        :class:`AddressError`, so a typo fails at load, not mid-run."""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise AddressError(
                f"unknown profile field(s): {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known))}"
            )
        for required in ("vendor", "model", "digital_inputs", "digital_outputs"):
            if required not in data:
                raise AddressError(f"profile is missing required field {required!r}")

        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key == "address_syntax":
                kwargs[key] = address_syntax_by_name(str(value))
            elif key in cls._TUPLE_FIELDS and value is not None:
                pair = tuple(value)
                if len(pair) != 2:
                    raise AddressError(f"{key} must be a [lo, hi] pair, got {value!r}")
                kwargs[key] = pair
            else:
                kwargs[key] = value
        return cls(**kwargs)

    def to_mapping(self) -> dict[str, Any]:
        """The inverse of :meth:`from_mapping` — tuples become lists and the
        address syntax becomes its name, so the result is round-trippable
        through TOML/JSON."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "address_syntax":
                out[f.name] = value.name
            elif f.name in self._TUPLE_FIELDS and value is not None:
                out[f.name] = list(value)
            else:
                out[f.name] = value
        return out
