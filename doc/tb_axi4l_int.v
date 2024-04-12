// File: tb_axi4l_int.v
// Brief: Test bench for module tb_axi4l_int.
`timescale 1 ns / 1 ps
//
`default_nettype none

module tb_axi4l_int;

  parameter integer ADDR_WIDTH = 10;
  parameter integer DATA_WIDTH = 32;

  reg                     s_axi_aclk;
  reg                     s_axi_aresetn;
  //
  reg  [  ADDR_WIDTH-1:0] s_axi_awaddr;
  reg  [             2:0] s_axi_awprot;
  reg                     s_axi_awvalid;
  wire                    s_axi_awready;
  //
  reg  [  DATA_WIDTH-1:0] s_axi_wdata;
  reg  [DATA_WIDTH/8-1:0] s_axi_wstrb;
  reg                     s_axi_wvalid;
  wire                    s_axi_wready;
  //
  wire [             1:0] s_axi_bresp;
  wire                    s_axi_bvalid;
  reg                     s_axi_bready;
  //
  reg  [  ADDR_WIDTH-1:0] s_axi_araddr;
  reg  [             2:0] s_axi_arprot;
  reg                     s_axi_arvalid;
  wire                    s_axi_arready;
  //
  wire [  DATA_WIDTH-1:0] s_axi_rdata;
  wire [             1:0] s_axi_rresp;
  wire                    s_axi_rvalid;
  reg                     s_axi_rready;
  //
  wire [  ADDR_WIDTH-1:0] int_addr;
  wire [  DATA_WIDTH-1:0] int_wr_data;
  wire [DATA_WIDTH/8-1:0] int_wr_strb;
  wire                    int_wr_en;
  wire                    int_rd_en;
  //
  reg                     int_wr_ack;
  reg                     int_wr_err;
  //
  reg                     int_rd_ack;
  reg                     int_rd_err;
  reg  [            31:0] int_rd_data;

  // Helpers

  task automatic axi_reset;
    begin
      s_axi_awaddr  <= 0;
      s_axi_awprot  <= 0;
      s_axi_awvalid <= 0;
      //
      s_axi_wdata   <= 0;
      s_axi_wstrb   <= 0;
      s_axi_wvalid  <= 0;
      //
      s_axi_bready  <= 0;
      //
      s_axi_araddr  <= 0;
      s_axi_arprot  <= 0;
      s_axi_arvalid <= 0;
      //
      s_axi_rready  <= 0;
    end
  endtask

  task automatic int_reset;
    begin
      int_wr_ack  <= 0;
      int_wr_err  <= 0;
      //
      int_rd_ack  <= 0;
      int_rd_err  <= 0;
      int_rd_data <= 0;
    end
  endtask

  task automatic dut_reset;
    begin
      s_axi_aresetn = 0;
      axi_reset;
      int_reset;
      repeat (16) @(posedge s_axi_aclk);
      s_axi_aresetn <= 1;
      repeat (16) @(posedge s_axi_aclk);
    end
  endtask

  task automatic axi_aw(input reg [8:0] addr, input integer d);
    begin
      repeat (d) @(posedge s_axi_aclk);
      s_axi_awaddr  <= addr;
      s_axi_awprot  <= 0;
      s_axi_awvalid <= 1;
      @(posedge s_axi_aclk);
      while (s_axi_awready == 0) begin
        @(posedge s_axi_aclk);
      end
      s_axi_awvalid <= 0;
    end
  endtask

  task automatic axi_w(input reg [31:0] data, input reg [3:0] strb, input integer d);
    begin
      repeat (d) @(posedge s_axi_aclk);
      s_axi_wdata  <= data;
      s_axi_wstrb  <= strb;
      s_axi_wvalid <= 1;
      @(posedge s_axi_aclk);
      while (s_axi_wready == 0) begin
        @(posedge s_axi_aclk);
      end
      s_axi_wvalid <= 0;
    end
  endtask

  task automatic axi_b(output reg [1:0] resp, input integer d);
    begin
      repeat (d) @(posedge s_axi_aclk);
      s_axi_bready <= 1;
      @(posedge s_axi_aclk);
      while (s_axi_bvalid == 0) begin
        @(posedge s_axi_aclk);
      end
      resp = s_axi_bresp;
      s_axi_bready <= 0;
    end
  endtask

  task automatic axi_ar(input reg [8:0] addr, input integer d);
    begin
      repeat (d) @(posedge s_axi_aclk);
      s_axi_araddr  <= addr;
      s_axi_arprot  <= 0;
      s_axi_arvalid <= 1;
      @(posedge s_axi_aclk);
      while (s_axi_arready == 0) begin
        @(posedge s_axi_aclk);
      end
      s_axi_arvalid <= 0;
    end
  endtask

  task automatic axi_r(output reg [31:0] data, output reg [1:0] resp, input integer d);
    begin
      repeat (d) @(posedge s_axi_aclk);
      s_axi_rready <= 1;
      @(posedge s_axi_aclk);
      while (s_axi_rvalid == 0) begin
        @(posedge s_axi_aclk);
      end
      data = s_axi_rdata;
      resp = s_axi_rresp;
      s_axi_rready <= 0;
    end
  endtask

  task automatic axi_write(input reg [8:0] addr, input reg [31:0] data, output reg [1:0] resp);
    begin
      fork
        begin
          axi_aw(addr, 0);
        end
        begin
          axi_w(data, 4'hF, 0);
        end
        begin
          axi_b(resp, 0);
        end
      join
      $display("Write: addr = %x, data = %x, resp = %x\n", addr, data, resp);
    end
  endtask

  task automatic axi_read(input reg [8:0] addr, output reg [31:0] data, output reg [1:0] resp);
    begin
      fork
        begin
          axi_ar(addr, 0);
        end
        begin
          axi_r(data, resp, 0);
        end
      join
      $display("Read: addr = %x, data = %x, resp = %x\n", addr, data, resp);
    end
  endtask

  task automatic int_write(input reg err, input integer d);
    begin
      wait (int_wr_en);
      repeat (d) @(posedge s_axi_aclk);
      int_wr_ack <= 1;
      @(posedge s_axi_aclk);
      int_wr_ack <= 0;
    end
  endtask

  task automatic int_read(input reg err, input integer d);
    begin
      wait (int_rd_en);
      repeat (d) @(posedge s_axi_aclk);
      int_rd_ack <= 1;
      @(posedge s_axi_aclk);
      int_rd_ack <= 0;
    end
  endtask

  // DUT

  axi4l_int DUT (
      .s_axi_aclk   (s_axi_aclk),
      .s_axi_aresetn(s_axi_aresetn),
      //
      .s_axi_awaddr (s_axi_awaddr),
      .s_axi_awprot (s_axi_awprot),
      .s_axi_awvalid(s_axi_awvalid),
      .s_axi_awready(s_axi_awready),
      //
      .s_axi_wdata  (s_axi_wdata),
      .s_axi_wstrb  (s_axi_wstrb),
      .s_axi_wvalid (s_axi_wvalid),
      .s_axi_wready (s_axi_wready),
      //
      .s_axi_bresp  (s_axi_bresp),
      .s_axi_bvalid (s_axi_bvalid),
      .s_axi_bready (s_axi_bready),
      //
      .s_axi_araddr (s_axi_araddr),
      .s_axi_arprot (s_axi_arprot),
      .s_axi_arvalid(s_axi_arvalid),
      .s_axi_arready(s_axi_arready),
      //
      .s_axi_rdata  (s_axi_rdata),
      .s_axi_rresp  (s_axi_rresp),
      .s_axi_rvalid (s_axi_rvalid),
      .s_axi_rready (s_axi_rready),
      //
      .int_addr     (int_addr),
      .int_wr_data  (int_wr_data),
      .int_wr_strb  (int_wr_strb),
      .int_wr_en    (int_wr_en),
      .int_rd_en    (int_rd_en),
      //
      .int_wr_ack   (int_wr_ack),
      .int_wr_err   (int_wr_err),
      //
      .int_rd_ack   (int_rd_ack),
      .int_rd_err   (int_rd_err),
      .int_rd_data  (int_rd_data)
  );


  // Stimulation
  //------------

  initial begin
    s_axi_aclk = 0;
    forever begin
      #5 s_axi_aclk = ~s_axi_aclk;
    end
  end

  initial begin
    dut_reset();
    fork
      begin : aw_driver
        integer i;
        integer d;
        for (i = 0; i < 10; i = i + 1) begin
          d = $urandom_range(0, 5);
          axi_aw(i, d);
        end
      end

      begin : w_driver
        integer i;
        integer d;
        for (i = 0; i < 10; i = i + 1) begin
          d = $urandom_range(0, 5);
          axi_w(i, 4'hF, d);
        end
      end

      begin : b_driver
        integer i;
        integer d;
        reg [1:0] resp;
        for (i = 0; i < 10; i = i + 1) begin
          d = $urandom_range(0, 5);
          axi_b(resp, d);
        end
      end

      begin : int_wr_dirver
        integer i;
        integer d;
        for (i = 0; i < 10; i = i + 1) begin
          d = $urandom_range(0, 5);
          int_write(0, d);
        end
      end
    join

    #1000;
    $finish;
  end

endmodule

`default_nettype wire
