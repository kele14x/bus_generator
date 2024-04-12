from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.runner import get_runner
from cocotb.triggers import ClockCycles, Combine, First, RisingEdge


async def dut_reset(dut):
    dut.s_axi_aresetn.value = 0
    await ClockCycles(dut.s_axi_aclk, 16)
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 16)


async def axi_reset(dut):
    dut.s_axi_awaddr.value = 0
    dut.s_axi_awprot.value = 0
    dut.s_axi_awvalid.value = 0
    #
    dut.s_axi_wdata.value = 0
    dut.s_axi_wstrb.value = 0
    dut.s_axi_wvalid.value = 0
    #
    dut.s_axi_bready.value = 0
    #
    dut.s_axi_araddr.value = 0
    dut.s_axi_arprot.value = 0
    dut.s_axi_arvalid.value = 0
    #
    dut.s_axi_rready.value = 0
    await RisingEdge(dut.s_axi_aclk)


async def int_reset(dut):
    dut.int_wr_ack.value = 0
    dut.int_wr_err.value = 0
    dut.int_rd_ack.value = 0
    dut.int_rd_err.value = 0
    dut.int_rd_data.value = 0
    await RisingEdge(dut.s_axi_aclk)


async def reset(dut):
    c_axi = cocotb.start(axi_reset(dut))
    c_int = cocotb.start(int_reset(dut))
    c_dut = cocotb.start(dut_reset(dut))
    await Combine(c_axi, c_int, c_dut)


async def axi_write(dut, transaction: dict):
    pass


async def axi_read(dut, transaction: dict):
    pass


async def axi_aw(dut, addr: int, num_cycles: int = 0):
    """Write address to AXI AW (write address) channel."""
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.s_axi_awaddr.value = addr
    dut.s_axi_awprot.value = 0
    dut.s_axi_awvalid.value = 1
    await RisingEdge(dut.s_axi_aclk)
    while dut.s_axi_awready == 0:
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_awvalid.value = 0


async def axi_w(dut, data: int, strb: int, num_cycles: int = 0):
    """Write data to AXI W (write data) channel."""
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.s_axi_wdata.value = data
    dut.s_axi_wstrb.value = strb
    dut.s_axi_wvalid.value = 1
    await RisingEdge(dut.s_axi_aclk)
    while dut.s_axi_wready == 0:
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_wvalid.value = 0


async def axi_b(dut, num_cycles: int):
    """Get response from AXI B (write response) channel."""
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.s_axi_bready.value = 1
    await RisingEdge(dut.s_axi_aclk)
    while dut.s_axi_bvalid == 0:
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_bready.value = 0
    return dut.s_axi_bresp.value


async def axi_ar(dut, addr: int, num_cycles: int):
    """Write address to AXI AR (read address) channel."""
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.s_axi_araddr.value = addr
    dut.s_axi_arprot.value = 0
    dut.s_axi_arvalid.value = 1
    await RisingEdge(dut.s_axi_aclk)
    while dut.s_axi_arready == 0:
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_arvalid.value = 0


async def axi_r(dut, num_cycles: int):
    """Get response from AXI R (read data & response) channel."""
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.s_axi_rready.value = 1
    await RisingEdge(dut.s_axi_aclk)
    while dut.s_axi_rvalid == 0:
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_rready.value = 0
    return dut.s_axi_rdata.value, dut.s_axi_rresp.value


async def int_resp(dut, num_cycles: int):
    """Handles internal interface write/read."""
    while dut.int_wr_en.value == 0 and dut.int_rd_en.value == 0:
        d1 = RisingEdge(dut.int_wr_en)
        d2 = RisingEdge(dut.int_rd_en)
        await First(d1, d2)
    wrb = dut.int_wr_en.value
    await ClockCycles(dut.s_axi_aclk, num_cycles)
    dut.int_wr_ack.value = wrb
    dut.int_rd_ack.value = not wrb
    await RisingEdge(dut.s_axi_aclk)
    dut.int_wr_ack.value = 0
    dut.int_rd_ack.value = 0


# Tests


@cocotb.test()
async def test_simple_write(dut):
    await cocotb.start(Clock(dut.s_axi_aclk, 10, "ns").start())

    await axi_reset(dut)
    await dut_reset(dut)

    c_aw = cocotb.start_soon(axi_aw(dut, 0xAB, 0))
    c_w = cocotb.start_soon(axi_w(dut, 0x12345678, 0xF, 0))
    c_b = cocotb.start_soon(axi_b(dut, 0))
    c_int = cocotb.start_soon(int_resp(dut, 0))

    await Combine(c_aw, c_w, c_b, c_int)
    await ClockCycles(dut.s_axi_aclk, 100)


@cocotb.test()
async def test_simple_read(dut):
    await cocotb.start(Clock(dut.s_axi_aclk, 10, "ns").start())

    await axi_reset(dut)
    await dut_reset(dut)

    c_ar = cocotb.start_soon(axi_ar(dut, 0xAB, 0))
    c_r = cocotb.start_soon(axi_r(dut, 0))
    c_int = cocotb.start_soon(int_resp(dut, 0))

    await Combine(c_ar, c_r, c_int)
    await ClockCycles(dut.s_axi_aclk, 100)


@cocotb.test()
async def test_write_read_simultaneous(dut):
    await cocotb.start(Clock(dut.s_axi_aclk, 10, "ns").start())

    await axi_reset(dut)
    await dut_reset(dut)

    c_aw = cocotb.start_soon(axi_aw(dut, 0xAB, 0))
    c_w = cocotb.start_soon(axi_w(dut, 0x12345678, 0xF, 0))
    c_b = cocotb.start_soon(axi_b(dut, 0))

    c_ar = cocotb.start_soon(axi_ar(dut, 0xAB, 0))
    c_r = cocotb.start_soon(axi_r(dut, 0))

    c_int = cocotb.start_soon(int_resp(dut, 0))

    await Combine(c_aw, c_w, c_b, c_ar, c_r, c_int)
    await ClockCycles(dut.s_axi_aclk, 100)


# Runner


def test_axi4l_int_runner():
    hdl_toplevel = "axi4l_int"
    sim = "questa"

    proj_path = Path(__file__).resolve().parent

    sources = [
        proj_path / "axi4l_int.v",
    ]

    runner = get_runner(sim)
    runner.build(
        verilog_sources=sources,
        hdl_toplevel=hdl_toplevel,
        always=True,
    )

    runner.test(hdl_toplevel=hdl_toplevel, waves=True, test_module="test_axi4l_int")


if __name__ == "__main__":
    test_axi4l_int_runner()
