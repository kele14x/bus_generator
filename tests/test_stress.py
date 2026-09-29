#!/usr/bin/env python3
"""Pytest wrappers and cocotb stress tests for generated AXI4-Lite RTL.

The pytest wrappers build generated AXI4-Lite RTL for each sample RDL and select
one ``@cocotb.test`` case from this module. Cocotb owns pass/fail;
``runner.test()`` exits non-zero under pytest if the selected test fails or
times out.

Sources are read from the ``generated/`` tree so manual edits to the RTL survive
a re-run. Select Icarus, Verilator, or Questa with ``SIM=icarus``,
``SIM=verilator``, or ``SIM=questa``.
``SIM`` is required and passed directly to cocotb's runner.
Missing simulator executables or generated DUT artifacts fail rather than skip.
"""

import os
import random
import re
import sys
from collections import Counter, deque
from pathlib import Path
from types import SimpleNamespace

import cocotb
import pytest
from bus_generator.bus_generator import FieldsGatheringListener, MemGatheringListener
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, SimTimeoutError, Timer, with_timeout
from systemrdl import RDLCompiler, RDLWalker

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"
TESTS_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = REPO_ROOT / "samples"

DATA_WIDTH = 32
STRB_WIDTH = DATA_WIDTH // 8
DATA_MASK = (1 << DATA_WIDTH) - 1
STRB_MASK = (1 << STRB_WIDTH) - 1
SAMPLES = [
    pytest.param("gpio", id="gpio"),
    pytest.param("mem_access", id="mem_access"),
    pytest.param("ram", id="ram"),
    pytest.param("simple", id="simple"),
    pytest.param("wstrb", id="wstrb"),
]


def _random_wstrb():
    return random.getrandbits(STRB_WIDTH)


