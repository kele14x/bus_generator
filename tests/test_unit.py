#!/usr/bin/env python3
"""Pure-Python unit tests for the bus_generator CLI and internals."""

import importlib
import importlib.metadata
import subprocess
import sys

import pytest

from bus_generator import main
import bus_generator.bus_generator as bus_generator_module
from bus_generator.bus_generator import (
    FieldsGatheringListener,
    MemGatheringListener,
    RegistersGatheringListener,
    convert,
    discover_templates,
    parse_arguments,
    warn_unsupported_side_effects,
)
from systemrdl import RDLCompiler, RDLWalker

GPIO_RDL = "tests/gpio.rdl"
FIELD_ACCESS_RDL = "tests/field_access.rdl"
MEM_ACCESS_RDL = "tests/mem_access.rdl"
RAM_RDL = "tests/ram.rdl"
SIMPLE_RDL = "tests/simple.rdl"
SIDE_EFFECTS_RDL = "tests/side_effects.rdl"


def _compile(rdl_path):
    rdlc = RDLCompiler()
    rdlc.compile_file(rdl_path)
    root = rdlc.elaborate()
    return root.top


def _gather(top, listener_cls):
    listener = listener_cls()
    RDLWalker(unroll=True).walk(top, listener)
    return listener


# ---------------------------------------------------------------------------
# discover_templates / parse_arguments
# ---------------------------------------------------------------------------


def test_version_prefers_distribution_metadata(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: "0.3.0"
    )

    assert bus_generator_module._resolve_version() == "0.3.0"


def test_version_is_unknown_when_distribution_metadata_is_missing(monkeypatch):
    def missing_distribution(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)

    assert bus_generator_module._resolve_version() == "unknown"


def test_import_is_safe_when_metadata_is_unavailable(monkeypatch):
    def missing_distribution(name):
        raise importlib.metadata.PackageNotFoundError(name)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(importlib.metadata, "version", missing_distribution)
            reloaded_module = importlib.reload(bus_generator_module)

            assert reloaded_module.__version__ == "unknown"
    finally:
        importlib.reload(bus_generator_module)


def test_discover_templates():
    templates = discover_templates()
    assert set(templates) == {"axi4l", "c_header", "tb_axi4l"}
    assert templates["axi4l"] == "{{axi4l}}_regs.v"
    assert templates["tb_axi4l"] == "tb_{{axi4l}}_regs.v"
    assert templates["c_header"] == "{{c_header}}.h"


def test_parse_arguments_defaults():
    args = parse_arguments(["foo.rdl"])
    assert args.input == ["foo.rdl"]
    assert args.templates == ["axi4l"]


def test_parse_arguments_invalid_template():
    with pytest.raises(SystemExit):
        parse_arguments(["foo.rdl", "-t", "bogus"])


# ---------------------------------------------------------------------------
# CLI smoke (absorbed from the old test file)
# ---------------------------------------------------------------------------


def test_version():
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0


def test_help():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


def test_cli_missing_input_raises():
    # cli() only catches RuntimeError from the compiler; a missing file raises
    # FileNotFoundError (which surfaces as a non-zero process exit when run as a
    # console script).
    with pytest.raises(FileNotFoundError):
        main(["./does_not_exist.rdl", "-o", "ignored"])


# ---------------------------------------------------------------------------
# Listeners on gpio.rdl
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gpio_top():
    return _compile(GPIO_RDL)


def test_gpio_fields(gpio_top):
    fields = _gather(gpio_top, FieldsGatheringListener).fields
    assert [f["name"] for f in fields] == ["data_data", "direction_direction"]
    by_name = {f["name"]: f for f in fields}

    data = by_name["data_data"]
    assert data["address"] == 0x0
    assert data["low"] == 0 and data["high"] == 31
    assert data["mask"] == 0xFFFFFFFF
    assert data["is_sw_writable"] and data["is_sw_readable"]
    assert data["is_hw_writable"] and data["is_hw_readable"]

    direction = by_name["direction_direction"]
    assert direction["address"] == 0x4
    assert direction["is_sw_writable"] and direction["is_sw_readable"]
    assert not direction["is_hw_writable"] and direction["is_hw_readable"]


