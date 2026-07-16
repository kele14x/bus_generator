# Bus Generator

**Bus Generator** is a script to generate a Verilog AXI slave CSR (Control & Status Register) block from [SystemRDL](https://www.accellera.org/downloads/standards/systemrdl) source.

## Dependency

Python 3.13 and [uv](https://docs.astral.sh/uv/). Runtime and dev dependencies are declared in `pyproject.toml`.

## Installation

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).

2. Sync the environment (creates `.venv` and installs everything):

    ```bash
    uv sync
    ```

## Usage

```bash
uv run bus-generator <input_files> -o <output_dir>
```

By default the AXI4-Lite register block template is rendered. Select other
templates with `-t` (omit the `.jinja2` suffix), e.g.:

```bash
uv run bus-generator gpio.rdl -o out -t {{axi4l}}_regs.v {{c_header}}.h
```

## Testing

```bash
uv run pytest
```
