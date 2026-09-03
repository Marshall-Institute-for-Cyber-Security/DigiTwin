from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass

class TagType(Enum):
    DISCRETE_INPUT = "discrete_input"
    DISCRETE_OUTPUT = "discrete_output"
    INTERNAL_BIT = "internal_bit"
    WORD = "word"

@dataclass
class Tag:
    name: str
    tag_type: TagType
    value: int | bool = 0
    native_address: str | None = None

class PLC(ABC):
    def __init__(self, name: str, program):
        self.name = name
        self.tags: dict[str, Tag] = {}
        self.program = program
        self.input_image: dict[str, object] = {}
        self.output_image: dict[str, object] = {}

    def define_tag(self, tag_name: str, tag_type: TagType, initial_value=0, native_address=None):
        if tag_name in self.tags:
            raise ValueError(f"Tag '{tag_name}', already exists")
        self.tags[tag_name] = Tag(tag_name, tag_type, initial_value, native_address)

    def read(self, tag_name):
        """Live reading for internal bits and words"""
        return self.tags[tag_name].value

    def write(self, tag_name, value):
        """Live write for internal bits and words"""
        self.tags[tag_name].value = value

    def read_input(self, tag_name):
        """Program read from physical inputs from the frozen image"""
        return self.input_image[tag_name]

    def write_input(self, tag_name, value):
            """Program writes from physical outputs to the image; pushed out at the end of scan"""
            self.output_image[tag_name] = value

    def scan(self):
        # Phase 1: input scan - freeze physical inputs
        self.input_image = {
            name: tag.value for name, tag in self.tags.items()
            if tag.tag_type == TagType.DISCRETE_INPUT
        }

        # Phase 2: program scan
        self.output_image = {}
        self.program(self)

        # Phase 3: output scan, push to physical outputs
        for name, value in self.output_image.items():
            self.tags[name].value = value
