#!/usr/bin/env python3
"""Pure-Python unit tests for the bus_generator CLI and internals."""

import importlib
import importlib.metadata
import re
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

GPIO_RDL = "samples/gpio.rdl"
FIELD_ACCESS_RDL = "samples/field_access.rdl"
MEM_ACCESS_RDL = "samples/mem_access.rdl"
RAM_RDL = "samples/ram.rdl"
SIMPLE_RDL = "samples/simple.rdl"
SIDE_EFFECTS_RDL = "samples/side_effects.rdl"


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
    args = parse_arguments(["foo.rdl", "--print"])
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


def test_cli_requires_output_or_print():
    result = subprocess.run(
        [sys.executable, "-m", "bus_generator.bus_generator", GPIO_RDL],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "error: either --output or --print is required" in result.stderr


def test_cli_print_without_output_displays_hierarchy():
    result = subprocess.run(
        [sys.executable, "-m", "bus_generator.bus_generator", GPIO_RDL, "--print"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "gpio @0x0(0x0) addrmap, size: 8" in result.stdout
    assert "\tdata @0x0(0x0) reg" in result.stdout


def test_cli_generates_with_output(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bus_generator.bus_generator",
            GPIO_RDL,
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (tmp_path / "gpio_regs.v").is_file()
    assert result.stdout == ""


def test_cli_generates_with_nested_output(tmp_path):
    output_dir = tmp_path / "build" / "generated"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bus_generator.bus_generator",
            GPIO_RDL,
            "--output",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (output_dir / "gpio_regs.v").is_file()
    assert result.stdout == ""


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
        pytest.param(MEM_ACCESS_RDL, "mem_access", id="mem_access"),
    ],
)
def test_convert_renders_module(rdl_path, top_name):
    content = convert(_compile(rdl_path), "{{axi4l}}_regs.v.jinja2")
    assert f"module {top_name}_regs (" in content
    assert "s_axi_awaddr" in content


def _compact_verilog(content):
    """Ignore formatting/comments, but retain expressions and signal widths."""
    return re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", content))


def _assigned_expression(content, signal):
    assignments = re.findall(rf"assign{re.escape(signal)}=([^;]+);", content)
    assert len(assignments) == 1, f"Expected one continuous driver for {signal}"
    return assignments[0]


@pytest.mark.parametrize("rdl_path", [GPIO_RDL, SIMPLE_RDL, RAM_RDL, MEM_ACCESS_RDL])
def test_convert_renders_response_targets(rdl_path):
    top = _compile(rdl_path)
    fields = _gather(top, FieldsGatheringListener).fields
    mems = _gather(top, MemGatheringListener).mems
    content = _compact_verilog(convert(top, "{{axi4l}}_regs.v.jinja2"))

    for component in [*fields, *mems]:
        name = component["name"]
        assert f"wire{name}_sel;" in content
        assert "int_addr" in _assigned_expression(content, f"{name}_sel")
        assert f"{name}_strb" not in content
    assert "reg[STRB_WIDTH-1:0]int_wr_strb;" in content
    assert "w_back_strb<=s_axi_wstrb;" in content

    assert f"localparamintegerTARGET_COUNT={len(mems) + 1};" in content
    assert "wire[TARGET_COUNT-1:0]int_target;" in content
    assert "reg[TARGET_COUNT-1:0]int_active_target;" in content
    assert _assigned_expression(content, "int_idle") == (
        "(b_wait_ack==2'd0)&&(r_wait_ack==2'd0)"
    )
    assert _assigned_expression(content, "int_issue") == (
        "int_valid&&(int_idle||(int_target==int_active_target))"
        "&&(int_write?b_credit:r_credit)"
    )
    assert "if(int_issue&&int_idle)beginint_active_target<=int_target;" in content
    for obsolete in ("_rd_sel", "rd_mem_pending", "rd_mem_valid", "rd_mem_issue",
                     "target_allowed", "read_waiting", "write_waiting"):
        assert obsolete not in content

    targets = [f"{mem['name']}_target" for mem in mems]
    local_target = "!(" + "||".join(["1'b0"] + targets) + ")"
    assert _assigned_expression(content, "int_target") == (
        "{" + ",".join(list(reversed(targets)) + [local_target]) + "}"
    )
    if not mems:
        assert "int_target[TARGET_COUNT-1:1]" not in content
        assert "_tags" not in content
    assert "[-1:0]" not in content

    for mem in mems:
        writable = int(mem["is_sw_writable"])
        readable = int(mem["is_sw_readable"])
        # Decode before issue: zero-strobe/prohibited operations use LOCAL.
        assert _assigned_expression(content, f"{mem['name']}_target") == (
            f"{mem['name']}_sel&&((int_write&&1'b{writable}&&(|int_wr_strb))"
            f"||(!int_write&&1'b{readable}))"
        )
        for direction, allowed in (("wr", writable), ("rd", readable)):
            error_decode = _assigned_expression(content, f"local_{direction}_err_next")
            assert (f"{mem['name']}_sel" in error_decode) == bool(allowed)


@pytest.mark.parametrize("rdl_path", [GPIO_RDL, SIMPLE_RDL, RAM_RDL, MEM_ACCESS_RDL])
def test_convert_renders_arbitration(rdl_path):
    content = _compact_verilog(convert(_compile(rdl_path), "{{axi4l}}_regs.v.jinja2"))
    expected_assignments = {
        "arb_ready": "!int_valid||int_issue",
        "arb_read_eligible": "(ar_back_valid||s_axi_arvalid)&&r_credit",
        "arb_write_eligible": "aw_back_valid&&w_back_valid&&b_credit",
        "arb_grant_read": (
            "arb_ready&&arb_read_eligible&&(!arb_write_eligible||arb_read_priority)"
        ),
        "arb_grant_write": (
            "arb_ready&&arb_write_eligible&&(!arb_read_eligible||!arb_read_priority)"
        ),
        "ar_load_back": "arb_grant_read&&ar_back_valid",
        "ar_load_direct": "arb_grant_read&&!ar_back_valid&&s_axi_arvalid",
        "s_axi_arready": "!ar_back_valid||ar_load_back",
        "s_axi_awready": "!aw_back_valid||arb_grant_write",
        "s_axi_wready": "!w_back_valid||arb_grant_write",
    }
    for signal, expression in expected_assignments.items():
        assert _assigned_expression(content, signal) == expression


@pytest.mark.parametrize("rdl_path", [GPIO_RDL, SIMPLE_RDL, RAM_RDL, MEM_ACCESS_RDL])
def test_convert_renders_registered_response_sources(rdl_path):
    top = _compile(rdl_path)
    mems = _gather(top, MemGatheringListener).mems
    content = _compact_verilog(convert(top, "{{axi4l}}_regs.v.jinja2"))

    for direction in ("rd", "wr"):
        assert f"wireint_{direction}_ack;" in content
        assert f"reglocal_{direction}_ack;" in content
        assert f"reglocal_{direction}_err;" in content
        assert _assigned_expression(content, f"local_{direction}_en") == (
            f"int_{direction}_en&&int_target[0]"
        )
        assert f"local_{direction}_ack<=local_{direction}_en;" in content
        assert f"local_{direction}_ack<=1'b0;" in content
        assert f"local_{direction}_err<=1'b0;" in content
        assert _assigned_expression(content, f"int_{direction}_ack") == "||".join(
            [f"local_{direction}_ack"] + [f"{m['name']}_{direction}_ack" for m in mems]
        )
        assert _assigned_expression(content, f"int_{direction}_err") == (
            f"local_{direction}_ack&&local_{direction}_err"
        )
        assert f"int_{direction}_ack<=" not in content
        assert f"int_{direction}_err<=" not in content

    assert "reg[DATA_WIDTH-1:0]local_rd_data;" in content
    assert "local_rd_data<={DATA_WIDTH{1'b0}};" in content
    assert (
        "if(local_rd_en)beginlocal_rd_err<=local_rd_err_next;"
        "local_rd_data<=local_rd_data_next;"
    ) in content
    assert "if(local_wr_en)beginlocal_wr_err<=local_wr_err_next;" in content
    assert "local_rd_data_next={DATA_WIDTH{1'b0}};" in content
    assert "if(local_rd_ack)beginint_rd_data=local_rd_data;" in content
    assert "int_rd_data<=" not in content
    merge_terms = re.findall(r"int_rd_data(?:\[[^]]+\])?=([^;]+);", content)
    assert len(merge_terms) == 2 + len(mems)
    for term in merge_terms:
        assert "_dout" not in term
        assert "int_addr" not in term
        assert "int_target" not in term
        assert "int_active_target" not in term

    for index, mem in enumerate(mems, 1):
        name = mem["name"]
        for suffix, width in (("tags", 4), ("tag_rptr", 2), ("tag_wptr", 2), ("tag_count", 3)):
            assert f"reg[{width - 1}:0]{name}_{suffix};" in content
            assert f"{name}_{suffix}<={width}'d0;" in content
        expected_assignments = {
            "en": f"int_issue&&int_target[{index}]",
            "we": f"{name}_en&&int_write",
            "be": f"{name}_we?int_wr_strb:{{STRB_WIDTH{{1'b0}}}}",
            "tag_empty": f"{name}_tag_count==3'd0",
            "tag_bypass": f"{name}_tag_empty&&{name}_en&&{name}_valid",
            "tag_push": f"{name}_en&&!{name}_tag_bypass",
            "tag_pop": f"!{name}_tag_empty&&{name}_valid",
            "response": f"{name}_valid&&(!{name}_tag_empty||{name}_en)",
            "response_we": f"{name}_tag_empty?{name}_we:{name}_tags[{name}_tag_rptr]",
            "rd_done": f"{name}_response&&!{name}_response_we",
            "wr_done": f"{name}_response&&{name}_response_we",
        }
        for suffix, expression in expected_assignments.items():
            assert _assigned_expression(content, f"{name}_{suffix}") == expression
        assert (
            f"if({name}_tag_push)begin{name}_tags[{name}_tag_wptr]<={name}_we;"
            f"{name}_tag_wptr<={name}_tag_wptr+2'd1;"
        ) in content
        assert f"if({name}_tag_pop)begin{name}_tag_rptr<={name}_tag_rptr+2'd1;" in content
        assert (
            f"case({{{name}_tag_push,{name}_tag_pop}})"
            f"2'b10:{name}_tag_count<={name}_tag_count+3'd1;"
            f"2'b01:{name}_tag_count<={name}_tag_count-3'd1;"
            f"default:{name}_tag_count<={name}_tag_count;endcase"
        ) in content
        for direction in ("rd", "wr"):
            assert f"reg{name}_{direction}_ack;" in content
            assert f"{name}_{direction}_ack<=1'b0;" in content
            assert f"{name}_{direction}_ack<={name}_{direction}_done;" in content
        assert f"reg[{mem['width'] - 1}:0]{name}_rd_data;" in content
        assert f"{name}_rd_data<={mem['width']}'d0;" in content
        assert f"if({name}_rd_done)begin{name}_rd_data<={name}_dout;" in content
        assert (
            f"if({name}_rd_ack)beginint_rd_data[{mem['width'] - 1}:0]="
            f"int_rd_data[{mem['width'] - 1}:0]|{name}_rd_data;"
        ) in content


@pytest.mark.parametrize("rdl_path", [GPIO_RDL, SIMPLE_RDL, RAM_RDL, MEM_ACCESS_RDL])
def test_convert_renders_memory_response_model(rdl_path):
    top = _compile(rdl_path)
    mems = _gather(top, MemGatheringListener).mems
    rendered = convert(top, "tb_{{axi4l}}_regs.v.jinja2")
    content = _compact_verilog(rendered)

    # Indexed shifting also elaborates at depth one: no negative part-select.
    assert "MEMORY_READ_LATENCY-2" not in content
    assert "[-1:0]" not in content
    assert "_rd_addr_pipe" not in content
    if not mems:
        assert "_response_valid_pipe" not in content
        assert "_response_data_pipe" not in content

    for mem in mems:
        name = mem["name"]
        valid_pipe = f"{name}_response_valid_pipe"
        data_pipe = f"{name}_response_data_pipe"
        index = f"{name}_response_idx"
        assert f"wire[{mem['width'] - 1}:0]{name}_dout;" in content
        assert f"wire{name}_valid;" in content
        assert f"{name}_valid<=" not in content
        assert f"{name}_dout<=" not in content
        assert f"reg[MEMORY_READ_LATENCY-1:0]{valid_pipe};" in content
        assert (
            f"reg[{mem['width'] - 1}:0]{data_pipe}[0:MEMORY_READ_LATENCY-1];"
        ) in content
        # Every en, including writes, captures a read-before-write snapshot.
        assert f"{valid_pipe}[0]<={name}_en;" in content
        assert f"if({name}_en)begin{data_pipe}[0]<={name}_mem[{name}_addr];" in content
        assert f"if(s_axi_aresetn==1'b0)begin{valid_pipe}<=0;" in content
        assert f"{data_pipe}[{index}]<=0;" in content
        assert (
            f"for({index}=1;{index}<MEMORY_READ_LATENCY;{index}={index}+1)begin"
            f"{valid_pipe}[{index}]<={valid_pipe}[{index}-1];"
            f"{data_pipe}[{index}]<={data_pipe}[{index}-1];"
        ) in content
        assert _assigned_expression(content, f"{name}_valid") == (
            f"s_axi_aresetn&&{valid_pipe}[MEMORY_READ_LATENCY-1]"
        )
        assert _assigned_expression(content, f"{name}_dout") == (
            f"{name}_valid?{data_pipe}[MEMORY_READ_LATENCY-1]:"
            f"{mem['width']}'hdeadbeef"
        )
        assert f"if(s_axi_aresetn&&{name}_en==1'b1&&{name}_we==1'b1)" in content
        assert (
            f"if({name}_be[{name}_be_idx])begin"
            f"{name}_mem[{name}_addr][{name}_be_idx*8+:8]<="
            f"{name}_din[{name}_be_idx*8+:8];"
        ) in content
        read_check = (
            "readable access signal mismatch" if mem["is_sw_readable"]
            else "prohibited read reached external memory"
        )
        write_check = (
            "byte enable/write mismatch" if mem["is_sw_writable"]
            else "prohibited write reached external memory"
        )
        assert f"{mem['hierarchy']} {read_check}" in rendered
        assert f"{mem['hierarchy']} {write_check}" in rendered
        if mem["is_sw_writable"]:
            assert f"{mem['hierarchy']} WSTRB=0 issued a physical memory access" in rendered
