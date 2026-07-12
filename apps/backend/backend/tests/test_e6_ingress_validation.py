from backend.api.enrich import _validate_ingress_e6_header
from backend.fieldx_kernel.e6_packet import pack_header_v0


def test_ingress_validation_missing_header() -> None:
    result = _validate_ingress_e6_header({})
    assert result["mode"] in {"soft", "hard", "off"}
    if result["mode"] != "off":
        assert result["status"] == "missing"


def test_ingress_validation_valid_header() -> None:
    data = pack_header_v0(
        mode=2,
        ptype=3,
        law=2,
        route=2,
        node=4,
        K=1,
        P=1,
        E=1,
        valid=1,
        dW=0,
        seq=42,
        t_ms=99,
        V_q=12345,
    )
    metadata = {
        "e6_header_v0_hex": data.hex(),
        "e6_header_v0_fields": {
            "mode": 2,
            "ptype": 3,
            "law": 2,
            "route": 2,
            "node": 4,
            "K": 1,
            "P": 1,
            "E": 1,
            "valid": 1,
            "dW": 0,
            "seq": 42,
            "V_q": 12345,
        },
    }
    result = _validate_ingress_e6_header(metadata)
    if result["mode"] != "off":
        assert result["status"] == "valid"


def test_ingress_validation_crc_failure() -> None:
    data = bytearray(
        pack_header_v0(
            mode=1,
            ptype=2,
            law=1,
            route=1,
            node=1,
            K=1,
            P=1,
            E=1,
            valid=1,
            dW=1,
            seq=9,
            t_ms=9,
            V_q=9,
        )
    )
    data[5] ^= 0x01
    result = _validate_ingress_e6_header({"e6_header_v0_hex": bytes(data).hex()})
    if result["mode"] != "off":
        assert result["status"] == "invalid"
        assert "bad_crc" in str(result.get("reason"))
