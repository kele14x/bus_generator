#!/usr/bin/env python3
"""
bus_generator.py is a tool to generate AXI4-Lite slave register SystemVerilog module.
It will generate a slave register module from a description file.

Copyright (c) 2021 Kele14x.
"""
import argparse
import logging
import sys

from systemrdl import RDLCompiler, RDLCompileError
from peakrdl_regblock import RegblockExporter
from peakrdl_regblock.cpuif.axi4lite import AXI4Lite_Cpuif

__version__ = '0.1.0'


def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse arguments for program."""
    # Input and output
    parser = argparse.ArgumentParser()
    parser.add_argument('input',
                        help='Read input from specified file(s)',
                        nargs='+')
    parser.add_argument('-o', '--output',
                        help='Output folder')
    # By default, the logging level is set to WARNING, which means all
    # warning, error and critical error messages will be shown.
    # Using -q will set the logging level to ERROR, this will filter all
    # warning and lower priority messages.
    parser.add_argument('-q', '--quiet',
                        help='Show only critical and errors',
                        dest='verbosity',
                        action='store_const',
                        const=logging.ERROR,
                        default=logging.WARNING)
    parser.add_argument('-v', '--verbose',
                        help='Show almost all messages, excluding debug messages',
                        dest='verbosity',
                        action='store_const',
                        const=logging.INFO)
    parser.add_argument('-d', '--debug',
                        help='show all messages, including debug messages',
                        dest='verbosity',
                        action='store_const',
                        const=logging.DEBUG)
    # Get the version string of this script.
    parser.add_argument('-V', '--version',
                        action='version',
                        version=__version__)
    args = parser.parse_args(argv)
    return args


def main(argv=None):
    """Will be called if script is executed as script."""
    args = parse_arguments(argv)

    logging.basicConfig(level=args.verbosity)
    logging.debug(f'Python version: {sys.version.split()[0]}')
    logging.debug(f'Script version: {__version__}')
    logging.debug(f'Arguments: {vars(args)}')

    # Collect input files from the command line arguments
    input_files = args.input

    # Create an instance of the compiler
    rdlc = RDLCompiler()
    try:
        # Compile all the files provided
        for input_file in input_files:
            rdlc.compile_file(input_file)
        # Elaborate the design
        root = rdlc.elaborate()
    except RDLCompileError:
        # A compilation error occurred. Exit with error code
        sys.exit(1)

    # Export a SystemVerilog implementation
    exporter = RegblockExporter()
    exporter.export(
        root, args.output,
        cpuif_cls=AXI4Lite_Cpuif
    )


if __name__ == '__main__':
    main(sys.argv[1:])
