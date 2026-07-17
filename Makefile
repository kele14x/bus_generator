# Makefile for bus_generator test tasks.
# Run `make` or `make all` to run the full suite; see targets below.

PYTEST := uv run pytest

.PHONY: all test unit gen sim fast clean

# Run every test layer (unit + generation + simulation).
all test:
	$(PYTEST)

# Pure-Python unit tests (CLI, listeners, discover_templates, convert).
unit:
	$(PYTEST) tests/test_unit.py

# Output-format generation tests for all templates and samples.
gen:
	$(PYTEST) tests/test_generation.py

# RTL simulation tests via iverilog/vvp (skipped if no simulator).
sim:
	$(PYTEST) tests/test_simulation.py

# Unit + generation only (fast path, no simulator needed).
fast:
	$(PYTEST) -m "not sim"

# Remove generated/local artifacts: bytecode caches, pytest cache, sim build
# dirs, and stray cocotb result XML files.
clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache sim_build
	find tests -name '*.result.xml' -delete
