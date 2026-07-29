# Code Review Findings

Review date: 2026-07-29

Review method:

- OpenAI Codex CLI read-only review of the repository.
- Manual source/template inspection.
- `uv run pytest -q`: 27 passed, 15 skipped.
- `make gen`: generation checks passed (9 tests).
- Questa Altera Starter FPGA Edition / VSIM smoke simulation:
  - `gpio`: `TEST PASSED`
  - `ram`: `TEST PASSED`
  - `simple`: `TEST PASSED`
- A minimal one-word address-map sample was compiled with Questa and produced an actual invalid part-select error.

No production code was modified during this review. The Git worktree was clean after review.

## Priority Summary

| Priority | Count | Main areas |
|---|---:|---|
| High | 2 | Field access permissions, SystemRDL side effects |
| Medium | 4 | Memory bounds, invalid widths, version lookup, simulation coverage |
| Low | 2 | Silent no-output invocation, nested output directories |

## Findings

### [HIGH] Field software access permissions are not fully enforced

Location:

- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:386`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:402`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:528`

Register readback iterates over all fields instead of filtering by
`is_sw_readable`. Read and write response decoding also treats every field
address as valid without checking the direction-specific software permission.

Impact:

- Software may read fields declared write-only.
- Writes to read-only fields and reads from write-only fields may report success.

Recommended fix:

- Include only `is_sw_readable` fields in readback and read-valid decode.
- Include only `is_sw_writable` fields in write-valid decode.
- Return a defined AXI error response for unsupported accesses.
- Add field fixtures for `sw = r`, `sw = w`, and `sw = na`.

Status: Open

### [HIGH] SystemRDL side-effect properties are silently ignored

Location:

- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:431`

The implementation handles basic reset, software writes, and hardware inputs,
but does not implement SystemRDL side-effect properties such as `onread`,
`onwrite`, write-one-to-clear, write-one-to-set, read-clear, or single-pulse
behavior. The write-once semantics of `sw=rw1` and `sw=w1` are also not
enforced for fields or memories.

Impact:

Valid SystemRDL inputs can generate RTL whose CSR semantics differ materially from the source description. Interrupt/status registers are especially vulnerable.

Recommended fix:

- Implement the supported side-effect subset explicitly, or
- Validate the elaborated RDL model and fail generation with a clear unsupported-feature error.
- Document the supported SystemRDL subset.

Status: Open

### [MEDIUM] Memory decode rounds regions up to a power of two

Location:

- `src/bus_generator/src/bus_generator/bus_generator.py:174`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:367`

Memory address decoding uses a power-of-two rounded region. In `tests/ram.rdl`, each memory has 14 32-bit entries (56 bytes), but the generated decoder accepts a 64-byte range.

Valid addresses for `ram0` are `0x100` through `0x134`, but the generated decode also accepts `0x138` and `0x13c`, which correspond to entries outside the declared range.

Impact:

Out-of-range accesses can reach invalid external-memory indices instead of returning an error.

Recommended fix:

- Decode the exact range `[base, base + size)`.
- Assert memory enable only for valid entry indices.
- Add tests for the first invalid word after every memory region.

Status: Open

### [MEDIUM] Small address maps can generate invalid Verilog

Location:

- `src/bus_generator/bus_generator.py:297`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:360`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:436`

A minimal address map containing one 32-bit word generated the part-select:

```verilog
int_addr[1:2]
```

where `int_addr` is `[1:0]`. Questa reported:

```text
Error: Range of part-select [1:2] into 'int_addr' [1:0] is reversed.
Warning: LSB 2 of part-select into 'int_addr' is out of bounds.
```

The generator also accepts fields wider than the fixed 32-bit bus and can emit out-of-range expressions such as `int_wr_data[63:0]`.

Impact:

Some valid or insufficiently validated SystemRDL inputs produce non-compilable or non-functional RTL.

Recommended fix:

- Handle the minimum address width explicitly.
- Ensure every generated vector and part-select has valid bounds.
- Reject fields/memories wider than the supported 32-bit bus unless multiword access is implemented.
- Add boundary fixtures for one-word maps, non-power-of-two maps, and oversized fields.

Status: Confirmed by Questa compilation

### [MEDIUM] Distribution version and runtime version are inconsistent

Location:

- `pyproject.toml:3`
- `src/bus_generator/bus_generator.py:27`

The distribution declares version `0.1.0`, while the CLI currently reports a Git-derived version similar to:

```text
v0.2.0-23-gb3df15f
```

The runtime version lookup also executes Git during module import. If Git is unavailable, importing the package raises an uncaught `FileNotFoundError`.

Impact:

- Installed artifacts can report a different version from the CLI.
- The package cannot start on systems without Git.

Recommended fix:

- Use `importlib.metadata.version("bus-generator")` for installed packages.
- Align package metadata and release tags.
- Keep Git-derived version lookup as an optional development fallback, not an import-time requirement.

Status: Open

### [MEDIUM] Default pytest command does not guarantee RTL simulation

Location:

- `README.md:35`
- `tests/test_simulation.py:107`
- `tests/test_stress.py:695`

`uv run pytest` can report success while simulation tests are skipped. In the reviewed environment, 27 tests passed and 15 were skipped because Icarus/Verilator were unavailable. The project test wrappers do not currently support the installed Questa/VSIM backend.

Impact:

A green default test run does not prove that generated RTL was compiled or simulated.

Recommended fix:

- Add a Questa runner/backend, or
- Generate temporary artifacts inside pytest fixtures and make missing artifacts a failure where simulation is required.
- Document exactly which tests are unit, generation, and simulator-dependent.

Status: Open

### [LOW] CLI succeeds without producing output when `--output` is omitted

Location:

- `src/bus_generator/bus_generator.py:226`
- `src/bus_generator/bus_generator.py:373`

This command exits successfully without producing an artifact:

```bash
uv run bus-generator tests/gpio.rdl
```

Impact:

A user can mistake a no-op invocation for a successful generation.

Recommended fix:

- Require `--output`, or
- Add an explicit stdout/dry-run mode and fail when neither output nor display mode is selected.

Status: Open

### [LOW] Nested output directories are not created

Location:

- `src/bus_generator/bus_generator.py:318`

`write_file()` uses `os.mkdir(output_dir)`, so an output path whose parent does not exist fails. For example, `-o build/generated` fails when `build/` is absent.

Recommended fix:

```python
os.makedirs(output_dir, exist_ok=True)
```

Keep the existing validation for a path that already exists as a non-directory.

Status: Confirmed by CLI execution

## Verification Notes

The generated samples compile and pass under Questa, but their testbenches do
not cover every remaining finding. In particular, field permission
combinations, exact memory-region boundaries, and unsupported SystemRDL side
effects still need dedicated coverage.
