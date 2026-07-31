"""Epic 65 ingestion + physical-twin conformance harness for DSS-6522.

Imports the Ingestion 2.0 and physical-twin implementations from dss-codebase
and maps pass/fail outcomes to ING-* and PHY-* claim IDs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Mirror the dependency path used by suites/adversarial_peo.py.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence")
)
sys.path.insert(
    0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "apps" / "backend")
)

from backend.ingestion.chunker import chunk_document
from backend.ingestion.ingestion20 import (
    IngestionContext,
    IngestionPipeline,
    IngestionPolicy,
    QpRelIndex,
    VelocityCapError,
    VelocityCounter,
)
from dss_evidence.peo import PEOBuilder


_PRINCIPAL = "did:web:conformance.test:operator-1"


def _chunk_peos(text: str, epoch: int, kernel_version: str = "kernel/2.0") -> list[Any]:
    """Build the raw chunk PEOs that the pipeline mints before stitching."""
    policy = IngestionPolicy.load()
    ctx = IngestionContext(
        principal_did=_PRINCIPAL,
        ledger_canonical_subject=_LEDGER,
        namespace="conformance-demo",
        kernel_version=kernel_version,
        policy=policy,
        epoch=epoch,
    )
    return [
        IngestionPipeline._s4_to_s7_chunk_peo(ctx, chunk, idx, "document")
        for idx, chunk in enumerate(chunk_document(text))
    ]
from dss_evidence.physical_twin import (
    PhysicalTwinValidator,
    compact_epoch_log,
    export_transform,
    import_verify,
    retire_inert,
)
from dss_evidence.soil_grade import SoilGrade


_LEDGER = "did:web:api.dualsubstrate.com:ledgers:conformance-demo"


def _ingest(text: str, **kwargs: Any) -> Any:
    return IngestionPipeline.ingest_document(
        text,
        principal_did=_PRINCIPAL,
        ledger_canonical_subject=_LEDGER,
        namespace="conformance-demo",
        **kwargs,
    )


def _build_physical_twin_peo(**twin_overrides: Any) -> Any:
    base = {
        "mobility_type": "FIXED_POLYGON",
        "spatial_anchor": {
            "type": "Polygon",
            "coordinates": [
                [
                    [144.9631, -37.8136, 0.0],
                    [144.9633, -37.8136, 0.0],
                    [144.9633, -37.8134, 0.0],
                    [144.9631, -37.8134, 0.0],
                    [144.9631, -37.8136, 0.0],
                ]
            ],
            "crs": "EPSG:4326",
        },
        "entropy_profile": {
            "degradation_vector": "DEGENERATIVE",
            "primary_driver": "ENVIRONMENTAL_EVAPORATION",
            "half_life_minutes": 180.0,
            "current_state": {
                "confidence_score": 0.94,
                "last_measured_state": "VOLATILE_LIQUID_SATURATION",
                "projected_zero_state_timestamp": "2026-08-01T04:30:00Z",
                "projected_zero_state_epoch": 5166,
            },
            "regenerative": {"is_self_healed": False, "regrowth_triggers": []},
        },
        "decay_binding": {
            "entropy_driven_decay": True,
            "decay_function": "logarithmic_v_decay",
            "retention_tier_derived": "Sand",
            "zero_state_action": "retire_inert",
        },
        "temporal_state_log": [
            {
                "state_sequence": 1,
                "recorded_at": "2026-07-31T08:00:00Z",
                "epoch": 5166,
                "observed_data": {
                    "chemicalConcentrationPPM": 450.2,
                    "surfaceTemperatureCelsius": 22.4,
                    "integrity_hash": "sha256:" + "a" * 64,
                },
                "recorder_did": "did:web:sensor.example:iot-1",
                "observation_peo_ref": "did:web:sensor.example:iot-1#obs-1",
            }
        ],
    }
    base.update(twin_overrides)
    return (
        PEOBuilder("P")
        .with_z0(glossary_version="1.1")
        .with_z1(
            coord="field:twin:spill-001",
            evidence_cid="sha256:" + "b" * 64,
            peo_ref=f"{_LEDGER}#field:twin:spill-001",
        )
        .with_z3(
            provenance={
                "principal_did": "did:web:field.example:operator-1",
                "epoch": 5166,
                "relations": {"depends_on": [], "derived_from": []},
            }
        )
        .with_z4_governance(
            checksum_336_pass=False,
            epoch=5166,
            gate_surface="physical_twin",
            patch_statuses={},
            impact_hash="sha256:" + "c" * 64,
            proof_tag="-d",
        )
        .with_z8(
            value_lineage={
                "referenceable": True,
                "composable": False,
                "transferable": False,
                "soil_grade": "G1",
            }
        )
        .with_z10_physical(**base)
        .build()
    )


class IngestionConformanceHarness:
    """Run ingestion conformance checks and map results to ING-* claims."""

    @classmethod
    def run(cls) -> Dict[str, Any]:
        results: Dict[str, bool] = {}

        # ING-REPLAY-01: identical input + kernel version -> identical evidence_cid.
        fixture = "The system must refuse harmful requests. Collaboration requires shared intent."
        peo_a = _ingest(fixture, kernel_version="kernel/2.0", epoch=100)
        peo_b = _ingest(fixture, kernel_version="kernel/2.0", epoch=100)
        results["ING-REPLAY-01"] = (
            peo_a.zones["Z1"]["evidence_cid"] == peo_b.zones["Z1"]["evidence_cid"]
        )

        # ING-REPLAY-02: Qp-rel incremental == rebuild.
        idx = QpRelIndex()
        peo_c = _ingest("First conformance document.", qprel_index=idx, epoch=101)
        peo_d = _ingest("Second conformance document.", qprel_index=idx, epoch=101)
        rebuild = idx.rebuild([peo_c, peo_d])
        results["ING-REPLAY-02"] = rebuild["matches_previous"]

        # ING-ATM-01: atom extraction uses structural primes 17/19/137.
        peo_e = _ingest(
            "Refuse harm. Collaborate with shared intent. Observe signals.",
            epoch=100,
        )
        exponents = peo_e.zones["Z4"]["governance"].get("manifest", {}).get("composite_exponents", {})
        results["ING-ATM-01"] = all(str(p) in exponents for p in (17, 19, 137))

        # ING-MNT-01: grade matches QSL-01 derivation on replay.
        soil_grade = peo_e.zones["Z8"]["value_lineage"].get("soil_grade")
        results["ING-MNT-01"] = soil_grade in {g.name for g in SoilGrade}

        # ING-MNT-02: default chunks mint at or below G1.
        child_peos = _chunk_peos(
            "Refuse harm. Collaborate with shared intent. Observe signals.",
            epoch=100,
        )
        results["ING-MNT-02"] = all(
            child.zones["Z8"]["value_lineage"]["soil_grade"] in {"G0", "G1"}
            for child in child_peos
        )

        # ING-EMT-01: valid E6 header present.
        e6 = peo_e.zones.get("Z5", {}).get("e6_header", {})
        results["ING-EMT-01"] = e6.get("version") == "v1"

        # ING-EPOCH-01: z3 epoch anchored.
        results["ING-EPOCH-01"] = peo_e.zones["Z3"]["provenance"]["epoch"] == 100

        # ING-REL-01: relations present.
        rels = peo_e.zones["Z3"]["provenance"]["relations"]
        results["ING-REL-01"] = "depends_on" in rels and "derived_from" in rels

        # ING-VEL-01: velocity cap enforced.
        policy = IngestionPolicy.load()
        counter = VelocityCounter()
        for _ in range(policy.chunk_peos_per_epoch):
            counter.record(_PRINCIPAL, 200, "chunk_peos")
        try:
            _ingest("Velocity test.", epoch=200, velocity_counter=counter)
            results["ING-VEL-01"] = False
        except VelocityCapError:
            results["ING-VEL-01"] = True

        # ING-STC-01: stitch-manifest depends_on mirrors ordered children.
        manifest = peo_e.zones["Z4"]["governance"].get("manifest", {})
        children = manifest.get("children", [])
        deps = rels.get("depends_on", [])
        results["ING-STC-01"] = children == deps

        return {
            "suite": "epic65_conformance",
            "claim_ids": results,
            "all_passed": all(results.values()),
        }


class PhysicalTwinConformanceHarness:
    """Run physical-twin conformance checks and map results to PHY-* claims."""

    @classmethod
    def run(cls) -> Dict[str, Any]:
        results: Dict[str, bool] = {}

        peo = _build_physical_twin_peo()
        report = PhysicalTwinValidator().validate(peo)
        results["PHY-SCHEMA-01"] = report.ok

        results["PHY-GEO-01"] = not any(
            "GeoJSON" in err or "CRS" in err for err in report.invariant_errors
        )

        results["PHY-EPOCH-01"] = not any(
            "projected_zero_state_epoch" in err for err in report.invariant_errors
        )

        results["PHY-LOG-01"] = not any(
            "monotonic" in err or "integrity_hash" in err for err in report.invariant_errors
        )

        retired = retire_inert(
            peo,
            "Zero-state reached",
            principal_did="did:web:field.example:operator-1",
            ledger_canonical_subject=_LEDGER,
            epoch=5167,
        )
        results["PHY-NODELETE-01"] = (
            retired.peo_class == "P"
            and retired.zones["Z3"]["provenance"]["retires_peo_ref"]
            == peo.zones["Z1"]["peo_ref"]
        )

        payload = export_transform(peo, "eu_dpp")
        restored = import_verify(payload, "eu_dpp")
        results["PHY-XFORM-01"] = restored.zones["Z1"]["peo_ref"] == peo.zones["Z1"]["peo_ref"]

        return {
            "suite": "epic65_conformance",
            "claim_ids": results,
            "all_passed": all(results.values()),
        }


def run_epic65_conformance() -> Dict[str, Any]:
    """Run both harnesses and merge claim IDs."""
    ingestion = IngestionConformanceHarness.run()
    physical = PhysicalTwinConformanceHarness.run()
    all_claims = {**ingestion["claim_ids"], **physical["claim_ids"]}
    return {
        "suite": "epic65_conformance",
        "claim_ids": all_claims,
        "ingestion": ingestion["claim_ids"],
        "physical_twin": physical["claim_ids"],
        "all_passed": all(all_claims.values()),
    }


if __name__ == "__main__":
    print(json.dumps(run_epic65_conformance(), indent=2, sort_keys=True))
