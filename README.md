# Bus Generator

**Bus Generator** is a simple wrapper of [PeakRDL-regblock](https://github.com/SystemRDL/PeakRDL-regblock).

- **ReakRDL-regblock** is a library to generate AXI4-Lite slave register SystemVerilog module based on **SystemRDL** file.
- **SystemRDL** is a register description standard format released by [Accellera](https://www.accellera.org/).

## Dependency

Install required package from [PyPi](https://pypi.org/project/systemrdl-compiler/) using:

```bash
python3 -m pip install peakrdl-regblock
```

## Usage

```bash
python bus_generator.py <input_files> -o <output_dir>
```
