#!/usr/bin/python3
"""
bus_generator.py is a tool to generate AXI4-Lite slave register SystemVerilog module.
It will generate a slave register module from a description file.

Copyright (c) 2021 Kele14x.
"""
import argparse
import logging
import sys

from systemrdl import RDLCompiler, RDLCompileError, RDLWalker
from systemrdl import RDLListener
from systemrdl.node import FieldNode


# Global
# ------

__version__ = '0.1.0'


# Classes
# -------


# Define a listener that will print out the register model hierarchy
class MyModelPrintingListener(RDLListener):
    def __init__(self):
        self.indent = 0

    def enter_Component(self, node):
        if not isinstance(node, FieldNode):
            print("\t"*self.indent, node.get_path_segment())
            self.indent += 1

    def enter_Field(self, node):
        # Print some stuff about the field
        bit_range_str = "[%d:%d]" % (node.high, node.low)
        sw_access_str = "sw=%s" % node.get_property('sw').name
        print("\t"*self.indent, bit_range_str, node.get_path_segment(), sw_access_str)

    def exit_Component(self, node):
        if not isinstance(node, FieldNode):
            self.indent -= 1


# Main
# ----

def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse arguments for program."""
    parser = argparse.ArgumentParser(
        prog='bus_generator',
        description='A tool to generate AXI4-Lite slave register module'
    )

    # Input and Output Options

    # Read input from file.
    # TODO: Reconsider pipe from <stdin>. For example, specify input using '-'
    parser.add_argument(
        'input',
        help='read input from specified file(s)',
        nargs='+',
    )

    # If user does not specify an output file, we will not write to file.
    parser.add_argument(
        '--sv', '--systemverilog',
        dest='systemverilog',
        help='write SystemVerilog output file(s) to specified path',
        nargs='?',
    )

    # Also, possible write a JSON file.
    parser.add_argument(
        '--json',
        dest='json',
        help='write JSON output file(s) to specified path',
        nargs='?'
    )

    # Logging Options

    # If user does not specify a log file, we write to stderr. Note stderr by
    # default goes to terminal together with stdout.
    # TODO: Setup logger to write to this file
    parser.add_argument(
        '-l', '--log',
        dest='log',
        help='write log to specified file instead of <stderr>',
        nargs='?',
    )

    # By default, the logging level is set to WARNING, which means all
    # warning, error and critical error messages will be shown.
    # Using -q will set the logging level to ERROR, this will filter all
    # warning and lower priority messages.
    parser.add_argument(
        '-q', '--quiet',
        action='store_const',
        const=logging.ERROR,
        default=logging.WARNING,
        dest='verbosity',
        help='show only critical and errors',
    )

    # Set the logging level to INFO, which will show most messages except DEBUG.
    parser.add_argument(
        '-v', '--verbose',
        action='store_const',
        const=logging.INFO,
        dest='verbosity',
        help='show almost all messages, excluding debug messages'
    )

    # Set the logging level to DEBUG, which will show all messages.
    parser.add_argument(
        '-d', '--debug',
        action='store_const',
        const=logging.DEBUG,
        dest='verbosity',
        help='show all messages, including debug messages'
    )

    # Misc options

    # Get the version string of this script.
    parser.add_argument(
        '-V', '--version',
        action='version',
        version=__version__,
    )

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

    # Traverse the register model!
    walker = RDLWalker(unroll=True)
    listener = MyModelPrintingListener()
    walker.walk(root, listener)


if __name__ == '__main__':
    main(sys.argv[1:])
