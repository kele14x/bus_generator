#!/usr/bin/env python3
import os

import pytest

from bus_generator import main


def test_version():
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0


def test_help():
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0


def test_axi_gpio(tmp_path):
    main(["./tests/gpio.rdl", "-o", str(tmp_path)])
    assert os.path.isfile(tmp_path / "gpio_regs.v")


def test_axi_mem(tmp_path):
    main(["./tests/ram.rdl", "-o", str(tmp_path)])
    assert os.path.isfile(tmp_path / "ram_regs.v")
