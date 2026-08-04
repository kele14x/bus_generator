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

## Known Issues

### [HIGH] SystemRDL side-effect properties are not supported

The implementation handles basic reset, software writes, and hardware inputs,
but does not implement SystemRDL side-effect properties such as `onread`,
`onwrite`, write-one-to-clear, write-one-to-set, read-clear, or single-pulse
behavior. The write-once semantics of `sw=rw1` and `sw=w1` are also not
enforced for fields or memories.

Currently it:

- Generation continues with the existing behavior, but emits `WARNING` messages
  for fields or memories using unsupported `onread`, `onwrite`, `sw=rw1`, or
  `sw=w1` semantics.
- Warning messages include the elaborated component path and the ignored
  property.
- The side-effect semantics themselves remain unsupported and are intentionally
  deferred; generated RTL must not be treated as implementing them.

## Verification Notes

The simulator policy requires an explicit selection:

```text
SIM=icarus
SIM=verilator
SIM=questa
SIM=vsim
```

Unset or unavailable simulator selections fail immediately instead of silently
skipping simulator-marked tests. The external-memory overlapping-read timeout
was resolved by the edge-synchronous cocotb BFM refactor, and the full suite
passes under both `SIM=icarus` and `SIM=verilator`.
