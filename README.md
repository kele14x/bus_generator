# Bus Generator

**Bus Generator** is a script to generate a Verilog AXI slave CSR (Control & Status Register) block from [SystemRDL](https://www.accellera.org/downloads/standards/systemrdl) source.

## Dependency

Python 3.14 and [uv](https://docs.astral.sh/uv/). Runtime and dev dependencies are declared in `pyproject.toml`.

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

By default the AXI4-Lite register block template (`axi4l`) is rendered. Select
one or more templates by alias with `-t`. Available aliases: `axi4l`,
`c_header`, `tb_axi4l`. Sample RDL files are in `samples/`. For example:

```bash
uv run bus-generator samples/gpio.rdl -o out -t axi4l c_header
```

## External RAM interface contract

Each generated RAM interface uses the following contract on `s_axi_aclk`:

1. Each asserted `ram_en` at a rising clock edge represents an accepted request.
2. Every request, including writes, produces exactly one response.
3. Responses arrive in request order, at most one per clock.
4. `ram_valid` qualifies `ram_dout`; data need not remain valid afterward.
5. Consecutive cycles with `ram_valid=1` represent consecutive responses.
6. Zero latency is supported, including combinational `ram_valid = ram_en`.
7. Reset flushes pending responses in both the adapter and external RAM; the
   external RAM must not return pre-reset responses after reset.

Here `ram_` stands for the generated memory instance's signal prefix. Physical
RAM writes are acknowledged on AXI only after their RAM response, not at issue.

RAM entry indices are derived from byte addresses relative to each memory's base.
For minimal address logic, align the base to the next power of two of the memory
size in bytes: three or four 32-bit entries both use a 16-byte alignment window.
This allows synthesis to eliminate address subtraction. Other base alignments
remain supported, with an advisory warning that can be suppressed with `-q`.

## Testing

```bash
uv run pytest
```

Simulator-marked tests read ``SIM`` directly from the environment without a
default. Use ``icarus``, ``verilator``, or ``questa``; the selected simulator
must be installed. Cocotb or subprocess execution reports missing executables.
For example:

```bash
SIM=icarus uv run pytest -m sim
```

The full ``uv run pytest`` suite includes simulator-marked tests, so it also
requires ``SIM``. Use ``uv run pytest -m "not sim"`` for tests that do not need a
simulator.
