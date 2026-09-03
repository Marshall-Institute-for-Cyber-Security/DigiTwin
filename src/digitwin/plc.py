"""Core soft-PLC engine: tags, the three-phase scan cycle, and firmware realism.

The scan cycle (freeze inputs -> run program -> flush outputs) is unchanged
from Phase 1. Phase 2 adds firmware-level behaviour around it: a first-scan
bit, retentive vs non-retentive tags, cold/warm/power-cycle restarts, and a
program-scan watchdog.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TagType(Enum):
    DISCRETE_INPUT = "discrete_input"
    DISCRETE_OUTPUT = "discrete_output"
    INTERNAL_BIT = "internal_bit"
    WORD = "word"


TagValue = int | bool


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


class PLC:
    """A minimal soft PLC.

    Runs a user program every scan using the classic three-phase cycle:
    freeze inputs, execute the program, then flush outputs.
    """

    def __init__(
        self,
        name: str,
        program: Program,
        *,
        watchdog_s: float | None = None,
    ) -> None:
        self.name = name
        self.program = program
        self.watchdog_s = watchdog_s
        self.tags: dict[str, Tag] = {}
        self.input_image: dict[str, TagValue] = {}
        self.output_image: dict[str, TagValue] = {}
        self.scan_count = 0
        self.first_scan = False
        self.last_scan_duration = 0.0
        self.watchdog_tripped = False

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
