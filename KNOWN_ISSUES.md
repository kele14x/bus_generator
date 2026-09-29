# Known Issues

Open correctness, compatibility, and verification issues identified during the
project review on 2026-09-25. Resolved issues are removed from this document.
Source references reflect the reorganized template; line numbers may change.

- **P1:** High priority: incorrect data, possible deadlock, unsupported semantics,
  or verification that can conceal these failures.
- **P2:** Medium priority: protocol compliance, generation/tool compatibility,
  or gaps in verification.

## RTL and generator

### KI-07 [P2] AXI interface inputs have combinational paths to outputs

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:127-138`
  and `:209-210`.
- **Evidence:** With buffered AW/W requests, asserting `ARVALID` changed
  `AWREADY` and `WREADY` without a clock edge. Response-ready signals also feed
  request-ready logic through credit recycling.
- **Impact:** This violates the AXI requirement against combinational paths
  between interface inputs and outputs and can complicate interconnect timing.
- **Suggested resolution:** Break the paths with registered readiness and
  appropriately reserved buffering.

### KI-08 [P2] Single-entry memories generate invalid Verilog slices

- **Location:** `src/bus_generator/bus_generator.py:275-277` and
  `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:55,463`.
- **Trigger:** A memory contains one 32-bit entry.
- **Impact:** The computed address width is zero, producing a `[-1:0]` address
  port and `int_addr[1:2]`. Icarus rejects the reversed part-select.
- **Suggested resolution:** Handle single-entry geometry explicitly, using a
  valid port width and constant entry index.

### KI-09 [P2] Ascending field ranges generate invalid Verilog slices

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:401`,
  `:411-413`, and `:512`.
- **Trigger:** Valid SystemRDL uses `msb0` with an ascending field such as `[0:7]`.
- **Impact:** The template inserts `[0:7]` into descending bus vectors. Icarus
  rejects the resulting mask, write-data, and readback part-selects.
- **Suggested resolution:** Translate field numbering to bus bit positions
  consistently, or reject unsupported numbering modes before rendering.

### KI-10 [P2] Verilator 5.052 rejects the response-FIFO index width

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:268`.
- **Cause:** `b_pending - 2'd1` is a two-bit index into the two-bit
  `b_err_fifo`, which requires a one-bit index.
- **Impact:** Verilator 5.052 emits `WIDTHTRUNC`; the existing test commands treat
  it as fatal. The review run had 26 failures caused by this warning.
- **Suggested resolution:** Use an explicitly correct-width index rather than
  globally suppressing warnings.

## Verification

### KI-12 [P2] Simulation may use stale artifacts or skip missing artifacts

- **Location:** `Makefile:33-43`, `tests/test_simulation.py:144-147`, and
  `tests/test_stress.py:727-729`.
- **Cause:** Artifact prerequisites omit generator Python sources. Direct pytest
  runs skip simulator cases when generated files are absent.
- **Evidence:** `make -n -W src/bus_generator/bus_generator.py artifacts` reported
  nothing to rebuild. With artifacts absent, all 26 artifact-dependent simulator
  cases can skip despite a valid simulator selection.
- **Impact:** A green run need not verify current generator output. This is
  separate from the explicit simulator-selection checks, which do fail when
  selection is missing or unavailable.
- **Suggested resolution:** Include generator dependencies and generate fresh
  artifacts for automated verification, or fail clearly when they are missing.

### KI-13 [P2] Read-overlap checks use indistinguishable data

- **Location:** `tests/test_stress.py:595-611`.
- **Cause:** The test issues reads immediately after reset without initializing
  address-distinct values. All expected read values in the five stress samples
  were zero during the review.
- **Impact:** Reordered or misaddressed responses can pass the data checks if
  response counts and statuses remain correct.
- **Suggested resolution:** Initialize distinguishable values before issuing the
  overlapped reads, including read-only locations through the test model.

### KI-14 [P2] Software/hardware merge checking trusts the DUT mask

- **Location:** `src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2:284-295`
  and `:452-494`.
- **Cause:** Expected merge data is calculated with `DUT.<field>_sw_mask`, the
  same mask used by the implementation under test.
- **Evidence:** An isolated mutation forcing GPIO's software mask to zero still
  produced `TEST PASSED`, despite disabling software updates to that field.
- **Suggested resolution:** Calculate expected masks independently from captured
  AXI WSTRB and the field layout.

### KI-15 [P2] Memory-boundary tests can reject legal adjacent mappings

- **Location:** `src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2:545-563`.
- **Cause:** The two words after each memory unconditionally require SLVERR,
  even if another accessible component occupies those addresses.
- **Evidence:** Extending `ram0` to 16 entries makes its end coincide with
  `ram1` at `0x140`. The generated testbench incorrectly rejects valid OKAY
  responses at `0x140` and `0x144`.
- **Suggested resolution:** Derive expected responses from the complete address
  map while separately checking that the preceding memory is not selected.

## Recorded verification results

These are results from the original review, not a fresh run after template
reorganization or creation of this document:

| Command | Result |
| --- | --- |
| `SIM=icarus uv run pytest -q` | 76 passed |
| `SIM=verilator uv run pytest -q` | 26 failed, 50 passed; Verilator 5.052, KI-10 |

The original focused reproductions used temporary fixtures and were not committed
regression tests. Those review results do not establish that the issues above are
fixed. No functional fixes or test changes accompanied the original review.
