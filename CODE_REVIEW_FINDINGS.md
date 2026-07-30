# Code Review Findings

Review date: 2026-07-29
Last reconciled: 2026-07-30

This file contains only findings that remain open or partially addressed. Resolved findings were removed after their fixes were committed; their implementation and verification history remains in Git.

## Review Summary

| Priority | Count | Main areas |
|---|---:|---|
| High | 1 | Unsupported SystemRDL side-effect semantics |
| Medium | 0 | — |
| Low | 0 | — |

## Remaining Findings

### [HIGH] SystemRDL side-effect properties are silently ignored

Location:

- `src/bus_generator/bus_generator.py:146`
- `src/bus_generator/bus_generator.py:166`
- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:453`

The implementation handles basic reset, software writes, and hardware inputs,
but does not implement SystemRDL side-effect properties such as `onread`,
`onwrite`, write-one-to-clear, write-one-to-set, read-clear, or single-pulse
behavior. The write-once semantics of `sw=rw1` and `sw=w1` are also not
enforced for fields or memories.

Impact:

Valid SystemRDL inputs can generate RTL whose CSR semantics differ materially
from the source description. Interrupt/status registers are especially
vulnerable.

Current status: Partially addressed (2026-07-30; semantics deferred)

Current behavior:

- Generation continues with the existing behavior, but emits `WARNING` messages
  for fields or memories using unsupported `onread`, `onwrite`, `sw=rw1`, or
  `sw=w1` semantics.
- Warning messages include the elaborated component path and the ignored
  property.
- The side-effect semantics themselves remain unsupported and are intentionally
  deferred; generated RTL must not be treated as implementing them.

Verification:

- `tests/side_effects.rdl` covers `onread=rclr`, `onwrite=woset`, `sw=rw1`,
  `sw=w1`, ordinary `rw`, `r`, and `w` fields, and an `sw=rw1` memory.
- CLI generation succeeds and emits five warnings for the unsupported fields and
  memory.
- Ordinary `gpio.rdl` generation emits no side-effect warnings.
- `-q` suppresses the warnings while generation still succeeds.

Recommended next step:

- Implement the supported side-effect subset explicitly, or reject unsupported
  semantics with a clear generation error.

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
