# TODO

- **P0:** Planned and important
- **P1:** Planned but not important
- **P2:** Not planned, may never do

## TODO List

- [P1] Support SystemRDL side-effect semantics (formerly KI-06), including
  `onread`, `onwrite`, write-one-to-clear/set, read-clear, `singlepulse`, and
  write-once access (`sw=rw1` and `sw=w1`) for fields and memories where applicable.
  Add RTL implementation and regression tests for these behaviors. Until
  implemented, generation continues with `WARNING` messages identifying the
  affected component path and unsupported property (`onread`, `onwrite`,
  `singlepulse`, `sw=rw1`, or `sw=w1`); quiet mode suppresses these warnings.
  Successful generation does not mean side effects are implemented; do not rely
  on them in generated hardware.
- [P2] Support registers spanning multiple aligned 32-bit AXI words, including narrow
  registers that straddle a word boundary (e.g. 16 bits at `0x3`) and registers
  wider than 32 bits (e.g. `regwidth=64; accesswidth=32`). Preserve logical fields
  and hardware ports while adding per-word read slices, byte-lane write masks,
  and regression tests. Until implemented, the CLI and `convert()` reject these
  layouts before rendering, even when only low register bits contain fields.
  This is a generator limitation, not invalid SystemRDL. Packed narrow registers
  contained within one word use byte-lane mapping; C field addresses, masks, and
  offsets describe aligned 32-bit MMIO words.
- [P2] Consider supporting 16-bit and 8-bit memory entries, and entry widths
  that are multiples of 32 bits. Require memory bases to be 16-bit (2-byte),
  8-bit (1-byte), and 32-bit (4-byte) aligned, respectively. Until implemented,
  the CLI and `convert()` reject memories unless `memwidth=32` and the absolute
  base address is a multiple of 4 bytes.
