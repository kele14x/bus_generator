#!/usr/bin/env python3
"""
bus_generator.py is a simple wrapper of [PeakRDL-regblock](https://github.com/SystemRDL/PeakRDL-regblock).
"""
import argparse
import logging
import sys

from systemrdl import RDLCompiler, RDLCompileError
from systemrdl import RDLListener, RDLWalker
from systemrdl.node import FieldNode
from peakrdl_regblock import RegblockExporter
from peakrdl_regblock.cpuif.axi4lite import AXI4Lite_Cpuif

__version__ = '0.1.0'


class ModelPrintingListener(RDLListener):
    def __init__(self):
        self.indent = 0

    def enter_Component(self, node):
        if not isinstance(node, FieldNode):
            print('\t'*self.indent, node.get_path_segment())
            self.indent += 1

    def enter_Field(self, node):
        bit_range_str = '[%d:%d]' % (node.high, node.low)
        sw_access_str = 'sw=%s' % node.get_property('sw').name
        print('\t'*self.indent, bit_range_str, node.get_path_segment(), sw_access_str)

    def exit_Component(self, node):
        if not isinstance(node, FieldNode):
            self.indent -= 1


def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse arguments for program."""
    # Input and output
    parser = argparse.ArgumentParser()
    parser.add_argument('input',
                        help='read input from specified file(s)',
                        nargs='+')
    parser.add_argument('-p', '--print',
                        help='print model heriarchy',
                        action='store_true')
    parser.add_argument('-o', '--output',
                        help='write output to specified folder')
    # By default, the logging level is set to WARNING, which means all
    # warning, error and critical error messages will be shown.
    # Using -q will set the logging level to ERROR, this will filter all
    # warning and lower priority messages.
    parser.add_argument('-q', '--quiet',
                        help='show only critical and errors',
                        dest='verbosity',
                        action='store_const',
                        const=logging.ERROR,
                        default=logging.WARNING)
    parser.add_argument('-v', '--verbose',
                        help='show almost all messages, excluding debug messages',
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

    # Print
    if args.print:
        walker = RDLWalker(unroll=True)
        listener = ModelPrintingListener()
        walker.walk(root, listener)

    # Export a SystemVerilog implementation
    if args.output is not None:
        exporter = RegblockExporter()
        exporter.export(
            root, args.output,
            cpuif_cls=AXI4Lite_Cpuif
        )


if __name__ == '__main__':
    main(sys.argv[1:])

