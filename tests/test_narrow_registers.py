"""Independent AXI and hardware-port checks using explicit expected values."""

import os
from textwrap import dedent, indent

import pytest
from bus_generator import main
from test_simulation import _run_icarus, _run_questa, _run_verilator

pytestmark = pytest.mark.sim


@pytest.fixture
def simulator():
    return os.environ["SIM"]


_PACKED = """
    reg {
        regwidth = 16; accesswidth = 16;
        field { sw = rw; hw = r; reset = 16'h1234; } value[15:0];
    } first @ 0;
    reg {
        regwidth = 16; accesswidth = 16;
        field { sw = rw; hw = r; reset = 16'habcd; } value[15:0];
    } second @ 2;
"""
_THIRD = """
    reg {
        field { sw = rw; hw = r; reset = 32'h89abcdef; } value[31:0];
    } third @ 4;
"""
_SINGLE = """
    reg {
        regwidth = 8; accesswidth = 8;
        field { sw = rw; hw = r; reset = 8'h6d; } value[7:0];
    } single_byte @ 0;
"""
_MIXED = """
    reg {
        regwidth = 16; accesswidth = 16;
        field { sw = r; hw = r; reset = 16'h1357; } value[15:0];
    } readonly @ 0;
    reg {
        regwidth = 16; accesswidth = 16;
        field { sw = w; hw = r; reset = 16'h2468; } value[15:0];
    } writeonly @ 2;
"""
_MSB0 = """
    msb0;
    reg {
        field { sw = rw; hw = r; reset = 8'ha6; } upper[0:7];
        field { sw = rw; hw = r; reset = 12'hb35; } middle[12:23];
        field { sw = rw; hw = r; reset = 1; } flag[31:31];
    } control @ 0;
    reg {
        field { sw = r; hw = w; } value[0:7];
    } readonly @ 4;
"""
_MSB0_NARROW = """
    msb0;
    reg {
        regwidth = 8; accesswidth = 8;
        field { sw = rw; hw = r; reset = 8'h6d; } value[0:7];
    } neighbor @ 0;
    reg {
        regwidth = 16; accesswidth = 16;
        field { sw = rw; hw = r; reset = 8'ha6; } value[2:9];
    } shifted @ 1;
"""


def _generate(tmp_path, body):
    rdl = tmp_path / "narrow.rdl"
    rdl.write_text("addrmap narrow {\n" + body + "\n};\n")
    main([str(rdl), "-o", str(tmp_path), "-t", "axi4l", "tb_axi4l"])
    return tmp_path / "narrow_regs.v"


def _simulate(simulator, dut, tb, tmp_path):
    runners = {
        "icarus": _run_icarus,
        "verilator": _run_verilator,
        "questa": _run_questa,
    }
    returncode, output = runners[simulator]("narrow", dut, tb, tmp_path)
    assert returncode == 0, f"{simulator} exited {returncode}:\n{output}"
    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"


