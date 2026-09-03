"""Core soft-PLC engine: tags and the three-phase scan cycle."""

from __future__ import annotations

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


class Program(Protocol):
    """A scan-cycle program: called once per scan with the owning PLC."""

    def __call__(self, plc: PLC) -> None: ...


class PLC:
    """A minimal soft PLC.

    Runs a user program every scan using the classic three-phase cycle:
    freeze inputs, execute the program, then flush outputs.
    """

    def __init__(self, name: str, program: Program) -> None:
        self.name = name
        self.program = program
        self.tags: dict[str, Tag] = {}
        self.input_image: dict[str, TagValue] = {}
        self.output_image: dict[str, TagValue] = {}

    def define_tag(
        self,
        tag_name: str,
        tag_type: TagType,
        initial_value: TagValue = 0,
        native_address: str | None = None,
    ) -> Tag:
        if tag_name in self.tags:
            raise ValueError(f"Tag {tag_name!r} already exists")
        tag = Tag(tag_name, tag_type, initial_value, native_address)
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
        # Phase 1: input scan — freeze physical inputs
        self.input_image = {
            name: tag.value
            for name, tag in self.tags.items()
            if tag.tag_type == TagType.DISCRETE_INPUT
        }

        # Phase 2: program scan
        self.output_image = {}
        self.program(self)

        # Phase 3: output scan — push to physical outputs
        for name, value in self.output_image.items():
            self.tags[name].value = value
