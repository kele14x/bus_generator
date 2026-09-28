# RAM transaction handling implementation plan

This is a handoff plan for the implementation agent. The planned RTL and test changes have not been implemented.

## 1. Scope and interface contract

Implement the agreed architecture:

- Treat all internal registers as one fixed-latency **local block**.
- Treat each external RAM interface as a separate variable-latency block.
- Permit multiple outstanding accesses to the **same block**.
- Do not issue accesses to another block until all internal completions from the active block have been captured.
- Give each RAM a read/write tag FIFO with zero-latency bypass.
- Register RAM read data and its read ACK together.

Keep the external ports and existing AXI B/R buffer capacities unchanged.

The RAM contract must be explicit:

1. Each asserted `ram_en` at a clock edge represents an accepted request.
2. Every request, including writes, produces exactly one response.
3. Responses arrive in request order, at most one per clock.
4. `ram_valid` qualifies `ram_dout`; data need not remain valid afterward.
5. Consecutive cycles with `ram_valid=1` represent consecutive responses.
6. Zero latency is supported.
7. Reset flushes pending responses in both the adapter and external RAM.

**Behavioral change:** physical RAM writes will now be acknowledged after their RAM response, not immediately after issue.

## 2. Files to change

Line references describe the source at plan creation and may move during implementation.

| File | Changes |
|---|---|
| `src/bus_generator/templates/{{axi4l}}_regs.v.jinja2:119` | Request issue gating and active-block tracking |
| Same template, `:207` | Preserve and verify AXI credit/completion accounting |
| Same template, `:308` | Decode effective response target and local error/no-op operations |
| Same template, `:361` | Replace universal ACK generation with source-specific responses |
| Same template, `:441` | Replace RAM pending-read selection with tag FIFOs |
| Same template, `:493` | Align read data with ACK and merge registered responses |
| `src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2:355` | Update generated RAM models |
| `tests/test_stress.py:144` | Update variable-latency RAM model and add directed coverage |
| `tests/test_stress.py:772` | Isolate simulation build directories |
| `tests/test_ram_regs.py:124` | Retain existing reproducer; extend focused RAM coverage |
| `tests/test_unit.py:424` | Extend rendering coverage |

Existing memory metadata already includes permissions and geometry at `src/bus_generator/bus_generator.py:267`; **no generator metadata or CLI changes should be needed**.

Preserve the previous Verilator index-width fix (`b_pending[0] - 1'b1`), the executable-bit change to `tests/test_ram_regs.py`, and the user's untracked `doc/` content. Inspect the working tree before starting and preserve any additional user changes.

## 3. Decode an effective target before issuing

Define a target for the buffered head request:

- `LOCAL`: register accesses and locally completed operations.
- `MEM_i`: an actual authorized transaction on RAM interface `i`.

A one-hot target vector of width `number_of_memories + 1` avoids zero-width IDs for register-only designs.

Decode using `int_addr`, `head_write`, permissions, and `int_wr_strb`—**not `int_rd_en`/`int_wr_en`**, since those depend on the issue decision.

### Handle nonphysical RAM accesses locally

| Request | Effective target | RAM `en` | Response |
|---|---|---:|---|
| Authorized RAM read | Corresponding RAM | 1 | RAM-derived OKAY |
| Authorized RAM write, nonzero strobes | Corresponding RAM | 1 | RAM-derived OKAY |
| Authorized RAM write, zero strobes | LOCAL | 0 | Local OKAY |
| Prohibited RAM access | LOCAL | 0 | Local SLVERR |
| Unmapped access | LOCAL | 0 | Local SLVERR |

Zero strobes **do not override permission errors**.

This classification matters: a local no-op/error response must not overtake or collide with an earlier delayed RAM response, even when both addresses belong to the same RAM window.

## 4. Add active-block issue gating

Reuse the existing completion counters instead of adding another outstanding counter:

```text
internal_idle =
    b_wait_ack == 0 &&
    r_wait_ack == 0

target_allowed =
    internal_idle ||
    head_target == active_target

issue =
    head_valid &&
    target_allowed &&
    (head_write ? b_credit : r_credit)
```

