# Makefile for bus_generator test tasks.
# Run `make` or `make all` to run the full suite; see targets below.

PYTEST := uv run pytest
GENERATED := generated
SAMPLES := gpio ram simple
TEMPLATES := axi4l c_header tb_axi4l

AXI4L_ARTIFACTS := $(addprefix $(GENERATED)/axi4l/,$(addsuffix _regs.v,$(SAMPLES)))
C_HEADER_ARTIFACTS := $(addprefix $(GENERATED)/c_header/,$(addsuffix .h,$(SAMPLES)))
TB_AXI4L_ARTIFACTS := $(addprefix $(GENERATED)/tb_axi4l/tb_,$(addsuffix _regs.v,$(SAMPLES)))
ARTIFACTS := $(AXI4L_ARTIFACTS) $(C_HEADER_ARTIFACTS) $(TB_AXI4L_ARTIFACTS)

.PHONY: all test unit artifacts gen sim stress fast clean

# Run every test layer (unit + generation + simulation).
all test: unit gen sim

# Pure-Python unit tests (CLI, listeners, discover_templates, convert).
unit:
	$(PYTEST) tests/test_unit.py

# Render every sample x template into ./generated/<template>/ for reuse.
artifacts: $(ARTIFACTS)

$(GENERATED)/axi4l/%_regs.v: tests/%.rdl src/bus_generator/templates/{{axi4l}}_regs.v.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t axi4l

$(GENERATED)/c_header/%.h: tests/%.rdl src/bus_generator/templates/{{c_header}}.h.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t c_header

$(GENERATED)/tb_axi4l/tb_%_regs.v: tests/%.rdl src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t tb_axi4l

# Render reusable artifacts, then run isolated generation content checks.
gen: artifacts
	$(PYTEST) tests/test_generation.py

# All sim-marked tests against reusable generated/ artifacts.
sim: $(AXI4L_ARTIFACTS) $(TB_AXI4L_ARTIFACTS)
	$(PYTEST) -m sim

# Cocotb random AXI4-Lite stress test only (overrides STRESS_COUNT for a soak).
stress: $(GENERATED)/axi4l/simple_regs.v
	$(PYTEST) tests/test_stress.py

# Unit + generation only (fast path, no simulator needed).
fast: unit gen

# Remove generated/local artifacts: bytecode caches, pytest cache, sim build
# dirs, reusable generated output, and stray cocotb result XML files.
clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache sim_build $(GENERATED)
	find tests -name '*.result.xml' -delete
