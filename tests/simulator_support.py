"""Shared simulator selection and executable checks for RTL tests."""

SIMULATOR_COMMANDS = {
    "icarus": ("iverilog", "vvp"),
    "verilator": ("verilator",),
    "questa": ("vlib", "vlog", "vsim"),
}

SIM_ALIASES = {
    "iverilog": "icarus",
    "vsim": "questa",
}

SUPPORTED_SIM_VALUES = ("icarus", "verilator", "questa", "iverilog", "vsim")


def _supported_values_message():
    values = ", ".join(f"SIM={value}" for value in SUPPORTED_SIM_VALUES[:-1])
    aliases = "; ".join(
        f"{alias} is an alias for {target}" for alias, target in SIM_ALIASES.items()
    )
    return f"{values}, or SIM={SUPPORTED_SIM_VALUES[-1]} ({aliases})"


def normalize_simulator(sim):
    """Return the cocotb/pytest simulator name for a ``SIM`` value."""
    sim = sim.strip().lower()
    return SIM_ALIASES.get(sim, sim)


def selected_simulator(environ):
    """Return the explicitly selected simulator from an environment mapping."""
    requested = environ.get("SIM")
    if requested is None or not requested.strip():
        raise ValueError(
            "SIM is required for simulator-marked tests. "
            f"Set one of: {_supported_values_message()}."
        )

    sim = normalize_simulator(requested)
    simulator_commands(sim)
    return sim


def simulator_commands(sim):
    """Return the required executable names for a normalized simulator name."""
    sim = normalize_simulator(sim)
    try:
        return SIMULATOR_COMMANDS[sim]
    except KeyError as error:
        choices = "', '".join(SIMULATOR_COMMANDS)
        aliases = ", ".join(f"'{alias}' for {target}" for alias, target in SIM_ALIASES.items())
        raise ValueError(f"unsupported SIM={sim!r}; expected '{choices}' (or {aliases})") from error


def missing_simulator_commands(sim, which):
    """Return required executables that cannot be found by ``which``."""
    return tuple(command for command in simulator_commands(sim) if not which(command))


def require_simulator(environ, which):
    """Return the selected simulator, failing if its executables are unavailable."""
    sim = selected_simulator(environ)
    required = simulator_commands(sim)
    missing = missing_simulator_commands(sim, which)
    if missing:
        raise RuntimeError(
            f"SIM={sim!r} requires executables: {', '.join(required)}; "
            f"missing from PATH: {', '.join(missing)}. "
            "Install the selected simulator or set a different supported SIM value."
        )
    return sim
