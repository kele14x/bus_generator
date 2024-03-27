#!/usr/bin/env python3
"""Generate Verilog Control/Status RegisterNode (CSR) module from a SystemRDL
source."""
import argparse
import logging
import os
import subprocess
import sys

from math import ceil, log2, floor

from jinja2 import Environment, FileSystemLoader
from systemrdl import RDLCompiler, RDLListener, RDLWalker
from systemrdl.node import Node, AddressableNode, VectorNode, \
    RegfileNode, FieldNode, AddrmapNode, MemNode, RegNode, SignalNode

try:
    cwd = os.path.dirname(os.path.abspath(__file__))
    __version__ = subprocess.check_output(["git", "describe", "--tags"], cwd=cwd).strip().decode('utf-8')
except subprocess.SubprocessError:
    __version__ = 'unknown'


class GeneralListener(RDLListener):
    """General walker listener."""

    def __init__(self):
        self._address = 0
        self._path = []
        self._addr_width = 1
        self._data_width = 32

    def enter_Component(self, node: Node):
        if isinstance(node, AddrmapNode):
            self._addr_width = ceil(log2(node.total_size))
        if not isinstance(node, AddrmapNode):
            self._path.append(node.inst_name)

    def exit_Component(self, node: Node):
        if not isinstance(node, AddrmapNode):
            self._path.pop()

    def enter_AddressableComponent(self, node: AddressableNode):
        self._address = node.absolute_address
        if node.current_idx:
            for c in node.current_idx:
                self._path[-1] += "_" + str(c)


class ModelPrintingListener(GeneralListener):
    """Listener used to print node hierarchy."""

    def __init__(self):
        super().__init__()

    # General node

    def enter_Component(self, node: Node):
        super().enter_Component(node)
        print('\t' * len(self._path), end='')
        print(f'{node.inst_name} ', end='')

    # Addressable node

    def enter_AddressableComponent(self, node: AddressableNode):
        super().enter_AddressableComponent(node)
        print(f'@{hex(node.absolute_address)}({hex(node.address_offset)}) ',
              end='')

    def enter_Addrmap(self, node: AddrmapNode):
        print(f'addrmap, size: {node.size}')

    def enter_Regfile(self, node: RegfileNode):
        print(f'regfile, size: {node.size}')

    def enter_Reg(self, node: RegNode):
        print(f'reg')

    def enter_Mem(self, node: MemNode):
        print(
            f'mem, sw: {node.get_property("sw").name}, size: {node.size}')

    # Vector node

    def enter_VectorComponent(self, node: VectorNode):
        print(f'[{node.high}:{node.low}] ', end='')

    def enter_Field(self, node: FieldNode):
        print(f'field, sw: {node.get_property("sw").name}, '
              f'hw: {node.get_property("hw").name}')

    def enter_Signal(self, node: SignalNode):
        print(f'signal')


class FieldsGatheringListener(GeneralListener):
    def __init__(self):
        super().__init__()
        self.fields = []

    def exit_Field(self, node: FieldNode):
        field = {
            'name': "_".join(self._path),
            'hierarchy': ".".join(self._path),
            'address': self._address,
            'aligned_address': int(self._address / 4),
            'reset': node.get_property('reset') or 0,
            'width': node.high - node.low + 1,
            'high': node.high,
            'low': node.low,
            'msb': node.msb,
            'lsb': node.lsb,
            'implements_storage': node.implements_storage,
            'is_sw_writable': node.is_sw_writable,
            'is_sw_readable': node.is_sw_readable,
            'is_hw_writable': node.is_hw_writable,
            'is_hw_readable': node.is_hw_readable,
        }
        self.fields.append(field)


class MemGatheringListener(GeneralListener):
    def __init__(self):
        super().__init__()
        self.mems = []

    def exit_Mem(self, node: MemNode):
        mem = {
            'name': "_".join(self._path),
            'hierarchy': ".".join(self._path),
            'address': node.absolute_address,
            'aligned_address': floor(
                node.absolute_address / 2 ** ceil(log2(node.size))),
            'mementries': node.get_property('mementries'),
            'addr_width': ceil(log2(node.size)) - ceil(log2(
                self._data_width / 8)),
            'addr_msb': ceil(log2(node.size)) - 1,
            'addr_lsb': ceil(log2(self._data_width / 8)),
            'width': node.get_property('memwidth'),
            'is_sw_writable': node.is_sw_writable,
            'is_sw_readable': node.is_sw_readable,
        }
        self.mems.append(mem)