def test_gpio_regs(gpio_top):
    regs = _gather(gpio_top, RegistersGatheringListener).regs
    assert len(regs) == 2
    assert [r["address"] for r in regs] == [0x0, 0x4]


def test_gpio_no_mems(gpio_top):
    mems = _gather(gpio_top, MemGatheringListener).mems
    assert mems == []


# ---------------------------------------------------------------------------
# Listeners on field_access.rdl
# ---------------------------------------------------------------------------


def test_field_access_permissions():
    fields = _gather(_compile(FIELD_ACCESS_RDL), FieldsGatheringListener).fields
    by_name = {f["name"]: f for f in fields}

    assert by_name["r_only_r_only"]["sw"] == "r"
    assert by_name["r_only_r_only"]["is_sw_readable"]
    assert not by_name["r_only_r_only"]["is_sw_writable"]

    assert by_name["w_only_w_only"]["sw"] == "w"
    assert not by_name["w_only_w_only"]["is_sw_readable"]
    assert by_name["w_only_w_only"]["is_sw_writable"]


# ---------------------------------------------------------------------------
# Unsupported SystemRDL side-effect compatibility warnings
# ---------------------------------------------------------------------------


def test_unsupported_side_effects_warn_with_field_path(caplog):
    top = _compile(SIDE_EFFECTS_RDL)

    with caplog.at_level("WARNING"):
        warn_unsupported_side_effects(top)

    warnings = [record.getMessage() for record in caplog.records]
    assert warnings == [
        "Ignoring unsupported SystemRDL side-effect semantics on field "
        "'side_effects.effects.read_clear': onread=rclr",
        "Ignoring unsupported SystemRDL side-effect semantics on field "
        "'side_effects.effects.write_set': onwrite=woset",
        "Ignoring unsupported SystemRDL side-effect semantics on field "
        "'side_effects.effects.write_once_rw': sw=rw1 (write-once)",
        "Ignoring unsupported SystemRDL side-effect semantics on field "
        "'side_effects.effects.write_once_w': sw=w1 (write-once)",
        "Ignoring unsupported SystemRDL side-effect semantics on memory "
        "'side_effects.write_once_mem': sw=rw1 (write-once)",
    ]


def test_ordinary_software_accesses_do_not_warn(caplog):
    top = _compile(SIDE_EFFECTS_RDL)

    with caplog.at_level("WARNING"):
        warn_unsupported_side_effects(top)

    warning_messages = [record.getMessage() for record in caplog.records]
    assert all("ordinary" not in message for message in warning_messages)


@pytest.mark.parametrize(
    ("quiet", "expect_warnings"),
    [
        pytest.param(False, True, id="default-verbosity"),
        pytest.param(True, False, id="quiet"),
    ],
)
def test_cli_reports_side_effect_warnings_at_default_verbosity(
    tmp_path, quiet, expect_warnings
):
    command = [
        sys.executable,
        "-m",
        "bus_generator.bus_generator",
        SIDE_EFFECTS_RDL,
        "-o",
        str(tmp_path),
    ]
    if quiet:
        command.append("-q")

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert (tmp_path / "side_effects_regs.v").is_file()
    assert ("Ignoring unsupported SystemRDL side-effect semantics" in result.stderr) is expect_warnings


# ---------------------------------------------------------------------------
# Listeners on ram.rdl
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ram_top():
    return _compile(RAM_RDL)


def test_ram_fields(ram_top):
    fields = _gather(ram_top, FieldsGatheringListener).fields
    assert [f["name"] for f in fields] == ["reg0_field0", "reg1_field0"]
    by_name = {f["name"]: f for f in fields}

    reg0 = by_name["reg0_field0"]
    assert reg0["address"] == 0x0
    assert reg0["is_sw_writable"] and reg0["is_sw_readable"]

    reg1 = by_name["reg1_field0"]
    assert reg1["address"] == 0x4
    assert not reg1["is_sw_writable"] and reg1["is_sw_readable"]
    assert reg1["is_hw_writable"]


