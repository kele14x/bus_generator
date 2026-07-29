#!/usr/bin/env python3
"""Generation tests: render every output format and check the produced files."""

import os

import pytest

from bus_generator import main

SAMPLES = [
    pytest.param("tests/gpio.rdl", "gpio", id="gpio"),
    pytest.param("tests/mem_access.rdl", "mem_access", id="mem_access"),
    pytest.param("tests/ram.rdl", "ram", id="ram"),
    pytest.param("tests/simple.rdl", "simple", id="simple"),
    pytest.param("tests/wstrb.rdl", "wstrb", id="wstrb"),
]

# alias -> (output filename relative to out dir, substrings the file must contain)
EXPECTED = {
    "axi4l": (lambda top: f"{top}_regs.v", ["module {top}_regs ("]),
    "c_header": (lambda top: f"{top}.h", ["#define"]),
    "tb_axi4l": (lambda top: f"tb_{top}_regs.v", ["TEST PASSED", "TEST FAILED"]),
}


@pytest.mark.parametrize("rdl,top", SAMPLES)
@pytest.mark.parametrize("alias", sorted(EXPECTED))
def test_generate_alias(rdl, top, alias, tmp_path):
    out = tmp_path / alias
    main([rdl, "-o", str(out), "-t", alias])

    name_fn, substrings = EXPECTED[alias]
    target = out / name_fn(top)
    assert os.path.isfile(target), f"missing {target}"
    assert os.path.getsize(target) > 0, f"empty {target}"

    text = target.read_text()
    for s in substrings:
        assert s.format(top=top) in text, f"{target} missing {s!r}"


def test_generate_wstrb_rtl_and_testbench(tmp_path):
    """Ensure the dedicated fixture renders the byte-strobe implementation."""
    axi4l_out = tmp_path / "axi4l"
    tb_out = tmp_path / "tb_axi4l"
    main(["tests/wstrb.rdl", "-o", str(axi4l_out), "-t", "axi4l"])
    main(["tests/wstrb.rdl", "-o", str(tb_out), "-t", "tb_axi4l"])

    rtl = (axi4l_out / "wstrb_regs.v").read_text()
    assert "output wire [ 3:0] ram_be" in rtl
    assert "assign sw_byte_mask[sw_byte_idx*8 +: 8] = {8{int_wr_strb[sw_byte_idx]}};" in rtl
    assert "assign regs_mixed_sw_mask = sw_byte_mask[11:4];" in rtl
    assert "assign ram_we   = (int_wr_en && ram_strb && (|int_wr_strb));" in rtl
    assert "assign ram_be   = (int_wr_en && ram_strb) ? int_wr_strb : {STRB_WIDTH{1'b0}};" in rtl

    tb = (tb_out / "tb_wstrb_regs.v").read_text()
    assert "task axi_write_be" in tb
    assert "be =  4'h2;" in tb  # The fixture has software and mixed cross-byte fields.
    assert "readable access signal mismatch" in tb
    assert "WSTRB=0 issued a physical memory access" in tb
    assert '$display("Read: addr = %x, data = %x, resp = %x", addr, data, resp);' in tb


def test_generate_memory_access_permissions(tmp_path):
    """Render each memory access mode into permission-gated physical signals."""
    out = tmp_path / "axi4l"
    main(["tests/mem_access.rdl", "-o", str(out), "-t", "axi4l"])

    rtl = (out / "mem_access_regs.v").read_text()

    assert "assign mem_r_en   = (int_rd_en && mem_r_strb);" in rtl
    assert "assign mem_r_we   = 1'b0;" in rtl
    assert "assign mem_r_be   = {STRB_WIDTH{1'b0}};" in rtl

    assert "assign mem_w_en   = (int_wr_en && mem_w_strb && (|int_wr_strb));" in rtl
    assert "assign mem_w_we   = (int_wr_en && mem_w_strb && (|int_wr_strb));" in rtl

    assert "assign mem_rw_en   = ((int_rd_en && mem_rw_strb) ||" in rtl
    assert "assign mem_rw_we   = (int_wr_en && mem_rw_strb && (|int_wr_strb));" in rtl

    assert "assign mem_na_en   = 1'b0;" in rtl
    assert "assign mem_na_we   = 1'b0;" in rtl
    assert "assign mem_na_be   = {STRB_WIDTH{1'b0}};" in rtl

    tb_out = tmp_path / "tb_axi4l"
    main(["tests/mem_access.rdl", "-o", str(tb_out), "-t", "tb_axi4l"])
    tb = (tb_out / "tb_mem_access_regs.v").read_text()
    assert "task check_error_resp" in tb
    assert "mem_r prohibited write reached external memory" in tb
    assert "mem_w prohibited read reached external memory" in tb
    assert "mem_na prohibited write reached external memory" in tb
    assert "mem_na prohibited read reached external memory" in tb
