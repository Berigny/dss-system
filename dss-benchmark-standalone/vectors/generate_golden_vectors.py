"""Generate stable golden vectors for Epic 65 PEO artifacts.

Run from the dss-system repo root with dss-codebase/packages on PYTHONPATH:

    PYTHONPATH=/Users/davidberigny/Documents/GitHub/dss-codebase/packages/evidence \
        python dss-benchmark-standalone/vectors/generate_golden_vectors.py

The generated files are committed as test fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence"))

from dss_evidence.cid import mint_cid
from dss_evidence.closure_fingerprint import ClosureFingerprint
from dss_evidence.peo import PEOBuilder, mint_peo_ref
from dss_evidence.peo_exchange import PEOExporter


LEDGER_DID = "did:web:api.dualsubstrate.com:ledgers:chat-demo"


def _build_peo_e():
    return (
        PEOBuilder("E")
        .with_z0(glossary_version="1.0")
        .with_z1(
            coord="chat-demo:GV-001",
            evidence_cid="sha256:" + "a" * 64,
            peo_ref=mint_peo_ref(LEDGER_DID, "chat-demo:GV-001"),
            runtime_identity={"principal_did": "did:key:z6Mkgolden"},
        )
        .with_z2(
            content_cids={
                "user_message": "sha256:" + "u" * 64,
                "assistant_reply": "sha256:" + "r" * 64,
            }
        )
        .with_z3(
            provenance={
                "ledger_id": "chat-demo",
                "principal_did": "did:key:z6Mkgolden",
                "parent_hash": "sha256:" + "p" * 64,
                "epoch": 42,
            }
        )
        .with_z4_governance(
            checksum_336_pass=True,
            A=0.8,
            U=0.7,
            E=0.9,
            V=0.6,
            epoch=42,
            gate_surface="chat",
            patch_statuses={"eq6": True, "eq7": True, "eq8": True},
            impact_hash="sha256:" + "i" * 64,
            proof_tag="+D",
        )
        .with_z5(e6_header={"mode": 3, "route": 3})
        .with_z6(
            prime_coordinate={
                "body_prime": 17,
                "token_prime_product": "12345678901234567890",
            }
        )
        .with_z7(
            defeasible_fragment={
                "closure_alg": "elephant.closure.v1",
                "strict_rules": [
                    {
                        "id": "r-336",
                        "body": ["and", "eq6-awared", "eq7-united", "eq8-admissible"],
                        "head": "structurally-valid",
                    }
                ],
                "proof_status": "+d",
                "linter_warnings": [],
            }
        )
        .with_z8(value_lineage={"construction_value": 1.0})
        .with_z9_efficiency(
            gen_output_tokens=100,
            max_tokens=200,
            latency_ms=500.0,
            cost_usd=0.01,
            tokens_in=50,
            tokens_out=100,
            finish_reason="stop",
        )
        .build()
    )


def main() -> None:
    out_dir = Path(__file__).parent
    peo = _build_peo_e()
    peo_with_fp = _add_closure_fingerprint(peo)

    (out_dir / "peo-envelope-gv.json").write_text(
        json.dumps(peo_with_fp.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    fingerprint = ClosureFingerprint.compute(peo_with_fp)
    (out_dir / "closure-fingerprint-gv.json").write_text(
        json.dumps(
            {
                "peo_ref": peo_with_fp.zones["Z1"]["peo_ref"],
                "closure_fingerprint": fingerprint,
                "tag_set": ClosureFingerprint.tag_set(peo_with_fp),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exporter = PEOExporter()
    export_peo = exporter.export(
        peo_with_fp,
        principal_did="did:key:z6Mkgolden",
        signing_key=b"golden-export-key-32-bytes!!",
        origin_ledger_did=LEDGER_DID,
        epoch_root_ref="epoch:42:root",
        export_coord="chat-demo:GV-EXPORT-001",
    )
    (out_dir / "export-signature-gv.json").write_text(
        json.dumps(
            {
                "peo_ref": export_peo.zones["Z1"]["peo_ref"],
                "export_signature": export_peo.zones["Z4"]["governance"]["export_signature"],
                "exported_at": export_peo.zones["Z3"]["provenance"]["exported_at"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"Wrote golden vectors to {out_dir}")


def _add_closure_fingerprint(peo):
    fp = ClosureFingerprint.compute(peo)
    zones = {k: dict(v) for k, v in peo.zones.items()}
    z4 = dict(zones["Z4"]["governance"])
    z4["closure_fingerprint"] = fp
    zones["Z4"] = {"governance": z4}
    from dss_evidence.peo import PEO

    return PEO(peo_class=peo.peo_class, zones=zones)


if __name__ == "__main__":
    main()
