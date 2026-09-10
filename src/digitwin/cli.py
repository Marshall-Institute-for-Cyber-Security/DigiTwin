"""Command-line entry point.

    digitwin run PROJECT.toml [--ticks N | --seconds S]

Loads a twin from a project file (see ``docs/BUILDING_A_TWIN.md``), scans it,
and prints a short summary of what the observers saw.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from digitwin.config import load_project


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="digitwin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="load a project file and scan it")
    run.add_argument("project", type=Path, help="path to a .toml project file")
    horizon = run.add_mutually_exclusive_group()
    horizon.add_argument("--ticks", type=int, help="number of scans to run (default 100)")
    horizon.add_argument("--seconds", type=float, help="seconds of simulated time to run")

    args = parser.parse_args(argv)
    if args.command == "run":
        _run(args.project, ticks=args.ticks, seconds=args.seconds)


def _run(project: Path, *, ticks: int | None, seconds: float | None) -> None:
    sim = load_project(project)
    if seconds is not None:
        sim.run_for(seconds)
    else:
        sim.run(ticks if ticks is not None else 100)

    print(f"{project.name}: {sim.scan_count} scans, t={sim.elapsed:.1f}s simulated")
    if sim.historian is not None:
        print(
            f"  historian: {len(sim.historian)} samples across "
            f"{len(sim.historian.tag_names())} tags"
        )
    if sim.events is not None and len(sim.events):
        print(f"  events: {len(sim.events)}")
        for event in sim.events:
            print(f"    t={event.timestamp:7.1f}s  {event.category.value:<9} {event.message}")


if __name__ == "__main__":
    main()