def _wstrb_to_mask(wstrb, width=DATA_WIDTH):
    mask = 0
    for byte in range((width + 7) // 8):
        if wstrb & (1 << byte):
            mask |= 0xFF << (byte * 8)
    return mask & ((1 << width) - 1)

MAX_IDLE = 4
MAX_BP = 4
SEED = 0xC0FFEE

MAX_IDLE_B = 3
MAX_BP_GAP = 16
SEED_B = 0xBEEF
SEED_R = 0x1234
SEED_MIXED = 0xACE5

MEM_READ_LATENCY_MIN = 1
MEM_READ_LATENCY_MAX = 6


def _initial_memory(mem, number):
    mask = (1 << mem["width"]) - 1
    return [
        (0x13579BDF ^ ((number + 1) * 0x10203040) ^ (index * 0x01010101)) & mask
        for index in range(mem["mementries"])
    ]


class RdlStressModel:
    def __init__(self, top):
        fields, mems = _load_rdl_metadata(top)
        self.regs = {}
        # Initialize before the external models copy these reference contents.
        self.mems = {m["name"]: _initial_memory(m, i) for i, m in enumerate(mems)}
        self.hw_fields = [f for f in fields if f["is_hw_writable"]]
        self.mem_specs = mems

        for field in fields:
            addr = field["address"]
            reg = self.regs.setdefault(
                addr,
                {"value": 0, "read_mask": 0, "write_mask": 0},
            )
            reset = (field["reset"] << field["low"]) & field["mask"]
            reg["value"] = (reg["value"] & ~field["mask"]) | reset
            if field["is_sw_readable"]:
                reg["read_mask"] |= field["mask"]
            if field["is_sw_writable"]:
                reg["write_mask"] |= field["mask"]

        self.read_ops = []
        self.write_ops = []
        for addr, reg in sorted(self.regs.items()):
            if reg["read_mask"]:
                self.read_ops.append({"kind": "reg", "addr": addr})
            if reg["write_mask"]:
                self.write_ops.append({"kind": "reg", "addr": addr})

        for mem in mems:
            for idx in range(mem["mementries"]):
                op = {"kind": "mem", "addr": mem["address"] + idx * 4, "mem": mem, "idx": idx}
                if mem["is_sw_readable"]:
                    self.read_ops.append(op)
                if mem["is_sw_writable"]:
                    self.write_ops.append(op)

    def write(self, op, data, wstrb, dut):
        data &= DATA_MASK
        wstrb &= STRB_MASK
        if op["kind"] == "reg":
            reg = self.regs[op["addr"]]
            write_mask = reg["write_mask"] & _wstrb_to_mask(wstrb)
            reg["value"] = (reg["value"] & ~write_mask) | (data & write_mask)
            self.drive_hw_inputs(dut)
        else:
            mem = op["mem"]
            mask = _wstrb_to_mask(wstrb, mem["width"])
            value = self.mems[mem["name"]][op["idx"]]
            self.mems[mem["name"]][op["idx"]] = (value & ~mask) | (data & mask)

    def expected_read(self, op):
        if op["kind"] == "reg":
            reg = self.regs[op["addr"]]
            return reg["value"] & reg["read_mask"], reg["read_mask"]
        mem = op["mem"]
        mask = (1 << mem["width"]) - 1
        return self.mems[mem["name"]][op["idx"]] & mask, mask

    def drive_hw_inputs(self, dut):
        for field in self.hw_fields:
            sig = getattr(dut, f"{field['name']}_in", None)
            if sig is None:
                continue
            value = (self.regs[field["address"]]["value"] & field["mask"]) >> field["low"]
            sig.value = value


class ExternalMemoryModel:
    """FIFO RAM with independent storage and a minimum one-cycle response latency."""

    def __init__(self, dut, mem, values, *, invalid_data=0xDEADBEEF,
                 latency=(MEM_READ_LATENCY_MIN, MEM_READ_LATENCY_MAX)):
        self.clk = dut.s_axi_aclk
        self.resetn = dut.s_axi_aresetn
        self.addr = getattr(dut, f"{mem['name']}_addr")
        self.en = getattr(dut, f"{mem['name']}_en")
        self.we = getattr(dut, f"{mem['name']}_we")
        self.be = getattr(dut, f"{mem['name']}_be")
        self.din = getattr(dut, f"{mem['name']}_din")
        self.dout = getattr(dut, f"{mem['name']}_dout")
        self.valid = getattr(dut, f"{mem['name']}_valid")
        self.values = list(values)
        self.mask = (1 << mem["width"]) - 1
        self.invalid_data = invalid_data
        self.latency = latency
        assert 1 <= latency[0] <= latency[1]
        self.hold = False
        self.response_budget = None
        self.pending = deque()
        seed = SEED ^ sum(ord(c) for c in mem["name"])
        self.random = random.Random(seed)

    async def run(self):
        cycle = 0
        inactive = 0 if self.invalid_data is None else self.invalid_data & self.mask
        self.dout.value = inactive
        self.valid.value = 0
        while True:
            await RisingEdge(self.clk)
            if not int(self.resetn.value):
                self.pending.clear()
                self.dout.value = inactive
                self.valid.value = 0
                cycle = 0
                continue

            cycle += 1
            if int(self.en.value):
                addr = int(self.addr.value)
                assert 0 <= addr < len(self.values), f"RAM address out of bounds: {addr}"
                payload = self.values[addr] & self.mask  # Read BEFORE this write.
                latency = self.random.randint(*self.latency)
                self.pending.append((cycle + latency - 1, payload))
                if int(self.we.value):
                    mask = _wstrb_to_mask(int(self.be.value), self.mask.bit_length())
                    self.values[addr] = (payload & ~mask) | (int(self.din.value) & mask)

            self.valid.value = 0
            if self.invalid_data is not None:
                self.dout.value = self.invalid_data & self.mask
            # Inspect only the oldest request, never a younger ready request.
            if (self.pending and self.pending[0][0] <= cycle and not self.hold
                    and self.response_budget != 0):
                _, payload = self.pending.popleft()
                self.dout.value = payload
                self.valid.value = 1
                if self.response_budget is not None:
                    self.response_budget -= 1


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
            await RisingEdge(self.clk)
            if int(valid_sig.value) == 1 and int(ready_sig.value) == 1:
                valid_sig.value = 0
                break

    async def _recv(self, valid_sig, ready_sig, read_payload):
        policy = random.choice(["early", "late"])
        ready_sig.value = 1 if policy == "early" else 0
        while True:
            await RisingEdge(self.clk)
            valid = int(valid_sig.value) == 1
            ready = int(ready_sig.value) == 1
            if valid and ready:
                payload = read_payload()
                ready_sig.value = 0
                return payload
            if valid and policy == "late":
                for _ in range(random.randint(0, MAX_BP)):
                    await RisingEdge(self.clk)
                ready_sig.value = 1

    async def _drive_aw(self, addr):
        await self._idle()
        self.dut.s_axi_awaddr.value = addr
        self.dut.s_axi_awprot.value = 0
        self.dut.s_axi_awvalid.value = 1
        await self._send(self.dut.s_axi_awvalid, self.dut.s_axi_awready)

    async def _drive_w(self, data, wstrb):
        await self._idle()
        self.dut.s_axi_wdata.value = data
        self.dut.s_axi_wstrb.value = wstrb
        self.dut.s_axi_wvalid.value = 1
        await self._send(self.dut.s_axi_wvalid, self.dut.s_axi_wready)

    async def write(self, addr, data, wstrb):
        aw_task = cocotb.start_soon(self._drive_aw(addr))
        w_task = cocotb.start_soon(self._drive_w(data, wstrb))
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
        self.write_count = 0

    async def _idle(self):
        for _ in range(random.randint(0, MAX_IDLE_B)):
            await RisingEdge(self.clk)

    async def _send(self, valid_sig, ready_sig):
        while True:
            await RisingEdge(self.clk)
            if int(valid_sig.value) == 1 and int(ready_sig.value) == 1:
                valid_sig.value = 0
                break

    async def _drive_aw(self, addr):
        await self._idle()
        self.dut.s_axi_awaddr.value = addr
        self.dut.s_axi_awprot.value = 0
        self.dut.s_axi_awvalid.value = 1
        await self._send(self.dut.s_axi_awvalid, self.dut.s_axi_awready)

    async def _drive_w(self, data, wstrb):
        await self._idle()
        self.dut.s_axi_wdata.value = data
        self.dut.s_axi_wstrb.value = wstrb
        self.dut.s_axi_wvalid.value = 1
        await self._send(self.dut.s_axi_wvalid, self.dut.s_axi_wready)

    async def issue_write(self, addr, data, wstrb):
        if random.random() < 0.5:
            await self._drive_aw(addr)
            await self._drive_w(data, wstrb)
        else:
            await self._drive_w(data, wstrb)
            await self._drive_aw(addr)
        self.write_count += 1

    async def issue_write_aw_first(self, addr, data, wstrb):
        await self._drive_aw(addr)
        await self._drive_w(data, wstrb)
        self.write_count += 1

    async def b_drain(self, expected):
        self.dut.s_axi_bready.value = 0
        gap = random.randint(0, MAX_BP_GAP)
        while self.b_count < expected:
            await RisingEdge(self.clk)
            bvalid = int(self.dut.s_axi_bvalid.value)
            bready = int(self.dut.s_axi_bready.value)
            if bvalid == 1 and bready == 1:
                bresp = int(self.dut.s_axi_bresp.value)
                self.b_count += 1
                if bresp != 0:
                    self.b_errors += 1
                    self.dut._log.error(
                        f"B[{self.b_count}] bresp={bresp}, expected 0"
                    )
                self.dut.s_axi_bready.value = 0
                gap = random.randint(0, MAX_BP_GAP)
            elif gap > 0:
                gap -= 1
            else:
                self.dut.s_axi_bready.value = 1

    async def read(self, addr):
        await self._idle()
        self.dut.s_axi_araddr.value = addr
        self.dut.s_axi_arprot.value = 0
        self.dut.s_axi_arvalid.value = 1
        await self._send(self.dut.s_axi_arvalid, self.dut.s_axi_arready)
        self.dut.s_axi_rready.value = 1
        while True:
            await RisingEdge(self.clk)
            if (
                int(self.dut.s_axi_rvalid.value) == 1
                and int(self.dut.s_axi_rready.value) == 1
            ):
                rdata = int(self.dut.s_axi_rdata.value)
                rresp = int(self.dut.s_axi_rresp.value)
                self.dut.s_axi_rready.value = 0
                return rdata, rresp


class PipelinedReadMaster:
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
        self.r_count = 0
        self.r_errors = 0
        self.read_count = 0

    async def _idle(self):
        for _ in range(random.randint(0, MAX_IDLE_B)):
            await RisingEdge(self.clk)

    async def issue_read(self, addr):
        await self._idle()
        self.dut.s_axi_araddr.value = addr
        self.dut.s_axi_arprot.value = 0
        self.dut.s_axi_arvalid.value = 1
        while True:
            await RisingEdge(self.clk)
            if (
                int(self.dut.s_axi_arvalid.value) == 1
                and int(self.dut.s_axi_arready.value) == 1
            ):
                self.dut.s_axi_arvalid.value = 0
                self.read_count += 1
                break

    async def r_drain(self, expected, count):
        self.dut.s_axi_rready.value = 0
        while self.r_count < count:
            for _ in range(random.randint(0, MAX_BP_GAP)):
                await RisingEdge(self.clk)
            self.dut.s_axi_rready.value = 1
            while True:
                await RisingEdge(self.clk)
                if (
                    int(self.dut.s_axi_rvalid.value) == 1
                    and int(self.dut.s_axi_rready.value) == 1
                ):
                    rdata = int(self.dut.s_axi_rdata.value)
                    rresp = int(self.dut.s_axi_rresp.value)
                    expected_data, mask, addr = expected[self.r_count]
                    self.r_count += 1
                    if rresp != 0 or (rdata & mask) != expected_data:
                        self.r_errors += 1
                        self.dut._log.error(
                            f"R[{self.r_count}] addr=0x{addr:02x} data=0x{rdata:08x} "
                            f"expected=0x{expected_data:08x} mask=0x{mask:08x} resp={rresp}"
                        )
                    self.dut.s_axi_rready.value = 0
                    break


def _load_rdl_metadata(top):
    rdlc = RDLCompiler()
    rdlc.compile_file(str(SAMPLES_DIR / f"{top}.rdl"))
    root = rdlc.elaborate()
    walker = RDLWalker(unroll=True)

    field_listener = FieldsGatheringListener()
    walker.walk(root.top, field_listener)

    mem_listener = MemGatheringListener()
    walker.walk(root.top, mem_listener)

    return field_listener.fields, mem_listener.mems


def _stress_top(dut):
    top = os.environ.get("STRESS_TOP")
    if top:
        return top
    name = str(dut._name)
    return name[: -len("_regs")] if name.endswith("_regs") else name


def _start_memory_models(dut, model):
    tasks = []
    for mem in model.mem_specs:
        memory = ExternalMemoryModel(dut, mem, model.mems[mem["name"]])
        tasks.append(cocotb.start_soon(memory.run()))
    return tasks


async def _setup_stress(dut, seed, master_cls):
    random.seed(seed)
    top = _stress_top(dut)
    model = RdlStressModel(top)
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, units="ns").start())
    _start_memory_models(dut, model)
    dut.s_axi_aresetn.value = 0
    master = master_cls(dut)
    model.drive_hw_inputs(dut)
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.s_axi_aclk)
    await RisingEdge(dut.s_axi_aclk)
    model.drive_hw_inputs(dut)
    return top, model, master


async def _check_readback(dut, master, model):
    errors = 0
    for op in model.read_ops:
        rdata, rresp = await master.read(op["addr"])
        expected, mask = model.expected_read(op)
        if rresp != 0 or (rdata & mask) != expected:
            errors += 1
            dut._log.error(
                f"readback addr=0x{op['addr']:02x} data=0x{rdata:08x} "
                f"expected=0x{expected:08x} mask=0x{mask:08x} resp={rresp}"
            )
    return errors


async def _check_memory_read_timing(dut, *, invalid_data):
    random.seed(SEED_R)
    fields, mems = _load_rdl_metadata(_stress_top(dut))
    dut.s_axi_aresetn.value = 0
    master = AxiLiteMaster(dut)
    for field in fields:
        if field["is_hw_writable"]:
            getattr(dut, f"{field['name']}_in").value = 0

    expected_reads = []
    for mem_index, mem in enumerate(mems):
        mask = (1 << mem["width"]) - 1
        values = [
            (0x12345678 + mem_index * 0x10000 + index) & mask
            for index in range(mem["mementries"])
        ]
        memory = ExternalMemoryModel(dut, mem, values, invalid_data=invalid_data)
        cocotb.start_soon(memory.run())
        if mem["is_sw_readable"]:
            for index in (0, mem["mementries"] - 1):
                expected_reads.append((mem["address"] + index * 4, values[index]))

    assert expected_reads
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.s_axi_aclk)

    for address, expected in expected_reads:
        data, response = await master.read(address)
        assert response == 0, f"addr=0x{address:x}: unexpected RRESP={response}"
        assert data == expected, (
            f"addr=0x{address:x}: expected=0x{expected:08x}, actual=0x{data:08x}, "
            f"invalid_data={invalid_data!r}"
        )


