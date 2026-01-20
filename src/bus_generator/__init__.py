import sys

from .bus_generator import cli


def main() -> None:
    cli(sys.argv[1:])
