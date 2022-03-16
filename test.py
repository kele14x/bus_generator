#!/usr/bin/python3
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


def test_simple():
    bus_generator.main(['test/simple.rdl'])
    return 0
