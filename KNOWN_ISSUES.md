# Known Issues

Open correctness, compatibility, and verification issues identified during the
project review on 2026-09-25. This document records findings, not implemented fixes.
Source references reflect the reorganized template; line numbers may change.

- **P1:** High priority: incorrect data, possible deadlock, unsupported semantics,
  or verification that can conceal these failures.
- **P2:** Medium priority: protocol compliance, generation/tool compatibility,
  or gaps in verification.

## RTL and generator

### KI-01 [P1] External-memory read data is captured one cycle late

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:373-374`,
  `:274-280`, and `:539-546`.
- **Trigger:** Memory asserts `valid` for one cycle and changes `dout` after
  deasserting `valid`.
- **Impact:** The acknowledgement is registered, but the response FIFO samples
  live memory data on the following clock. An isolated Icarus reproduction
  returned `0xdeadbeef` with OKAY instead of the valid `0x12345678`.
- **Suggested resolution:** Capture memory data alongside its acknowledgement.
  Holding `dout` through the following sampling edge avoids this specific failure,
  but that extra hold requirement is not expressed by the interface.
- **Rechecked 2026-09-28:** Confirmed with Questa against both the existing
  generated RAM RTL and freshly generated output. `memory_read_held_data` passes;
  `memory_read_valid_pulse` fails with `0xdeadbeef` instead of `0x12345678`.
  The simple `tests/test_ram_regs.py` previously accessed only register `0x0`,
  so its passing result did not exercise RAM. It now writes and reads RAM0 at
  `0x100` and reproduces the same failure. In that test's waveform, the memory
  presents valid data after 360 ns; at 370 ns the RTL registers the acknowledgement
  while the model deasserts valid and changes data; at 380 ns the response FIFO
  captures the changed data. Run with `SIM=questa`:
  `uv run pytest tests/test_ram_regs.py tests/test_stress.py::test_memory_read_timing -v`.

### KI-02 [P1] An ineligible preferred request blocks the opposite channel

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:129-132`.
- **Trigger:** Fill both read-response slots with `RREADY=0`, buffer another read,
  complete one write, then submit another write.
- **Impact:** Read priority prevents the write from advancing even with an empty
  head and available write credit. Simulation confirmed that the write resumes
  only after read backpressure is released. A master waiting for the write
  response before asserting `RREADY` can deadlock.
- **Suggested resolution:** Arbitrate among eligible requests; an ineligible
  preferred request must not veto an eligible competitor.

### KI-03 [P1] External-memory addresses do not subtract the memory base

- **Location:** `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:463`.
- **Trigger:** A memory base is not aligned to the address window implied by the
  generated slice. A valid three-entry, 32-bit memory at byte address `0x4` is
  one example.
- **Impact:** Slicing the absolute address produces indices `1,2,3` instead of
  `0,1,2`, including an out-of-range index. A write/read round trip can conceal
  this when both operations use the same incorrect mapping.
- **Suggested resolution:** Derive the external entry index from the byte offset
  relative to the memory base.

### KI-04 [P1] Narrow registers can alias and overwrite each other

- **Location:** `src/bus_generator/bus_generator.py:236` and
  `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:317`.
- **Trigger:** Two 16-bit registers occupy byte offsets `0` and `2` in a map
  large enough to use the word-address decoder, for example with a third
  register at offset `4`.
- **Impact:** Both registers receive the same word address and field bit
  positions. An AXI write of `0x1234` to offset `0` with `WSTRB=0011` updated
  both registers in simulation.
- **Suggested resolution:** Implement subword address/byte-lane mapping or reject
  layouts that the fixed 32-bit implementation cannot represent.

### KI-05 [P1] Narrow memories lose addressability and have incorrect C sizes

- **Location:** `src/bus_generator/bus_generator.py:275-277`,
  `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:463`, and
  `src/bus_generator/templates/{{c_header}}.h.jinja2:17`.
- **Trigger:** A memory has entries narrower than 32 bits, such as eight 16-bit
  entries occupying 16 bytes.
- **Impact:** The generator always discards two byte-address bits. That example
  exposes only four entry indices instead of eight, and the C header reports
  32 bytes instead of 16. Data and byte-enable mapping also assumes bus-sized
  entries.
- **Suggested resolution:** Handle memory entry geometry consistently across RTL,
  headers, and tests, or reject unsupported widths explicitly.

### KI-06 [P1] SystemRDL side-effect semantics are unsupported

- **Location:** `src/bus_generator/bus_generator.py:140-179` and `AGENTS.md:28-44`.
- **Limitation:** Generated RTL does not implement `onread`, `onwrite`,
  write-one-to-clear/set, read-clear, single-pulse, or write-once semantics.
- **Current behavior:** Generation continues and warns for `onread`, `onwrite`,
  `sw=rw1`, and `sw=w1`, including the affected component path. These warnings
  do not cover every unsupported property; quiet mode suppresses them.
- **Impact:** Successful generation does not mean the declared side effects are
  implemented. Do not rely on them in generated hardware.
- **Status:** Previously documented limitation; implementation is intentionally
  deferred.

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

### KI-11 [P1] Physical memory and the stress scoreboard share storage

- **Location:** `tests/test_stress.py:112-124`, `:154`, and `:484-488`.
- **Cause:** `ExternalMemoryModel` receives the same list used by the expected
  results model.
- **Impact:** Updating expected state changes physical memory before the DUT
  writes. Incorrect physical writes also change expected results. An isolated
  check confirmed both directions of this aliasing, so missing or corrupted
  memory writes can escape detection.
- **Suggested resolution:** Maintain independent physical and expected storage;
  only DUT memory-interface transactions should update physical storage.

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

Focused reproductions used temporary fixtures; they are not committed regression
tests. Passing the existing suite does not establish that the issues above are
fixed. No functional fixes or test changes accompany this document.
