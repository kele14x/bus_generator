// File: axi4l_int.v
// Brief: AXI4-Lite to Internal Interface
`timescale 1 ns / 1 ps
//
`default_nettype none

module axi4l_int #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer DATA_WIDTH = 32
) (
    // AXI4-Lite Slave Interface
    input  wire                    s_axi_aclk,
    input  wire                    s_axi_aresetn,
    //
    input  wire [  ADDR_WIDTH-1:0] s_axi_awaddr,
    // input  wire [             2:0] s_axi_awprot,
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,
    //
    input  wire [  DATA_WIDTH-1:0] s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,
    //
    output wire [             1:0] s_axi_bresp,
    output wire                    s_axi_bvalid,
    input  wire                    s_axi_bready,
    //
    input  wire [  ADDR_WIDTH-1:0] s_axi_araddr,
    // input  wire [             2:0] s_axi_arprot,
    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,
    //
    output wire [  DATA_WIDTH-1:0] s_axi_rdata,
    output wire [             1:0] s_axi_rresp,
    output wire                    s_axi_rvalid,
    input  wire                    s_axi_rready,
    // Internal Interface
    output wire [  ADDR_WIDTH-1:0] int_addr,
    output wire [  DATA_WIDTH-1:0] int_wr_data,
    output wire [DATA_WIDTH/8-1:0] int_wr_strb,
    output wire                    int_wr_en,
    output wire                    int_rd_en,
    //
    input  wire                    int_wr_ack,
    input  wire                    int_wr_err,
    //
    input  wire                    int_rd_ack,
    input  wire                    int_rd_err,
    input  wire [  DATA_WIDTH-1:0] int_rd_data
);

  // Signals
  //--------

  wire                    aclk;
  wire                    aresetn;

  // AW buffer

  reg  [  ADDR_WIDTH-1:0] awaddr;
  reg                     awready;

  wire                    aw_hsk;
  wire [  ADDR_WIDTH-1:0] aw_addr;
  wire                    aw_vld;
  wire                    aw_rdy;
  reg                     aw_pending;

  // W buffer

  reg  [  DATA_WIDTH-1:0] wdata;
  reg  [DATA_WIDTH/8-1:0] wstrb;
  reg                     wready;

  wire                    w_hsk;
  wire [  DATA_WIDTH-1:0] w_data;
  wire [DATA_WIDTH/8-1:0] w_strb;
  wire                    w_vld;
  wire                    w_rdy;
  reg                     w_pending;

  // AR buffer

  reg  [  ADDR_WIDTH-1:0] araddr;
  reg                     arready;

  wire                    ar_hsk;
  wire [  ADDR_WIDTH-1:0] ar_addr;
  wire                    ar_vld;
  wire                    ar_rdy;
  reg                     ar_pending;

  // Write read arbitration

  reg                     rr_state;

  wire [  ADDR_WIDTH-1:0] wrr_addr;
  wire [  DATA_WIDTH-1:0] wrr_data;
  wire [DATA_WIDTH/8-1:0] wrr_strb;
  wire                    wrr_wrn;
  wire                    wrr_vld;
  wire                    wrr_rdy;

  // Internal signals

  reg  [  ADDR_WIDTH-1:0] int_addr_r;
  reg  [  DATA_WIDTH-1:0] int_wr_data_r;
  reg  [DATA_WIDTH/8-1:0] int_wr_strb_r;
  reg                     int_wr_en_r;
  reg                     int_rd_en_r;

  reg                     int_wr_req;
  reg                     int_rd_req;

  // B buffer

  reg  [             1:0] bresp;
  reg                     bvalid;

  wire                    b_err;
  wire                    b_vld;
  wire                    b_rdy;

  // R buffer

  reg  [  DATA_WIDTH-1:0] rdata;
  reg  [             1:0] rresp;
  reg                     rvalid;

  wire [  DATA_WIDTH-1:0] r_data;
  wire                    r_err;
  wire                    r_vld;
  wire                    r_rdy;

  // Main
  //-----

  // AXI4-Lite Interface

  assign aclk    = s_axi_aclk;
  assign aresetn = s_axi_aresetn;

  // AW buffer

  assign s_axi_awready = awready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      awaddr <= {ADDR_WIDTH{1'b0}};
    end else if (aw_hsk && !aw_rdy) begin
      awaddr <= s_axi_awaddr;
    end
  end

  // aw_pending, awready
  //   = 0, 0: under reset
  //   = 1, 0: awaddr stored, waiting for `aw_rdy`
  //   = 0, 1: ready to accept new address
  //   = 1, 1: illegal state
  always @(posedge aclk) begin
    if (!aresetn) begin
      aw_pending <= 1'b0;
    end else if (aw_hsk && !aw_rdy) begin
      aw_pending <= 1'b1;
    end else if (aw_pending && !aw_rdy) begin
      aw_pending <= 1'b1;
    end else begin
      aw_pending <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      awready <= 1'b0;
    end else if (aw_hsk && !aw_rdy) begin
      awready <= 1'b0;
    end else if (aw_pending && !aw_rdy) begin
      awready <= 1'b0;
    end else begin
      awready <= 1'b1;
    end
  end

  assign aw_hsk       = s_axi_awvalid && s_axi_awready;
  assign aw_addr      = aw_pending ? awaddr : s_axi_awaddr;
  assign aw_vld       = aw_hsk || aw_pending;

  // W buffer

  assign s_axi_wready = wready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      wdata <= {DATA_WIDTH{1'b0}};
    end else if (w_hsk && !w_rdy) begin
      wdata <= s_axi_wdata;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      wstrb <= {DATA_WIDTH / 8{1'b0}};
    end else if (w_hsk && !w_rdy) begin
      wstrb <= s_axi_wstrb;
    end
  end

  // w_pending, wready
  //   = 0, 0: under reset
  //   = 1, 0: wdata stored, waiting for `w_rdy`
  //   = 0, 1: ready to accept new data
  //   = 1, 1: illegal state
  always @(posedge aclk) begin
    if (!aresetn) begin
      w_pending <= 1'b0;
    end else if (w_hsk && !w_rdy) begin
      w_pending <= 1'b1;
    end else if (w_pending && !w_rdy) begin
      w_pending <= 1'b1;
    end else begin
      w_pending <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      wready <= 1'b0;
    end else if (w_hsk && !w_rdy) begin
      wready <= 1'b0;
    end else if (w_pending && !w_rdy) begin
      wready <= 1'b0;
    end else begin
      wready <= 1'b1;
    end
  end

  assign w_hsk         = s_axi_wvalid && s_axi_wready;
  assign w_data        = w_pending ? wdata : s_axi_wdata;
  assign w_strb        = w_pending ? wstrb : s_axi_wstrb;
  assign w_vld         = w_hsk || w_pending;

  // R buffer

  assign s_axi_arready = arready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      araddr <= {ADDR_WIDTH{1'b0}};
    end else if (ar_hsk && !ar_rdy) begin
      araddr <= s_axi_araddr;
    end
  end

  // ar_pending, arready
  //   = 0, 0: under reset
  //   = 1, 0: wdata stored, waiting for `w_rdy`
  //   = 0, 1: ready to accept new data
  //   = 1, 1: illegal state
  always @(posedge aclk) begin
    if (!aresetn) begin
      ar_pending <= 1'b0;
    end else if (ar_hsk && !ar_rdy) begin
      ar_pending <= 1'b1;
    end else if (ar_pending && !ar_rdy) begin
      ar_pending <= 1'b1;
    end else begin
      ar_pending <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      arready <= 1'b0;
    end else if (ar_hsk && !ar_rdy) begin
      arready <= 1'b0;
    end else if (ar_pending && !ar_rdy) begin
      arready <= 1'b0;
    end else begin
      arready <= 1'b1;
    end
  end

  assign ar_hsk  = (s_axi_arvalid & s_axi_arready);
  assign ar_addr = ar_pending ? araddr : s_axi_araddr;
  assign ar_vld  = ar_hsk || ar_pending;

  // Write / read arbitration

  always @(posedge aclk) begin
    if (!aresetn) begin
      rr_state <= 1'b0;  // Write has priority
    end else if (wrr_vld && wrr_rdy) begin
      rr_state <= !wrr_wrn;
    end
  end

  assign wrr_addr = wrr_wrn ? ar_addr : aw_addr;
  assign wrr_data = w_data;
  assign wrr_strb = w_strb;
  assign wrr_wrn  = ar_vld && rr_state;
  assign wrr_vld  = (aw_vld && w_vld) || ar_vld;

  assign aw_rdy   = aw_vld && w_vld && wrr_rdy && (!rr_state || !ar_vld);
  assign w_rdy    = aw_vld && w_vld && wrr_rdy && (!rr_state || !ar_vld);
  assign ar_rdy   = ar_vld && wrr_rdy && (rr_state || !(aw_vld && w_vld));

  // Internal interface

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_addr_r <= {ADDR_WIDTH{1'b0}};
    end else if (wrr_vld && wrr_rdy) begin
      int_addr_r <= wrr_addr;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_data_r <= {DATA_WIDTH{1'b0}};
    end else if (wrr_vld && wrr_rdy && !wrr_wrn) begin
      int_wr_data_r <= wrr_data;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_strb_r <= {DATA_WIDTH / 8{1'b0}};
    end else if (wrr_vld && wrr_rdy && !wrr_wrn) begin
      int_wr_strb_r <= wrr_strb;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_en_r <= 1'b0;
    end else if (wrr_vld && wrr_rdy && !wrr_wrn) begin
      int_wr_en_r <= 1'b1;
    end else begin
      int_wr_en_r <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_rd_en_r <= 1'b0;
    end else if (wrr_vld && wrr_rdy && wrr_wrn) begin
      int_rd_en_r <= 1'b1;
    end else begin
      int_rd_en_r <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_req <= 1'b0;
    end else if (wrr_vld && wrr_rdy && !wrr_wrn) begin
      int_wr_req <= 1'b1;
    end else if (int_wr_ack) begin
      int_wr_req <= 1'b0;
    end else begin
      int_wr_req <= int_wr_req;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_rd_req <= 1'b0;
    end else if (wrr_vld && wrr_rdy && wrr_wrn) begin
      int_rd_req <= 1'b1;
    end else if (int_rd_ack) begin
      int_rd_req <= 1'b0;
    end else begin
      int_rd_req <= int_rd_req;
    end
  end

  assign int_addr     = int_addr_r;
  assign int_wr_data  = int_wr_data_r;
  assign int_wr_strb  = int_wr_strb_r;
  assign int_wr_en    = int_wr_en_r;
  assign int_rd_en    = int_rd_en_r;

  assign wrr_rdy      = !int_wr_req && !int_rd_req && (wrr_wrn ? r_rdy : b_rdy);

  assign b_err        = int_wr_err;
  assign b_vld        = int_wr_ack;

  assign r_data       = int_rd_data;
  assign r_err        = int_rd_err;
  assign r_vld        = int_rd_ack;

  // B buffer

  assign s_axi_bresp  = bresp;
  assign s_axi_bvalid = bvalid;

  always @(posedge aclk) begin
    if (!aresetn) begin
      bresp <= 2'b00;
    end else if (b_vld & b_rdy) begin
      bresp <= {2{b_err}};
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      bvalid <= 1'b0;
    end else if (b_vld && b_rdy) begin
      bvalid <= 1'b1;
    end else if (bvalid && !s_axi_bready) begin
      bvalid <= 1'b1;
    end else begin
      bvalid <= 1'b0;
    end
  end

  assign b_rdy        = !bvalid || s_axi_bready;

  // R buffer

  assign s_axi_rdata  = rdata;
  assign s_axi_rresp  = rresp;
  assign s_axi_rvalid = rvalid;

  always @(posedge aclk) begin
    if (!aresetn) begin
      rdata <= {DATA_WIDTH{1'b0}};
    end else if (r_vld && r_rdy) begin
      rdata <= r_data;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      rresp <= 2'b00;
    end else if (r_vld && r_rdy) begin
      rresp <= {2{r_err}};
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      rvalid <= 1'b0;
    end else if (r_vld && r_rdy) begin
      rvalid <= 1'b1;
    end else if (rvalid && !s_axi_rready) begin
      rvalid <= 1'b1;
    end else begin
      rvalid <= 1'b0;
    end
  end

  assign r_rdy = !rvalid || s_axi_rready;

endmodule

`default_nettype wire
