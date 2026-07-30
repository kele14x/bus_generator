"""Unit tests for simulator-name normalization and availability decisions."""

import pytest

from simulator_support import (
    missing_simulator_commands,
    normalize_simulator,
    require_simulator,
    selected_simulator,
    simulator_commands,
)


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


@pytest.mark.parametrize("requested", (None, "", "   "))
def test_selected_simulator_requires_an_explicit_nonblank_sim(requested):
    environ = {} if requested is None else {"SIM": requested}

    with pytest.raises(ValueError, match="SIM is required for simulator-marked tests") as error:
        selected_simulator(environ)

    assert "SIM=icarus, SIM=verilator, SIM=questa, or SIM=vsim" in str(error.value)


def test_selected_simulator_normalizes_vsim_alias():
    assert selected_simulator({"SIM": "vsim"}) == "questa"


def test_questa_availability_requires_vlib_vlog_and_vsim():
    available = {"vlib", "vsim"}

    assert simulator_commands(normalize_simulator("vsim")) == ("vlib", "vlog", "vsim")
    assert missing_simulator_commands("questa", available.__contains__) == ("vlog",)


def test_other_simulator_availability_requirements_are_preserved():
    assert missing_simulator_commands("icarus", lambda command: command == "iverilog") == (
        "vvp",
    )
    assert missing_simulator_commands("verilator", lambda command: False) == ("verilator",)


@pytest.mark.parametrize(
    ("requested", "available", "missing"),
    [
        ("icarus", {"iverilog"}, "vvp"),
        ("verilator", set(), "verilator"),
        ("questa", {"vlib", "vsim"}, "vlog"),
        ("vsim", {"vlib", "vlog"}, "vsim"),
    ],
)
def test_require_simulator_fails_with_missing_executables_without_system_simulator(
    requested, available, missing
):
    with pytest.raises(RuntimeError, match="requires executables") as error:
        require_simulator({"SIM": requested}, available.__contains__)

    assert f"missing from PATH: {missing}" in str(error.value)


def test_require_simulator_accepts_available_simulator_without_system_simulator():
    assert require_simulator({"SIM": "verilator"}, lambda command: command == "verilator") == "verilator"


def test_unknown_simulator_is_rejected():
    with pytest.raises(ValueError, match="unsupported SIM='xcelium'"):
        simulator_commands("xcelium")
