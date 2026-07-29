#!/usr/bin/env python3
"""RTL simulation tests.

Compile the reusable ``<top>_regs.v`` with its self-checking ``tb_<top>_regs.v``
testbench, then run the selected simulator. The testbench drives all AXI traffic,
counts mismatches, and ends with ``$finish`` (pass, prints ``TEST PASSED``) or
``$fatal`` (fail, prints ``TEST FAILED``). We assert on the simulator exit code
and the pass/fail banner.

The Verilog sources are read from the ``generated/`` tree (produced by
``make gen``) so manual edits to those files are picked up by re-running
``make sim`` — the test never regenerates over them. Set ``SIM=verilator`` to
run with Verilator; the default is ``SIM=icarus``.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"

SAMPLES = [
    pytest.param("gpio", id="gpio"),
    pytest.param("mem_access", id="mem_access"),
    pytest.param("ram", id="ram"),
    pytest.param("simple", id="simple"),
    pytest.param("wstrb", id="wstrb"),
]


def _selected_simulator():
    return os.environ.get("SIM", "icarus").lower()


def _need_simulator(sim):
    if sim == "icarus":
        if not shutil.which("iverilog") or not shutil.which("vvp"):
            pytest.skip("iverilog/vvp not installed")
    elif sim == "verilator":
        if not shutil.which("verilator"):
            pytest.skip("verilator not installed")
    else:
        pytest.fail(f"unsupported SIM={sim!r}; expected 'icarus' or 'verilator'")


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


@pytest.mark.sim
@pytest.mark.parametrize("top", SAMPLES)
def test_self_check_tb(top, tmp_path):
    sim = _selected_simulator()
    _need_simulator(sim)

    dut = GENERATED / "axi4l" / f"{top}_regs.v"
    tb = GENERATED / "tb_axi4l" / f"tb_{top}_regs.v"
    if not dut.is_file() or not tb.is_file():
        pytest.skip(f"missing {dut.name}/{tb.name}; run `make gen` first")

    if sim == "icarus":
        returncode, output = _run_icarus(top, dut, tb, tmp_path)
    else:
        returncode, output = _run_verilator(top, dut, tb, tmp_path)

    assert "TEST PASSED" in output, f"TB did not pass:\n{output}"
    assert "TEST FAILED" not in output, f"TB reported failures:\n{output}"
    assert returncode == 0, f"{sim} exited {returncode}:\n{output}"
