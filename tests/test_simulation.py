#!/usr/bin/env python3
"""RTL simulation tests.

Compile the reusable ``<top>_regs.v`` with its self-checking ``tb_<top>_regs.v``
testbench, then run the selected simulator. The testbench drives all AXI traffic,
counts mismatches, and ends with ``$finish`` (pass, prints ``TEST PASSED``) or
``$fatal`` (fail, prints ``TEST FAILED``). We assert on the simulator exit code
and the pass/fail banner.

The Verilog sources are read from the ``generated/`` tree (produced by
``make artifacts``) so manual edits to those files are picked up by re-running
``make sim`` — the test never regenerates over them. Set ``SIM=icarus``,
``SIM=verilator``, ``SIM=questa``, or ``SIM=vsim`` (an alias for Questa) to
select a backend. ``SIM`` is required; the selected backend must be installed.
``SIM=iverilog`` is accepted as an alias for Icarus.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from bus_generator import main
from simulator_support import (
    require_simulator,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"

SAMPLES = [
    pytest.param("field_access", id="field_access"),
    pytest.param("gpio", id="gpio"),
    pytest.param("mem_access", id="mem_access"),
    pytest.param("ram", id="ram"),
    pytest.param("simple", id="simple"),
    pytest.param("wstrb", id="wstrb"),
]


def _selected_simulator():
    try:
        return require_simulator(os.environ, shutil.which)
    except (RuntimeError, ValueError) as error:
        pytest.fail(str(error), pytrace=False)


def _run_icarus(top, dut, tb, tmp_path):
    sim = tmp_path / "sim.vvp"
    compile_proc = subprocess.run(
        ["iverilog", "-g2012", "-o", str(sim), "-s", f"tb_{top}_regs", str(dut), str(tb)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert compile_proc.returncode == 0, (
        f"iverilog failed:\n{compile_proc.stdout}\n{compile_proc.stderr}"
    )

    run_proc = subprocess.run(
        ["vvp", str(sim)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    return run_proc.returncode, run_proc.stdout + run_proc.stderr


def _run_verilator(top, dut, tb, tmp_path):
    build_dir = tmp_path / "verilator"
    compile_proc = subprocess.run(
        [
            "verilator",
            "--binary",
            "--timing",
            "-Mdir",
            str(build_dir),
            "--top-module",
            f"tb_{top}_regs",
            str(dut),
            str(tb),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert compile_proc.returncode == 0, (
        f"verilator failed:\n{compile_proc.stdout}\n{compile_proc.stderr}"
    )

    sim = build_dir / f"Vtb_{top}_regs"
    run_proc = subprocess.run(
        [str(sim)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    return run_proc.returncode, run_proc.stdout + run_proc.stderr


def _run_questa(top, dut, tb, tmp_path):
    library_proc = subprocess.run(
        ["vlib", "work"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert library_proc.returncode == 0, (
        f"vlib failed:\n{library_proc.stdout}\n{library_proc.stderr}"
    )

    compile_proc = subprocess.run(
        ["vlog", "-work", "work", str(dut), str(tb)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert compile_proc.returncode == 0, (
        f"vlog failed:\n{compile_proc.stdout}\n{compile_proc.stderr}"
    )

    run_proc = subprocess.run(
        [
            "vsim",
            "-c",
            "-lib",
            "work",
            "-do",
            "run -all; quit -f",
            f"tb_{top}_regs",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    return run_proc.returncode, run_proc.stdout + run_proc.stderr


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_self_check_tb(top, tmp_path):
    sim = _selected_simulator()

    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    tb = GENERATED / "tb_axi4l" / f"tb_{top}_regs.v"
    if not dut.is_file() or not tb.is_file():
        pytest.skip(f"missing {dut.name}/{tb.name}; run `make artifacts` first")

    if sim == "icarus":
        returncode, output = _run_icarus(top, dut, tb, tmp_path)
    elif sim == "verilator":
        returncode, output = _run_verilator(top, dut, tb, tmp_path)
    else:
        returncode, output = _run_questa(top, dut, tb, tmp_path)

    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"


@pytest.mark.sim
@pytest.mark.parametrize("base", [0x0, 0x4, 0x100])
def test_single_entry_memory_tb(tmp_path, base):
    sim = _selected_simulator()
    top = "single_entry"
    rdl = tmp_path / f"{top}.rdl"
    rdl.write_text(f"""addrmap {top} {{
        external mem {{
            mementries = 1;
            memwidth = 32;
            sw = rw;
        }} ram @ 0x{base:x};
        reg {{ field {{ sw = rw; hw = r; }} data[31:0]; }} control @ 0x200;
    }};
    """)
    main([str(rdl), "-o", str(tmp_path), "-t", "axi4l", "tb_axi4l"])
    dut = tmp_path / f"{top}_regs.v"
    tb = tmp_path / f"tb_{top}_regs.v"
    if sim == "icarus":
        returncode, output = _run_icarus(top, dut, tb, tmp_path)
    elif sim == "verilator":
        returncode, output = _run_verilator(top, dut, tb, tmp_path)
    else:
        returncode, output = _run_questa(top, dut, tb, tmp_path)

    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"


@pytest.mark.sim
@pytest.mark.parametrize("scenario", ["output_isolation", "queue_ordering_reset"])
def test_request_fifo_tb(tmp_path, scenario):
    sim = _selected_simulator()
    top = "request_fifo"
    rdl = tmp_path / f"{top}.rdl"
    rdl.write_text("""addrmap request_fifo {
        reg word_t { field { sw = rw; hw = r; reset = 0; } data[31:0]; };
        word_t reg0 @ 0x0;
        word_t reg1 @ 0x4;
        word_t reg2 @ 0x8;
        word_t reg3 @ 0xc;
    };
    """)
    main([str(rdl), "-o", str(tmp_path), "-t", "axi4l"])
    dut = tmp_path / f"{top}_regs.v"
    tb = tmp_path / f"tb_{top}_regs.v"
    tb.write_text(r"""`timescale 1ns / 1ps
module tb_request_fifo_regs;
    reg clk = 0, resetn = 0;
    reg [3:0] awaddr = 0, araddr = 0, wstrb = 0;
    reg [2:0] awprot = 0, arprot = 0;
    reg [31:0] wdata = 0, read_expected = 0;
    reg awvalid = 0, wvalid = 0, arvalid = 0, bready = 0, rready = 0;
    wire awready, wready, arready, bvalid, rvalid;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;
    wire [40:0] axi_outputs = {awready, wready, bvalid, bresp,
                              arready, rvalid, rresp, rdata};
    integer aw_sent = 0, w_sent = 0, ar_sent = 0, b_seen = 0, r_seen = 0;
    integer aw_depth = 0, w_depth = 0, ar_depth = 0;
    integer isolation_errors = 0;
    reg aw_push_pop = 0, w_push_pop = 0, ar_push_pop = 0;
    reg b_stalled = 0, r_stalled = 0;
    reg [1:0] held_bresp = 0;
    reg [33:0] held_read = 0;
    reg [31:0] expected_reads [0:31];

    request_fifo_regs dut (
        .s_axi_aclk(clk), .s_axi_aresetn(resetn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(arprot),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .reg0_data_out(), .reg1_data_out(), .reg2_data_out(), .reg3_data_out()
    );

    task tick;
        begin
            #1;
            if (!resetn) begin
                aw_sent = 0; w_sent = 0; ar_sent = 0; b_seen = 0; r_seen = 0;
                aw_depth = 0; w_depth = 0; ar_depth = 0;
                b_stalled = 0; r_stalled = 0;
            end else begin
                if (b_stalled && (bvalid !== 1'b1 || bresp !== held_bresp))
                    $fatal(1, "B response changed while stalled");
                if (r_stalled && (rvalid !== 1'b1 || {rresp, rdata} !== held_read))
                    $fatal(1, "R response changed while stalled");
                b_stalled = bvalid && !bready;
                r_stalled = rvalid && !rready;
                held_bresp = bresp;
                held_read = {rresp, rdata};
                if (awvalid && awready) aw_sent = aw_sent + 1;
                if (wvalid && wready) w_sent = w_sent + 1;
                if (arvalid && arready) begin
                    expected_reads[ar_sent] = read_expected;
                    ar_sent = ar_sent + 1;
                end
                if (bvalid && bready) begin
                    if (bresp !== 2'b00 || b_seen >= aw_sent || b_seen >= w_sent)
                        $fatal(1, "Unexpected B response %0d: %b", b_seen, bresp);
                    b_seen = b_seen + 1;
                end
                if (rvalid && rready) begin
                    if (r_seen >= ar_sent || rresp !== 2'b00 ||
                        rdata !== expected_reads[r_seen])
                        $fatal(1, "R response %0d: got %h expected %h, resp %b",
                               r_seen, rdata, expected_reads[r_seen], rresp);
                    r_seen = r_seen + 1;
                end
                if (aw_depth == 1 && awvalid && awready && dut.arb_grant_write)
                    aw_push_pop = 1;
                if (w_depth == 1 && wvalid && wready && dut.arb_grant_write)
                    w_push_pop = 1;
                if (ar_depth == 1 && arvalid && arready && dut.arb_grant_read)
                    ar_push_pop = 1;
                if (awvalid && awready) aw_depth = aw_depth + 1;
                if (wvalid && wready) w_depth = w_depth + 1;
                if (arvalid && arready) ar_depth = ar_depth + 1;
                if (dut.arb_grant_write) begin
                    aw_depth = aw_depth - 1;
                    w_depth = w_depth - 1;
                end
                if (dut.arb_grant_read) ar_depth = ar_depth - 1;
            end
            clk = 1; #1; clk = 0; #1;
        end
    endtask

    task reset_dut;
        begin
            resetn = 0;
            awvalid = 0; wvalid = 0; arvalid = 0; bready = 0; rready = 0;
            tick; tick;
            resetn = 1;
            #1;
            if ({awready, wready, arready, bvalid, rvalid} !== 5'b11100)
                $fatal(1, "Reset did not empty requests and responses");
        end
    endtask

    task put_aw(input [3:0] address);
        begin
            awaddr = address; awvalid = 1;
            #1;
            if (awready !== 1'b1) $fatal(1, "AW not accepted on consecutive cycle");
            tick;
            awvalid = 0;
        end
    endtask

    task put_w(input [31:0] data, input [3:0] strb);
        begin
            wdata = data; wstrb = strb; wvalid = 1;
            #1;
            if (wready !== 1'b1) $fatal(1, "W not accepted on consecutive cycle");
            tick;
            wvalid = 0;
        end
    endtask

    task put_write(input [3:0] address, input [31:0] data, input [3:0] strb);
        begin
            awaddr = address; awvalid = 1;
            wdata = data; wstrb = strb; wvalid = 1;
            #1;
            if ({awready, wready} !== 2'b11)
                $fatal(1, "AW/W not accepted on consecutive cycle");
            tick;
            awvalid = 0; wvalid = 0;
        end
    endtask

    task put_read(input [3:0] address, input [31:0] expected);
        begin
            araddr = address; arvalid = 1; read_expected = expected;
            #1;
            if (arready !== 1'b1) $fatal(1, "AR not accepted on consecutive cycle");
            tick;
            arvalid = 0;
        end
    endtask

    task drain(input integer writes, input integer reads);
        integer cycles;
        begin
            bready = 1; rready = 1;
            cycles = 0;
            while ((b_seen < writes || r_seen < reads) && cycles < 40) begin
                tick;
                cycles = cycles + 1;
            end
            repeat (4) tick;
            if (b_seen != writes || r_seen != reads || bvalid || rvalid ||
                aw_depth != 0 || w_depth != 0 || ar_depth != 0 || dut.int_valid)
                $fatal(1, "Drain mismatch: B=%0d/%0d R=%0d/%0d",
                       b_seen, writes, r_seen, reads);
        end
    endtask

    task toggle_input(input integer which);
        begin
            case (which)
                0: arvalid = ~arvalid;
                1: bready = ~bready;
                2: rready = ~rready;
                3: awvalid = ~awvalid;
                4: wvalid = ~wvalid;
                5: awaddr = ~awaddr;
                6: araddr = ~araddr;
                7: wdata = ~wdata;
                8: wstrb = ~wstrb;
                9: awprot = ~awprot;
                10: arprot = ~arprot;
                11: resetn = ~resetn;
            endcase
        end
    endtask

    task probe_outputs(input integer phase);
        integer which;
        reg [40:0] before_outputs;
        begin
            #1;
            before_outputs = axi_outputs;
            for (which = 0; which < 12; which = which + 1) begin
                toggle_input(which); #1;
                if (axi_outputs !== before_outputs) begin
                    isolation_errors = isolation_errors + 1;
                    $display("Isolation phase %0d input %0d: %h -> %h",
                             phase, which, before_outputs, axi_outputs);
                end
                toggle_input(which); #1;
                if (axi_outputs !== before_outputs)
                    $fatal(1, "Outputs did not recover after restoring input %0d", which);
            end
        end
    endtask

    task output_isolation;
        begin
            reset_dut;
            probe_outputs(0);
            put_write(4'h0, 32'h11223344, 4'hf);
            if (aw_sent != 1 || w_sent != 1 || aw_depth != 1 || w_depth != 1 ||
                dut.int_valid !== 1'b0 || dut.arb_write_eligible !== 1'b1 ||
                dut.arb_read_priority !== 1'b1)
                $fatal(1, "Buffered AW/W read-priority scenario not reached");
            probe_outputs(1);

            reset_dut;
            put_write(4'h0, 32'h11223344, 4'hf);
            put_write(4'h4, 32'haabbccdd, 4'h5);
            put_write(4'h8, 32'hdeadbeef, 4'ha);
            put_write(4'hc, 32'h55667788, 4'hf);
            repeat (5) tick;
            if (aw_sent != 4 || w_sent != 4 || aw_depth < 1 || w_depth < 1 ||
                b_seen != 0 || bvalid !== 1'b1 || dut.b_outstanding !== 2'd2 ||
                dut.b_fifo_count !== 2'd2 || dut.int_valid !== 1'b1)
                $fatal(1, "Full B credits with queued writes scenario not reached");
            probe_outputs(2);

            reset_dut;
            put_read(4'h0, 32'd0);
            put_read(4'h4, 32'd0);
            put_read(4'h8, 32'd0);
            put_read(4'hc, 32'd0);
            repeat (5) tick;
            if (ar_sent != 4 || ar_depth < 1 || r_seen != 0 || rvalid !== 1'b1 ||
                dut.r_outstanding !== 2'd2 || dut.r_fifo_count !== 2'd2 ||
                dut.int_valid !== 1'b1)
                $fatal(1, "Full R credits with queued reads scenario not reached");
            probe_outputs(3);
            if (isolation_errors != 0)
                $fatal(1, "AXI input-to-output combinational paths: %0d", isolation_errors);
        end
    endtask

    task queue_ordering_reset;
        begin
            reset_dut;
            put_aw(4'h0);
            put_aw(4'h4);
            if (awready !== 1'b0 || aw_depth != 2 || w_sent != 0)
                $fatal(1, "Two AW-before-W requests did not fill AW FIFO");
            put_w(32'h11223344, 4'hf);
            put_w(32'haabbccdd, 4'h5);
            repeat (5) tick;
            if (dut.b_fifo_count !== 2'd2) $fatal(1, "B backpressure not reached");
            drain(2, 0);

            bready = 0;
            put_w(32'hdeadbeef, 4'ha);
            put_w(32'h55667788, 4'hf);
            if (wready !== 1'b0 || w_depth != 2 || aw_depth != 0)
                $fatal(1, "Two W-before-AW requests did not fill W FIFO");
            put_aw(4'h8);
            put_aw(4'hc);
            repeat (5) tick;
            drain(4, 0);

            put_write(4'h0, 32'h99abcdef, 4'h3);
            put_write(4'h4, 32'h87654321, 4'ha);
            put_write(4'h8, 32'h01234567, 4'h5);
            drain(7, 0);
            rready = 0;
            put_read(4'h0, 32'h1122cdef);
            put_read(4'h4, 32'h87bb43dd);
            put_read(4'h8, 32'hde23be67);
            put_read(4'hc, 32'h55667788);
            put_read(4'h0, 32'h1122cdef);
            repeat (5) tick;
            if (arready !== 1'b0 || ar_depth != 2 || dut.r_fifo_count !== 2'd2)
                $fatal(1, "Read FIFO and response backpressure not reached");
            drain(7, 5);
            if (!aw_push_pop || !w_push_pop || !ar_push_pop)
                $fatal(1, "Missing simultaneous push/pop coverage: AW=%b W=%b AR=%b",
                       aw_push_pop, w_push_pop, ar_push_pop);

            bready = 0; rready = 0;
            put_write(4'h0, 32'hffffffff, 4'hf);
            put_write(4'h4, 32'heeeeeeee, 4'hf);
            put_write(4'h8, 32'hdddddddd, 4'hf);
            put_write(4'hc, 32'hcccccccc, 4'hf);
            put_write(4'h0, 32'hbbbbbbbb, 4'hf);
            put_read(4'h0, 32'd0);
            put_read(4'h4, 32'd0);
            repeat (5) tick;
            if ({awready, wready, arready} !== 3'b000 ||
                aw_depth != 2 || w_depth != 2 || ar_depth != 2 ||
                dut.b_fifo_count !== 2'd2 || dut.int_valid !== 1'b1)
                $fatal(1, "Reset with all three FIFOs full was not reached");
            reset_dut;
            bready = 1; rready = 1;
            repeat (8) tick;
            if (b_seen != 0 || r_seen != 0) $fatal(1, "Stale response after reset");
            put_read(4'h0, 32'd0);
            put_read(4'h4, 32'd0);
            put_read(4'h8, 32'd0);
            put_read(4'hc, 32'd0);
            drain(0, 4);
            put_write(4'hc, 32'hf00dbead, 4'h9);
            drain(1, 4);
            put_read(4'hc, 32'hf00000ad);
            drain(1, 5);
        end
    endtask

    initial begin
        @SCENARIO@;
        $display("TEST PASSED");
        $finish;
    end
    initial begin
        #10000;
        $fatal(1, "Testbench timeout");
    end
endmodule
""".replace("@SCENARIO@", scenario))
    if sim == "icarus":
        returncode, output = _run_icarus(top, dut, tb, tmp_path)
    elif sim == "verilator":
        returncode, output = _run_verilator(top, dut, tb, tmp_path)
    else:
        returncode, output = _run_questa(top, dut, tb, tmp_path)

    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"
    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