# Hold responses until checked; bound missing handshakes with a watchdog.
_WATCHDOG = """
    initial begin
        #100000;
        $fatal(1, "TEST FAILED: narrow-register transaction timeout");
    end
"""
_DIRECTED_TB = """
`timescale 1ns / 1ps
module tb_narrow_regs;
    localparam integer AW = @AW@;
    reg clk = 0;
    always #5 clk = ~clk;
    reg resetn = 0;
    reg [AW-1:0] awaddr = 0, araddr = 0;
    reg awvalid = 0, wvalid = 0, arvalid = 0;
    reg bready = 0, rready = 0;
    reg [31:0] wdata = 0;
    reg [3:0] wstrb = 0;
    wire awready, wready, bvalid, arready, rvalid;
    wire [1:0] bresp, rresp;
    wire [31:0] rdata;
    @DECLARATIONS@

    narrow_regs DUT (
        .s_axi_aclk(clk), .s_axi_aresetn(resetn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(3'b000),
        .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(3'b000),
        .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid), .s_axi_rready(rready)
        @CONNECTIONS@
    );

    task reset_dut;
        begin
            @(negedge clk);
            resetn = 0;
            repeat (4) @(posedge clk);
            @(negedge clk);
@RESET_CHECKS@
            resetn = 1;
            repeat (2) @(negedge clk);
        end
    endtask

    task write_word(input [AW-1:0] addr, input [31:0] data,
                    input [3:0] strb, input [1:0] expected_resp = 2'b00);
        begin
            @(negedge clk);
            awaddr = addr; awvalid = 1;
            wdata = data; wstrb = strb; wvalid = 1;
            fork
                begin
                    @(posedge clk);
                    while (!awready) @(posedge clk);
                    @(negedge clk);
                    awvalid = 0;
                end
                begin
                    @(posedge clk);
                    while (!wready) @(posedge clk);
                    @(negedge clk);
                    wvalid = 0;
                end
            join
            @(posedge clk);
            while (!bvalid) @(posedge clk);
            if (bresp !== expected_resp)
                $fatal(1, "TEST FAILED: write addr=%h strb=%h resp=%h expected=%h",
                       addr, strb, bresp, expected_resp);
            @(negedge clk);
            bready = 1;
            @(posedge clk);
            @(negedge clk);
            bready = 0;
        end
    endtask

    task expect_word(input [AW-1:0] addr, input [31:0] expected,
                     input [1:0] expected_resp = 2'b00);
        begin
            @(negedge clk);
            araddr = addr; arvalid = 1;
            @(posedge clk);
            while (!arready) @(posedge clk);
            @(negedge clk);
            arvalid = 0;
            @(posedge clk);
            while (!rvalid) @(posedge clk);
            if (rresp !== expected_resp || rdata !== expected)
                $fatal(1, "TEST FAILED: read addr=%h got=%h expected=%h resp=%h expected_resp=%h",
                       addr, rdata, expected, rresp, expected_resp);
            @(negedge clk);
            rready = 1;
            @(posedge clk);
            @(negedge clk);
            rready = 0;
        end
    endtask

    @HELPERS@
    @WATCHDOG@
    initial begin
        // These widths come from the fixture's byte addresses, not metadata.
        if ($bits(DUT.s_axi_awaddr) != AW || $bits(DUT.s_axi_araddr) != AW)
            $fatal(1, "TEST FAILED: unexpected AXI byte-address port width");
        reset_dut;
        @STEPS@
        $display("TEST PASSED");
        $finish;
    end
endmodule
"""


def _directed(tmp_path, simulator, body, addr_width, hardware, steps,
              helpers="", reset_checks=""):
    """hardware maps explicit port names to (width, input initial value or None)."""
    dut = _generate(tmp_path, body)
    declarations = []
    connections = []
    for name, (width, initial) in hardware.items():
        if initial is None:
            declarations.append(f"wire [{width - 1}:0] {name};")
        else:
            declarations.append(f"reg [{width - 1}:0] {name} = {initial};")
        connections.append(f", .{name}({name})")
    replacements = {
        "AW": str(addr_width),
        "DECLARATIONS": "\n    ".join(declarations),
        "CONNECTIONS": "\n        ".join(connections),
        "RESET_CHECKS": indent(dedent(reset_checks).strip(), "            "),
        "HELPERS": helpers,
        "WATCHDOG": _WATCHDOG,
        "STEPS": steps,
    }
    source = _DIRECTED_TB
    for token, value in replacements.items():
        source = source.replace(f"@{token}@", value)
    tb = tmp_path / "directed.sv"
    tb.write_text(source)
    _simulate(simulator, dut, tb, tmp_path)