@cocotb.test(timeout_time=10, timeout_unit="us")
async def memory_read_held_data(dut):
    await _check_memory_read_timing(dut, invalid_data=None)


@cocotb.test(timeout_time=10, timeout_unit="us")
async def memory_read_valid_pulse(dut):
    await _check_memory_read_timing(dut, invalid_data=0xDEADBEEF)


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def memory_write_scoreboard(dut):
    """Readback must detect omitted/corrupted writes without changing expectations."""
    _, model, master = await _setup_stress(dut, SEED, AxiLiteMaster)
    ops = [op for op in model.write_ops if op["kind"] == "mem"
           and op["mem"]["is_sw_readable"]
           and op["idx"] in (0, op["mem"]["mementries"] - 1)]
    assert ops
    assert await _check_readback(dut, master, model) == 0

    for op in ops:
        for wstrb in (STRB_MASK, 0x5, 0xA, 0):
            previous, _ = model.expected_read(op)
            data = previous ^ DATA_MASK
            model.write(op, data, wstrb, dut)
            expected = model.expected_read(op)
            assert (expected[0] != previous) == bool(wstrb)

            # No AXI write: an expected update must not reach physical RAM.
            assert await _check_readback(dut, master, model) == int(bool(wstrb))
            if wstrb:
                byte = (wstrb & -wstrb).bit_length() - 1
                corrupted = data ^ (1 << (8 * byte))
                assert await master.write(op["addr"], corrupted, wstrb) == 0
                assert model.expected_read(op) == expected
                assert await _check_readback(dut, master, model) == 1

            assert await master.write(op["addr"], data, wstrb) == 0
            assert model.expected_read(op) == expected
            assert await _check_readback(dut, master, model) == 0


@cocotb.test(timeout_time=1, timeout_unit="ms")
async def stress_random_axi(dut):
    """Random read/write traffic with randomized AXI handshaking + checker."""
    top, model, master = await _setup_stress(dut, SEED, AxiLiteMaster)

    count = int(os.environ.get("STRESS_COUNT", "200"))
    errors = 0

    for i in range(count):
        do_write = bool(model.write_ops) and (not model.read_ops or random.random() < 0.5)
        if do_write:
            op = random.choice(model.write_ops)
            data = random.getrandbits(DATA_WIDTH)
            wstrb = _random_wstrb()
            model.write(op, data, wstrb, dut)
            bresp = await master.write(op["addr"], data, wstrb)
            if bresp != 0:
                errors += 1
                dut._log.error(
                    f"[{i}] write addr=0x{op['addr']:02x} wstrb=0x{wstrb:x} "
                    f"got bresp={bresp}, expected 0"
                )
        else:
            op = random.choice(model.read_ops)
            rdata, rresp = await master.read(op["addr"])
            expected, mask = model.expected_read(op)
            if (rdata & mask) != expected or rresp != 0:
                errors += 1
                dut._log.error(
                    f"[{i}] read  addr=0x{op['addr']:02x} data=0x{rdata:08x} "
                    f"expected=0x{expected:08x} mask=0x{mask:08x} resp={rresp}"
                )

    assert errors == 0, f"{errors}/{count} mismatches"
    dut._log.info(f"{top} stress passed: {count} transactions, 0 mismatches")


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def stress_write_overlap(dut):
    top, model, master = await _setup_stress(dut, SEED_B, PipelinedWriteMaster)

    count = int(os.environ.get("STRESS_B_COUNT", "64"))
    writes = []
    for _ in range(count):
        op = random.choice(model.write_ops)
        writes.append((op, random.getrandbits(DATA_WIDTH), _random_wstrb()))

    dut._log.info(f"issuing {count} {top} overlapped writes with B backpressure")

    drain_task = cocotb.start_soon(master.b_drain(count))
    for op, data, wstrb in writes:
        model.write(op, data, wstrb, dut)
        await master.issue_write(op["addr"], data, wstrb)
    await drain_task

    errors = master.b_errors
    if master.b_count != count:
        errors += 1
        dut._log.error(f"B count mismatch: received {master.b_count}, expected {count}")

    errors += await _check_readback(dut, master, model)

    assert errors == 0, (
        f"{errors} errors (b_errors={master.b_errors}, b_count={master.b_count})"
    )
    dut._log.info(
        f"{top} write-overlap stress passed: {count} writes, "
        f"{master.b_count} B responses, 0 errors"
    )


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def stress_read_overlap(dut):
    top, model, master = await _setup_stress(dut, SEED_R, PipelinedReadMaster)

    initializer = AxiLiteMaster(dut)
    for number, op in enumerate(op for op in model.write_ops if op["kind"] == "reg"):
        value = (0x89ABCDEF ^ (number * 0x10204081)) & DATA_MASK
        assert await initializer.write(op["addr"], value, STRB_MASK) == 0
        model.write(op, value, STRB_MASK, dut)

    count = int(os.environ.get("STRESS_R_COUNT", "64"))
    reads = [random.choice(model.read_ops) for _ in range(count)]
    expected = []
    for op in reads:
        data, mask = model.expected_read(op)
        expected.append((data, mask, op["addr"]))

    dut._log.info(f"issuing {count} {top} overlapped reads with R backpressure")

    drain_task = cocotb.start_soon(master.r_drain(expected, count))
    for op in reads:
        await master.issue_read(op["addr"])
    await drain_task

    assert master.r_errors == 0, (
        f"{master.r_errors} errors (r_count={master.r_count}, expected={count})"
    )
    dut._log.info(f"{top} read-overlap stress passed: {count} reads, 0 errors")


