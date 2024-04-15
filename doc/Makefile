
.PHONY: all waves clean

tb_axi4l_int_behav.wdb: axi4l_int.v tb_axi4l_int.sv
	xvlog ./axi4l_int.v
	xvlog -sv ./tb_axi4l_int.sv
	xelab --debug typical --snapshot tb_axi4l_int_behav --cc_type sbct --top tb_axi4l_int
	xsim tb_axi4l_int_behav --tclbatch tb_axi4l_int.tcl
	xcrg --cc_db tb_axi4l_int_behav

all: tb_axi4l_int_behav.wdb

waves: tb_axi4l_int_behav.wdb
	xsim --gui tb_axi4l_int_behav.wdb

clean:
	-rm -rf .Xil
	-rm -rf xcrg_code_cov_report
	-rm -rf xsim.codeCov
	-rm -rf xsim.dir
	-rm *.jou
	-rm *.log
	-rm *.pb
	-rm *.wdb