@pytest.mark.parametrize("with_third", [False, True], ids=["one_word", "two_words"])
def test_packed_halfwords(tmp_path, simulator, with_third):
    hardware = {"first_value_out": (16, None), "second_value_out": (16, None)}
    if with_third:
        hardware["third_value_out"] = (32, None)
    _directed(
        tmp_path, simulator, _PACKED + (_THIRD if with_third else ""),
        3, hardware,
        """
        check_pair(32'habcd1234);
        write_word(0, 32'h87654321, 4'hf); check_pair(32'h87654321);
        write_word(0, 32'hffffbbaa, 4'h3); check_pair(32'h8765bbaa);
        write_word(2, 32'h24680000, 4'hc); check_pair(32'h2468bbaa);
        write_word(2, 32'hffffff19, 4'h1); check_pair(32'h2468bb19);
        write_word(0, 32'hffff2aff, 4'h2); check_pair(32'h24682a19);
        write_word(2, 32'hff3bffff, 4'h4); check_pair(32'h243b2a19);
        write_word(0, 32'h4cffffff, 4'h8); check_pair(32'h4c3b2a19);
        write_word(2, 32'hffffffff, 4'h0); check_pair(32'h4c3b2a19);
        """ + ("""
        // The adjacent aligned word must neither alias nor be clobbered.
        expect_word(4, 32'h89abcdef);
        write_word(4, 32'h10203040, 4'hf);
        expect_word(4, 32'h10203040);
        if (third_value_out !== 32'h10203040)
            $fatal(1, "TEST FAILED: third hardware output");
        check_pair(32'h4c3b2a19);
        """ if with_third else """
        expect_word(4, 32'h0, 2'b10);
        write_word(4, 32'hffffffff, 4'hf, 2'b10);
        expect_word(7, 32'h0, 2'b10);
        write_word(7, 32'hffffffff, 4'hf, 2'b10);
        check_pair(32'h4c3b2a19);
        """) + """
        reset_dut;
        check_pair(32'habcd1234);
        """ + ("expect_word(4, 32'h89abcdef);" if with_third else ""),
        helpers="""
        task check_pair(input [31:0] expected);
            begin
                expect_word(0, expected);
                expect_word(2, expected);
                if ({second_value_out, first_value_out} !== expected)
                    $fatal(1, "TEST FAILED: halfword hardware outputs");
            end
        endtask
        """,
    )


def test_nested_byte_array(tmp_path, simulator):
    _directed(
        tmp_path, simulator,
        """
        regfile {
            reg {
                regwidth = 8; accesswidth = 8;
                field { sw = rw; hw = r; reset = 8'h5a; } value[7:0];
            } bytes[4] @ 0 += 1;
        } bank @ 0x20;
        """,
        6, {f"bank_bytes_{index}_value_out": (8, None) for index in range(4)},
        """
        check_bytes(32'h5a5a5a5a);
        write_word('h20, 32'h44332211, 4'hf); check_bytes(32'h44332211);
        write_word('h20, 32'hffffffa1, 4'h1); check_bytes(32'h443322a1);
        write_word('h21, 32'hffffb2ff, 4'h2); check_bytes(32'h4433b2a1);
        write_word('h22, 32'hffc3ffff, 4'h4); check_bytes(32'h44c3b2a1);
        write_word('h23, 32'hd4ffffff, 4'h8); check_bytes(32'hd4c3b2a1);
        write_word('h22, 32'hffffffff, 4'h0); check_bytes(32'hd4c3b2a1);
        write_word('h20, 32'hffff8877, 4'h3); check_bytes(32'hd4c38877);
        write_word('h22, 32'h6655ffff, 4'hc); check_bytes(32'h66558877);
        reset_dut;
        check_bytes(32'h5a5a5a5a);
        """,
        helpers="""
        task check_bytes(input [31:0] expected);
            begin
                expect_word('h20, expected);
                expect_word('h21, expected);
                expect_word('h22, expected);
                expect_word('h23, expected);
                if ({bank_bytes_3_value_out, bank_bytes_2_value_out,
                     bank_bytes_1_value_out, bank_bytes_0_value_out} !== expected)
                    $fatal(1, "TEST FAILED: nested byte hardware outputs");
            end
        endtask
        """,
    )


@pytest.mark.parametrize("width", [8, 16, 32])
@pytest.mark.parametrize("base", [0, 4])
def test_unused_word_returns_slverr(tmp_path, simulator, width, base):
    _directed(
        tmp_path, simulator,
        f"""
        reg {{
            regwidth = {width};
            field {{ sw = rw; hw = r; reset = 0x5a; }} value[{width - 1}:0];
        }} target @ {base};
        """,
        3, {"target_value_out": (width, None)},
        f"""
        expect_word({base}, 32'h5a);
        write_word({base}, 32'hc3, 4'hf);
        for (integer i = {4 - base}; i < {8 - base}; i = i + 1) begin
            expect_word(3'(i), 32'h0, 2'b10);
            write_word(3'(i), 32'hffffffff, 4'hf, 2'b10);
            write_word(3'(i), 32'hffffffff, 4'h0, 2'b10);
        end
        expect_word({base}, 32'hc3);
        if (target_value_out !== {width}'hc3)
            $fatal(1, "TEST FAILED: unmapped write changed register");
        """,
    )


