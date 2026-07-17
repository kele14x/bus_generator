#!/usr/bin/env python3
"""RTL simulation tests.

Compile the reusable ``<top>_regs.v`` with its self-checking ``tb_<top>_regs.v``
testbench using Icarus Verilog, then run with ``vvp``. The testbench drives all
AXI traffic, counts mismatches, and ends with ``$finish`` (pass, prints
``TEST PASSED``) or ``$fatal`` (fail, prints ``TEST FAILED``). We assert on the
simulator exit code and the pass/fail banner.

The Verilog sources are read from the ``generated/`` tree (produced by
``make gen``) so manual edits to those files are picked up by re-running
``make sim`` — the test never regenerates over them. Skipped automatically when
``iverilog``/``vvp`` are not on PATH or when the generated files are missing
(run ``make gen`` first).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"

SAMPLES = [
    pytest.param("gpio", id="gpio"),
    pytest.param("ram", id="ram"),
]


def _need_iverilog():
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("iverilog/vvp not installed")


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_self_check_tb(top, tmp_path):
    _need_iverilog()

    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    tb = GENERATED / "tb_axi4l" / f"tb_{top}_regs.v"
    if not dut.is_file() or not tb.is_file():
        pytest.skip(f"missing {dut.name}/{tb.name}; run `make gen` first")

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