@cocotb.test(timeout_time=3, timeout_unit="ms")
async def stress_mixed_overlap(dut):
    random.seed(SEED_MIXED)
    top = _stress_top(dut)
    model = RdlStressModel(top)
    cocotb.start_soon(Clock(dut.s_axi_aclk, 10, units="ns").start())
    _start_memory_models(dut, model)
    dut.s_axi_aresetn.value = 0
    write_master = PipelinedWriteMaster(dut)
    read_master = PipelinedReadMaster(dut)
    model.drive_hw_inputs(dut)
    for _ in range(10):
        await RisingEdge(dut.s_axi_aclk)
    dut.s_axi_aresetn.value = 1
    await RisingEdge(dut.s_axi_aclk)
    await RisingEdge(dut.s_axi_aclk)
    model.drive_hw_inputs(dut)

    addrs = sorted({op["addr"] for op in model.read_ops} & {op["addr"] for op in model.write_ops})
    write_addrs = set(addrs[::2])
    if not write_addrs or write_addrs == set(addrs):
        write_addrs = set(addrs[:1])
    write_ops = [op for op in model.write_ops if op["addr"] in write_addrs]
    read_ops = [op for op in model.read_ops if op["addr"] not in write_addrs]
    if not read_ops:
        read_ops = model.read_ops

    write_count = int(os.environ.get("STRESS_MIXED_W_COUNT", "48"))
    read_count = int(os.environ.get("STRESS_MIXED_R_COUNT", "48"))
    writes = [
        (random.choice(write_ops), random.getrandbits(DATA_WIDTH), _random_wstrb())
        for _ in range(write_count)
    ]
    reads = [random.choice(read_ops) for _ in range(read_count)]
    expected_reads = []
    for op in reads:
        data, mask = model.expected_read(op)
        expected_reads.append((data, mask, op["addr"]))

    dut._log.info(
        f"issuing {top} mixed overlap: {write_count} writes, {read_count} reads"
    )

    b_drain_task = cocotb.start_soon(write_master.b_drain(write_count))
    r_drain_task = cocotb.start_soon(read_master.r_drain(expected_reads, read_count))

    async def issue_writes():
        for op, data, wstrb in writes:
            model.write(op, data, wstrb, dut)
            await write_master.issue_write_aw_first(op["addr"], data, wstrb)

    async def issue_reads():
        for op in reads:
            await read_master.issue_read(op["addr"])

    write_task = cocotb.start_soon(issue_writes())
    read_task = cocotb.start_soon(issue_reads())
    try:
        await with_timeout(write_task, 100, "us")
        await with_timeout(read_task, 100, "us")
        await with_timeout(b_drain_task, 100, "us")
        await with_timeout(r_drain_task, 100, "us")
    except SimTimeoutError:
        dut._log.error(
            "mixed overlap stalled: "
            f"writes issued={write_master.write_count}/{write_count}, "
            f"B received={write_master.b_count}/{write_count}, "
            f"reads issued={read_master.read_count}/{read_count}, "
            f"R received={read_master.r_count}/{read_count}, "
            f"AW valid/ready={int(dut.s_axi_awvalid.value)}/{int(dut.s_axi_awready.value)}, "
            f"W valid/ready={int(dut.s_axi_wvalid.value)}/{int(dut.s_axi_wready.value)}, "
            f"B valid/ready={int(dut.s_axi_bvalid.value)}/{int(dut.s_axi_bready.value)}, "
            f"AR valid/ready={int(dut.s_axi_arvalid.value)}/{int(dut.s_axi_arready.value)}, "
            f"R valid/ready={int(dut.s_axi_rvalid.value)}/{int(dut.s_axi_rready.value)}"
        )
        raise

    errors = write_master.b_errors + read_master.r_errors
    if write_master.b_count != write_count:
        errors += 1
        dut._log.error(
            f"B count mismatch: received {write_master.b_count}, expected {write_count}"
        )
    if read_master.r_count != read_count:
        errors += 1
        dut._log.error(
            f"R count mismatch: received {read_master.r_count}, expected {read_count}"
        )

    errors += await _check_readback(dut, write_master, model)

    assert errors == 0, (
        f"{errors} errors (b_errors={write_master.b_errors}, "
        f"r_errors={read_master.r_errors})"
    )
    dut._log.info(
        f"{top} mixed-overlap stress passed: {write_count} writes, "
        f"{read_count} reads, 0 errors"
    )


def _monitor_widths(mems, address_width):
    """Export checked internals through wrapper ports, even under Verilator."""
    widths = {name: 1 for name in (
        "int_issue int_valid int_write arb_ready arb_read_priority "
        "arb_grant_read arb_grant_write b_credit r_credit int_idle "
        "int_rd_en int_wr_en int_rd_ack int_wr_ack int_rd_err "
        "int_wr_err local_rd_ack local_wr_ack local_rd_err local_wr_err "
        "b_fifo_idx r_fifo_idx"
    ).split()}
    widths.update({name: 2 for name in (
        "b_outstanding r_outstanding b_wait_ack r_wait_ack b_fifo_count r_fifo_count "
        "b_err_fifo r_err_fifo ar_fifo_count aw_fifo_count w_fifo_count"
    ).split()})
    widths.update(int_addr=address_width, int_wr_data=32, int_wr_strb=4,
                  int_rd_data=32, local_rd_data=32, r_data_fifo=2 * DATA_WIDTH,
                  int_target=len(mems) + 1, int_active_target=len(mems) + 1)
    for mem in mems:
        prefix = mem["name"] + "_"
        widths.update({prefix + name: 1 for name in (
            "tag_empty tag_bypass tag_push tag_pop response response_we "
            "rd_done wr_done rd_ack wr_ack"
        ).split()})
        widths.update({prefix + "tag_fifo": 4, prefix + "tag_fifo_idx": 2,
                       prefix + "tag_fifo_count": 3, prefix + "rd_data": 32})
    return widths