def test_single_byte_minimum_address_width(tmp_path, simulator):
    _directed(
        tmp_path, simulator, _SINGLE, 3, {"single_byte_value_out": (8, None)},
        """
        check_byte(32'h0000006d);
        write_word(0, 32'hfedcbab5, 4'hf); check_byte(32'h000000b5);
        write_word(0, 32'hffffffff, 4'he); check_byte(32'h000000b5);
        write_word(0, 32'hffffffff, 4'h0); check_byte(32'h000000b5);
        write_word(0, 32'hffffff3c, 4'h1); check_byte(32'h0000003c);
        reset_dut;
        check_byte(32'h0000006d);
        """,
        helpers="""
        task check_byte(input [31:0] expected);
            begin
                expect_word(0, expected);
                if (single_byte_value_out !== expected[7:0])
                    $fatal(1, "TEST FAILED: single byte hardware output");
            end
        endtask
        """,
    )


def test_unaligned_field_crosses_byte_lanes(tmp_path, simulator):
    _directed(
        tmp_path, simulator,
        """
        reg {
            regwidth = 16; accesswidth = 16;
            field { sw = rw; hw = r; reset = 8'ha6; } value[11:4];
        } shifted @ 1;
        """,
        3, {"shifted_value_out": (8, None)},
        """
        // Register bits [11:4] at byte 1 occupy AXI bits [19:12].
        check_shifted(32'h000a6000, 8'ha6);
        write_word(1, 32'h12345678, 4'hf); check_shifted(32'h00045000, 8'h45);
        write_word(0, 32'hffffbfff, 4'h2); check_shifted(32'h0004b000, 8'h4b);
        write_word(1, 32'hfff2ffff, 4'h4); check_shifted(32'h0002b000, 8'h2b);
        write_word(0, 32'h00000000, 4'h1); check_shifted(32'h0002b000, 8'h2b);
        write_word(1, 32'h00000000, 4'h8); check_shifted(32'h0002b000, 8'h2b);
        write_word(2, 32'hffffffff, 4'h0); check_shifted(32'h0002b000, 8'h2b);
        write_word(0, 32'hfffdefff, 4'h6); check_shifted(32'h000de000, 8'hde);
        reset_dut;
        check_shifted(32'h000a6000, 8'ha6);
        """,
        helpers="""
        task check_shifted(input [31:0] expected, input [7:0] field_value);
            begin
                expect_word(0, expected);
                expect_word(1, expected);
                expect_word(2, expected);
                if (shifted_value_out !== field_value)
                    $fatal(1, "TEST FAILED: shifted field hardware output");
            end
        endtask
        """,
    )


def test_packed_readonly_writeonly(tmp_path, simulator):
    _directed(
        tmp_path, simulator, _MIXED, 3,
        {"readonly_value_out": (16, None), "writeonly_value_out": (16, None)},
        """
        check_permissions(16'h2468);
        // Permissions apply to the whole bus word, not the byte address.
        write_word(0, 32'ha1b2ffff, 4'hf); check_permissions(16'ha1b2);
        write_word(2, 32'hffffffff, 4'h3); check_permissions(16'ha1b2);
        write_word(2, 32'hd4e50000, 4'hc); check_permissions(16'hd4e5);
        write_word(0, 32'h00aaffff, 4'h4); check_permissions(16'hd4aa);
        write_word(2, 32'hbb00ffff, 4'h8); check_permissions(16'hbbaa);
        write_word(0, 32'hffffffff, 4'h0); check_permissions(16'hbbaa);
        reset_dut;
        check_permissions(16'h2468);
        """,
        helpers="""
        task check_permissions(input [15:0] expected_wo);
            begin
                // WO reset and written data must never leak into AXI reads.
                expect_word(0, 32'h00001357);
                expect_word(2, 32'h00001357);
                if (readonly_value_out !== 16'h1357 ||
                    writeonly_value_out !== expected_wo)
                    $fatal(1, "TEST FAILED: mixed permission hardware outputs");
            end
        endtask
        """,
    )