class RegistersGatheringListener(GeneralListener):
    def __init__(self):
        super().__init__()
        self.regs = []

    def exit_Reg(self, node: RegNode):
        reg = {
            'name': "_".join(self._path),
            'hierarchy': ".".join(self._path),
            'address': node.absolute_address,
            'aligned_address': int(node.absolute_address / 4),
            'has_sw_writable': node.has_sw_writable,
            'has_sw_readable': node.has_sw_readable,
            'has_hw_writable': node.has_hw_writable,
            'has_hw_readable': node.has_hw_readable,
        }
        self.regs.append(reg)


def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse arguments for program."""
    # Input and output
    parser = argparse.ArgumentParser()
    parser.add_argument('input',
                        help='read input from specified file(s)',
                        nargs='+')
    parser.add_argument('-p', '--print',
                        help='print model hierarchy',
                        action='store_true')
    parser.add_argument('-o', '--output',
                        help='write output to specified folder')
    # By default, the logging level is set to WARNING, which means all
    # warning, error and critical error messages will be shown.
    # Using -q -v or -d to set the logging level.
    parser.add_argument('-q', '--quiet',
                        help='show only critical and errors',
                        dest='verbosity',
                        action='store_const',
                        const=logging.ERROR,
                        default=logging.WARNING)
    parser.add_argument('-v', '--verbose',
                        help='show almost all messages, excluding debug '
                             'messages',
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


def print_hierarchy(rdlc: RDLCompiler, top: AddrmapNode):
    """Print compiled hierarchy for node."""
    walker = RDLWalker(unroll=True)
    listener = ModelPrintingListener()
    walker.walk(top, listener)


def convert(rdlc: RDLCompiler, top: AddrmapNode,
            template_name: str = 'axi4l.v.jinja2'):
    """Convert compiled node hierarchy to generator friendly format."""
    walker = RDLWalker(unroll=True)

    listener = FieldsGatheringListener()
    walker.walk(top, listener)
    fields = listener.fields

    listener = RegistersGatheringListener()
    walker.walk(top, listener)
    regs = listener.regs

    listener = MemGatheringListener()
    walker.walk(top, listener)
    mems = listener.mems

    # Get Jinja2 Environment
    env = Environment(
        loader=FileSystemLoader(os.path.join(
            os.path.dirname(__file__), 'templates')),
        newline_sequence='\n',
        autoescape=False,
        keep_trailing_newline=True
    )

    # Render and write target files
    template = env.get_template(template_name)
    content = template.render({
        'top_name': top.inst_name,
        'addr_width': ceil(log2(top.total_size)),
        'addr_width_lsb': 2,
        'data_width': 32,
        'fields': fields,
        'regs': regs,
        'mems': mems,
    })

    return content


def write_file(output_dir, content, name):
    output_dir = os.path.abspath(output_dir)
    if os.path.isdir(output_dir):
        logging.info(f'Folder "{output_dir}" already exists.')
    elif os.path.exists(output_dir):
        logging.error(
            f'File "{output_dir} already exists but is not a folder, '
            f'abort.')
        sys.exit(2)
    else:
        logging.info(f'Create folder "{output_dir}".')
        os.mkdir(output_dir)

    target_file = os.path.join(output_dir, name)

    if os.path.isfile(target_file):
        logging.warning(
            f'File "{target_file}" already exists, it will be overwrite.')
    elif os.path.exists(target_file):
        logging.error(
            f'File "{target_file}" already exists but is not a regular '
            f'file, abort.')
        sys.exit(2)

    with open(target_file, mode='w', encoding='utf8', newline='\n') as fd:
        fd.write(content)


def main(argv=None):
    """Will be called if script is executed as script."""
    args = parse_arguments(argv)

    logging.basicConfig(level=args.verbosity,
                        format='%(levelname)s: %(funcName)s(): L%(lineno)d: '
                               '%(message)s')
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
        top = root.top
    except RuntimeError:
        # A compilation error occurred. Exit with error code
        sys.exit(1)

    # Print
    if args.print:
        print_hierarchy(rdlc, top)

    # Export a SystemVerilog implementation
    content = convert(rdlc, top, template_name='axi4l.v.jinja2')
    # content = convert(rdlc, top, template_name='avalon_mm.v.jinja2')
    if args.output is not None:
        write_file(args.output, content, root.top.inst_name + '_regs.v')

    # tb_content = convert(rdlc, top, template_name='tb_axi4l.v.jinja2')
    # if args.output is not None:
    #     write_file(args.output, tb_content,
    #                'tb_' + root.top.inst_name + '_regs.v')


if __name__ == '__main__':
    main(sys.argv[1:])
