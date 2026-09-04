"""Core soft-PLC engine: tags, the three-phase scan cycle, and firmware realism.

The scan cycle (freeze inputs -> run program -> flush outputs) is unchanged
from Phase 1. Phase 2 added firmware behaviour around it: a first-scan bit,
retentive vs non-retentive tags, cold/warm/power-cycle restarts, and a
program-scan watchdog. Phase 2b makes ``PLC`` abstract — a concrete model
subclass carries a ``HardwareProfile`` and every ``native_address`` is
validated against it.
"""

from __future__ import annotations

import time
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from digitwin.hardware import HardwareProfile


class TagType(Enum):
    DISCRETE_INPUT = "discrete_input"
    DISCRETE_OUTPUT = "discrete_output"
    INTERNAL_BIT = "internal_bit"
    WORD = "word"


TagValue = int | bool

# System tags PLC auto-populates from its HardwareProfile (see
# PLC._define_system_tags). Programs read them like any other tag, e.g.
# plc.read(FIRST_SCAN_TAG).
FIRST_SCAN_TAG = "first_scan"
ALWAYS_ON_TAG = "always_on"
ALWAYS_OFF_TAG = "always_off"
SCAN_TIME_MS_TAG = "scan_time_ms"


@dataclass
class Tag:
    name: str
    tag_type: TagType
    value: TagValue = 0
    native_address: str | None = None
    initial_value: TagValue = 0
    retentive: bool = False


class Program(Protocol):
    """A scan-cycle program: called once per scan with the owning PLC."""

    def __call__(self, plc: PLC) -> None: ...


class PLC(ABC):
    """Abstract soft-PLC engine: the three-phase scan cycle plus firmware
    behaviour (first-scan bit, retentive tags, restarts, watchdog).

    Concrete controllers subclass this and set a class-level ``profile``
    (see ``digitwin.models``). ``PLC`` itself will not instantiate — there is
    no hardware to validate native addresses against.
    """

    profile: ClassVar[HardwareProfile]

    def __init__(
        self,
        name: str,
        program: Program,
        *,
        watchdog_s: float | None = None,
    ) -> None:
        if not hasattr(type(self), "profile"):
            raise TypeError(
                f"{type(self).__name__} has no hardware profile; instantiate a "
                "concrete PLC subclass from digitwin.models"
            )
        self.name = name
        self.program = program
        self.watchdog_s = (
            watchdog_s
            if watchdog_s is not None
            else self.profile.default_watchdog_ms / 1000
        )
        self.tags: dict[str, Tag] = {}
        self.input_image: dict[str, TagValue] = {}
        self.output_image: dict[str, TagValue] = {}
        self.scan_count = 0
        self.first_scan = False
        self.last_scan_duration = 0.0
        self.watchdog_tripped = False
        self._define_system_tags()

    def _define_system_tags(self) -> None:
        """Auto-populate the system tags the hardware profile declares
        addresses for: first-scan bit, always-on/off bits, scan-time word.
        A profile that leaves one unset (None) simply doesn't get that tag."""
        p = self.profile
        if p.first_scan_bit is not None:
            self.define_tag(FIRST_SCAN_TAG, TagType.INTERNAL_BIT, False, p.first_scan_bit)
        if p.always_on_bit is not None:
            self.define_tag(ALWAYS_ON_TAG, TagType.INTERNAL_BIT, True, p.always_on_bit)
        if p.always_off_bit is not None:
            self.define_tag(ALWAYS_OFF_TAG, TagType.INTERNAL_BIT, False, p.always_off_bit)
        if p.scan_time_word is not None:
            self.define_tag(SCAN_TIME_MS_TAG, TagType.WORD, 0, p.scan_time_word)

    def define_tag(
        self,
        tag_name: str,
        tag_type: TagType,
        initial_value: TagValue = 0,
        native_address: str | None = None,
        *,
        retentive: bool = False,
    ) -> Tag:
        if tag_name in self.tags:
            raise ValueError(f"Tag {tag_name!r} already exists")
        if native_address is not None:
            self.profile.validate_address(native_address, tag_type)
        tag = Tag(
            tag_name,
            tag_type,
            value=initial_value,
            native_address=native_address,
            initial_value=initial_value,
            retentive=retentive,
        )
        self.tags[tag_name] = tag
        return tag

    def read(self, tag_name: str) -> TagValue:
        """Live read — for internal bits and words, not physical inputs."""
        return self.tags[tag_name].value

    def write(self, tag_name: str, value: TagValue) -> None:
        """Live write — for internal bits and words, not physical outputs."""
        self.tags[tag_name].value = value

    def read_input(self, tag_name: str) -> TagValue:
        """Programs read physical inputs from the frozen image, not live."""
        return self.input_image[tag_name]

    def write_output(self, tag_name: str, value: TagValue) -> None:
        """Programs stage physical outputs here; flushed at end of scan."""
        self.output_image[tag_name] = value

    def scan(self) -> None:
        self.first_scan = self.scan_count == 0
        if FIRST_SCAN_TAG in self.tags:
            self.tags[FIRST_SCAN_TAG].value = self.first_scan
        if ALWAYS_ON_TAG in self.tags:
            self.tags[ALWAYS_ON_TAG].value = True
        if ALWAYS_OFF_TAG in self.tags:
            self.tags[ALWAYS_OFF_TAG].value = False

        # Phase 1: input scan — freeze physical inputs
        self.input_image = {
            name: tag.value
            for name, tag in self.tags.items()
            if tag.tag_type == TagType.DISCRETE_INPUT
        }

        # Phase 2: program scan — timed for the watchdog
        self.output_image = {}
        started = time.perf_counter()
        self.program(self)
        self.last_scan_duration = time.perf_counter() - started
        if self.watchdog_s is not None and self.last_scan_duration > self.watchdog_s:
            self.watchdog_tripped = True

        # Phase 3: output scan — push to physical outputs
        for name, value in self.output_image.items():
            self.tags[name].value = value

        if SCAN_TIME_MS_TAG in self.tags:
            self.tags[SCAN_TIME_MS_TAG].value = int(self.last_scan_duration * 1000)

        self.scan_count += 1

    def cold_start(self) -> None:
        """Power-on start: non-retentive tags return to their initial value,
        retentive tags keep whatever they held. The scan cycle restarts, so the
        first-scan bit fires again."""
        for tag in self.tags.values():
            if not tag.retentive:
                tag.value = tag.initial_value
        self._restart()

    def warm_start(self) -> None:
        """Resume with every tag value retained; only the scan cycle restarts."""
        self._restart()

    def power_cycle(self) -> None:
        """Simulate a power off/on — identical to a cold start."""
        self.cold_start()

    def _restart(self) -> None:
        self.scan_count = 0
        self.first_scan = False
        self.watchdog_tripped = False
        self.input_image = {}
        self.output_image = {}
