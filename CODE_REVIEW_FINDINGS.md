# Code Review Findings

Review date: 2026-07-29
Last reconciled: 2026-07-30

This file contains only findings that remain open or partially addressed. Resolved findings were removed after their fixes were committed; their implementation and verification history remains in Git.

## Review Summary

| Priority | Count | Main areas |
|---|---:|---|
| High | 1 | Unsupported SystemRDL side-effect semantics |
| Medium | 1 | External-memory overlapping-read stress timeout |
| Low | 0 | — |

## Remaining Findings

### [HIGH] SystemRDL side-effect properties are silently ignored

Location:

- `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:431`

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
  for fields using unsupported `onread`, `onwrite`, `sw=rw1`, or `sw=w1`
  semantics.
- Warning messages include the elaborated field path and the ignored property.
- The side-effect semantics themselves remain unsupported and are intentionally
  deferred; generated RTL must not be treated as implementing them.

Verification:

- `tests/side_effects.rdl` covers `onread=rclr`, `onwrite=woset`, `sw=rw1`,
  `sw=w1`, and ordinary `rw`, `r`, and `w` fields.
- CLI generation succeeds and emits four warnings for the unsupported fields.
- Ordinary `gpio.rdl` generation emits no side-effect warnings.
- `-q` suppresses the warnings while generation still succeeds.

Recommended next step:

- Implement the supported side-effect subset explicitly, or reject unsupported
  semantics with a clear generation error.

### [MEDIUM] External-memory overlapping reads can time out under R backpressure

Location:

- `tests/test_stress.py::test_stress_read_overlap[mem_access]`
- `generated/axi4l/mem_access_regs.v`

The Questa/VSIM stress runner reaches the `mem_access_regs` DUT but the
`stress_read_overlap` case times out while issuing 64 overlapping reads with
R-channel backpressure against the external-memory design.

Reproducer:

```text
SIM=questa uv run pytest -q 'tests/test_stress.py::test_stress_read_overlap[mem_access]'
```

Observed behavior:

- Exact collected case: `tests/test_stress.py::test_stress_read_overlap[mem_access]`
- DUT top: `mem_access_regs`
- Generated RTL: `generated/axi4l/mem_access_regs.v`
- Timeout at `2,000,000 ns`
- Questa/cocotb compilation and startup succeed; this is not a simulator command
  or compilation failure.
- The other 25 simulator cases in the same `make sim` run passed.

Status: Open; potential RTL/external-memory outstanding-read or handshake bug
suspected, but the smallest failing transaction sequence and RTL root cause have
not been independently confirmed.

## Verification Notes

The simulator policy now requires an explicit selection:

```text
SIM=icarus
SIM=verilator
SIM=questa
SIM=vsim
```

Unset or unavailable simulator selections fail immediately instead of silently
skipping simulator-marked tests. Ordinary RTL self-checking tests passed under
both `SIM=questa` and `SIM=vsim`; the overlapping-read timeout above remains a
separate RTL investigation.