Update `active_target` when issuing the first request while `internal_idle`.

Implementation rules:

- Remove the existing `rd_mem_pending` restriction.
- Allow additional reads and writes to the active target when their respective credits permit.
- Retire internal outstanding requests only when their **registered ACKs are captured into the AXI response FIFOs**.
- Do not release the lock on raw RAM `valid` or tag-FIFO empty.
- For the initial implementation, switch targets only after both wait counters have become zero; omit same-cycle final-ACK switching optimizations.
- AXI request buffers may accept requests for other targets; the lock applies to **internal issue**, not AXI acceptance.

When updating request arbitration, select between credit-eligible candidates so an ineligible preferred channel does not veto an eligible competitor.

Retain the existing head/back buffers; bypassing an already-loaded blocked head is outside scope.

### Preserve the two kinds of accounting

```text
b_outstanding = b_wait_ack + b_pending
r_outstanding = r_wait_ack + r_pending
```

- `*_wait_ack`: issued operations awaiting internal response capture.
- `*_pending`: responses already buffered for AXI.
- `*_outstanding`: capacity reserved until AXI handshake.

Switching targets does **not** require draining already-buffered B/R responses.

## 5. Implement each RAM's tag FIFO

Replace `*_rd_sel` and the associated global pending-read logic.

### FIFO contents and capacity

Each tag stores one bit:

```text
0 = read
1 = write
```

Use depth **4** with the current limits:

```text
maximum outstanding reads  = 2
maximum outstanding writes = 2
maximum queued RAM tags   <= 4
```

Every queued tag corresponds to an operation still counted in `*_wait_ack`; requests merely buffered in AW/W/AR/head storage do not consume tags.

Use explicit widths: two-bit pointers and a three-bit occupancy counter. No configurable generic FIFO framework is needed.

### Combinational response matching

For each RAM:

```text
req         = ram_en
bypass      = empty && req && ram_valid

push        = req && !bypass
pop         = !empty && ram_valid

response    = ram_valid && (!empty || req)
response_we = empty ? ram_we : fifo_head

wr_done     = response &&  response_we
rd_done     = response && !response_we
```

Required behavior:

- Empty, immediate response: bypass using the current request's tag; leave FIFO state unchanged.
- Empty, delayed response: enqueue the tag.
- Nonempty, response plus new request: classify using the **old head**, then pop and push.
- Nonempty always takes precedence over bypass, preserving response order.
- FWFT head and empty indication must remain aligned and support consecutive completions without bubbles.
- Track writes even on write-only RAMs.
- Never enqueue prohibited or zero-strobe operations that do not assert RAM `en`.

### Register ACK and data together

Conceptually, on every clock:

```text
ram_rd_ack <= rd_done
ram_wr_ack <= wr_done

if rd_done:
    ram_rd_data <= ram_dout
```

Reset ACKs, FIFO state, and captured data.

The data capture enable is **raw `rd_done`**, not the already-registered read ACK. ACKs update every cycle; read data may hold between read completions.

## 6. Produce and merge complete responses

### Local block

Retain one-cycle register access behavior:

- Capture register read data at local read issue.
- Register local read/write ACKs.
- Register each local error result alongside its corresponding ACK.
- Return deterministic zero read data for prohibited/unmapped reads.

Reuse existing field storage, byte masks, and permission decoding.

### Global response interface

Merge the registered source responses combinationally:

```text
int_rd_ack = local_rd_ack OR all ram_rd_ack
int_wr_ack = local_wr_ack OR all ram_wr_ack
```

Select or mask data using the **source's registered read ACK**:

```text
int_rd_data =
    masked_local_data OR
    masked_ram0_data OR
    masked_ram1_data ...
```

Likewise qualify error signals with their matching source ACK.

Do not:

- Select responses using the current address or current request target.
- OR held data from inactive sources without masking.
- Sample live RAM output in the final response mux.
- Add another register after the source ACK registers.
- Retain unconditional `int_wr_ack <= int_wr_en` for RAM writes.