class RamContractBench:
    """Validate physical issue order against independently submitted AXI contents."""

    def __init__(self, dut, *, latency=(1, 6), combinational=False):
        self.dut = dut
        self.reference = RdlStressModel(_stress_top(dut))
        self.mems = self.reference.mem_specs
        assert self.mems, "directed RAM coverage requires memories"
        self.widths = _monitor_widths(self.mems, len(dut.s_axi_araddr))
        self.memories = {}
        self.tasks = []
        if not combinational:
            for mem in self.mems:
                model = ExternalMemoryModel(
                    dut, mem, self.reference.mems[mem["name"]], latency=latency)
                self.memories[mem["name"]] = model
                self.tasks.append(cocotb.start_soon(model.run()))
        self.cover = Counter()
        self.transitions = set()
        self.tag_pairs = set()
        self.tag_transitions = set()
        self.latencies = set()
        self.last_tag = {}
        self.last_target = None
        self.cycle = 0
        self.submitted = self.retired = self.aborted = 0
        self.bready = self.rready = False
        self._clear_queues()
        AxiLiteMaster(dut)
        dut.s_axi_aresetn.value = 0
        self.reference.drive_hw_inputs(dut)
        self.clock = cocotb.start_soon(Clock(dut.s_axi_aclk, 10, unit="ns").start())

    def _clear_queues(self):
        self.drive = {ch: deque() for ch in ("aw", "w", "ar")}
        self.requests = {ch: deque() for ch in ("aw", "w", "ar")}
        self.expected = {ch: deque() for ch in ("r", "w")}
        self.responses = {ch: deque() for ch in ("r", "w")}
        self.inflight = {ch: deque() for ch in ("r", "w")}
        self.buffered = {ch: deque() for ch in ("r", "w")}
        self.tags = {mem["name"]: deque() for mem in self.mems}
        self.drained_tags = set()
        self.next_acks = {}
        self.stalled = {}
        self.last_response_cycle = {}

    def submit(self, write, addr, data=0, strb=STRB_MASK):
        tx = dict(write=bool(write), addr=addr, data=data & DATA_MASK,
                  strb=strb & STRB_MASK, accepted=set(), issued=False)
        ch = "w" if write else "r"
        self.expected[ch].append(tx)
        self.responses[ch].append(tx)
        for bus in (("aw", "w") if write else ("ar",)):
            self.drive[bus].append(tx)
        self.submitted += 1
        return tx

    def _sample(self):
        storage = {"b_err_fifo": ("b_fifo_count", 1),
                   "r_err_fifo": ("r_fifo_count", 1),
                   "r_data_fifo": ("r_fifo_count", DATA_WIDTH)}
        storage.update({m["name"] + "_tag_fifo": (m["name"] + "_tag_fifo_count", 1)
                        for m in self.mems})
        sample = {name: int(getattr(self.dut, "mon_" + name).value)
                  for name in self.widths if name not in storage}
        # Ignore unoccupied SRL bits, but fail on any X in an occupied entry.
        for name, (count, width) in storage.items():
            bits = sample[count] * width
            assert 0 <= bits <= self.widths[name], f"invalid FIFO occupancy: {count}"
            sample[name] = (int(str(getattr(self.dut, "mon_" + name).value)[-bits:], 2)
                            if bits else 0)
        for suffix in ("awvalid awready wvalid wready arvalid arready "
                       "bvalid bready rvalid rready").split():
            sample["axi_" + suffix] = int(getattr(self.dut, "s_axi_" + suffix).value)
        for suffix in ("bresp", "rresp", "rdata"):
            sample["axi_" + suffix] = (int(getattr(self.dut, "s_axi_" + suffix).value)
                                       if sample["axi_" + suffix[0] + "valid"] else 0)
        for mem in self.mems:
            for suffix in ("en", "we", "addr", "din", "be", "valid", "dout"):
                name = mem["name"] + "_" + suffix
                sample[name] = int(getattr(self.dut, name).value)
        return sample

    async def step(self, *, reset=False):
        await FallingEdge(self.dut.s_axi_aclk)
        self.dut.s_axi_aresetn.value = int(not reset)
        for bus in ("aw", "w", "ar"):
            queue = self.drive[bus]
            getattr(self.dut, "s_axi_" + bus + "valid").value = int(bool(queue) and not reset)
            if queue and not reset:
                tx = queue[0]
                if bus == "w":
                    self.dut.s_axi_wdata.value = tx["data"]
                    self.dut.s_axi_wstrb.value = tx["strb"]
                else:
                    getattr(self.dut, "s_axi_" + bus + "addr").value = tx["addr"]
        self.dut.s_axi_bready.value = int(self.bready and not reset)
        self.dut.s_axi_rready.value = int(self.rready and not reset)
        # Sample before the edge to avoid races with RTL and RAM response updates.
        await Timer(1, unit="ns")
        sample = None if reset else self._sample()
        await RisingEdge(self.dut.s_axi_aclk)
        if sample is not None:
            self.cycle += 1
            self._check_edge(sample)
        await Timer(1, unit="ns")

    async def until(self, predicate, description, limit=300):
        for _ in range(limit):
            if predicate():
                return
            await self.step()
        raise AssertionError(f"timeout waiting for {description}; coverage={self.cover}")

    async def issue(self, write, addr, data=0, strb=STRB_MASK):
        tx = self.submit(write, addr, data, strb)
        await self.until(lambda: tx["issued"], f"physical/local issue of {tx}")
        return tx

    async def reset(self):
        self.aborted += sum(map(len, self.responses.values()))
        self._clear_queues()
        self.last_target = None
        self.last_tag.clear()
        # RAM storage survives reset, but registers and ALL pending work do not.
        fresh = RdlStressModel(_stress_top(self.dut))
        self.reference.regs = fresh.regs
        self.reference.drive_hw_inputs(self.dut)
        for _ in range(3):
            await self.step(reset=True)
        state = self._sample()
        for name in ("b_outstanding r_outstanding b_wait_ack r_wait_ack "
                     "b_fifo_count r_fifo_count int_rd_ack int_wr_ack "
                     "ar_fifo_count aw_fifo_count w_fifo_count "
                     "local_rd_ack local_wr_ack local_rd_data axi_bvalid axi_rvalid").split():
            assert state[name] == 0, f"reset did not clear {name}"
        for mem in self.mems:
            name = mem["name"]
            for suffix in ("tag_fifo_count", "rd_ack", "wr_ack", "rd_data"):
                assert state[name + "_" + suffix] == 0, f"reset did not clear {name}_{suffix}"
            if name in self.memories:
                assert not self.memories[name].pending
        await self.step()

    async def drain(self):
        self.bready = self.rready = True
        for memory in self.memories.values():
            memory.hold = False
            memory.response_budget = None
        await self.until(lambda: not any(self.responses.values()), "all AXI completions")
        # Detect late duplicate/unsolicited completions, not just missing ones.
        for _ in range(8):
            await self.step()
        assert not any(self.expected.values()) and not any(self.drive.values())
        assert not any(self.requests.values())
        assert not any(self.inflight.values()) and not any(self.buffered.values())
        assert not any(self.tags.values()) and not self.next_acks
        assert self.retired + self.aborted == self.submitted
        for name, memory in self.memories.items():
            assert not memory.pending
            assert memory.values == self.reference.mems[name]

    def _decode(self, tx):
        addr, write = tx["addr"], tx["write"]
        for i, mem in enumerate(self.mems):
            if mem["address"] <= addr < mem["address"] + 4 * mem["mementries"]:
                permitted = mem["is_sw_writable" if write else "is_sw_readable"]
                physical = permitted and (not write or tx["strb"] != 0)
                return (1 << (i + 1)) if physical else 1, mem, (addr - mem["address"]) // 4, not permitted
        reg = self.reference.regs.get(addr)
        permitted = reg and reg["write_mask" if write else "read_mask"]
        return 1, None, None, not permitted

    def _check_edge(self, s):
        old_wait = sum(len(q) for q in self.inflight.values())
        active_targets = {tx["target"] for queue in self.inflight.values() for tx in queue}
        assert s["int_idle"] == (old_wait == 0)
        if old_wait:
            assert active_targets == {s["int_active_target"]}
        # Derive the lock permission from submitted requests and uncaptured work.
        target_allowed = not old_wait
        if s["int_valid"]:
            ch = "w" if s["int_write"] else "r"
            assert self.expected[ch], "unexpected internal request"
            request_target, _, _, _ = self._decode(self.expected[ch][0])
            assert s["int_target"] == request_target
            target_allowed = not old_wait or request_target in active_targets
        assert s["int_issue"] == bool(s["int_valid"] and target_allowed
                                      and s["b_credit" if s["int_write"] else "r_credit"])
        for ch, prefix in (("w", "b"), ("r", "r")):
            wait, pending = len(self.inflight[ch]), len(self.buffered[ch])
            assert s[prefix + "_wait_ack"] == wait
            assert s[prefix + "_fifo_count"] == pending
            assert s[prefix + "_fifo_idx"] == ((pending - 1) & 1)
            assert s[prefix + "_outstanding"] == wait + pending <= 2
            assert s["axi_" + prefix + "valid"] == bool(pending)
            newest_first = list(reversed(self.buffered[ch]))
            assert s[prefix + "_err_fifo"] == sum(
                (tx["resp"] != 0) << slot for slot, tx in enumerate(newest_first))
            if ch == "r":
                assert s["r_data_fifo"] == sum(
                    tx["result"] << (slot * DATA_WIDTH) for slot, tx in enumerate(newest_first))

        for bus, queue in self.requests.items():
            assert s[bus + "_fifo_count"] == len(queue) <= 2
            assert s["axi_" + bus + "ready"] == (len(queue) < 2)

        # Check KI02 at the arbitration boundary, not by bypassing a blocked head.
        read_waiting = bool(self.requests["ar"])
        write_waiting = bool(self.requests["aw"] and self.requests["w"])
        for preferred, other, bit in (("read", "write", 1), ("write", "read", 0)):
            pc, oc = ("r", "b") if preferred == "read" else ("b", "r")
            if (s["arb_ready"] and read_waiting and write_waiting
                    and s["arb_read_priority"] == bit and not s[pc + "_credit"] and s[oc + "_credit"]):
                assert s["arb_grant_" + other] and not s["arb_grant_" + preferred], "KI02 credit veto"
                self.cover["eligible_" + other] += 1

        # ACKs must be exactly last edge's source completion, not merely onehot.
        sources = ["local"] + [m["name"] for m in self.mems]
        for ch, kind in (("r", "rd"), ("w", "wr")):
            active = [name for name in sources if s[name + "_" + kind + "_ack"]]
            expected_sources = [name for name, channel in self.next_acks if channel == ch]
            assert len(active) <= 1 and active == expected_sources, (active, expected_sources)
            assert s["int_" + kind + "_ack"] == bool(active)
            if active:
                tx = self.next_acks[(active[0], ch)]
                assert self.inflight[ch] and self.inflight[ch][0] is tx, "completion reordered/duplicated"
                assert s["int_" + kind + "_err"] == (tx["resp"] != 0)
                if ch == "r":
                    assert s[active[0] + "_rd_data"] == tx["result"]
                    assert s["int_rd_data"] == tx["result"], "ACK/data misalignment or unmasked source"
        assert len(self.next_acks) <= 1, "two blocks completed together"

        if s["arb_grant_read"]:
            self.requests["ar"].popleft()
        if s["arb_grant_write"]:
            assert self.requests["aw"].popleft() is self.requests["w"].popleft()
        for bus, queue in self.drive.items():
            if s["axi_" + bus + "valid"] and s["axi_" + bus + "ready"]:
                tx = queue.popleft()
                tx["accepted"].add(bus)
                self.requests[bus].append(tx)
        for ch, bus in (("r", "r"), ("w", "b")):
            valid, ready = s["axi_" + bus + "valid"], s["axi_" + bus + "ready"]
            payload = (s["axi_rdata"], s["axi_rresp"]) if ch == "r" else (s["axi_bresp"],)
            if bus in self.stalled:
                assert valid and payload == self.stalled[bus], f"{bus.upper()} payload changed under backpressure"
                self.cover["stable_" + bus] += 1
            self.stalled.pop(bus, None)
            if valid:
                assert self.buffered[ch], "unsolicited AXI response"
                tx = self.buffered[ch][0]
                assert self.responses[ch][0] is tx, "AXI per-channel order violated"
                expected = (tx["result"], tx["resp"]) if ch == "r" else (tx["resp"],)
                assert payload == expected, f"{bus}: {payload} != {expected}; tx={tx}"
                pushed = next((item for (_, channel), item in self.next_acks.items()
                               if channel == ch), None)
                if pushed is not None:
                    assert len(self.buffered[ch]) == 1, "response push without credit"
                    event = bus + ("_push_pop_one" if ready else "_push_stalled")
                    self.cover[event] += 1
                    self.cover[event + "_mixed"] += tx["resp"] != pushed["resp"]
                    if ch == "r":
                        self.cover[event + "_distinct"] += tx["result"] != pushed["result"]
                if ready:
                    self.buffered[ch].popleft()
                    self.responses[ch].popleft()
                    self.retired += 1
                else:
                    self.stalled[bus] = payload

        for (source, ch), tx in self.next_acks.items():
            self.inflight[ch].popleft()
            self.buffered[ch].append(tx)
            if source != "local" and not self.tags[source] and old_wait:
                self.cover["empty_ack"] += 1
                if s["int_valid"] and s["int_target"] != s["int_active_target"]:
                    assert not s["int_issue"] and not target_allowed, "lock released before ACK CAPTURE"
                    self.cover["empty_ack_blocked"] += 1
        next_acks = {}
        issued = None
        if s["int_issue"]:
            ch = "w" if s["int_write"] else "r"
            assert self.expected[ch], "unexpected internal issue"
            tx = self.expected[ch].popleft()
            assert tx["accepted"] == ({"aw", "w"} if tx["write"] else {"ar"})
            assert s["int_addr"] == tx["addr"]
            assert s["int_wr_en"] == tx["write"] and s["int_rd_en"] == (not tx["write"])
            if tx["write"]:
                assert (s["int_wr_data"], s["int_wr_strb"]) == (tx["data"], tx["strb"])
            target, mem, index, error = self._decode(tx)
            assert s["int_target"] == target
            if old_wait:
                assert target_allowed and target == s["int_active_target"], "target switched with uncaptured completions"
            if self.last_target is not None and target != self.last_target:
                self.transitions.add((self.last_target, target))
                if s["b_fifo_count"] or s["r_fifo_count"]:
                    self.cover["buffered_switch"] += 1
            self.last_target = target
            tx.update(target=target, resp=2 if error else 0, result=0,
                      issued=True, cycle=self.cycle)
            physical = mem is not None and target != 1
            for spec in self.mems:
                name = spec["name"]
                selected = physical and spec is mem
                assert s[name + "_en"] == selected, f"unexpected/missing physical request on {name}"
                if selected:
                    assert s[name + "_addr"] == index and s[name + "_we"] == tx["write"]
                    if tx["write"]:
                        assert s[name + "_din"] == tx["data"] & ((1 << spec["width"]) - 1)
                        assert s[name + "_be"] == tx["strb"]
            if physical:
                name = mem["name"]
                tx["snapshot"] = self.reference.mems[name][index]
                tx["result"] = tx["snapshot"] if not tx["write"] else 0
                if tx["write"]:
                    mask = _wstrb_to_mask(tx["strb"], mem["width"])
                    self.reference.mems[name][index] = (tx["snapshot"] & ~mask) | (tx["data"] & mask)
                issued = (name, tx)
                if name in self.last_tag:
                    self.tag_pairs.add((self.last_tag[name], tx["write"]))
                self.last_tag[name] = tx["write"]
                self.cover["physical"] += 1
            else:
                if not error and mem is None:
                    reg = self.reference.regs[tx["addr"]]
                    if tx["write"]:
                        mask = reg["write_mask"] & _wstrb_to_mask(tx["strb"])
                        reg["value"] = (reg["value"] & ~mask) | (tx["data"] & mask)
                    else:
                        tx["result"] = reg["value"] & reg["read_mask"]
                next_acks[("local", ch)] = tx
                self.cover["local_error" if error else "local_okay"] += 1
            self.inflight[ch].append(tx)
        else:
            assert not s["int_rd_en"] and not s["int_wr_en"]
            assert not any(s[m["name"] + "_en"] for m in self.mems)

        for mem in self.mems:
            name = mem["name"]
            queue = self.tags[name]
            old_count = len(queue)
            assert s[name + "_tag_fifo_count"] == old_count <= 4
            assert s[name + "_tag_empty"] == (old_count == 0)
            assert s[name + "_tag_fifo_idx"] == ((old_count - 1) & 3)
            assert s[name + "_tag_fifo"] == sum(
                tx["write"] << slot for slot, tx in enumerate(reversed(queue)))
            req, valid = s[name + "_en"], s[name + "_valid"]
            bypass = not old_count and req and valid
            push, pop = req and not bypass, bool(old_count) and valid
            response = valid and (old_count or req)
            for suffix, expected in (("tag_bypass", bypass), ("tag_push", push),
                                     ("tag_pop", pop), ("response", bool(response))):
                assert s[name + "_" + suffix] == bool(expected), (name, suffix)
            if req:
                assert issued is not None and issued[0] == name
                queue.append(issued[1])
            if valid:
                assert queue, f"unsolicited RAM response on {name}"
                tx = queue.popleft()
                ch = "w" if tx["write"] else "r"
                assert s[name + "_response_we"] == tx["write"]
                assert s[name + "_rd_done"] == (not tx["write"])
                assert s[name + "_wr_done"] == tx["write"]
                assert s[name + "_dout"] == tx["snapshot"], "RAM did not return request-edge snapshot"
                next_acks[(name, ch)] = tx
                self.latencies.add(self.cycle - tx["cycle"])
                self.cover["raw_responses"] += 1
                if self.last_response_cycle.get(name) == self.cycle - 1:
                    self.cover["consecutive_responses"] += 1
                self.last_response_cycle[name] = self.cycle
            else:
                assert not s[name + "_rd_done"] and not s[name + "_wr_done"]
                assert s[name + "_dout"] == 0xDEADBEEF & ((1 << mem["width"]) - 1)
            assert len(queue) <= 4
            self.cover["max_tags"] = max(self.cover["max_tags"], len(queue))
            self.cover["bypass"] += bool(bypass)
            self.cover["enqueue_empty"] += bool(push and not old_count)
            self.cover["push_pop"] += bool(push and pop)
            if push and pop and tx["write"] != issued[1]["write"]:
                self.cover["push_pop_different_tag"] += 1
            self.tag_transitions.add((old_count, len(queue)))
            if push and not old_count and name in self.drained_tags:
                self.cover["tag_reuse"] += 1
            if pop and not queue:
                self.cover["tag_drain"] += 1
                self.drained_tags.add(name)
        self.next_acks = next_acks


