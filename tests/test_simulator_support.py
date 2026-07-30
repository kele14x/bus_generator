"""Unit tests for simulator-name normalization and availability decisions."""

import pytest

from simulator_support import missing_simulator_commands, normalize_simulator, simulator_commands


@pytest.mark.parametrize(
    ("requested", "normalized"),
    [
        ("icarus", "icarus"),
        ("VERILATOR", "verilator"),
        ("questa", "questa"),
        ("VSIM", "questa"),
    ],
)
def test_normalize_simulator(requested, normalized):
    assert normalize_simulator(requested) == normalized


def test_questa_availability_requires_vlib_vlog_and_vsim():
    available = {"vlib", "vsim"}

    assert simulator_commands(normalize_simulator("vsim")) == ("vlib", "vlog", "vsim")
    assert missing_simulator_commands("questa", available.__contains__) == ("vlog",)


def test_other_simulator_availability_requirements_are_preserved():
    assert missing_simulator_commands("icarus", lambda command: command == "iverilog") == (
        "vvp",
    )
    assert missing_simulator_commands("verilator", lambda command: False) == ("verilator",)


def test_unknown_simulator_is_rejected():
    with pytest.raises(ValueError, match="unsupported SIM='xcelium'"):
        simulator_commands("xcelium")
