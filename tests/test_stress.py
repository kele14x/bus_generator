#!/usr/bin/env python3
"""Pytest wrappers that launch cocotb stress tests via the icarus runner.

Each wrapper builds the reusable ``generated/axi4l/simple_regs.v`` as the
simulation top and runs one ``@cocotb.test`` case from the merged
``cocotb_stress`` module against it (selected via the runner's ``testcase``
arg). cocotb owns pass/fail (no self-checking Verilog TB, no ``$finish``);
``runner.test()`` exits non-zero under pytest if the cocotb test fails or
times out.

Sources are read from the ``generated/`` tree (produced by ``make gen``) so
manual edits to the RTL survive a re-run. Skipped when iverilog/vvp or
cocotb_tools are unavailable, or when the generated DUT is missing.
"""

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "generated"
TESTS_DIR = Path(__file__).resolve().parent


def _run_cocotb_test(testcase, tmp_path):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("iverilog/vvp not installed")
    pytest.importorskip("cocotb_tools.runner")

    dut = GENERATED / "axi4l" / "simple_regs.v"
    if not dut.is_file():
        pytest.skip(f"missing {dut}; run `make gen` first")

    # cocotb sets PYTHONPATH=sys.path for the vvp subprocess; ensure the tests
    # dir is importable so the test module resolves regardless of pytest mode.
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    from cocotb_tools.runner import get_runner

    runner = get_runner("icarus")
    runner.build(
        sources=[str(dut)],
        hdl_toplevel="simple_regs",
        build_dir=str(tmp_path),
        always=True,
    )
    runner.test(
        test_module="cocotb_stress",
        hdl_toplevel="simple_regs",
        build_dir=str(tmp_path),
        test_dir=str(TESTS_DIR),
        testcase=testcase,
        seed=0xC0FFEE,
    )


@pytest.mark.sim
def test_stress_axi4l_simple(tmp_path):
    _run_cocotb_test("stress_random_axi", tmp_path)


@pytest.mark.sim
def test_stress_b_backpressure(tmp_path):
    _run_cocotb_test("stress_b_backpressure", tmp_path)
