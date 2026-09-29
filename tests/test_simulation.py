#!/usr/bin/env python3
"""Run generated Verilog testbenches with the simulator selected by SIM."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from bus_generator import main

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"


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
@pytest.mark.parametrize("top", ["field_access", "gpio", "mem_access", "ram", "simple", "wstrb"])
def test_self_check_tb(top, tmp_path):
    sim = os.environ["SIM"]

    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    tb = GENERATED / "tb_axi4l" / f"tb_{top}_regs.v"
    if not dut.is_file() or not tb.is_file():
        pytest.skip(f"missing {dut.name}/{tb.name}; run `make artifacts` first")

    returncode, output = {
        "icarus": _run_icarus,
        "verilator": _run_verilator,
        "questa": _run_questa,
    }[sim](top, dut, tb, tmp_path)

    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"


@pytest.mark.sim
@pytest.mark.parametrize("base", [0x0, 0x4, 0x100])
def test_single_entry_memory_tb(tmp_path, base):
    sim = os.environ["SIM"]
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
    returncode, output = {
        "icarus": _run_icarus,
        "verilator": _run_verilator,
        "questa": _run_questa,
    }[sim](top, dut, tb, tmp_path)

    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