def _rw_memory(bench):
    return next(m for m in bench.mems if m["is_sw_readable"] and m["is_sw_writable"])


def _unmapped_address(bench):
    for address in range(0, 1 << len(bench.dut.s_axi_araddr), 4):
        if address not in bench.reference.regs and all(
            not m["address"] <= address < m["address"] + 4 * m["mementries"] for m in bench.mems
        ):
            return address
    raise AssertionError("sample needs an unmapped address for error coverage")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_tag_fifo_delayed(dut):
    bench = RamContractBench(dut)
    await bench.reset()
    mem = _rw_memory(bench)
    memory = bench.memories[mem["name"]]
    addr = mem["address"]
    for batch, sequence in enumerate(((False, True, False, True),
                                       (False, False, True, True),
                                       (True, True, False, False))):
        memory.hold = True
        bench.bready = bench.rready = False
        raw_before = bench.cover["raw_responses"]
        for index, write in enumerate(sequence):
            await bench.issue(write, addr, 0xA1B2C3D4 ^ (batch * 0x11111111 + index),
                              (0x5, 0xA, 0x3, 0xC)[index])
        assert len(bench.tags[mem["name"]]) == 4
        assert bench.cover["raw_responses"] == raw_before, "not four requests before first response"
        bench.cover["four_before_response"] += 1
        # Fill AXI response slots and check payload stability before draining.
        if batch == 0:
            memory.hold = False
            memory.response_budget = 1
            bench.bready = bench.rready = True
            retired = bench.retired
            await bench.until(lambda: bench.retired == retired + 1, "first read retirement")
            replacement = bench.submit(False, addr)
            await bench.until(lambda: replacement["accepted"] == {"ar"}, "replacement read acceptance")
            # Release the old W response one edge before the new R physically issues.
            memory.response_budget = 1
            await bench.until(lambda: replacement["issued"], "simultaneous old-W pop/new-R push")
            assert bench.cover["push_pop_different_tag"], "simultaneous push/pop was not exercised"
        bench.bready = bench.rready = False
        memory.hold = False
        memory.response_budget = None
        await bench.until(lambda: not any(bench.inflight.values()), "registered ACK capture")
        for _ in range(5):
            await bench.step()
        await bench.drain()
    assert bench.cover["four_before_response"] == 3 and bench.cover["max_tags"] == 4
    assert bench.tag_pairs == {(False, False), (False, True), (True, False), (True, True)}
    for coverage in ("enqueue_empty", "push_pop", "tag_drain", "tag_reuse",
                     "stable_b", "stable_r", "consecutive_responses"):
        assert bench.cover[coverage], f"missing coverage: {coverage}"
    assert {(0, 1), (1, 2), (2, 3), (3, 4),
            (4, 3), (3, 2), (2, 1), (1, 0)} <= bench.tag_transitions
    assert len(bench.latencies) > 1 and max(bench.latencies) > 6


