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
    output reg  [  ADDR_WIDTH-1:0] int_addr,
    output reg  [  DATA_WIDTH-1:0] int_wr_data,
    output reg  [DATA_WIDTH/8-1:0] int_wr_strb,
    output reg                     int_wr_en,
    output reg                     int_rd_en,
    //
    input  wire                    int_wr_ack,
    input  wire                    int_wr_err,
    //
    input  wire                    int_rd_ack,
    input  wire                    int_rd_err,
    input  wire [  DATA_WIDTH-1:0] int_rd_data
);

  wire                    aclk;
  wire                    aresetn;

  reg                     init_n;

  // AXI Signals

  wire                    aw_hsk;
  reg  [  ADDR_WIDTH-1:0] aw_addr;
  reg                     aw_ready;
  reg                     aw_req;

  wire                    w_hsk;
  reg  [  DATA_WIDTH-1:0] w_data;
  reg  [DATA_WIDTH/8-1:0] w_strb;
  reg                     w_ready;
  reg                     w_req;

  wire                    b_hsk;
  reg  [             1:0] b_resp;
  reg                     b_valid;

  wire                    ar_hsk;
  reg  [  ADDR_WIDTH-1:0] ar_addr;
  reg                     ar_ready;
  reg                     ar_req;

  wire                    r_hsk;
  reg  [  DATA_WIDTH-1:0] r_data;
  reg  [             1:0] r_resp;
  reg                     r_valid;

  // Internal signals

  reg                     int_wr_req;
  reg                     int_wr_pend;
  reg                     int_wr_err_reg;

  reg                     int_rd_req;
  reg                     int_rd_pend;
  reg                     int_rd_err_reg;
  reg  [  DATA_WIDTH-1:0] int_rd_data_reg;


  // AXI4-Lite Interface
  //--------------------

  assign aclk    = s_axi_aclk;
  assign aresetn = s_axi_aresetn;

  // Out of reset initialize

  always @(posedge aclk) begin
    if (!aresetn) begin
      init_n <= 1'b0;
    end else begin
      init_n <= 1'b1;
    end
  end

  // Write address

  assign aw_hsk        = (s_axi_awvalid & s_axi_awready);
  assign s_axi_awready = aw_ready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      aw_addr <= {ADDR_WIDTH{1'b0}};
    end else if (aw_hsk) begin
      aw_addr <= s_axi_awaddr;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      aw_req <= 1'b0;
    end else if (aw_hsk) begin
      aw_req <= 1'b1;
    end else if (aw_req && w_req && ~int_wr_req) begin
      aw_req <= 1'b0;
    end else begin
      aw_req <= aw_req;
    end
  end

  // aw_ready = ~aw_req, unless under reset
  always @(posedge aclk) begin
    if (!aresetn) begin
      aw_ready <= 1'b0;
    end else if (~init_n) begin
      aw_ready <= 1'b1;
    end else if (aw_hsk) begin
      aw_ready <= 1'b0;
    end else if (aw_req && w_req && ~int_wr_req) begin
      aw_ready <= 1'b1;
    end else begin
      aw_ready <= aw_ready;
    end
  end

  // Write data

  assign w_hsk        = (s_axi_wvalid & s_axi_wready);
  assign s_axi_wready = w_ready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      w_data <= {DATA_WIDTH{1'b0}};
    end else if (w_hsk) begin
      w_data <= s_axi_wdata;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      w_strb <= {DATA_WIDTH / 8{1'b0}};
    end else if (w_hsk) begin
      w_strb <= s_axi_wstrb;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      w_req <= 1'b0;
    end else if (w_hsk) begin
      w_req <= 1'b1;
    end else if (aw_req && w_req && ~int_wr_req) begin
      w_req <= 1'b0;
    end else begin
      w_req <= w_req;
    end
  end

  // w_ready = ~w_req, unless under reset
  always @(posedge aclk) begin
    if (!aresetn) begin
      w_ready <= 1'b0;
    end else if (~init_n) begin
      w_ready <= 1'b1;
    end else if (w_hsk) begin
      w_ready <= 1'b0;
    end else if (aw_req && w_req && ~int_wr_req) begin
      w_ready <= 1'b1;
    end else begin
      w_ready <= w_ready;
    end
  end

  // Write response

  assign b_hsk        = (s_axi_bvalid && s_axi_bready);
  assign s_axi_bresp  = b_resp;
  assign s_axi_bvalid = b_valid;

  always @(posedge aclk) begin
    if (!aresetn) begin
      b_resp <= 2'b00;
    end else if (~b_valid && int_wr_pend) begin
      b_resp <= {2{int_wr_err_reg}};
    end else if (~b_valid && int_wr_req && int_wr_ack) begin
      b_resp <= {2{int_wr_err}};
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      b_valid <= 1'b0;
    end else if (b_hsk) begin
      b_valid <= 1'b0;
    end else if (int_wr_req && int_wr_ack || int_wr_pend) begin
      b_valid <= 1'b1;
    end else begin
      b_valid <= b_valid;
    end
  end

  // Read address

  assign ar_hsk        = (s_axi_arvalid & s_axi_arready);
  assign s_axi_arready = ar_ready;

  always @(posedge aclk) begin
    if (!aresetn) begin
      ar_addr <= {ADDR_WIDTH{1'b0}};
    end else if (ar_hsk) begin
      ar_addr <= s_axi_araddr;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      ar_req <= 1'b0;
    end else if (ar_hsk) begin
      ar_req <= 1'b1;
    end else if (~(aw_req && w_req) && ar_req && ~int_rd_req) begin
      ar_req <= 1'b0;
    end else begin
      ar_req <= ar_req;
    end
  end

  // ar_ready = ~ar_req, unless under reset
  always @(posedge aclk) begin
    if (!aresetn) begin
      ar_ready <= 1'b0;
    end else if (~init_n) begin
      ar_ready <= 1'b1;
    end else if (ar_hsk) begin
      ar_ready <= 1'b0;
    end else if (~(aw_req && w_req) && ar_req && ~int_rd_req) begin
      ar_ready <= 1'b1;
    end else begin
      ar_ready <= ar_ready;
    end
  end

  // Read response

  assign r_hsk        = (s_axi_rvalid && s_axi_rready);
  assign s_axi_rdata  = r_data;
  assign s_axi_rresp  = r_resp;
  assign s_axi_rvalid = r_valid;

  always @(posedge aclk) begin
    if (!aresetn) begin
      r_data <= {DATA_WIDTH{1'b0}};
    end else if (~r_valid && int_rd_pend) begin
      r_data <= int_rd_data_reg;
    end else if (~r_valid && int_rd_req && int_rd_ack) begin
      r_data <= int_rd_data;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      r_resp <= 2'b00;
    end else if (int_rd_pend && ~r_valid) begin
      r_resp <= {2{int_rd_err_reg}};
    end else if (int_rd_req && int_rd_ack && ~r_valid) begin
      r_resp <= {2{int_rd_err}};
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      r_valid <= 1'b0;
    end else if (r_hsk) begin
      r_valid <= 1'b0;
    end else if (int_rd_req && int_rd_ack || int_rd_pend) begin
      r_valid <= 1'b1;
    end else begin
      r_valid <= r_valid;
    end
  end


  // Internal interface
  //-------------------

  // Write / read arbitration

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_addr <= {ADDR_WIDTH{1'b0}};
    end else if (aw_req && w_req && ~int_wr_req) begin
      int_addr <= aw_addr;
    end else if (~(aw_req && w_req) && ar_req && ~int_rd_req) begin
      int_addr <= ar_addr;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_data <= {DATA_WIDTH{1'b0}};
    end else if (w_req && aw_req && ~int_wr_req) begin
      int_wr_data <= w_data;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_strb <= {DATA_WIDTH / 8{1'b0}};
    end else if (w_req && aw_req && ~int_wr_req) begin
      int_wr_strb <= w_strb;
    end
  end

  always @(posedge aclk) begin
    if (aw_req && w_req && ~int_wr_req) begin
      int_wr_en <= 1'b1;
    end else begin
      int_wr_en <= 1'b0;
    end
  end

  always @(posedge aclk) begin
    if (~(aw_req && w_req) && ar_req && ~int_rd_req) begin
      int_rd_en <= 1'b1;
    end else begin
      int_rd_en <= 1'b0;
    end
  end

  // Response

  always @(posedge aclk) begin
    if (int_wr_req && ~int_wr_pend && int_wr_ack) begin
      int_wr_err_reg <= int_wr_err;
    end
  end

  always @(posedge aclk) begin
    if (int_rd_req && ~int_rd_pend && int_rd_ack) begin
      int_rd_err_reg <= int_rd_err;
    end
  end

  always @(posedge aclk) begin
    if (int_rd_req && ~int_rd_pend && int_rd_ack) begin
      int_rd_data_reg <= int_rd_data;
    end
  end

  // Internal state

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_req <= 1'b0;
    end else if (~int_wr_req && aw_req && w_req) begin
      int_wr_req <= 1'b1;
    end else if (int_wr_req && (int_wr_ack || int_wr_pend) && ~b_valid) begin
      int_wr_req <= 1'b0;
    end else begin
      int_wr_req <= int_wr_req;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_wr_pend <= 1'b0;
    end else if (int_wr_req && int_wr_ack && b_valid) begin
      int_wr_pend <= 1'b1;
    end else if (int_wr_pend && ~b_valid) begin
      int_wr_pend <= 1'b0;
    end else begin
      int_wr_pend <= int_wr_pend;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_rd_req <= 1'b0;
    end else if (~int_rd_req && ~(aw_req && w_req) && ar_req) begin
      int_rd_req <= 1'b1;
    end else if (int_rd_req && (int_rd_ack || int_rd_pend) && ~r_valid) begin
      int_rd_req <= 1'b0;
    end else begin
      int_rd_req <= int_rd_req;
    end
  end

  always @(posedge aclk) begin
    if (!aresetn) begin
      int_rd_pend <= 1'b0;
    end else if (int_rd_req && int_rd_ack && r_valid) begin
      int_rd_pend <= 1'b1;
    end else if (int_rd_pend && ~r_valid) begin
      int_rd_pend <= 1'b0;
    end else begin
      int_rd_pend <= int_rd_pend;
    end
  end

endmodule

`default_nettype wire
