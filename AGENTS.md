# AGENTS.md

This file provides guidance to the AI agent when working with code in this repository.

## What this is

A CLI that generates a Verilog AXI4-Lite CSR register block (and C header, testbench) from a SystemRDL source file. It walks the compiled RDL model with `RDLListener`/`RDLWalker`, gathers fields/regs/mems into dicts, then renders Jinja2 templates.

## Commands

- Run: `uv run bus-generator <input.rdl> -o <output_dir> [-t <template_name>]`
- Test: `uv run pytest`
- Templates are selected by friendly alias via `-t`; default is `axi4l`. Available aliases: `axi4l`, `c_header`, `tb_axi4l` (e.g. `-t axi4l c_header`).

## Non-obvious details

- Templates live in `src/bus_generator/templates/` and are named with literal `{{...}}` braces (e.g. `{{axi4l}}_regs.v.jinja2`). The `{{...}}` in the output filename is regex-replaced with the RDL top instance name at generation time — it is NOT a Jinja placeholder in the filename. The `-t` aliases are auto-discovered by `discover_templates()`: the alias is the prefix before `{{...}}` plus the label inside the braces (so `tb_{{axi4l}}_regs.v.jinja2` -> `tb_axi4l`).
- Bus geometry is fixed by the `DATA_WIDTH` (32) and `ADDR_WIDTH_LSB` (2) module constants in `bus_generator.py`. Generated Verilog assumes a 32-bit AXI4-Lite bus.
- `__version__` comes from `git describe --tags`; falls back to `"unknown"` with no tags. Don't rely on it in tests.
- Cocotb dev-dependency is used for simulating generated Verilog; `sim_build/` is gitignored.

## Entry point

`bus_generator.__init__:main(argv=None)` is the console-script entry (see `[project.scripts]`); it forwards to `cli(argv)`. Tests import `from bus_generator import main` and call `main([...])`.

## TODO

- [P1] Support SystemRDL side-effect semantics (formerly KI-06), including
  `onread`, `onwrite`, write-one-to-clear/set, read-clear, `singlepulse`, and
  write-once access (`sw=rw1` and `sw=w1`) for fields and memories where applicable.
  Add RTL implementation and regression tests for these behaviors. Until
  implemented, generation continues with `WARNING` messages identifying the
  affected component path and unsupported property (`onread`, `onwrite`,
  `singlepulse`, `sw=rw1`, or `sw=w1`); quiet mode suppresses these warnings.
  Successful generation does not mean side effects are implemented; do not rely
  on them in generated hardware.
- [P2] Support registers spanning multiple aligned 32-bit AXI words, including narrow
  registers that straddle a word boundary (e.g. 16 bits at `0x3`) and registers
  wider than 32 bits (e.g. `regwidth=64; accesswidth=32`). Preserve logical fields
  and hardware ports while adding per-word read slices, byte-lane write masks,
  and regression tests. Until implemented, the CLI and `convert()` reject these
  layouts before rendering, even when only low register bits contain fields.
  This is a generator limitation, not invalid SystemRDL. Packed narrow registers
  contained within one word use byte-lane mapping; C field addresses, masks, and
  offsets describe aligned 32-bit MMIO words.
- [P2] Consider supporting 16-bit and 8-bit memory entries, and entry widths
  that are multiples of 32 bits. Require memory bases to be 16-bit (2-byte),
  8-bit (1-byte), and 32-bit (4-byte) aligned, respectively. Until implemented,
  the CLI and `convert()` reject memories unless `memwidth=32` and the absolute
  base address is a multiple of 4 bytes.

## Verification Notes

The simulator policy requires an explicit selection:

```text
SIM=icarus
SIM=verilator
SIM=questa
```

Tests read `SIM` directly from the environment without a default or aliases;
cocotb or subprocess execution reports missing executables. The external-memory
overlapping-read timeout was resolved by the edge-synchronous cocotb BFM refactor,
and the full suite passes under both `SIM=icarus` and `SIM=verilator`.