async def _delayed_switch(bench, source, operation):
    memory = bench.memories[source["name"]]
    memory.hold = True
    bench.bready = bench.rready = False
    await bench.issue(False, source["address"])
    await bench.issue(True, source["address"] + 4, 0xD7C6B5A4, 0x6)
    tx = bench.submit(*operation)
    for _ in range(6):
        await bench.step()
    assert tx["accepted"] and not tx["issued"], "cross-target operation overtook delayed RAM"
    before = bench.cover["empty_ack_blocked"]
    memory.hold = False
    await bench.until(lambda: tx["issued"], "target switch with buffered AXI responses")
    assert bench.cover["empty_ack_blocked"] > before, "missed tag-empty/ACK-pending boundary"
    assert bench.cover["buffered_switch"], "switch incorrectly requires AXI response drain"
    for _ in range(12):
        await bench.step()
    await bench.drain()


@cocotb.test(timeout_time=200, timeout_unit="us")
async def ram_block_switching(dut):
    bench = RamContractBench(dut)
    await bench.reset()
    source = _rw_memory(bench)
    # Check data masking between distinct RAMs and LOCAL, both directions.
    if bench.reference.regs:
        local = next(addr for addr, reg in bench.reference.regs.items() if reg["write_mask"])
        await bench.issue(True, local, 0xCAFEBABE)
        await bench.drain()
        await _delayed_switch(bench, source, (False, local))
        for mem in bench.mems:
            if mem is not source and mem["is_sw_readable"] and mem["is_sw_writable"]:
                await _delayed_switch(bench, source, (False, mem["address"]))
                await _delayed_switch(bench, mem, (False, source["address"] + 4))
        await bench.issue(False, local)
        await bench.issue(False, source["address"])
        await bench.drain()
        assert {(1, 2), (2, 1), (2, 4), (4, 2)} <= bench.transitions

    operations = [(True, source["address"], 0xFFFFFFFF, 0)]
    for mem in bench.mems:
        for write in (False, True):
            if not mem["is_sw_writable" if write else "is_sw_readable"]:
                operations.append((write, mem["address"], 0xFEDCBA98, STRB_MASK))
                if write:
                    operations.append((write, mem["address"], 0xFFFFFFFF, 0))
    hole = _unmapped_address(bench)
    operations += [(False, hole), (True, hole, 0x11223344, 0xF), (True, hole, 0, 0)]
    for operation in operations:
        await _delayed_switch(bench, source, operation)
    for mem in bench.mems:
        if mem["is_sw_writable"]:
            await bench.issue(True, mem["address"], 0x87654321, 0xB)
        if mem["is_sw_readable"]:
            await bench.issue(False, mem["address"])
        await bench.drain()
    assert bench.cover["local_error"] and bench.cover["local_okay"]
    assert bench.cover["stable_b"] and bench.cover["stable_r"]


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_reset_outstanding(dut):
    bench = RamContractBench(dut)
    await bench.reset()
    mem = _rw_memory(bench)
    memory = bench.memories[mem["name"]]
    for ack_pending in (False, True):
        bench.bready = bench.rready = False
        memory.hold = True
        memory.response_budget = None
        for write in (False, True, False, True):
            await bench.issue(write, mem["address"], 0x1234ABCD, 0xA)
        # Also flush AXI-accepted but not internally issued head/back work.
        bench.submit(False, _unmapped_address(bench))
        for _ in range(3):
            await bench.step()
        if ack_pending:
            memory.hold = False
            memory.response_budget = 1
            await bench.until(lambda: bool(bench.next_acks), "raw response before ACK capture")
        assert any(bench.inflight.values()) and bench.tags[mem["name"]]
        await bench.reset()
        memory.hold = False
        memory.response_budget = None
        bench.bready = bench.rready = True
        for _ in range(12):
            await bench.step()
        # Reference retains already-applied writes but forgets aborted responses.
        for write in (False, True, False):
            await bench.issue(write, mem["address"], 0x76543210, 0x5)
        await bench.drain()
        bench.cover["reset_ack" if ack_pending else "reset_tags"] += 1
    assert bench.aborted >= 10 and bench.cover["reset_ack"] and bench.cover["reset_tags"]


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_credit_arbitration(dut):
    bench = RamContractBench(dut)
    await bench.reset()
    mem = _rw_memory(bench)
    memory = bench.memories[mem["name"]]
    for saturated_write in (False, True):
        memory.hold = True
        bench.bready = bench.rready = False
        await bench.issue(saturated_write, mem["address"], 0x32107654, 0x3)
        await bench.issue(saturated_write, mem["address"] + 4, 0xBA98FEDC, 0xC)
        # Keep the saturated channel out of the head to isolate credit arbitration.
        bench.submit(saturated_write, mem["address"], 0x01234567, 0xF)
        bench.submit(not saturated_write, mem["address"], 0x89ABCDEF, 0x5)
        bench.submit(not saturated_write, mem["address"] + 4, 0xFEDCBA98, 0xA)
        key = "eligible_read" if saturated_write else "eligible_write"
        await bench.until(lambda: bool(bench.cover[key]), f"KI02 {key}")
        await bench.drain()
    assert bench.cover["eligible_read"] and bench.cover["eligible_write"]


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_response_fifo(dut):
    bench = RamContractBench(dut, latency=(1, 1))
    await bench.reset()
    addr = _rw_memory(bench)["address"]
    hole = _unmapped_address(bench)

    async def capture(write, error, data=0x6B42D915):
        tx = await bench.issue(write, hole if error else addr, data)
        await bench.until(lambda: not bench.inflight["w" if write else "r"],
                          "response FIFO capture")
        return tx

    for write, bus in ((False, "r"), (True, "b")):
        ch = "w" if write else "r"
        # Reverse OKAY/SLVERR order to expose both data and error tap mistakes.
        for error_first in (False, True):
            for simultaneous in (False, True):
                bench.bready = bench.rready = False
                first = await capture(write, error_first)
                second = await bench.issue(write, addr if error_first else hole, 0xA5C317E9)
                event = bus + ("_push_pop_one" if simultaneous else "_push_stalled")
                before = bench.cover[event + "_mixed"]
                if simultaneous:
                    # Align READY with ACK capture to exercise simultaneous push/pop.
                    await bench.until(lambda: any(channel == ch for _, channel in bench.next_acks),
                                      "second response ACK before capture")
                    assert len(bench.buffered[ch]) == 1
                    bench.bready = bench.rready = True
                    await bench.step()
                    bench.bready = bench.rready = False
                    assert list(bench.buffered[ch]) == [second]
                else:
                    await bench.until(lambda: len(bench.buffered[ch]) == 2,
                                      "push into stalled response FIFO")
                assert bench.cover[event + "_mixed"] == before + 1
                if not write:
                    assert first["result"] != second["result"]
                for _ in range(4):
                    await bench.step()
                await bench.drain()

    for bus in ("b", "r"):
        for event in ("_push_stalled", "_push_pop_one"):
            assert bench.cover[bus + event + "_mixed"] == 2
            if bus == "r":
                assert bench.cover[bus + event + "_distinct"] == 2

    # Opposite errors and new data expose stale SRL contents after reset/refill.
    for depth in (1, 2):
        bench.bready = bench.rready = False
        for write in (True, False):
            for error in (False, True)[:depth]:
                await capture(write, error, 0x12345678)
        assert all(len(queue) == depth for queue in bench.buffered.values())
        stale_data = bench.buffered["r"][0]["result"]
        await bench.reset()
        for _ in range(5):
            await bench.step()
        for write in (True, False):
            for error in (True, False):
                tx = await capture(write, error, 0xFEDCBA98 ^ depth)
                if not write and not error:
                    assert tx["result"] != stale_data
        assert all(len(queue) == 2 for queue in bench.buffered.values())
        for _ in range(4):
            await bench.step()
        await bench.drain()
    assert bench.aborted == 6
    assert bench.cover["stable_b"] and bench.cover["stable_r"]


