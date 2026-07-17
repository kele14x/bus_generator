#!/usr/bin/env python3
"""Generation tests: render every output format and check the produced files."""

import os

import pytest

from bus_generator import main

SAMPLES = [
    pytest.param("tests/gpio.rdl", "gpio", id="gpio"),
    pytest.param("tests/ram.rdl", "ram", id="ram"),
]

# alias -> (output filename relative to out dir, substrings the file must contain)
EXPECTED = {
    "axi4l": (lambda top: f"{top}_regs.v", ["module {top}_regs ("]),
    "avalon_mm": (lambda top: f"{top}_regs.v", ["module {top}_regs ("]),
    "c_header": (lambda top: f"{top}.h", ["#define"]),
    "tb_axi4l": (lambda top: f"tb_{top}_regs.v", ["TEST PASSED", "TEST FAILED"]),
}


@pytest.mark.parametrize("rdl,top", SAMPLES)
@pytest.mark.parametrize("alias", sorted(EXPECTED))
def test_generate_alias(rdl, top, alias, tmp_path):
    out = tmp_path / alias
    main([rdl, "-o", str(out), "-t", alias])

    name_fn, substrings = EXPECTED[alias]
    target = out / name_fn(top)
    assert os.path.isfile(target), f"missing {target}"
    assert os.path.getsize(target) > 0, f"empty {target}"

    text = target.read_text()
    for s in substrings:
        assert s.format(top=top) in text, f"{target} missing {s!r}"
