"""24-hour drift test pattern for PoE value replay equivalence.

Verifies that replaying the same ledger twice produces identical value states,
and that cache invalidation / recompute does not drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "poe"))

from dss_evidence.peo import PEOBuilder, mint_peo_ref
from dss_poe.value import PoEValue


_LEDGER_DID = "did:web:api.dualsubstrate.com:ledgers:chat-demo"


def _build_subject_peo(coord: str, principal_did: str, epoch: int) -> Any:
    return (
        PEOBuilder("E")
        .with_z0(glossary_version="1.0")
        .with_z1(
            coord=coord,
            evidence_cid="sha256:" + "a" * 64,
            peo_ref=mint_peo_ref(_LEDGER_DID, coord),
            runtime_identity={"principal_did": principal_did},
        )
        .with_z2(content_cids={"assistant_reply": "sha256:" + "r" * 64})
        .with_z3(
            provenance={
                "ledger_id": "chat-demo",
                "principal_did": principal_did,
                "parent_hash": "sha256:" + "p" * 64,
                "epoch": epoch,
            }
        )
        .with_z4_governance(
            checksum_336_pass=True,
            A=0.8,
            U=0.7,
            E=0.9,
            V=0.6,
            epoch=epoch,
            gate_surface="chat",
            patch_statuses={"eq6": True, "eq7": True, "eq8": True},
            impact_hash="sha256:" + "i" * 64,
            proof_tag="+D",
        )
        .with_z5(e6_header={"mode": 3, "route": 3})
        .with_z6(prime_coordinate={"body_prime": 17, "token_prime_product": "12345678901234567890"})
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
        .with_z8(value_lineage={"construction_value": 1.0, "soil_grade": "G4"})
        .with_z9_efficiency(
            gen_output_tokens=100,
            max_tokens=200,
            latency_ms=500.0,
            cost_usd=0.01,
        )
        .build()
    )


def _build_reuse_event(subject_peo: Any, principal_did: str, delta_value: float, epoch: int) -> Any:
    coord = f"chat-demo:DRIFT-REUSE-{principal_did[-8:]}-{epoch}"
    return (
        PEOBuilder("T")
        .with_z0(glossary_version="1.0")
        .with_z1(
            coord=coord,
            evidence_cid="sha256:" + "e" * 64,
            peo_ref=mint_peo_ref(_LEDGER_DID, coord),
            runtime_identity={"principal_did": principal_did},
        )
        .with_z3(
            provenance={
                "enrichment_event": "REUSE",
                "subject_peo_ref": subject_peo.zones["Z1"]["peo_ref"],
                "principal_did": principal_did,
                "epoch": epoch,
            }
        )
        .with_z4_governance(checksum_336_pass=True, epoch=epoch)
        .with_z5(e6_header={"mode": 3, "route": 3})
        .with_z8(value_lineage={"delta_value": delta_value})
        .build()
    )


def test_value_replay_is_stable_across_replays() -> None:
    """Kill-the-cache pattern: replay twice, expect identical value state."""
    subject = _build_subject_peo("chat-demo:DRIFT-SUBJECT-001", "did:key:z6Mkauthor", epoch=1)
    reuse = _build_reuse_event(subject, "did:key:z6Mkreuser", delta_value=0.5, epoch=1)
    ledger = [subject, reuse]

    state1 = PoEValue.replay(ledger, peo_ref=subject.zones["Z1"]["peo_ref"])
    state2 = PoEValue.replay(ledger, peo_ref=subject.zones["Z1"]["peo_ref"])

    assert state1 == state2
    assert state1.construction_value > 0.0
    assert state1.enriched_value == 0.5


def test_value_replay_zero_drift_after_24h_simulation() -> None:
    """Simulate 24h later by replaying the same immutable ledger; drift must be zero."""
    subject = _build_subject_peo("chat-demo:DRIFT-SUBJECT-002", "did:key:z6Mkauthor", epoch=1)
    reuses = [
        _build_reuse_event(subject, f"did:key:z6Mkreuser{i}", delta_value=0.5, epoch=1)
        for i in range(3)
    ]
    ledger = [subject] + reuses

    baseline = PoEValue.replay(ledger, peo_ref=subject.zones["Z1"]["peo_ref"])
    # Simulate second pass (e.g. after cache expiry / 24h) by reconstructing ledger.
    replayed = PoEValue.replay(list(ledger), peo_ref=subject.zones["Z1"]["peo_ref"])

    assert replayed.construction_value == baseline.construction_value
    assert replayed.enriched_value == baseline.enriched_value
    assert replayed.gravity_tax == baseline.gravity_tax
    assert replayed.vested_value == baseline.vested_value
    assert replayed.events == baseline.events
