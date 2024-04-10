#!/usr/bin/env python3
import bus_generator


def test_version():
    try:
        bus_generator.main(['--version'])
    except SystemExit as e:
        assert e.code == 0


def test_help():
    try:
        bus_generator.main(['--help'])
    except SystemExit as e:
        assert e.code == 0


def test_axi_gpio():
    bus_generator.main(['./tests/gpio.rdl'])
    return 0


def test_axi_mem():
    bus_generator.main(['./tests/ram.rdl'])
    return 0
