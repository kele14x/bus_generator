#!/usr/bin/env python3
"""Pure-Python unit tests for the bus_generator CLI and internals."""

import pytest

from bus_generator import main
from bus_generator.bus_generator import (
    FieldsGatheringListener,
    MemGatheringListener,
    RegistersGatheringListener,
    convert,
    discover_templates,
    parse_arguments,
)
from systemrdl import RDLCompiler, RDLWalker

GPIO_RDL = "tests/gpio.rdl"
RAM_RDL = "tests/ram.rdl"


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


def test_discover_templates():
    templates = discover_templates()
    assert set(templates) == {"axi4l", "avalon_mm", "c_header", "tb_axi4l"}
    assert templates["axi4l"] == "{{axi4l}}_regs.v"
    assert templates["tb_axi4l"] == "tb_{{axi4l}}_regs.v"
    assert templates["avalon_mm"] == "{{avalon_mm}}_regs.v"
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
        assert mem["width"] == 32
        assert mem["is_sw_writable"] and mem["is_sw_readable"]
        # data_width=32 -> 4 bytes -> LSB at bit ceil(log2(4)) = 2
        assert mem["addr_lsb"] == 2
        assert mem["addr_width"] == mem["addr_msb"] - mem["addr_lsb"] + 1
    assert {m["address"] for m in mems} == {0x100, 0x140}


# ---------------------------------------------------------------------------
# convert() rendered content
# ---------------------------------------------------------------------------


def test_convert_gpio_renders_module(gpio_top):
    content = convert(gpio_top, "{{axi4l}}_regs.v.jinja2")
    assert "module gpio_regs (" in content
    assert "s_axi_awaddr" in content
