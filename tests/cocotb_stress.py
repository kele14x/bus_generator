"""Cocotb stress tests for the generated AXI4-Lite register block.

Two test cases, both driving ``simple_regs`` as an AXI4-Lite master with
randomized handshaking. This module is imported by cocotb (via
``COCOTB_TEST_MODULES``), not collected by pytest (no ``test_`` prefix);
``tests/test_stress.py`` selects each case via the runner's ``testcase`` arg.

* ``stress_random_axi`` -- random read/write traffic with randomized AW/W
  order (AW-before-W, W-before-AW, or concurrent overlap), random pre-idle
  before VALID, and B/R backpressure ("early" vs "late" ready). A software
  reference model (one word per register, full-word writes) is checked
  against every read.

* ``stress_b_backpressure`` -- issues many write transactions back-to-back
  WITHOUT waiting for B between them, while a background task drains B
  responses with randomized (often long) BREADY-low gaps. This stresses the
  DUT's B-response pipeline and the aw_ready backpressure path. Verifies
  every accepted AW+W eventually receives exactly one B (a dropped/lost B or
  an accepted write that never commits surfaces as a B-count mismatch or a
  test timeout), no B is an error, and final register values match the issued
  write sequence (last write to each address wins).

Transactions are sequential (one outstanding): the DUT is AXI4-Lite,
single-outstanding by design. Sequential + random per-channel timing still
exercises every ordering/backpressure case.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

DATA_WIDTH = 32
NUM_REGS = 16

# stress_random_axi tuning
MAX_IDLE = 4
MAX_BP = 4
SEED = 0xC0FFEE

# stress_b_backpressure tuning
MAX_IDLE_B = 3
MAX_BP_GAP = 16
SEED_B = 0xBEEF


class AxiLiteMaster:
    """Hand-rolled AXI4-Lite master BFM driving the DUT's s_axi_* ports.

    Used by ``stress_random_axi``. ``write`` blocks until B is received;
    ``read`` blocks until R is received.
    """

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.s_axi_aclk
        dut.s_axi_awvalid.value = 0
        dut.s_axi_wvalid.value = 0
        dut.s_axi_arvalid.value = 0
        dut.s_axi_bready.value = 0
        dut.s_axi_rready.value = 0
        dut.s_axi_awaddr.value = 0
        dut.s_axi_awprot.value = 0
        dut.s_axi_wdata.value = 0
        dut.s_axi_wstrb.value = 0
        dut.s_axi_araddr.value = 0
        dut.s_axi_arprot.value = 0

    async def _idle(self):
        for _ in range(random.randint(0, MAX_IDLE)):
            await RisingEdge(self.clk)

    async def _send(self, valid_sig, ready_sig):
        # Caller has asserted VALID and set the payload. Ready/valid are
        # registered, so sample READY before the edge: the value read between
        # edges is the value that will be present at the upcoming edge. The
        # handshake then occurs at that edge (both high), after which VALID is
        # deasserted. Sampling after the edge would instead see the *next*
        # cycle's READY and can miss a handshake that just cleared it.
        while True:
            if int(ready_sig.value) == 1:
                await RisingEdge(self.clk)
                break
            await RisingEdge(self.clk)
        valid_sig.value = 0

    async def _recv(self, valid_sig, ready_sig, read_payload):
        # DUT drives VALID, cocotb drives READY. Random policy: "early" (READY
        # before VALID) or "late" (backpressure: READY low for random cycles
        # after VALID, then asserted). Payload is read while VALID is asserted
        # and stable, before the handshake edge.
        policy = random.choice(["early", "late"])
        ready_sig.value = 1 if policy == "early" else 0
        while True:
            if int(valid_sig.value) == 1:
                break
            await RisingEdge(self.clk)
        payload = read_payload()
        if policy == "late":
            for _ in range(random.randint(0, MAX_BP)):
                await RisingEdge(self.clk)
            ready_sig.value = 1
        await RisingEdge(self.clk)  # edge where VALID & READY handshake
        ready_sig.value = 0
        return payload

    async def _drive_aw(self, addr):
        await self._idle()
        self.dut.s_axi_awaddr.value = addr
        self.dut.s_axi_awprot.value = 0
        self.dut.s_axi_awvalid.value = 1
        await self._send(self.dut.s_axi_awvalid, self.dut.s_axi_awready)

    async def _drive_w(self, data):
        await self._idle()
        self.dut.s_axi_wdata.value = data
        self.dut.s_axi_wstrb.value = 0xF
        self.dut.s_axi_wvalid.value = 1
        await self._send(self.dut.s_axi_wvalid, self.dut.s_axi_wready)

    async def write(self, addr, data):
        aw_task = cocotb.start_soon(self._drive_aw(addr))
        w_task = cocotb.start_soon(self._drive_w(data))
        await aw_task
        await w_task
        return await self._recv(
            self.dut.s_axi_bvalid,
            self.dut.s_axi_bready,
            lambda: int(self.dut.s_axi_bresp.value),
        )

    async def read(self, addr):
        await self._idle()
        self.dut.s_axi_araddr.value = addr
        self.dut.s_axi_arprot.value = 0
        self.dut.s_axi_arvalid.value = 1
        await self._send(self.dut.s_axi_arvalid, self.dut.s_axi_arready)
        return await self._recv(
            self.dut.s_axi_rvalid,
            self.dut.s_axi_rready,
            lambda: (
                int(self.dut.s_axi_rdata.value),
                int(self.dut.s_axi_rresp.value),
            ),
        )


class PipelinedWriteMaster:
    """AXI4-Lite master that issues AW+W without blocking on B.

    Used by ``stress_b_backpressure``. A concurrent ``b_drain`` task collects
    B responses. Read transactions are supported for post-write readback.
    """

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.s_axi_aclk
        dut.s_axi_awvalid.value = 0
        dut.s_axi_wvalid.value = 0
        dut.s_axi_arvalid.value = 0
        dut.s_axi_bready.value = 0
        dut.s_axi_rready.value = 0
        dut.s_axi_awaddr.value = 0
        dut.s_axi_awprot.value = 0
        dut.s_axi_wdata.value = 0
        dut.s_axi_wstrb.value = 0
        dut.s_axi_araddr.value = 0
        dut.s_axi_arprot.value = 0
        self.b_count = 0
        self.b_errors = 0

    async def _idle(self):
        for _ in range(random.randint(0, MAX_IDLE_B)):
            await RisingEdge(self.clk)

    async def _send(self, valid_sig, ready_sig):
        # Pre-edge sampling: ready/valid are registered, so the value read
        # between edges is the value present at the upcoming edge. Wait for
        # ready, take the edge (handshake), then deassert valid.
        while True:
            if int(ready_sig.value) == 1:
                await RisingEdge(self.clk)
                break
            await RisingEdge(self.clk)
        valid_sig.value = 0

    async def _drive_aw(self, addr):
        await self._idle()
        self.dut.s_axi_awaddr.value = addr
        self.dut.s_axi_awprot.value = 0
        self.dut.s_axi_awvalid.value = 1
        await self._send(self.dut.s_axi_awvalid, self.dut.s_axi_awready)

    async def _drive_w(self, data):
        await self._idle()
        self.dut.s_axi_wdata.value = data
        self.dut.s_axi_wstrb.value = 0xF
        self.dut.s_axi_wvalid.value = 1
        await self._send(self.dut.s_axi_wvalid, self.dut.s_axi_wready)

    async def issue_write(self, addr, data):
        """Issue AW+W; return once both are accepted. Does NOT wait for B.

        AW and W are driven sequentially in random order (AW-first or
        W-first). Concurrent driving via start_soon races _send's pre-edge
        ready sample, so the master can report a write as issued before its
        W is actually accepted. Sequential driving still exercises both
        AW-before-W and W-before-AW orderings.
        """
        if random.random() < 0.5:
            await self._drive_aw(addr)
            await self._drive_w(data)
        else:
            await self._drive_w(data)
            await self._drive_aw(addr)

    async def b_drain(self, expected):
        """Drain ``expected`` B responses with randomized backpressure.

        BREADY is held low for random gaps (0..MAX_BP_GAP cycles) between
        acceptances to pile up B-channel backpressure. A B is counted only
        when BVALID and BREADY are both 1 at the pre-edge sample (a real
        handshake); after each handshake BREADY is dropped so a following
        back-to-back B pulse is not consumed until the next intended
        acceptance. Counting the await rather than the observed handshake
        mis-counts when BREADY toggles 0->1 between back-to-back B pulses.
        """
        self.dut.s_axi_bready.value = 0
        gap = random.randint(0, MAX_BP_GAP)
        while self.b_count < expected:
            bvalid = int(self.dut.s_axi_bvalid.value)
            bready = int(self.dut.s_axi_bready.value)
            if bvalid == 1 and bready == 1:
                bresp = int(self.dut.s_axi_bresp.value)
                await RisingEdge(self.clk)  # handshake edge
                self.b_count += 1
                if bresp != 0:
                    self.b_errors += 1
                    self.dut._log.error(
                        f"B[{self.b_count}] bresp={bresp}, expected 0"
                    )
                self.dut.s_axi_bready.value = 0
                gap = random.randint(0, MAX_BP_GAP)
            else:
                if gap > 0:
                    gap -= 1
                else:
                    self.dut.s_axi_bready.value = 1
                await RisingEdge(self.clk)

    async def read(self, addr):
        await self._idle()
        self.dut.s_axi_araddr.value = addr
        self.dut.s_axi_arprot.value = 0
        self.dut.s_axi_arvalid.value = 1
        await self._send(self.dut.s_axi_arvalid, self.dut.s_axi_arready)
        self.dut.s_axi_rready.value = 1
        while int(self.dut.s_axi_rvalid.value) == 0:
            await RisingEdge(self.clk)
        rdata = int(self.dut.s_axi_rdata.value)
        rresp = int(self.dut.s_axi_rresp.value)
        await RisingEdge(self.clk)  # handshake edge
        self.dut.s_axi_rready.value = 0
        return rdata, rresp


@cocotb.test(timeout_time=500, timeout_unit="us")
async def stress_random_axi(dut):
    """Random read/write traffic with randomized AXI handshaking + checker."""
    random.seed(SEED)

    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, units="ns").start())
    dut.s_axi_aresetn.value = 0
    master = AxiLiteMaster(dut)
    await Timer(100, "ns")
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.s_axi_aclk)
    await RisingEdge(dut.s_axi_aclk)

    word_addrs = [i * 4 for i in range(NUM_REGS)]
    model = {a: 0 for a in word_addrs}
    count = int(os.environ.get("STRESS_COUNT", "200"))
    errors = 0

    for i in range(count):
        addr = random.choice(word_addrs)
        if random.random() < 0.5:
            data = random.getrandbits(DATA_WIDTH)
            bresp = await master.write(addr, data)
            model[addr] = data
            if bresp != 0:
                errors += 1
                dut._log.error(
                    f"[{i}] write addr=0x{addr:02x} got bresp={bresp}, expected 0"
                )
        else:
            rdata, rresp = await master.read(addr)
            if rdata != model[addr] or rresp != 0:
                errors += 1
                dut._log.error(
                    f"[{i}] read  addr=0x{addr:02x} data=0x{rdata:08x} "
                    f"expected=0x{model[addr]:08x} resp={rresp}"
                )

    assert errors == 0, f"{errors}/{count} mismatches"
    dut._log.info(f"stress test passed: {count} transactions, 0 mismatches")


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def stress_b_backpressure(dut):
    """Pipelined writes with heavy B backpressure; verify no B is dropped/lost."""
    random.seed(SEED_B)

    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    dut.s_axi_aresetn.value = 0
    master = PipelinedWriteMaster(dut)
    await Timer(100, "ns")
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.s_axi_aclk)
    await RisingEdge(dut.s_axi_aclk)

    count = int(os.environ.get("STRESS_B_COUNT", "64"))
    word_addrs = [i * 4 for i in range(NUM_REGS)]
    writes = []
    for _ in range(count):
        writes.append(
            (random.choice(word_addrs), random.getrandbits(DATA_WIDTH))
        )

    dut._log.info(f"issuing {count} pipelined writes with B backpressure")

    drain_task = cocotb.start_soon(master.b_drain(count))
    for addr, data in writes:
        await master.issue_write(addr, data)
    await drain_task

    errors = master.b_errors
    if master.b_count != count:
        errors += 1
        dut._log.error(
            f"B count mismatch: received {master.b_count}, expected {count}"
        )

    expected = {a: 0 for a in word_addrs}
    for addr, data in writes:
        expected[addr] = data

    for addr in word_addrs:
        rdata, rresp = await master.read(addr)
        if rresp != 0 or rdata != expected[addr]:
            errors += 1
            dut._log.error(
                f"readback addr=0x{addr:02x} data=0x{rdata:08x} "
                f"expected=0x{expected[addr]:08x} resp={rresp}"
            )

    assert errors == 0, (
        f"{errors} errors (b_errors={master.b_errors}, "
        f"b_count={master.b_count})"
    )
    dut._log.info(
        f"B-backpressure stress passed: {count} writes, "
        f"{master.b_count} B responses, 0 errors"
    )
