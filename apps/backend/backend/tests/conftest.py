"""Shared pytest fixtures and test-wide environment defaults."""

import os

import pytest


# DSS-232: source code no longer hardcodes deployment-specific hosts/DIDs.
# Some functions capture DEFAULT_DID_HOST in default argument values at import
# time, so it must be present before the modules under test are imported.
os.environ.setdefault("DEFAULT_DID_HOST", "id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_HOST", "id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("ISSUER_DID", "did:web:id.dualsubstrate.com")


@pytest.fixture(autouse=True)
def default_test_env(monkeypatch):
    """Provide canonical test defaults without forcing PUBLIC_BASE_URL.

    PUBLIC_BASE_URL is intentionally NOT set here so endpoints that derive
    canonical subjects from the incoming request host continue to see the
    testserver hostname where the test expects it.
    """
    monkeypatch.setenv("DEFAULT_DID_HOST", "id.dualsubstrate.com")
    monkeypatch.setenv("DEFAULT_HOST", "id.dualsubstrate.com")
    monkeypatch.setenv("DEFAULT_ISSUER_DID", "did:web:id.dualsubstrate.com")
    monkeypatch.setenv("ISSUER_DID", "did:web:id.dualsubstrate.com")
    monkeypatch.setenv("BASE_DOMAIN", "dualsubstrate.com")
    monkeypatch.setenv(
        "AUTH_WEBAUTHN_ALLOWED_ORIGINS",
        "https://id.dualsubstrate.com,https://chat.dualsubstrate.com,http://localhost:3000",
    )
    # Preserve any explicitly configured value
    if not os.getenv("PUBLIC_BASE_URL"):
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
