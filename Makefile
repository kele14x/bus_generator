# Makefile for bus_generator test tasks.
# Run `make` or `make all` to run the full suite; see targets below.

PYTEST := uv run pytest
GENERATED := generated
SAMPLES := gpio ram simple
TEMPLATES := axi4l avalon_mm c_header tb_axi4l

.PHONY: all test unit gen sim stress fast clean

# Run every test layer (unit + generation + simulation).
all test:
	$(PYTEST)

# Pure-Python unit tests (CLI, listeners, discover_templates, convert).
unit:
	$(PYTEST) tests/test_unit.py

# Render every sample x template into ./generated/<template>/ for reuse,
# then run the generation tests (which render to an isolated tmp dir for
# content checks). Per-template subdirs avoid the axi4l/avalon_mm
# <top>_regs.v filename collision.
gen:
	@for t in $(TEMPLATES); do mkdir -p $(GENERATED)/$$t; done
	@for rdl in $(SAMPLES); do \
	  for t in $(TEMPLATES); do \
	    uv run bus-generator tests/$$rdl.rdl -o $(GENERATED)/$$t -t $$t; \
	  done; \
	done
	$(PYTEST) tests/test_generation.py

# All sim-marked tests (self-checking TBs + cocotb random stress) against the
# reusable generated/ artifacts (no regeneration — manual edits survive).
# Prerequisite: run `make gen` first. Skipped if iverilog/vvp missing or no
# artifacts.
sim:
	$(PYTEST) -m sim

# Cocotb random AXI4-Lite stress test only (overrides STRESS_COUNT for a soak).
stress:
	$(PYTEST) tests/test_stress.py

# Unit + generation only (fast path, no simulator needed).
fast:
	$(PYTEST) -m "not sim"

# Remove generated/local artifacts: bytecode caches, pytest cache, sim build
# dirs, reusable generated output, and stray cocotb result XML files.
clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache sim_build $(GENERATED)
	find tests -name '*.result.xml' -delete
