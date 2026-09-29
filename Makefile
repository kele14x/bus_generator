# Makefile for bus_generator test tasks.
# Run `make` for help or `make all SIM=<simulator>` for the full suite.

PYTEST := uv run pytest
GENERATED := generated
SAMPLES := field_access gpio mem_access nested_addrmaps ram simple wstrb
TEMPLATES := axi4l c_header tb_axi4l

AXI4L_ARTIFACTS := $(addprefix $(GENERATED)/axi4l/,$(addsuffix _regs.v,$(SAMPLES)))
C_HEADER_ARTIFACTS := $(addprefix $(GENERATED)/c_header/,$(addsuffix .h,$(SAMPLES)))
TB_AXI4L_ARTIFACTS := $(addprefix $(GENERATED)/tb_axi4l/tb_,$(addsuffix _regs.v,$(SAMPLES)))
ARTIFACTS := $(AXI4L_ARTIFACTS) $(C_HEADER_ARTIFACTS) $(TB_AXI4L_ARTIFACTS)

.PHONY: all tests unit artifacts sim clean help

help:
	@printf '\n%s\n' 'bus_generator — development commands'
	@printf '\n  %s\n' 'Usage: make <target> [SIM=<simulator>]'
	@printf '\n%s\n' 'Tests'
	@printf '  %-14s %s\n' \
		'unit'        'Run Python-only tests; no simulator needed' \
		'sim'         'Run simulation and stress tests (requires SIM)' \
		'all / tests' 'Run both unit and sim (requires SIM)'
	@printf '\n%s\n' 'Utilities'
	@printf '  %-14s %s\n' \
		'artifacts'  'Generate configured samples and templates into ./generated/' \
		'clean'      'Remove generated output, caches, and simulation results' \
		'help'       'Show this help (default target)'
	@printf '\n%s\n' 'Simulator selection'
	@printf '  %s\n' \
		'Set SIM explicitly for sim, all, and tests.' \
		'Supported: icarus, verilator, questa' \
		'Aliases:   iverilog = icarus, vsim = questa'
	@printf '\n%s\n' 'Examples'
	@printf '  %s\n' \
		'make unit' \
		'make sim SIM=icarus' \
		'make all SIM=verilator'
	@printf '\n'

all tests: unit sim

unit:
	$(PYTEST) -m 'not sim'

# Render configured samples and templates into ./generated/<template>/ for reuse.
artifacts: $(ARTIFACTS)

$(GENERATED)/axi4l/%_regs.v: samples/%.rdl src/bus_generator/templates/{{axi4l}}_regs.v.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t axi4l

$(GENERATED)/c_header/%.h: samples/%.rdl src/bus_generator/templates/{{c_header}}.h.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t c_header

$(GENERATED)/tb_axi4l/tb_%_regs.v: samples/%.rdl src/bus_generator/templates/tb_{{axi4l}}_regs.v.jinja2
	@mkdir -p $(@D)
	uv run bus-generator $< -o $(@D) -t tb_axi4l

# All sim-marked tests against reusable generated/ artifacts; requires SIM.
sim: $(AXI4L_ARTIFACTS) $(TB_AXI4L_ARTIFACTS)
	$(PYTEST) -m sim

# Remove generated/local artifacts: bytecode caches, pytest cache, sim build
# dirs, reusable generated output, and stray cocotb result XML files.
clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache sim_build $(GENERATED)
	find tests -name '*.result.xml' -delete