def test_ram_regs(ram_top):
    regs = _gather(ram_top, RegistersGatheringListener).regs
    assert len(regs) == 2
    assert [r["address"] for r in regs] == [0x0, 0x4]


def test_ram_mems(ram_top):
    mems = _gather(ram_top, MemGatheringListener).mems
    assert len(mems) == 2
    assert {m["name"] for m in mems} == {"ram0", "ram1"}
    for mem in mems:
        assert mem["mementries"] == 14
        assert mem["size"] == 56
        assert mem["width"] == 32
        assert mem["is_sw_writable"] and mem["is_sw_readable"]
        # data_width=32 -> 4 bytes -> LSB at bit ceil(log2(4)) = 2
        assert mem["addr_lsb"] == 2
        assert mem["addr_width"] == mem["addr_msb"] - mem["addr_lsb"] + 1
    assert {m["address"] for m in mems} == {0x100, 0x140}


# ---------------------------------------------------------------------------
# Listeners on mem_access.rdl
# ---------------------------------------------------------------------------


def test_memory_access_permissions():
    mems = _gather(_compile(MEM_ACCESS_RDL), MemGatheringListener).mems
    by_name = {m["name"]: m for m in mems}

    assert by_name["mem_r"]["sw"] == "r"
    assert by_name["mem_r"]["is_sw_readable"]
    assert not by_name["mem_r"]["is_sw_writable"]

    assert by_name["mem_w"]["sw"] == "w"
    assert not by_name["mem_w"]["is_sw_readable"]
    assert by_name["mem_w"]["is_sw_writable"]

    assert by_name["mem_rw"]["sw"] == "rw"
    assert by_name["mem_rw"]["is_sw_readable"]
    assert by_name["mem_rw"]["is_sw_writable"]

    assert by_name["mem_na"]["sw"] == "na"
    assert not by_name["mem_na"]["is_sw_readable"]
    assert not by_name["mem_na"]["is_sw_writable"]


# ---------------------------------------------------------------------------
# Listeners on simple.rdl
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def simple_top():
    return _compile(SIMPLE_RDL)


def test_simple_fields(simple_top):
    fields = _gather(simple_top, FieldsGatheringListener).fields
    assert len(fields) == 16
    assert [f["name"] for f in fields] == [f"reg{i}_field0" for i in range(16)]
    assert [f["address"] for f in fields] == [i * 4 for i in range(16)]
    for field in fields:
        assert field["low"] == 0 and field["high"] == 31
        assert field["mask"] == 0xFFFFFFFF
        assert field["is_sw_writable"] and field["is_sw_readable"]
        assert not field["is_hw_writable"] and field["is_hw_readable"]


def test_simple_regs(simple_top):
    regs = _gather(simple_top, RegistersGatheringListener).regs
    assert len(regs) == 16
    assert [r["name"] for r in regs] == [f"reg{i}" for i in range(16)]
    assert [r["address"] for r in regs] == [i * 4 for i in range(16)]


def test_simple_no_mems(simple_top):
    mems = _gather(simple_top, MemGatheringListener).mems
    assert mems == []


# ---------------------------------------------------------------------------
# convert() rendered content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rdl_path,top_name",
    [
        pytest.param(GPIO_RDL, "gpio", id="gpio"),
        pytest.param(RAM_RDL, "ram", id="ram"),
        pytest.param(SIMPLE_RDL, "simple", id="simple"),
    ],
)
def test_convert_renders_module(rdl_path, top_name):
    content = convert(_compile(rdl_path), "{{axi4l}}_regs.v.jinja2")
    assert f"module {top_name}_regs (" in content
    assert "s_axi_awaddr" in content
