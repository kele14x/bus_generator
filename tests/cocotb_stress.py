"""Cocotb random stress test for the generated AXI4-Lite register block.

Drives ``simple_regs`` as an AXI4-Lite master with random read/write traffic and
fully randomized handshaking:

* Write address/data channel order: AW-before-W, W-before-AW, or simultaneous
  overlap (AW and W run as concurrent tasks with independent random pre-idle).
* Optional random idle cycles before asserting VALID on AW/W/AR.
* B and R response channels (cocotb owns BREADY/RREADY): "early" ready
  (asserted before VALID arrives) or "late" ready (held low for a random number
  of cycles after VALID, then asserted) to apply backpressure.

A software reference model (one word per register, full-word writes) is checked
against every read. This module is imported by cocotb (via COCOTB_TEST_MODULES),
not collected by pytest (no ``test_`` prefix).
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

DATA_WIDTH = 32
NUM_REGS = 16
MAX_IDLE = 4
MAX_BP = 4
SEED = 0xC0FFEE


class AxiLiteMaster:
    """Hand-rolled AXI4-Lite master BFM driving the DUT's s_axi_* ports."""

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