async def _latency_contract(dut, *, combinational):
    bench = RamContractBench(dut, latency=(1, 1), combinational=combinational)
    await bench.reset()
    bench.bready = bench.rready = True
    mem = _rw_memory(bench)
    for batch in range(4):
        for index, write in enumerate((False, True, True, False)):
            bench.submit(write, mem["address"] + 4 * (index % 2),
                         0x10293847 ^ (batch * 0x1234567 + index), (0xF, 0x5, 0xA, 0xF)[index])
        await bench.drain()
    # Isolate each channel so arbitration cannot alternate the request types.
    for write in (False, True):
        for index in range(2):
            bench.submit(write, mem["address"] + index * 4, 0xABCDEF01 ^ index, 0xF)
        await bench.drain()
    for spec in bench.mems:
        for write in (False, True):
            await bench.issue(write, spec["address"], 0x7654ABCD, 0x3)
            await bench.drain()
    assert bench.latencies == ({0} if combinational else {1}), bench.latencies
    assert bench.tag_pairs == {(False, False), (False, True), (True, False), (True, True)}
    assert bench.cover["consecutive_responses"]
    if combinational:
        assert bench.cover["bypass"] == bench.cover["physical"] > 0
        assert bench.cover["max_tags"] == 0
    else:
        assert bench.cover["enqueue_empty"] and not bench.cover["bypass"]
        assert bench.cover["tag_drain"] and bench.cover["tag_reuse"]
        assert {(0, 1), (1, 0)} <= bench.tag_transitions


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_latency_zero(dut):
    await _latency_contract(dut, combinational=True)


@cocotb.test(timeout_time=100, timeout_unit="us")
async def ram_latency_one(dut):
    await _latency_contract(dut, combinational=False)


def _write_contract_wrapper(source, top, build_dir, *, combinational):
    """Build a test-only wrapper; no generated DUT artifact is modified."""
    _, mems = _load_rdl_metadata(top)
    header = source.read_text().split(");", 1)[0]
    ports = re.findall(r"^\s*(input|output)\s+wire\s*(\[[^\]]+\])?\s*(\w+)", header, re.MULTILINE)
    assert ports, f"could not parse ports in {source}"
    address_width = next(int(width.strip("[]").split(":")[0]) + 1
                         for _, width, name in ports if name == "s_axi_araddr")
    monitors = _monitor_widths(mems, address_width)
    outputs = {mem["name"] + suffix for mem in mems for suffix in ("_valid", "_dout")}
    declarations = [f"    {'output' if combinational and name in outputs else direction} wire {width} {name}"
                    for direction, width, name in ports]
    declarations += [f"    output wire [{width - 1}:0] mon_{name}" for name, width in monitors.items()]
    lines = ["`timescale 1ns/1ps", f"module stress_{top} (", ",\n".join(declarations), ");",
             f"{top}_regs core (", ",\n".join(f"    .{name}({name})" for _, _, name in ports), ");"]
    lines += [f"assign mon_{name} = core.{name};" for name in monitors]
    if combinational:
        for number, mem in enumerate(mems):
            name, width = mem["name"], mem["width"]
            lines.append(f"reg [{width - 1}:0] {name}_storage [0:{mem['mementries'] - 1}];")
            lines.append("initial begin")
            lines += [f"    {name}_storage[{index}] = {width}'h{value:x};"
                      for index, value in enumerate(_initial_memory(mem, number))]
            lines += ["end", f"assign {name}_valid = s_axi_aresetn && {name}_en;",
                      f"assign {name}_dout = {name}_en ? {name}_storage[{name}_addr] : {width}'h{0xDEADBEEF & ((1 << width) - 1):x};",
                      "always @(posedge s_axi_aclk) begin",
                      f"    if (s_axi_aresetn && {name}_en && {name}_we) begin"]
            for byte in range((width + 7) // 8):
                high, low = min(width, byte * 8 + 8) - 1, byte * 8
                lines.append(f"        if ({name}_be[{byte}]) {name}_storage[{name}_addr][{high}:{low}] <= {name}_din[{high}:{low}];")
            lines += ["    end", "end"]
    lines += ["endmodule", ""]
    wrapper = build_dir / f"stress_{top}.v"
    wrapper.write_text("\n".join(lines))
    return wrapper


def _run_cocotb_test(top, testcase):
    sim = os.environ["SIM"]
    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    if not dut.is_file():
        pytest.fail(f"missing {dut}; run `make artifacts` first", pytrace=False)

    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    from cocotb_tools.runner import get_runner

    runner = get_runner(sim)
    build_dir = REPO_ROOT / "sim_build" / "stress" / top / sim / testcase
    build_dir.mkdir(parents=True, exist_ok=True)
    hdl_toplevel = f"{top}_regs"
    sources = [str(dut)]
    if testcase.startswith("ram_"):
        wrapper = _write_contract_wrapper(
            dut, top, build_dir, combinational=testcase == "ram_latency_zero")
        sources.append(str(wrapper))
        hdl_toplevel = f"stress_{top}"
    runner.build(
        sources=sources,
        hdl_toplevel=hdl_toplevel,
        build_dir=str(build_dir),
        always=True,
    )

    old_top = os.environ.get("STRESS_TOP")
    os.environ["STRESS_TOP"] = top
    try:
        runner.test(
            test_module="test_stress",
            hdl_toplevel=hdl_toplevel,
            testcase=testcase,
            test_dir=str(build_dir),
            seed=0xC0FFEE,
        )
    finally:
        if old_top is None:
            os.environ.pop("STRESS_TOP", None)
        else:
            os.environ["STRESS_TOP"] = old_top


@pytest.mark.parametrize("top", ["ram", "mem_access"])
@pytest.mark.parametrize("wstrb", [STRB_MASK, 0x5, 0xA, 0])
def test_memory_storage_is_independent(top, wstrb):
    model = RdlStressModel(top)
    for mem in model.mem_specs:
        name = mem["name"]
        dut = SimpleNamespace(s_axi_aclk=None, s_axi_aresetn=None, **{
            f"{name}_{suffix}": None
            for suffix in ("addr", "en", "we", "be", "din", "dout", "valid")
        })
        initial = list(model.mems[name])
        memory = ExternalMemoryModel(dut, mem, model.mems[name])
        assert memory.values == initial
        assert memory.values is not model.mems[name]

        for op in model.write_ops:
            if op["kind"] == "mem" and op["mem"] is mem:
                model.write(op, initial[op["idx"]] ^ DATA_MASK, wstrb, dut)
        assert memory.values == initial

        expected = list(model.mems[name])
        for index in range(len(memory.values)):
            memory.values[index] ^= DATA_MASK
        assert model.mems[name] == expected


@pytest.mark.sim
@pytest.mark.parametrize("top", ["ram", "mem_access"])
def test_memory_write_scoreboard(top):
    _run_cocotb_test(top, "memory_write_scoreboard")


@pytest.mark.sim
@pytest.mark.parametrize("testcase", ["memory_read_held_data", "memory_read_valid_pulse"])
def test_memory_read_timing(testcase):
    _run_cocotb_test("ram", testcase)


@pytest.mark.sim
@pytest.mark.parametrize("top", ["ram", "mem_access"])
@pytest.mark.parametrize("testcase", [
    "ram_tag_fifo_delayed", "ram_block_switching", "ram_reset_outstanding",
    "ram_credit_arbitration", "ram_response_fifo", "ram_latency_zero", "ram_latency_one",
])
def test_ram_contract(top, testcase):
    _run_cocotb_test(top, testcase)


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_stress_random_axi(top):
    _run_cocotb_test(top, "stress_random_axi")


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_stress_write_overlap(top):
    _run_cocotb_test(top, "stress_write_overlap")


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_stress_read_overlap(top):
    _run_cocotb_test(top, "stress_read_overlap")


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_stress_mixed_overlap(top):
    _run_cocotb_test(top, "stress_mixed_overlap")
