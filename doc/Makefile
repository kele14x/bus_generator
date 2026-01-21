.PHONY: build test clean

build:
	verilator -Wall --lint-only axi4l_int.v

test:
	python test_axi4l_int.py

clean:
	-rm -rf __pycache__
	-rm -rf sim_build