def test_msb0_fields(tmp_path, simulator):
    _directed(
        tmp_path, simulator, _MSB0, 3,
        {"control_upper_out": (8, None), "control_middle_out": (12, None),
         "control_flag_out": (1, None), "readonly_value_in": (8, "8'h69")},
        """
        check_control(32'ha60b3501, 8'ha6, 12'hb35, 1'b1);
        write_word(0, 32'h12345678, 4'hf);
        check_control(32'h12045600, 8'h12, 12'h456, 1'b0);
        write_word(0, 32'hffffffff, 4'h1);
        check_control(32'h12045601, 8'h12, 12'h456, 1'b1);
        write_word(0, 32'hd4ffffff, 4'h8);
        check_control(32'hd4045601, 8'hd4, 12'h456, 1'b1);
        write_word(0, 32'hffffff00, 4'h1);
        check_control(32'hd4045600, 8'hd4, 12'h456, 1'b0);
        write_word(0, 32'hffffabff, 4'h2);
        check_control(32'hd404ab00, 8'hd4, 12'h4ab, 1'b0);
        write_word(0, 32'hfff3ffff, 4'h4);
        check_control(32'hd403ab00, 8'hd4, 12'h3ab, 1'b0);
        write_word(0, 32'h89abcdef, 4'h0);
        check_control(32'hd403ab00, 8'hd4, 12'h3ab, 1'b0);
        write_word(0, 32'h89abcdef, 4'hf);
        check_control(32'h890bcd01, 8'h89, 12'hbcd, 1'b1);
        readonly_value_in = 8'hc3;
        expect_word(4, 32'hc3000000);
        readonly_value_in = 8'h69;
        expect_word(4, 32'h69000000);
        readonly_value_in = 8'hc3;
        write_word(4, 32'hffffffff, 4'hf, 2'b10);
        expect_word(4, 32'hc3000000);
        reset_dut;
        check_control(32'ha60b3501, 8'ha6, 12'hb35, 1'b1);
        """,
        helpers="""
        task check_control(input [31:0] expected, input [7:0] upper,
                           input [11:0] middle, input flag);
            begin
                expect_word(0, expected);
                if (control_upper_out !== upper || control_middle_out !== middle ||
                    control_flag_out !== flag)
                    $fatal(1, "TEST FAILED: msb0 hardware outputs");
            end
        endtask
        """,
    )


def test_msb0_narrow_cross_byte_field(tmp_path, simulator):
    _directed(
        tmp_path, simulator, _MSB0_NARROW, 3,
        {"neighbor_value_out": (8, None), "shifted_value_out": (8, None)},
        """
        check_fields(32'h0029806d, 8'ha6, 8'h6d);
        write_word(1, 32'h0014c022, 4'hf);
        check_fields(32'h0014c022, 8'h53, 8'h22);
        write_word(0, 32'hffff3fff, 4'h2);
        check_fields(32'h00140022, 8'h50, 8'h22);
        write_word(1, 32'hff2dffff, 4'h4);
        check_fields(32'h002d0022, 8'hb4, 8'h22);
        write_word(0, 32'hffff7fff, 4'h2);
        check_fields(32'h002d4022, 8'hb5, 8'h22);
        write_word(1, 32'hffffffff, 4'h8);
        check_fields(32'h002d4022, 8'hb5, 8'h22);
        write_word(0, 32'h00000000, 4'h0);
        check_fields(32'h002d4022, 8'hb5, 8'h22);
        write_word(1, 32'h0000009a, 4'h1);
        check_fields(32'h002d409a, 8'hb5, 8'h9a);
        reset_dut;
        check_fields(32'h0029806d, 8'ha6, 8'h6d);
        """,
        helpers="""
        task check_fields(input [31:0] expected, input [7:0] shifted,
                          input [7:0] neighbor);
            begin
                expect_word(0, expected);
                expect_word(1, expected);
                if (shifted_value_out !== shifted || neighbor_value_out !== neighbor)
                    $fatal(1, "TEST FAILED: narrow msb0 hardware outputs");
            end
        endtask
        """,
    )


