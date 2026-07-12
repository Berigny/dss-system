"""Shared pytest fixtures and test-wide environment defaults."""

from __future__ import annotations

import os

import pytest

from utils.qp_pure_metrics import qp_pure_metrics


# DSS-232: source code no longer hardcodes deployment-specific hosts/DIDs.
# The middleware app reads several constants at import time, so these defaults
# must be present before `app.py` is imported by the test modules.
os.environ.setdefault("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("WALT_ID_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_ORGANISATION_URI", "https://dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_ORGANISATION_URI", "https://dualsubstrate.com")
os.environ.setdefault("BASE_DOMAIN", "dualsubstrate.com")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")


@pytest.fixture(autouse=True)
def _reset_qp_pure_metrics() -> None:
    """Reset qp_pure telemetry between tests so thresholds stay deterministic."""
    qp_pure_metrics.reset()
    yield
