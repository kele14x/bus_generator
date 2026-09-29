#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb_tools.runner import get_runner
from simulator_support import require_simulator

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# ---------------------------------
# AXI Helper
# ---------------------------------


async def axi_aw(dut, addr: int):
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awprot.value = 0
    dut.s_axi_awvalid.value = 1

    for _ in range(16):
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_awready.value) == 1:
            break
    else:
        raise AssertionError(f"AW timeout: addr=0x{addr:x}")

    dut.s_axi_awvalid.value = 0
    dut._log.info("AXI AW 0x%x", addr)


async def axi_w(dut, data: int, strb: int = 0xF):
    dut.s_axi_wdata.value = data
    dut.s_axi_wstrb.value = strb
    dut.s_axi_wvalid.value = 1

    for _ in range(16):
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_wready.value) == 1:
            break
    else:
        raise AssertionError(f"W timeout: data=0x{data:08x}")

    dut.s_axi_wvalid.value = 0
    dut._log.info("AXI W 0x%08x strb=0x%x", data, strb)


async def axi_b(dut):
    dut.s_axi_bready.value = 1

    for _ in range(16):
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_bvalid.value) == 1:
            resp = int(dut.s_axi_bresp.value)
            break
    else:
        raise AssertionError("B timeout")

    dut.s_axi_bready.value = 0
    dut._log.info("AXI B resp=%d", resp)
    return resp


async def axi_ar(dut, addr: int):
    dut.s_axi_araddr.value = addr
    dut.s_axi_arprot.value = 0
    dut.s_axi_arvalid.value = 1

    for _ in range(16):
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_arready.value) == 1:
            break
    else:
        raise AssertionError(f"AR timeout: addr=0x{addr:x}")

    dut.s_axi_arvalid.value = 0
    dut._log.info("AXI AR 0x%x", addr)


async def axi_r(dut):
    dut.s_axi_rready.value = 1

    for _ in range(16):
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_rvalid.value) == 1:
            data = int(dut.s_axi_rdata.value)
            resp = int(dut.s_axi_rresp.value)
            break
    else:
        raise AssertionError("R timeout")

    dut.s_axi_rready.value = 0
    dut._log.info("AXI R 0x%08x resp=%d", data, resp)
    return data, resp


async def axi_write(dut, addr: int, data: int, strb: int = 0xF):
    """Complete one write and require an OKAY response; await before reusing."""
    # AW and W are independent: neither channel waits for the other to finish.
    aw_task = cocotb.start_soon(axi_aw(dut, addr))
    w_task = cocotb.start_soon(axi_w(dut, data, strb))
    await aw_task
    await w_task
    resp = await axi_b(dut)
    assert resp == 0, f"Write addr=0x{addr:x}: BRESP={resp}, expected OKAY"


async def axi_read(dut, addr: int):
    """Complete one read, require OKAY, and return data; await before reusing."""
    await axi_ar(dut, addr)
    data, resp = await axi_r(dut)
    assert resp == 0, f"Read addr=0x{addr:x}: RRESP={resp}, expected OKAY"
    return data


# ---------------------------------
# Memory model
# ---------------------------------


async def mem0_model(dut):
    """Synchronous read-before-write RAM; data is valid for each enabled cycle."""
    mem0 = [0] * 16
    dut.ram0_dout.value = 0
    dut.ram0_valid.value = 0

    while True:
        await RisingEdge(dut.s_axi_aclk)
        if int(dut.s_axi_aresetn.value) == 0:
            dut.ram0_dout.value = 0
            dut.ram0_valid.value = 0
            continue

        dut.ram0_valid.value = 0
        dut.ram0_dout.value = 0xDEADBEEF
        if int(dut.ram0_en.value) == 0:
            continue

        addr = int(dut.ram0_addr.value)
        # Memory read, read before write
        dut.ram0_dout.value = mem0[addr]
        dut.ram0_valid.value = 1

        # Memory write
        if int(dut.ram0_we.value) == 1:
            be = int(dut.ram0_be.value)
            mask = 0
            for byte in range(4):
                if be & (1 << byte):
                    mask |= 0xFF << (8 * byte)
            data = int(dut.ram0_din.value)
            mem0[addr] = (mem0[addr] & ~mask) | (data & mask)


# ---------------------------------
# Tests
# ---------------------------------


@cocotb.test()
async def test_ram_read(dut):
    # Keep every master-driven channel idle during reset.
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awprot.value = 0
    dut.s_axi_awvalid.value = 0
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    dut.s_axi_bready.value = 0
    dut.s_axi_araddr.value = 0
    dut.s_axi_arprot.value = 0
    dut.s_axi_arvalid.value = 0
    dut.s_axi_rready.value = 0
    dut.reg1_field0_in.value = 0
    dut.ram0_dout.value = 0
    dut.ram0_valid.value = 0
    dut.ram1_dout.value = 0
    dut.ram1_valid.value = 0

    # Start a 100 MHz clock and hold the active-low reset for five cycles.
    dut.s_axi_aresetn.value = 0
    cocotb.start_soon(mem0_model(dut))
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    await ClockCycles(dut.s_axi_aclk, 5)

    # Release reset after this edge, then allow two settling cycles.
    await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 2)

    # Check the AXI helpers against reg0 first.
    assert await axi_read(dut, 0x0) == 0
    await axi_write(dut, 0x0, 0x12345678)
    assert await axi_read(dut, 0x0) == 0x12345678
    await axi_write(dut, 0x0, 0xAABBCCDD, strb=0x5)
    assert await axi_read(dut, 0x0) == 0x12BB56DD

    # RAM0 starts at 0x100; accesses to reg0 do not exercise mem0_model.
    await axi_write(dut, 0x100, 0x12345678)
    data = await axi_read(dut, 0x100)
    assert data == 0x12345678, (
        f"RAM0 read addr=0x100: expected=0x12345678, actual=0x{data:08x}"
    )

    await axi_write(dut, 0x104, 0x89ABCDEF)
    await axi_write(dut, 0x134, 0x76543210)
    await axi_write(dut, 0x100, 0xAABBCCDD, strb=0x5)
    await axi_write(dut, 0x104, 0x10203040, strb=0xA)
    await axi_write(dut, 0x134, 0xFFEEDDCC, strb=0x8)
    expected = {0x100: 0x12BB56DD, 0x104: 0x10AB30EF, 0x134: 0xFF543210}
    for addr in (0x134, 0x100, 0x104):
        data = await axi_read(dut, addr)
        assert data == expected[addr], (
            f"RAM0 partial write addr=0x{addr:x}: "
            f"expected=0x{expected[addr]:08x}, actual=0x{data:08x}"
        )

    for addr in (0x104, 0x134, 0x100):
        await axi_write(dut, addr, 0xDEADBEEF, strb=0)
    for addr in (0x100, 0x134, 0x104):
        data = await axi_read(dut, addr)
        assert data == expected[addr], (
            f"RAM0 zero-strobe write addr=0x{addr:x}: "
            f"expected=0x{expected[addr]:08x}, actual=0x{data:08x}"
        )


@pytest.mark.sim
def test_ram_regs_runner():
    sim = require_simulator(os.environ, shutil.which)

    sources = [REPO_ROOT / "generated" / "axi4l" / "ram_regs.v"]
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="ram_regs",
        build_dir=REPO_ROOT / "sim_build" / "ram_regs" / sim,
        always=True,
        waves=True,
    )
    runner.test(
        hdl_toplevel="ram_regs",
        test_module="test_ram_regs",
        waves=True,
    )


if __name__ == "__main__":
    test_ram_regs_runner()
