"""Golden-vector integrity tests for Epic 65 artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence"))

from dss_evidence.closure_fingerprint import ClosureFingerprint
from dss_evidence.peo import PEO, PEOValidator


VECTORS_DIR = Path(__file__).resolve().parent.parent / "vectors"


def _load_vector(name: str) -> dict:
    path = VECTORS_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_peo_envelope_golden_vector_validates() -> None:
    data = _load_vector("peo-envelope-gv.json")
    peo = PEO(peo_class=data["peo_class"], zones={k: v for k, v in data.items() if k != "peo_class"})
    result = PEOValidator().validate(peo)
    assert result.ok, result.errors
    assert peo.peo_class == "E"
    assert peo.zones["Z9"]["efficiency"]["efficiency_eta"] > 0.0


def test_closure_fingerprint_golden_vector_matches() -> None:
    peo_data = _load_vector("peo-envelope-gv.json")
    fp_data = _load_vector("closure-fingerprint-gv.json")
    peo = PEO(peo_class=peo_data["peo_class"], zones={k: v for k, v in peo_data.items() if k != "peo_class"})
    recomputed = ClosureFingerprint.compute(peo)
    assert recomputed == fp_data["closure_fingerprint"]


def test_closure_fingerprint_tamper_detected() -> None:
    peo_data = _load_vector("peo-envelope-gv.json")
    fp_data = _load_vector("closure-fingerprint-gv.json")
    peo_data["Z9"]["efficiency"]["efficiency_eta"] = 0.123
    peo = PEO(peo_class=peo_data["peo_class"], zones={k: v for k, v in peo_data.items() if k != "peo_class"})
    recomputed = ClosureFingerprint.compute(peo)
    assert recomputed != fp_data["closure_fingerprint"]


def test_export_signature_golden_vector_present() -> None:
    sig_data = _load_vector("export-signature-gv.json")
    assert sig_data["export_signature"].startswith("hmac-sha256:")
    assert sig_data["exported_at"]
