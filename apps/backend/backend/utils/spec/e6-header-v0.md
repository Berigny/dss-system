# E6 Header v0 (Normative)

Status: Draft v0  
Scope: 16-byte fixed header for E6 routing/gating on edge and server ingress.

This spec is canonical for `backend/fieldx_kernel/e6_packet.py`.

## 1. Byte Size and Order

- Header size is exactly 16 bytes.
- Byte order is big-endian.
- Bit numbering in this spec uses `[127:0]` (MSB to LSB).

## 2. Field Layout

| Bits | Size | Name | Type | Notes |
| --- | ---: | --- | --- | --- |
| 127..112 | 16 | `magic` | u16 | MUST be `0x4347` |
| 111..108 | 4 | `ver` | u4 | protocol version, v0 uses `1` |
| 107..104 | 4 | `fmt` | u4 | header format, v0 uses `0` |
| 103..102 | 2 | `mode` | u2 | `0=HALT,1=PROBE,2=STABILISE,3=EXPRESS` |
| 101..100 | 2 | `ptype` | u2 | `0=WU,1=HR,2=PP,3=CA` |
| 99..98 | 2 | `law` | u2 | lawfulness level `0..3` |
| 97..96 | 2 | `route` | u2 | `0=block,1=quarantine,2=local_commit,3=ledger_commit` |
| 95..92 | 4 | `node` | u4 | source node id `0..15` |
| 91..88 | 4 | `flags` | u4 | bit0=`K`, bit1=`P`, bit2=`E`, bit3=`valid` |
| 87..80 | 8 | `dW` | i8 | signed two's complement |
| 79..56 | 24 | `seq` | u24 | monotonic per `(ledger_id, origin_repo)` stream |
| 55..32 | 24 | `t_ms` | u24 | local time modulo `2^24` ms |
| 31..16 | 16 | `V_q` | u16 | Q0.16 value in `[0, 65535]` |
| 15..0 | 16 | `crc16` | u16 | CRC-16/CCITT-FALSE over bytes `0..13` |

## 3. Packing

- Byte 2 MUST pack `ver` high nibble and `fmt` low nibble.
- Byte 3 MUST pack `mode|ptype|law|route` as four 2-bit values:
  - `b3 = (mode<<6) | (ptype<<4) | (law<<2) | route`
- Byte 4 MUST pack `node` high nibble and `flags` low nibble:
  - `b4 = (node<<4) | flags`
- `dW` MUST be encoded as signed int8 two's complement.
- `seq` and `t_ms` MUST be encoded as big-endian u24.
- `V_q` MUST be encoded as big-endian u16.

## 4. CRC

CRC profile MUST be CRC-16/CCITT-FALSE:

- polynomial: `0x1021`
- init: `0xFFFF`
- xorout: `0x0000`
- reflected input/output: no

Validation rule:

- Receiver MUST compute expected CRC over bytes `0..13`.
- Packet is syntactically valid only if computed CRC equals bytes `14..15`.

## 5. Validation Rules

Ingress parser MUST:

1. Reject any payload not exactly 16 bytes.
2. Parse all fields even on CRC mismatch (for diagnostics).
3. Mark packet invalid if:
   - `magic != 0x4347`, or
   - CRC mismatch.
4. Expose `K`, `P`, `E`, `valid` as derived flag bits.

## 6. Semantics

- `route` is an E6 decision output, not transport destination metadata.
- `seq` uniqueness is scoped to origin stream; it is not globally unique.
- `t_ms` is advisory only and MUST NOT be used as sole conflict arbiter.

## 7. Compatibility

- v0 implementations MUST ignore unknown higher `ver/fmt` on write.
- Readers MAY parse higher versions only if explicitly supported.
- All v0 writers SHOULD emit `ver=1`, `fmt=0`.

## 8. Reference Implementation

- `backend/fieldx_kernel/e6_packet.py`
- `backend/tests/test_e6_packet.py`
- `backend/tests/test_e6_ingress_validation.py`
