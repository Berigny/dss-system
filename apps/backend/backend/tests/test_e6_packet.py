from backend.fieldx_kernel.e6_packet import MAGIC_V0, pack_header_v0, unpack_header_v0


def test_pack_unpack_roundtrip_v0() -> None:
    data = pack_header_v0(
        mode=2,
        ptype=3,
        law=1,
        route=2,
        node=7,
        K=1,
        P=1,
        E=0,
        valid=1,
        dW=-1,
        seq=0x00A1B2,
        t_ms=0x000102,
        V_q=50000,
    )

    assert len(data) == 16
    parsed = unpack_header_v0(data)
    assert parsed['magic'] == MAGIC_V0
    assert parsed['mode'] == 2
    assert parsed['ptype'] == 3
    assert parsed['law'] == 1
    assert parsed['route'] == 2
    assert parsed['node'] == 7
    assert parsed['K'] == 1
    assert parsed['P'] == 1
    assert parsed['E'] == 0
    assert parsed['valid'] == 1
    assert parsed['dW'] == -1
    assert parsed['seq'] == 0x00A1B2
    assert parsed['t_ms'] == 0x000102
    assert parsed['V_q'] == 50000
    assert parsed['crc_ok'] is True


def test_crc_detects_tamper() -> None:
    data = bytearray(
        pack_header_v0(
            mode=3,
            ptype=0,
            law=3,
            route=3,
            node=1,
            K=1,
            P=1,
            E=1,
            valid=1,
            dW=1,
            seq=123,
            t_ms=456,
            V_q=65535,
        )
    )
    data[6] ^= 0x01
    parsed = unpack_header_v0(bytes(data))
    assert parsed['crc_ok'] is False
