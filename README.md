# DigiTwin

A small soft-PLC engine and simulation harness.

## Usage

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync            # create the venv and install dev tools
uv run digitwin    # run the demo simulation
```

With plain pip:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                             # editable install + dev tools
digitwin                                            # run the demo simulation
```

This is a standard PEP 621 project, so `pip install .` (or from a built wheel)
works too. `uv.lock` is uv-only; pip ignores it.

## Development

```bash
uv run pytest          # or: pytest
uv run ruff check .    # or: ruff check .
uv run mypy            # or: mypy
```

## Writing a program

A program is any callable taking a `PLC`. Read physical inputs from the frozen
image with `read_input`, stage physical outputs with `write_output`, and use
`read` / `write` for internal bits and words. See
`digitwin.programs.start_stop_tank` for a worked example.

The program contains **only control logic**. Physical behaviour lives in a
plant model (`digitwin.plant`) that the PLC reaches only through the I/O bus
(`digitwin.io`); the `Executive` steps the plant and the PLC together each
tick. `digitwin.demo` wires a tank plant to the start/stop program.
