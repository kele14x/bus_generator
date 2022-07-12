# Bus Generator

**Bus Generator** is a tool to generate AXI4-Lite slave register SystemVerilog module. It supports **SystemRDL** - a standard register description format released by [accellera](https://www.accellera.org/). Currently it's a simple wrapper of [PeakRDL-regblock](https://github.com/SystemRDL/PeakRDL-regblock).

## Dependency

Install required package from [PyPi](https://pypi.org/project/systemrdl-compiler/) using:

```bash
python3 -m pip install peakrdl-regblock
```

## Usage

```bash
python bus_generator.py <input_files> -o <output_dir>
```