@pytest.mark.parametrize("numbering", ["lsb0", "msb0"])
def test_high_lane_software_hardware_merge(tmp_path, simulator, numbering):
    body = """
        reg {
            regwidth = 16; accesswidth = 16;
            field { sw = rw; hw = r; reset = 16'h1357; } value[15:0];
        } neighbor @ 0;
        reg {
            regwidth = 16; accesswidth = 16;
            field { sw = rw; hw = rw; reset = 16'h2468; } value[15:0];
        } merged @ 2;
    """
    if numbering == "msb0":
        body = "msb0;\n" + body.replace("[15:0]", "[0:15]")
    _directed(
        tmp_path, simulator, body,
        3, {"neighbor_value_out": (16, None), "merged_value_out": (16, None),
            "merged_value_in": (16, "16'h69c3")},
        """
        expect_word(0, 32'h69c31357);
        // Lanes 2 and 3 select the low and high field bytes independently.
        merged_write(0, 32'ha1b2fedc, 4'h4, 16'h69b2, 32'h69c31357);
        merged_write(2, 32'ha1b2fedc, 4'h8, 16'ha1c3, 32'h69c31357);
        merged_write(0, 32'hd4e5ffff, 4'hc, 16'hd4e5, 32'h69c31357);
        merged_write(2, 32'hffff8877, 4'h3, 16'h69c3, 32'h69c38877);
        merged_write(0, 32'hffffaa22, 4'h1, 16'h69c3, 32'h69c38822);
        merged_write(2, 32'hffff3344, 4'h2, 16'h69c3, 32'h69c33322);
        merged_write(0, 32'h1020abcd, 4'hf, 16'h1020, 32'h69c3abcd);
        merged_write(2, 32'hffffffff, 4'h0, 16'h69c3, 32'h69c3abcd);
        if (issue_checks != 8)
            $fatal(1, "TEST FAILED: missing merged-field issue checks");
        """,
        reset_checks="""
        if (merged_value_out !== 16'h2468 || neighbor_value_out !== 16'h1357)
            $fatal(1, "TEST FAILED: hardware/software field reset");
        """,
        helpers="""
        reg [15:0] expected_at_issue = 0;
        integer issue_checks = 0;
        // Sample the transient output before hardware overwrites it next cycle.
        always @(posedge clk) begin
            if (resetn && DUT.int_wr_en) begin
                #1;
                if (merged_value_out !== expected_at_issue)
                    $fatal(1, "TEST FAILED: merged output got=%h expected=%h",
                           merged_value_out, expected_at_issue);
                issue_checks = issue_checks + 1;
            end
        end

        task merged_write(input [AW-1:0] addr, input [31:0] data,
                          input [3:0] strb, input [15:0] transient_value,
                          input [31:0] settled_word);
            integer before_checks;
            begin
                before_checks = issue_checks;
                expected_at_issue = transient_value;
                write_word(addr, data, strb);
                if (issue_checks != before_checks + 1)
                    $fatal(1, "TEST FAILED: write issue not observed exactly once");
                expect_word(0, settled_word);
                expect_word(2, settled_word);
                if ({merged_value_out, neighbor_value_out} !== settled_word)
                    $fatal(1, "TEST FAILED: settled hardware/neighbor outputs");
            end
        endtask
        """,
    )


@pytest.mark.parametrize("body", [
    pytest.param(_PACKED, id="packed"),
    pytest.param(_PACKED + _THIRD, id="packed_with_third"),
    pytest.param(_MIXED, id="mixed_permissions"),
    pytest.param(_SINGLE, id="single_byte"),
    pytest.param(_MSB0, id="msb0"),
    pytest.param(_MSB0_NARROW, id="msb0_narrow"),
])
def test_generated_narrow_testbench(tmp_path, simulator, body):
    dut = _generate(tmp_path, body)
    generated = (tmp_path / "tb_narrow_regs.v").read_text()
    # Keep generated checks intact; .sv enables SystemVerilog for Questa.
    tb = tmp_path / "generated_runner.sv"
    tb.write_text(generated.replace("\nendmodule", _WATCHDOG + "\nendmodule", 1))
    _simulate(simulator, dut, tb, tmp_path)
