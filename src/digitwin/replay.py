"""Recorded-I/O replay: capture what the PLC saw, then re-run it with no plant.

A field incident is reproducible if you kept the inputs. :class:`RecordingTransport`
wraps any :class:`~digitwin.io.IOTransport` and stores the input image it hands
the PLC each scan (and the outputs the PLC produced). :class:`ReplayTransport`
feeds those frames straight back, so the same PLC and program can be re-run
against a :class:`~digitwin.plant.base.NullPlant` — no physics, no bus, just the
recorded stimulus.

Because both are transports, recording and replay are transport swaps: neither
the plant, the program, nor the executive changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from digitwin.io import IOBus, IOTransport, TransportError
from digitwin.plant.base import NullPlant
from digitwin.plc import PLC, TagType, TagValue

if TYPE_CHECKING:
    from digitwin.executive import Executive, ExecutiveMode

RECORDING_VERSION = 1

Frame = dict[str, TagValue]


@dataclass
class IORecording:
    """The per-scan I/O of a run: what came in, and what went out.

    ``inputs[i]`` is the input image of scan ``i``; ``outputs[i]`` is what that
    scan produced. Keeping both means a replay can be *checked*, not just
    re-run — see :func:`diff_outputs`.
    """

    dt: float = 0.1
    inputs: list[Frame] = field(default_factory=list)
    outputs: list[Frame] = field(default_factory=list)
    version: int = RECORDING_VERSION

    def __len__(self) -> int:
        return len(self.inputs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "dt": self.dt,
            "inputs": self.inputs,
            "outputs": self.outputs,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IORecording:
        version = int(data.get("version", RECORDING_VERSION))
        if version != RECORDING_VERSION:
            raise ValueError(
                f"recording version {version} != supported {RECORDING_VERSION}"
            )
        return cls(
            dt=float(data.get("dt", 0.1)),
            inputs=[dict(frame) for frame in data.get("inputs", [])],
            outputs=[dict(frame) for frame in data.get("outputs", [])],
            version=version,
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> IORecording:
        return cls.from_dict(json.loads(text))

    def save(self, path: Path | str, *, indent: int | None = None) -> None:
        Path(path).write_text(self.to_json(indent=indent), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> IORecording:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


@dataclass
class RecordingTransport:
    """Transport decorator that logs every frame passing the I/O boundary.

    Wrap the transport the executive already uses; behaviour is unchanged
    apart from the recording that accumulates.

    Pass ``plc`` to also capture its discrete inputs. Not every input
    necessarily arrives over the bus — a field-wired pushbutton, an HMI, or a
    test may drive an input tag directly — and a replay is only faithful if
    the recording holds everything the controller froze that scan, not just
    what this transport supplied.
    """

    transport: IOTransport
    recording: IORecording = field(default_factory=IORecording)
    plc: PLC | None = None

    def read_inputs(self) -> dict[str, TagValue]:
        frame: dict[str, TagValue] = {}
        if self.plc is not None:
            frame.update(
                {
                    name: tag.value
                    for name, tag in self.plc.tags.items()
                    if tag.tag_type is TagType.DISCRETE_INPUT
                }
            )
        # The transport wins for the tags it drives: it is about to set them.
        frame.update(self.transport.read_inputs())
        self.recording.inputs.append(dict(frame))
        return frame

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        self.recording.outputs.append(dict(outputs))
        self.transport.write_outputs(outputs)


@dataclass
class ReplayTransport:
    """Plays a recording back into a PLC, one frame per scan.

    Outputs the replayed PLC produces are collected in :attr:`outputs` for
    comparison with the original run. When the recording runs out,
    ``read_inputs`` raises :class:`~digitwin.io.TransportError` — the same
    failure a networked transport reports when it stops answering — unless
    ``hold_last`` says to keep repeating the final frame.
    """

    recording: IORecording
    hold_last: bool = False
    index: int = 0
    outputs: list[Frame] = field(default_factory=list, repr=False)

    @property
    def exhausted(self) -> bool:
        return self.index >= len(self.recording.inputs)

    def read_inputs(self) -> dict[str, TagValue]:
        frames = self.recording.inputs
        if self.exhausted:
            if not frames or not self.hold_last:
                raise TransportError(
                    f"recording exhausted after {len(frames)} frames"
                )
            return dict(frames[-1])
        frame = frames[self.index]
        self.index += 1
        return dict(frame)

    def write_outputs(self, outputs: dict[str, TagValue]) -> None:
        self.outputs.append(dict(outputs))

    def rewind(self) -> None:
        """Start the recording over, discarding replayed outputs."""
        self.index = 0
        self.outputs.clear()


def build_replay(
    plc: PLC,
    recording: IORecording,
    *,
    mode: ExecutiveMode | None = None,
) -> Executive:
    """An executive that drives ``plc`` from ``recording`` with no plant.

    Run it for ``len(recording)`` ticks to reproduce the recorded run:

        sim = build_replay(build_demo_plc(), recording)
        sim.run(len(recording))
    """
    # Imported here: the executive imports the observability modules, so a
    # module-scope import would be circular.
    from digitwin.executive import Executive, ExecutiveMode

    return Executive(
        plc,
        NullPlant(),
        IOBus(),
        ReplayTransport(recording),
        dt=recording.dt,
        mode=mode if mode is not None else ExecutiveMode.FREE_RUN,
    )


def diff_outputs(
    recorded: IORecording,
    replayed: list[Frame],
) -> list[tuple[int, str, TagValue | None, TagValue | None]]:
    """Compare a replay's outputs against the recorded ones.

    Returns ``(scan, tag, recorded value, replayed value)`` for every
    disagreement — empty means the replay reproduced the run bit for bit. This
    is the same comparison Phase 6's divergence detector will run against a
    real controller, on a recording instead of a live one.
    """
    differences: list[tuple[int, str, TagValue | None, TagValue | None]] = []
    for scan in range(max(len(recorded.outputs), len(replayed))):
        original = recorded.outputs[scan] if scan < len(recorded.outputs) else {}
        current = replayed[scan] if scan < len(replayed) else {}
        for tag in sorted(set(original) | set(current)):
            was, now = original.get(tag), current.get(tag)
            if was != now or type(was) is not type(now):
                differences.append((scan, tag, was, now))
    return differences
