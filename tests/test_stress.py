#!/usr/bin/env python3
"""Pytest wrappers and cocotb stress tests for the generated AXI4-Lite RTL.

The pytest wrappers build ``generated/axi4l/simple_regs.v`` with the icarus
runner and select one ``@cocotb.test`` case from this module. Cocotb owns
pass/fail; ``runner.test()`` exits non-zero under pytest if the selected test
fails or times out.

Sources are read from the ``generated/`` tree (produced by ``make gen``) so
manual edits to the RTL survive a re-run. Skipped when iverilog/vvp or
cocotb_tools are unavailable, or when the generated DUT is missing.
"""

import os
import random
import shutil
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"
TESTS_DIR = Path(__file__).resolve().parent

DATA_WIDTH = 32
NUM_REGS = 16

MAX_IDLE = 4
MAX_BP = 4
SEED = 0xC0FFEE

MAX_IDLE_B = 3
MAX_BP_GAP = 16
SEED_B = 0xBEEF


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
        while True:
            if int(ready_sig.value) == 1:
                await RisingEdge(self.clk)
                break
            await RisingEdge(self.clk)
        valid_sig.value = 0

    async def _recv(self, valid_sig, ready_sig, read_payload):
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
        await RisingEdge(self.clk)
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
    """AXI4-Lite master that issues AW+W without blocking on B."""

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
        if random.random() < 0.5:
            await self._drive_aw(addr)
            await self._drive_w(data)
        else:
            await self._drive_w(data)
            await self._drive_aw(addr)

    async def b_drain(self, expected):
        self.dut.s_axi_bready.value = 0
        gap = random.randint(0, MAX_BP_GAP)
        while self.b_count < expected:
            bvalid = int(self.dut.s_axi_bvalid.value)
            bready = int(self.dut.s_axi_bready.value)
            if bvalid == 1 and bready == 1:
                bresp = int(self.dut.s_axi_bresp.value)
                await RisingEdge(self.clk)
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
        await RisingEdge(self.clk)
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
        writes.append((random.choice(word_addrs), random.getrandbits(DATA_WIDTH)))

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


def _run_cocotb_test(testcase):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("iverilog/vvp not installed")
    pytest.importorskip("cocotb_tools.runner")

    dut = GENERATED / "axi4l" / "simple_regs.v"
    if not dut.is_file():
        pytest.skip(f"missing {dut}; run `make gen` first")

    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    from cocotb_tools.runner import get_runner

    runner = get_runner("icarus")
    runner.build(
        sources=[str(dut)],
        hdl_toplevel="simple_regs",
        always=True,
    )
    runner.test(
        test_module="test_stress",
        hdl_toplevel="simple_regs",
        test_dir=str(TESTS_DIR),
        testcase=testcase,
        seed=0xC0FFEE,
    )


@pytest.mark.sim
def test_stress_axi4l_simple():
    _run_cocotb_test("stress_random_axi")


@pytest.mark.sim
def test_stress_b_backpressure():
    _run_cocotb_test("stress_b_backpressure")
