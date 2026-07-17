#!/usr/bin/env python3
"""RTL simulation tests.

Generate ``<top>_regs.v`` and ``tb_<top>_regs.v`` for each sample, then compile
with Icarus Verilog and run the self-checking testbench with ``vvp``. The
testbench drives all AXI traffic, counts mismatches, and ends with ``$finish``
(pass, prints ``TEST PASSED``) or ``$fatal`` (fail, prints ``TEST FAILED``). We
assert on the simulator exit code and the pass/fail banner.

Skipped automatically when ``iverilog``/``vvp`` are not on PATH.
"""

import os
import shutil
import subprocess

import pytest

from bus_generator import main

SAMPLES = [
    pytest.param("tests/gpio.rdl", "gpio", id="gpio"),
    pytest.param("tests/ram.rdl", "ram", id="ram"),
]


def _need_iverilog():
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("iverilog/vvp not installed")


@pytest.mark.sim
@pytest.mark.parametrize("rdl,top", SAMPLES)
def test_self_check_tb(rdl, top, tmp_path):
    _need_iverilog()

    main([rdl, "-o", str(tmp_path), "-t", "axi4l", "tb_axi4l"])
    dut = tmp_path / f"{top}_regs.v"
    tb = tmp_path / f"tb_{top}_regs.v"
    assert dut.is_file() and tb.is_file()

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
    output = run_proc.stdout + run_proc.stderr
    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert run_proc.returncode == 0, (
        f"vvp exited {run_proc.returncode}:\n{output}"
    )
