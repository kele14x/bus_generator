#!/usr/bin/env python3
import os
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from cocotb_tools.runner import get_runner

from bus_generator import main

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


@cocotb.test(timeout_time=10, timeout_unit="us")
async def test_ram_address(dut):
    for name in (
        "s_axi_aresetn", "s_axi_awaddr", "s_axi_awprot", "s_axi_awvalid",
        "s_axi_wdata", "s_axi_wstrb", "s_axi_wvalid", "s_axi_bready",
        "s_axi_araddr", "s_axi_arprot", "s_axi_arvalid", "s_axi_rready",
        "ram0_dout", "ram0_valid",
    ):
        getattr(dut, name).value = 0

    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    await ClockCycles(dut.s_axi_aclk, 5)
    await FallingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    dut.s_axi_rready.value = 1

    entries = int(os.environ["RAM_ENTRIES"])
    base = int(os.environ["RAM_BASE"])
    assert len(dut.ram0_addr) == max(1, (entries - 1).bit_length())
    if entries == 1:
        async def check_constant_address():
            while True:
                await RisingEdge(dut.s_axi_aclk)
                await ReadOnly()
                assert int(dut.ram0_addr.value) == 0

        cocotb.start_soon(check_constant_address())

    contents = (0x12345678, 0x89ABCDEF, 0x76543210)[:entries]
    for expected_index, expected_data in enumerate(contents):
        byte_address = base + 4 * expected_index
        await FallingEdge(dut.s_axi_aclk)
        dut.s_axi_araddr.value = byte_address
        dut.s_axi_arvalid.value = 1
        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.s_axi_arready.value):
                break
        else:
            raise AssertionError("AXI AR timeout")
        await FallingEdge(dut.s_axi_aclk)
        dut.s_axi_arvalid.value = 0

        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.ram0_en.value):
                break
        else:
            raise AssertionError("RAM request timeout")
        index = int(dut.ram0_addr.value)
        assert int(dut.ram0_we.value) == 0
        assert index == expected_index, (
            f"AXI byte address 0x{byte_address:x}: expected RAM entry "
            f"{expected_index}, got {index} (RAM base is 0x{base:x})"
        )

        await FallingEdge(dut.s_axi_aclk)
        dut.ram0_dout.value = contents[index]
        dut.ram0_valid.value = 1
        await RisingEdge(dut.s_axi_aclk)
        await FallingEdge(dut.s_axi_aclk)
        dut.ram0_valid.value = 0
        dut.ram0_dout.value = 0xDEADBEEF

        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.s_axi_rvalid.value):
                assert int(dut.s_axi_rresp.value) == 0
                assert int(dut.s_axi_rdata.value) == expected_data
                break
        else:
            raise AssertionError("AXI R timeout")

    for expected_index in (entries - 1, *range(entries - 1)):
        byte_address = base + 4 * expected_index
        data = 0xA5A50000 | expected_index
        await FallingEdge(dut.s_axi_aclk)
        dut.s_axi_awaddr.value = byte_address
        dut.s_axi_awvalid.value = 1
        dut.s_axi_wdata.value = data
        dut.s_axi_wstrb.value = 0xF
        dut.s_axi_wvalid.value = 1
        dut.s_axi_bready.value = 1
        aw_pending = w_pending = True
        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.s_axi_awready.value):
                aw_pending = False
            if int(dut.s_axi_wready.value):
                w_pending = False
            await FallingEdge(dut.s_axi_aclk)
            dut.s_axi_awvalid.value = int(aw_pending)
            dut.s_axi_wvalid.value = int(w_pending)
            if not aw_pending and not w_pending:
                break
        else:
            raise AssertionError("AXI AW/W timeout")

        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.ram0_en.value):
                break
        else:
            raise AssertionError("RAM write request timeout")
        index = int(dut.ram0_addr.value)
        assert index == expected_index, (
            f"AXI write address 0x{byte_address:x}: expected RAM entry "
            f"{expected_index}, got {index} (RAM base is 0x{base:x})"
        )
        assert int(dut.ram0_we.value) == 1
        assert int(dut.ram0_din.value) == data
        assert int(dut.ram0_be.value) == 0xF

        await FallingEdge(dut.s_axi_aclk)
        dut.ram0_valid.value = 1
        await RisingEdge(dut.s_axi_aclk)
        await FallingEdge(dut.s_axi_aclk)
        dut.ram0_valid.value = 0
        for _ in range(16):
            await RisingEdge(dut.s_axi_aclk)
            if int(dut.s_axi_bvalid.value):
                assert int(dut.s_axi_bresp.value) == 0
                break
        else:
            raise AssertionError("AXI B timeout")

    unmapped = [
        address for address in (base - 4, base + 4 * entries)
        if 0 <= address < (1 << len(dut.s_axi_araddr))
    ]
    assert unmapped
    for byte_address in unmapped:
        for channels, response in ((('ar',), 'r'), (('aw', 'w'), 'b')):
            await FallingEdge(dut.s_axi_aclk)
            dut.s_axi_araddr.value = byte_address
            dut.s_axi_awaddr.value = byte_address
            pending = set(channels)
            for channel in channels:
                getattr(dut, f"s_axi_{channel}valid").value = 1
            for _ in range(16):
                await RisingEdge(dut.s_axi_aclk)
                assert int(dut.ram0_en.value) == 0
                for channel in channels:
                    if int(getattr(dut, f"s_axi_{channel}ready").value):
                        pending.discard(channel)
                await FallingEdge(dut.s_axi_aclk)
                for channel in channels:
                    getattr(dut, f"s_axi_{channel}valid").value = int(channel in pending)
                if not pending:
                    break
            else:
                raise AssertionError("Unmapped AXI request timeout")
            for _ in range(16):
                await RisingEdge(dut.s_axi_aclk)
                assert int(dut.ram0_en.value) == 0
                if int(getattr(dut, f"s_axi_{response}valid").value):
                    assert int(getattr(dut, f"s_axi_{response}resp").value) == 2
                    break
            else:
                raise AssertionError("Unmapped AXI response timeout")


@pytest.mark.sim
@pytest.mark.parametrize("entries,base", [
    pytest.param(1, 0x0, id="single-zero-base"),
    pytest.param(1, 0x4, id="single-nonzero-base"),
    pytest.param(1, 0x100, id="single-high-base"),
    pytest.param(2, 0x4, id="two-entries"),
    pytest.param(3, 0x4, id="three-entries"),
])
def test_ram_address_runner(entries, base):
    sim = os.environ["SIM"]
    runner = get_runner(sim)
    build_dir = REPO_ROOT / "sim_build" / "ram_address" / sim / f"{entries}_{base:x}"
    build_dir.mkdir(parents=True, exist_ok=True)
    rdl = build_dir / "ram_address.rdl"
    rdl.write_text(f"""addrmap ram_address {{
    external mem {{
        mementries = {entries};
        memwidth = 32;
        sw = rw;
    }} ram0 @ 0x{base:x};
}};
""")
    main([str(rdl), "-o", str(build_dir), "-t", "axi4l"])
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    runner.build(
        sources=[build_dir / "ram_address_regs.v"],
        hdl_toplevel="ram_address_regs",
        build_dir=build_dir,
        always=True,
        waves=True,
    )
    runner.test(
        hdl_toplevel="ram_address_regs",
        test_module="test_ram_address",
        test_dir=build_dir,
        extra_env={"RAM_ENTRIES": str(entries), "RAM_BASE": str(base)},
        waves=True,
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