The existing AXI response FIFOs should consume these aligned responses without structural changes.

## 7. Correct the simulation models

This is necessary for the new write-completion contract.

### Cocotb model

`tests/test_stress.py:144` currently responds only to reads, can reorder requests with randomized latency, and shares memory storage with the expected-results model.

Change it to:

- Own an independent copy of initial memory contents.
- Queue one response for every physical `en`, including writes.
- Capture response data at request processing time, using read-before-write semantics.
- Apply writes through observed RAM requests only.
- Return responses strictly FIFO-ordered, at most one per clock.
- Permit arbitrary delays without allowing younger requests to overtake.
- Poison inactive `dout`.
- Flush pending responses on reset.

For mixed same-address accesses, derive expectations from the observed physical issue order—not coroutine launch order.

### Generated Verilog model

Update `src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2:355` similarly:

- Pipeline `en`, not only `en && !we`.
- Pipeline captured response payload instead of reading live memory contents later.
- Return valid for writes as well as reads.
- Qualify memory writes with reset.
- Preserve existing byte-enable and access-permission checks.

### True zero-latency coverage

Add an HDL combinational model/wrapper, generated by the test runner if convenient:

```text
ram_valid = ram_en
ram_dout  = combinational addressed data
```

Do not call a cocotb model that responds after `RisingEdge` "zero latency."

### Build-directory isolation

Give stress cases distinct directories, for example:

```text
sim_build/stress/<top>/<sim>/<testcase>/
```

The current default build directory can make Verilator's `ram_regs` executable collide with the standalone RAM test's directory.

## 8. Verification requirements

Add directed tests in addition to existing random stress tests.

| Area | Required cases |
|---|---|
| Original bug | Existing `test_ram_read` and poison-after-valid regression pass |
| Latency | True zero latency, one-cycle response, variable delayed response |
| Tag matching | Read→write, write→read, consecutive reads/writes, mixed sequences |
| FIFO behavior | Empty bypass, first enqueue, simultaneous push/pop, pointer wrap, capacity limit |
| Actual overlap | Prove more than one request reaches the same RAM before its first response |
| Block switching | Register↔RAM0, RAM0↔RAM1; wait for **all** prior completions |
| Pipeline boundary | Empty tag FIFO while registered ACK still awaits capture must not unlock early |
| Local responses | Zero-strobe, prohibited, and unmapped requests following delayed RAM operations |
| Data integrity | Different values per address/RAM; same-address read-before-write snapshots |
| AXI backpressure | Stable B/R payloads while stalled; no overflow or response loss |
| Buffered responses | Target switching allowed after internal capture even when older B/R responses remain buffered |
| Reset | Flush tags and ACKs during outstanding accesses; clean restart |
| Generation | No-memory designs and read-only/write-only/inaccessible memories |

Check these invariants with monitors/assertions:

- ACK source vectors are one-hot-or-zero.
- No new target issues while another target has unfinished operations.
- Every internal issue produces exactly one matching completion.
- RAM write responses never become read ACKs.
- Tags never overflow or underflow.
- Per-channel accounting identities hold, with counters bounded by two.
- No unsolicited RAM responses outside the reset contract.

Check AXI read and write response ordering independently; do not require a combined B-versus-R handshake order.

## 9. Implementation order and acceptance

Recommended sequence:

1. Update RAM test models and add failing directed regressions.
2. Implement effective-target decoding and the active-block lock.
3. Implement tag FIFOs, zero-latency bypass, and aligned RAM responses.
4. Integrate local responses and the response merge.
5. Remove obsolete pending-read/live-data paths.
6. Regenerate artifacts and run all verification.

```bash
make -B artifacts
uv run pytest tests/test_unit.py tests/test_simulator_support.py -q
SIM=icarus uv run pytest -q
SIM=verilator uv run pytest -q
```

Acceptance requires passing both simulator suites without weakening the poison-data tests or suppressing Verilator warnings. Keep unrelated address-geometry, SystemRDL side-effect, and broader AXI timing refactors outside this change.
