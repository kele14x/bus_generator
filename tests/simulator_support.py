"""Shared simulator selection and executable checks for RTL tests."""

SIMULATOR_COMMANDS = {
    "icarus": ("iverilog", "vvp"),
    "verilator": ("verilator",),
    "questa": ("vlib", "vlog", "vsim"),
}


def normalize_simulator(sim):
    """Return the cocotb/pytest simulator name for a ``SIM`` value."""
    sim = sim.strip().lower()
    return "questa" if sim == "vsim" else sim


def simulator_commands(sim):
    """Return the required executable names for a normalized simulator name."""
    sim = normalize_simulator(sim)
    try:
        return SIMULATOR_COMMANDS[sim]
    except KeyError as error:
        choices = "', '".join(SIMULATOR_COMMANDS)
        raise ValueError(f"unsupported SIM={sim!r}; expected '{choices}' (or 'vsim')") from error


def missing_simulator_commands(sim, which):
    """Return required executables that cannot be found by ``which``."""
    return tuple(command for command in simulator_commands(sim) if not which(command))
