#!/usr/bin/env python3
"""Pytest wrappers and cocotb stress tests for generated AXI4-Lite RTL.

The pytest wrappers build generated AXI4-Lite RTL for each sample RDL and select
one ``@cocotb.test`` case from this module. Cocotb owns pass/fail;
``runner.test()`` exits non-zero under pytest if the selected test fails or
times out.

Sources are read from the ``generated/`` tree so manual edits to the RTL survive
a re-run. Skipped when iverilog/vvp or cocotb_tools are unavailable, or when the
generated DUT is missing.
"""

import os
import random
import shutil
import sys
from pathlib import Path

import cocotb
import pytest
from bus_generator.bus_generator import FieldsGatheringListener, MemGatheringListener
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, SimTimeoutError, Timer, with_timeout
from systemrdl import RDLCompiler, RDLWalker

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"
TESTS_DIR = Path(__file__).resolve().parent

DATA_WIDTH = 32
DATA_MASK = (1 << DATA_WIDTH) - 1
SAMPLES = [
    pytest.param("gpio", id="gpio"),
    pytest.param("ram", id="ram"),
    pytest.param("simple", id="simple"),
]

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


class RdlStressModel:
    def __init__(self, top):
        fields, mems = _load_rdl_metadata(top)
        self.regs = {}
        self.mems = {m["name"]: [0] * m["mementries"] for m in mems}
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

    def write(self, op, data, dut):
        data &= DATA_MASK
        if op["kind"] == "reg":
            reg = self.regs[op["addr"]]
            reg["value"] = (reg["value"] & ~reg["write_mask"]) | (data & reg["write_mask"])
            self.drive_hw_inputs(dut)
        else:
            mem = op["mem"]
            mask = (1 << mem["width"]) - 1
            self.mems[mem["name"]][op["idx"]] = data & mask

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
    def __init__(self, dut, mem, values):
        self.clk = dut.s_axi_aclk
        self.resetn = dut.s_axi_aresetn
        self.addr = getattr(dut, f"{mem['name']}_addr")
        self.en = getattr(dut, f"{mem['name']}_en")
        self.we = getattr(dut, f"{mem['name']}_we")
        self.din = getattr(dut, f"{mem['name']}_din")
        self.dout = getattr(dut, f"{mem['name']}_dout")
        self.valid = getattr(dut, f"{mem['name']}_valid")
        self.values = values
        self.mask = (1 << mem["width"]) - 1
        seed = SEED ^ sum(ord(c) for c in mem["name"])
        self.random = random.Random(seed)

    async def run(self):
        def sampled_int(sig):
            try:
                return int(sig.value)
            except ValueError:
                return 0

        pending = []
        self.dout.value = 0
        self.valid.value = 0
        while True:
            resetn = sampled_int(self.resetn)
            en = sampled_int(self.en)
            we = sampled_int(self.we)
            addr = sampled_int(self.addr)
            din = sampled_int(self.din)
            await RisingEdge(self.clk)
            if resetn == 0:
                pending = []
                self.dout.value = 0
                self.valid.value = 0
                continue

            ready_idx = None
            next_pending = []
            for latency, idx in pending:
                latency -= 1
                if latency <= 0 and ready_idx is None:
                    ready_idx = idx
                else:
                    next_pending.append((latency, idx))

            self.valid.value = 0
            if ready_idx is not None:
                self.dout.value = self.values[ready_idx] & self.mask
                self.valid.value = 1

            if en == 1:
                if we == 1:
                    self.values[addr] = din & self.mask
                else:
                    latency = self.random.randint(MEM_READ_LATENCY_MIN, MEM_READ_LATENCY_MAX)
                    next_pending.append((latency, addr))

            pending = next_pending


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
        self.write_count = 0

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
        self.write_count += 1

    async def issue_write_aw_first(self, addr, data):
        await self._drive_aw(addr)
        await self._drive_w(data)
        self.write_count += 1

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
            if int(self.dut.s_axi_arready.value) == 1:
                await RisingEdge(self.clk)
                break
            await RisingEdge(self.clk)
        self.dut.s_axi_arvalid.value = 0
        self.read_count += 1

    async def r_drain(self, expected, count):
        async def tick():
            await RisingEdge(self.clk)
            await Timer(1, "step")

        self.dut.s_axi_rready.value = 0
        while self.r_count < count:
            for _ in range(random.randint(0, MAX_BP_GAP)):
                await tick()
            self.dut.s_axi_rready.value = 1
            await Timer(1, "step")
            while int(self.dut.s_axi_rvalid.value) == 0:
                await tick()
            rdata = int(self.dut.s_axi_rdata.value)
            rresp = int(self.dut.s_axi_rresp.value)
            expected_data, mask, addr = expected[self.r_count]
            await tick()
            self.r_count += 1
            if rresp != 0 or (rdata & mask) != expected_data:
                self.r_errors += 1
                self.dut._log.error(
                    f"R[{self.r_count}] addr=0x{addr:02x} data=0x{rdata:08x} "
                    f"expected=0x{expected_data:08x} mask=0x{mask:08x} resp={rresp}"
                )
            self.dut.s_axi_rready.value = 0
            await tick()
        self.dut.s_axi_rready.value = 1
        await tick()
        self.dut.s_axi_rready.value = 0


def _load_rdl_metadata(top):
    rdlc = RDLCompiler()
    rdlc.compile_file(str(TESTS_DIR / f"{top}.rdl"))
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
    await Timer(100, "ns")
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
            model.write(op, data, dut)
            bresp = await master.write(op["addr"], data)
            if bresp != 0:
                errors += 1
                dut._log.error(
                    f"[{i}] write addr=0x{op['addr']:02x} got bresp={bresp}, expected 0"
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
        writes.append((op, random.getrandbits(DATA_WIDTH)))

    dut._log.info(f"issuing {count} {top} overlapped writes with B backpressure")

    drain_task = cocotb.start_soon(master.b_drain(count))
    for op, data in writes:
        model.write(op, data, dut)
        await master.issue_write(op["addr"], data)
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
    await Timer(100, "ns")
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
    writes = [(random.choice(write_ops), random.getrandbits(DATA_WIDTH)) for _ in range(write_count)]
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
        for op, data in writes:
            model.write(op, data, dut)
            await write_master.issue_write_aw_first(op["addr"], data)

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


def _run_cocotb_test(top, testcase):
    sim = os.environ.get("SIM", "icarus").lower()
    if sim == "icarus" and (not shutil.which("iverilog") or not shutil.which("vvp")):
        pytest.skip("iverilog/vvp not installed")
    if sim == "verilator" and not shutil.which("verilator"):
        pytest.skip("verilator not installed")
    pytest.importorskip("cocotb_tools.runner")

    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    if not dut.is_file():
        pytest.skip(f"missing {dut}; run `make gen` first")

    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    from cocotb_tools.runner import get_runner

    runner = get_runner(sim)
    hdl_toplevel = f"{top}_regs"
    runner.build(
        sources=[str(dut)],
        hdl_toplevel=hdl_toplevel,
        always=True,
    )

    old_top = os.environ.get("STRESS_TOP")
    os.environ["STRESS_TOP"] = top
    try:
        runner.test(
            test_module="test_stress",
            hdl_toplevel=hdl_toplevel,
            testcase=testcase,
            seed=0xC0FFEE,
        )
    finally:
        if old_top is None:
            os.environ.pop("STRESS_TOP", None)
        else:
            os.environ["STRESS_TOP"] = old_top


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
