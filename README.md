# Bus Generator

**Bus Generator** is a script to generate a Verilog AXI slave CSR (Control & Status Register) block from [SystemRDL](https://www.accellera.org/downloads/standards/systemrdl) source.

## Dependency

Python3 and some packages (see **requirements.txt**).

## Installation

1. Install [Python3](https://www.python.org/downloads/).

2. (Optional) Create a virtual environment:

    ```bash
    python -m venv venv
    ```

3. (Optinal) Active the virtual environment:

    ```bash
    . .\\venv\\Scripts\\Activate.ps1
    ```

4. Install required package from [PyPi](https://pypi.org/) using:

    ```bash
    python -m pip install -r requirements.txt
    ```

## Usage

```bash
python bus_generator.py <input_files> -o <output_dir>
```
